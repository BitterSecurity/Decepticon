use axum::{
    Json, Router,
    extract::{ConnectInfo, OriginalUri, State},
    http::{HeaderMap, HeaderName, HeaderValue, StatusCode},
    response::{
        IntoResponse, Response,
        sse::{Event, Sse},
    },
    routing::{get, post},
    serve::ListenerExt,
};
use futures_util::StreamExt;
use serde_json::{Value, json};
use std::{
    collections::BTreeSet,
    convert::Infallible,
    fs,
    net::SocketAddr,
    sync::{
        Arc, Mutex,
        atomic::{AtomicUsize, Ordering},
    },
    time::Duration,
};

type Error = Box<dyn std::error::Error + Send + Sync>;

fn resources() -> Result<Value, Error> {
    let status = fs::read_to_string("/proc/self/status")?;
    let field = |key: &str| -> Result<u64, Error> {
        Ok(status
            .lines()
            .find_map(|l| l.strip_prefix(key))
            .ok_or("missing proc field")?
            .split_whitespace()
            .next()
            .ok_or("field number")?
            .parse()?)
    };
    let mut t = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    // POSIX writes only to this initialized, live timespec.
    if unsafe { libc::clock_gettime(libc::CLOCK_PROCESS_CPUTIME_ID, &mut t) } != 0 {
        return Err(std::io::Error::last_os_error().into());
    }
    Ok(
        json!({"cpu_ns":t.tv_sec as u64*1_000_000_000+t.tv_nsec as u64,
 "peak_rss_kib":field("VmHWM:")?,"rss_kib":field("VmRSS:" )?,
 "threads":field("Threads:")?,"fds":fs::read_dir("/proc/self/fd")?.count()}),
    )
}
struct Shared {
    mode: String,
    scenario: Value,
    base: String,
    http: reqwest::Client,
    attempts: Arc<AtomicUsize>,
    completed: AtomicUsize,
    failures: AtomicUsize,
    peers: Mutex<BTreeSet<SocketAddr>>,
    sse: String,
    active: Arc<AtomicUsize>,
}
async fn metrics(State(s): State<Arc<Shared>>) -> Json<Value> {
    Json(
        json!({"framework":s.mode,"pid":std::process::id(),"resources":resources().expect("proc metrics"),
 "attempts":s.attempts.load(Ordering::Relaxed),"completed":s.completed.load(Ordering::Relaxed),
 "failures":s.failures.load(Ordering::Relaxed),"active":s.active.load(Ordering::Relaxed),"peer_connections":s.peers.lock().expect("peer lock").len()}),
    )
}
fn inspect(request: &reqwest::Request, expected: &Value) -> Result<(), Error> {
    let bytes = request
        .body()
        .and_then(|b| b.as_bytes())
        .ok_or("request body")?;
    let body: Value = serde_json::from_slice(bytes)?;
    if &body != expected {
        eprintln!("Outbound body mismatch: {body}");
        return Err(format!("outbound body differs: {body}").into());
    }
    if request
        .headers()
        .get("content-type")
        .and_then(|v| v.to_str().ok())
        != Some("application/json")
    {
        return Err("content type differs".into());
    }
    Ok(())
}
struct Active(Arc<AtomicUsize>);
impl Drop for Active {
    fn drop(&mut self) {
        self.0.fetch_sub(1, Ordering::Relaxed);
    }
}

async fn fixture(
    State(s): State<Arc<Shared>>,
    ConnectInfo(peer): ConnectInfo<SocketAddr>,
    OriginalUri(uri): OriginalUri,
    headers: HeaderMap,
    Json(body): Json<Value>,
) -> Response {
    s.attempts.fetch_add(1, Ordering::Relaxed);
    s.peers.lock().expect("peer lock").insert(peer);
    if uri.path().starts_with("/reject")
        || body != s.scenario["body"]
        || headers.get("accept-encoding").and_then(|h| h.to_str().ok()) != Some("identity")
    {
        s.failures.fetch_add(1, Ordering::Relaxed);
        return (
            StatusCode::BAD_REQUEST,
            [("content-type", "application/json")],
            "{\"error\":\"request mismatch\"}".to_string(),
        )
            .into_response();
    }
    if uri.path().starts_with("/paced") {
        let frames = s
            .sse
            .split("\n\n")
            .filter(|f| !f.trim().is_empty())
            .map(str::to_owned)
            .collect::<Vec<_>>();
        let stream = async_stream::stream! {
            s.active.fetch_add(1,Ordering::Relaxed);let _active=Active(s.active.clone());
            let mut first_text=true;
            for frame in frames{
                let is_text=frame.starts_with("event: response.output_text.delta\n");
                if is_text&&first_text{tokio::time::sleep(Duration::from_millis(1750)).await;first_text=false;}
                yield Ok::<_,Infallible>(frame+"\n\n");
                if is_text{tokio::time::sleep(Duration::from_millis(5)).await;}
            }
            s.completed.fetch_add(1,Ordering::Relaxed);
        };
        return (
            [("content-type", "text/event-stream")],
            axum::body::Body::from_stream(stream),
        )
            .into_response();
    }
    s.completed.fetch_add(1, Ordering::Relaxed);
    let output = if uri.path().starts_with("/truncated") {
        s.sse
            .split("\n\n")
            .filter(|frame| !frame.contains("event: response.completed"))
            .collect::<Vec<_>>()
            .join("\n\n")
            + "\n\n"
    } else {
        s.sse.clone()
    };
    (
        StatusCode::OK,
        [("content-type", "text/event-stream")],
        output,
    )
        .into_response()
}
async fn baseline(State(s): State<Arc<Shared>>) -> ([(&'static str, &'static str); 1], String) {
    let mut output = String::new();
    for line in s.sse.lines() {
        if let Some(data) = line.strip_prefix("data: ")
            && let Ok(item) = serde_json::from_str::<Value>(data)
            && item["type"] == "response.output_text.delta"
        {
            output.push_str(&format!("data: {}\n\n", json!({"delta":item["delta"]})));
        }
    }
    output.push_str(&format!(
        "data: {}\n\n",
        json!({"done":true,"text":s.scenario["expected"],"model":s.scenario["body"]["model"],"usage":s.scenario["recorded_usage"]})
    ));
    ([("content-type", "text/event-stream")], output)
}

fn event(value: Value) -> Event {
    Event::default().data(value.to_string())
}
fn upstream(s: Arc<Shared>) -> impl futures_util::Stream<Item = Result<Value, Error>> {
    async_stream::try_stream! {
       let mut text=String::new();let mut completed=false;
    let mut model_name=Value::Null;let mut usage=Value::Null;
       let request=s.http.post(format!("{}/responses",s.base)).json(&s.scenario["body"]).build()?;
       s.attempts.fetch_add(1,Ordering::Relaxed);
       inspect(&request,&s.scenario["body"])?;
       let response=s.http.execute(request).await?.error_for_status()?;
       let mut source=response.bytes_stream();let mut buffer=Vec::new();
       while let Some(chunk)=source.next().await{
        buffer.extend_from_slice(&chunk?);
        while let Some(index)=buffer.iter().position(|b|*b==b'\n'){
         let line:Vec<_>=buffer.drain(..=index).collect();
         let line=std::str::from_utf8(&line)?.trim();
         if let Some(data)=line.strip_prefix("data: "){
          if data=="[DONE]"{continue}
          let item:Value=serde_json::from_str(data)?;
          match item["type"].as_str(){
           Some("response.output_text.delta")=>{
            let delta=item["delta"].as_str().ok_or("delta")?;
            text.push_str(delta);yield json!({"delta":delta});
           },
           Some("response.completed")=>{
         completed=true;model_name=item["response"]["model"].clone();
         let u=&item["response"]["usage"];
         usage=json!({"input_tokens":u["input_tokens"],"output_tokens":u["output_tokens"],"total_tokens":u["total_tokens"]});
        },
           Some("error"|"response.failed"|"response.incomplete")=>{
            Err::<(),Error>("upstream incomplete".into())?;
           },
           _=>{}
          }
         }
        }
       }
       if !completed||text!=s.scenario["expected"].as_str().ok_or("expected output")?||model_name!=s.scenario["body"]["model"]{
        Err::<(),Error>("incomplete or incorrect output".into())?;
       }
       yield json!({"done":true,"text":text,"model":model_name,"usage":usage});
      }
}
async fn invoke(
    State(s): State<Arc<Shared>>,
) -> Sse<impl futures_util::Stream<Item = Result<Event, Infallible>>> {
    let stream = async_stream::stream! {
     let source=upstream(s.clone());futures_util::pin_mut!(source);
     while let Some(item)=source.next().await{
      match item{
       Ok(payload)=>{
        if payload["done"]==true{s.completed.fetch_add(1,Ordering::Relaxed);}
        yield Ok(event(payload));
       },
       Err(error)=>{
        s.failures.fetch_add(1,Ordering::Relaxed);
        eprintln!("upstream error: {error}");
        yield Ok(event(json!({"error":"upstream request or stream failed"})));break;
       }
      }
     }
    };
    Sse::new(stream)
}

fn main() -> Result<(), Error> {
    let args: Vec<String> = std::env::args().collect();
    let mode = args.get(1).ok_or("mode")?.clone();
    if !matches!(mode.as_str(), "native" | "fixture") {
        return Err("unsupported server mode".into());
    }
    let port: u16 = args.get(2).ok_or("port")?.parse()?;
    let folder = std::path::Path::new(args.get(3).ok_or("fixture folder")?);
    let scenario: Value = serde_json::from_slice(&fs::read(folder.join("scenario.json"))?)?;
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()?;
    runtime.block_on(async move {
        let mut headers = HeaderMap::new();
        if let Ok(raw) = std::env::var("BENCH_HEADERS") {
            let values: std::collections::BTreeMap<String, String> = serde_json::from_str(&raw)?;
            for (k, v) in values {
                headers.insert(
                    HeaderName::from_bytes(k.as_bytes())?,
                    HeaderValue::from_str(&v)?,
                );
            }
        }
        let pool_limit: usize = std::env::var("BENCH_POOL_LIMIT")
            .unwrap_or_else(|_| "4".into())
            .parse()?;
        let http = reqwest::Client::builder()
            .default_headers(headers)
            .timeout(Duration::from_secs(120))
            .pool_max_idle_per_host(pool_limit)
            .pool_idle_timeout(Duration::from_secs(120))
            .http1_only()
            .no_proxy()
            .build()?;
        let base = std::env::var("BENCH_BASE").unwrap_or_default();
        let attempts = Arc::new(AtomicUsize::new(0));
        let shared = Arc::new(Shared {
            mode: mode.clone(),
            scenario,
            base,
            http,
            attempts,
            completed: AtomicUsize::new(0),
            failures: AtomicUsize::new(0),
            active: Arc::new(AtomicUsize::new(0)),
            peers: Mutex::new(BTreeSet::new()),
            sse: if mode == "fixture" {
                fs::read_to_string(folder.join("recorded.sse"))?
            } else {
                String::new()
            },
        });
        let app = Router::new().route("/metrics", get(metrics));
        let app = if mode == "fixture" {
            app.route("/invoke", post(baseline))
                .route("/responses", post(fixture))
                .route("/paced/responses", post(fixture))
                .route("/reject/responses", post(fixture))
                .route("/truncated/responses", post(fixture))
        } else {
            app.route("/invoke", post(invoke))
        }
        .with_state(shared);
        let listener = tokio::net::TcpListener::bind(("127.0.0.1", port)).await?;
        // Match asyncio's default TCP_NODELAY for small SSE writes.
        let listener = listener.tap_io(|stream| stream.set_nodelay(true).expect("TCP_NODELAY"));
        axum::serve(
            listener,
            app.into_make_service_with_connect_info::<SocketAddr>(),
        )
        .await?;
        Ok::<(), Error>(())
    })
}
