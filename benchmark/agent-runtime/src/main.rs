use serde_json::{Value, json};
use std::io::{self, Write};
type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

fn gate(value: Value) -> Result<String> {
    println!("{value}");
    io::stdout().flush()?;
    let mut line = String::new();
    if io::stdin().read_line(&mut line)? == 0 {
        return Err("closed benchmark gate".into());
    }
    Ok(line)
}

#[cfg(feature = "native-case")]
fn run(c: &Value) -> Result<Value> {
    let client = reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(120))
        .build()?;
    let mut messages = vec![
        json!({"role":"system","content":c["system"]}),
        json!({"role":"user","content":c["prompt"]}),
    ];
    let tools = json!([{"type":"function","function":{"name":"echo","description":"Return the supplied text.","parameters":c["schema"]}}]);
    let mut count = 0;
    for _ in 0..3 {
        let response: Value = client
            .post(format!(
                "{}/chat/completions",
                c["base_url"].as_str().ok_or("base URL")?
            ))
            .json(&json!({"model":c["model"],"messages":messages,"tools":tools,"stream":false}))
            .send()?
            .error_for_status()?
            .json()?;
        let m = response["choices"][0]["message"].clone();
        if m.is_null() {
            return Err("missing assistant response".into());
        }
        messages.push(m.clone());
        if let Some(calls) = m["tool_calls"].as_array().filter(|v| !v.is_empty()) {
            for call in calls {
                if call["function"]["name"] != "echo" {
                    return Err("unexpected tool".into());
                }
                let args: Value = serde_json::from_str(
                    call["function"]["arguments"].as_str().ok_or("arguments")?,
                )?;
                if args["text"] != c["marker"] || count != 0 {
                    return Err("unexpected/repeated tool arguments".into());
                }
                count += 1;
                messages.push(json!({"role":"tool","tool_call_id":call["id"],"content":json!({"text":args["text"]}).to_string()}));
            }
        } else {
            return Ok(json!({"output":m["content"],"tool_calls":count}));
        }
    }
    Err("model turn limit".into())
}

#[cfg(feature = "rig-case")]
fn run(c: &Value) -> Result<Value> {
    use rig::AgentBuilder;
    use rig::tool::{Tool, ToolContext};
    use rig_core::providers::openai::OpenAI;
    #[derive(serde::Deserialize)]
    struct Args {
        text: String,
    }
    struct Echo {
        marker: String,
        count: std::sync::Arc<std::sync::atomic::AtomicUsize>,
        schema: Value,
    }
    impl Tool for Echo {
        const NAME: &'static str = "echo";
        type Args = Args;
        type Output = Value;
        type Error = io::Error;
        fn description(&self) -> String {
            "Return the supplied text.".into()
        }
        fn parameters(&self) -> Value {
            self.schema.clone()
        }
        async fn call(
            &self,
            _: &mut ToolContext,
            args: Args,
        ) -> std::result::Result<Value, io::Error> {
            if args.text != self.marker
                || self.count.fetch_add(1, std::sync::atomic::Ordering::SeqCst) != 0
            {
                return Err(io::Error::other("unexpected/repeated tool arguments"));
            }
            Ok(json!({"text":args.text}))
        }
    }
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()?;
    runtime.block_on(async {
        let model = OpenAI::from_env()?.chat(c["model"].as_str().ok_or("model")?);
        let count = std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let agent = AgentBuilder::new(model)
            .preamble(c["system"].as_str().ok_or("system")?)
            .tool(Echo { marker:c["marker"].as_str().ok_or("marker")?.into(),count:count.clone(),schema:c["schema"].clone() })
            .default_max_turns(3)
            .build();
        let response = agent.prompt(c["prompt"].as_str().ok_or("prompt")?).await?;
        Ok(json!({"output":response.output(),"tool_calls":count.load(std::sync::atomic::Ordering::SeqCst)}))
    })
}

fn main() -> Result<()> {
    let c: Value = serde_json::from_str(&gate(json!({"stage":"ready"}))?)?;
    let result = run(&c)?;
    gate(result)?;
    Ok(())
}
