//! Stateful test framework: planning, isolated children, files, memory, sandbox and durable resumption.
use rusqlite::{Connection, OptionalExtension, params};
use serde::Serialize;
use serde_json::{Value, json};
use std::{
    fs::{self, OpenOptions},
    io::Write,
    path::{Component, Path, PathBuf},
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use tokio::process::Command;
type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

fn resources() -> Result<Value> {
    let status = fs::read_to_string("/proc/self/status")?;
    let smaps = fs::read_to_string("/proc/self/smaps_rollup")?;
    let number = |text: &str, key: &str| -> Result<u64> {
        Ok(text
            .lines()
            .find_map(|line| line.strip_prefix(key))
            .ok_or("proc metric")?
            .split_whitespace()
            .next()
            .ok_or("metric number")?
            .parse()?)
    };
    let mut clock = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    // The initialized timespec is writable and lives through this POSIX call.
    if unsafe { libc::clock_gettime(libc::CLOCK_PROCESS_CPUTIME_ID, &mut clock) } != 0 {
        return Err(std::io::Error::last_os_error().into());
    }
    Ok(
        json!({"cpu_ns":clock.tv_sec as u64*1_000_000_000+clock.tv_nsec as u64,"peak_rss_kib":number(&status,"VmHWM:")?,"rss_kib":number(&status,"VmRSS:")?,"pss_kib":number(&smaps,"Pss:")?,"threads":number(&status,"Threads:")?,"fds":fs::read_dir("/proc/self/fd")?.count()}),
    )
}
#[cfg(feature = "native")]
const ENGINE: &str = "native";
#[cfg(feature = "rig")]
const ENGINE: &str = "rig";

struct Framework {
    root: PathBuf,
    phase: String,
    profile: Value,
    scenario: Value,
    db: Connection,
    #[cfg(feature = "native")]
    client: reqwest::Client,
    #[cfg(feature = "rig")]
    model: rig_core::DynModel<rig_core::operation::Completion>,
}
enum Outcome {
    Paused(Value),
    Done(String),
}

impl Framework {
    fn event(&self, kind: &str, fields: Value) -> Result<()> {
        let mut row = json!({"event":kind,"phase":self.phase,"pid":std::process::id(),"at_unix":SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs_f64(),"engine":ENGINE});
        row.as_object_mut()
            .ok_or("event object")?
            .extend(fields.as_object().ok_or("event fields")?.clone());
        writeln!(
            OpenOptions::new()
                .create(true)
                .append(true)
                .open(self.root.join("events.jsonl"))?,
            "{row}"
        )?;
        Ok(())
    }
    fn load(&self, role: &str) -> Result<Option<(Value, Value, Option<String>)>> {
        let saved: Option<(String, String, Option<String>)> = self
            .db
            .query_row(
                "SELECT state,todos,done FROM checkpoints WHERE role=?1 AND engine=?2",
                params![role, ENGINE],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
            )
            .optional()?;
        saved
            .map(|(state, todos, done)| {
                Ok((
                    serde_json::from_str(&state)?,
                    serde_json::from_str(&todos)?,
                    done,
                ))
            })
            .transpose()
    }
    fn save(
        &self,
        role: &str,
        state: &impl Serialize,
        todos: &Value,
        done: Option<&str>,
    ) -> Result<()> {
        let state = serde_json::to_string(state)?;
        let todos_json = todos.to_string();
        let transaction = self.db.unchecked_transaction()?;
        transaction.execute(
            "INSERT INTO history(role,state,todos,done) VALUES(?1,?2,?3,?4)",
            params![role, &state, &todos_json, done],
        )?;
        transaction.execute("INSERT INTO checkpoints(role,engine,state,todos,done) VALUES(?1,?2,?3,?4,?5) ON CONFLICT(role) DO UPDATE SET engine=excluded.engine,state=excluded.state,todos=excluded.todos,done=excluded.done",params![role,ENGINE,&state,&todos_json,done])?;
        transaction.commit()?;
        self.event(
            "checkpoint.saved",
            json!({"role":role,"todos":todos,"completed":done.is_some()}),
        )
    }
    fn file_path(&self, virtual_path: &str, writing: bool) -> Result<PathBuf> {
        if virtual_path.contains('~') || virtual_path.contains('\\') {
            return Err("invalid virtual path".into());
        }
        let (base, relative) = if let Some(p) = virtual_path.strip_prefix("/memories/") {
            (self.root.join("memory"), p)
        } else {
            (
                self.root.join("files"),
                virtual_path.trim_start_matches('/'),
            )
        };
        let relative = Path::new(relative);
        if relative
            .components()
            .any(|c| !matches!(c, Component::Normal(_)))
        {
            return Err("path traversal refused".into());
        }
        let target = base.join(relative);
        let checked = if writing {
            target.parent().ok_or("parent path")?.canonicalize()?
        } else {
            target.canonicalize()?
        };
        if !checked.starts_with(base.canonicalize()?) || (writing && target.exists()) {
            return Err("file scope or overwrite refused".into());
        }
        Ok(target)
    }
    fn should_pause(&self, role: &str, name: &str, args: &Value) -> bool {
        role == "parent"
            && self.phase == "prepare"
            && name == "read_file"
            && args["file_path"] == "/memories/result.json"
    }
    async fn command(args: &[String]) -> Result<std::process::Output> {
        let mut command = Command::new("docker");
        command.args(args).kill_on_drop(true);
        let output = tokio::time::timeout(Duration::from_secs(30), command.output()).await??;
        if !output.status.success() {
            return Err(
                format!("docker failed: {}", String::from_utf8_lossy(&output.stderr)).into(),
            );
        }
        Ok(output)
    }
    async fn sandbox(&self, role: &str, command: &str) -> Result<String> {
        if role != "inspector" || command != "python3 /work/analyze.py" {
            return Err("sandbox command/role denied".into());
        }
        let name = format!("agent-parity-{}-{}", ENGINE, std::process::id());
        let args = vec![
            "create".into(),
            "--name".into(),
            name.clone(),
            "--network".into(),
            "none".into(),
            "--read-only".into(),
            "--cap-drop".into(),
            "ALL".into(),
            "--security-opt".into(),
            "no-new-privileges".into(),
            "--memory".into(),
            "128m".into(),
            "--cpus".into(),
            "1".into(),
            "--pids-limit".into(),
            "32".into(),
            "--user".into(),
            "65534:65534".into(),
            "--workdir".into(),
            "/work".into(),
            "--mount".into(),
            format!(
                "type=bind,src={},dst=/work,readonly",
                self.root.join("files").display()
            ),
            "--entrypoint".into(),
            "python3".into(),
            std::env::var("PARITY_SANDBOX_IMAGE")?,
            "/work/analyze.py".into(),
        ];
        let created = Self::command(&args).await?;
        self.event("sandbox.created",json!({"role":role,"container_id":String::from_utf8_lossy(&created.stdout).trim(),"command":command}))?;
        let execution=async {
            let started=Self::command(&["start".into(),"--attach".into(),name.clone()]).await?;
            let inspect=Self::command(&["inspect".into(),name.clone()]).await?;
            let inspected:Value=serde_json::from_slice(&inspect.stdout)?;let c=&inspected[0];
            let keys=["NetworkMode","ReadonlyRootfs","CapDrop","SecurityOpt","Memory","NanoCpus","PidsLimit"];
            let host:serde_json::Map<String,Value>=keys.iter().map(|k|(k.to_string(),c["HostConfig"][*k].clone())).collect();
            self.event("sandbox.finished",json!({"role":role,"container_id":c["Id"],"image":c["Image"],"state":c["State"],"host_config":host,"user":c["Config"]["User"],"output":String::from_utf8_lossy(&started.stdout),"stderr":String::from_utf8_lossy(&started.stderr)}))?;
            Ok::<_,Box<dyn std::error::Error>>(format!("{}\n[Command succeeded with exit code 0]",String::from_utf8(started.stdout)?))
        }.await;
        let removed = Self::command(&["rm".into(), "--force".into(), name.clone()]).await;
        self.event("sandbox.removed", json!({"name":name}))?;
        removed?;
        execution
    }
    async fn tool(
        &self,
        role: &str,
        name: &str,
        args: &Value,
        todos: &mut Value,
    ) -> Result<String> {
        if !self.profile[role]["tools"]
            .as_array()
            .ok_or("tool profile")?
            .iter()
            .any(|t| t["function"]["name"] == name)
        {
            return Err("tool not exposed".into());
        }
        self.event(
            "tool.start",
            json!({"role":role,"name":name,"arguments":args}),
        )?;
        let output = match name {
            "write_todos" => {
                let list = args["todos"].as_array().ok_or("todos list")?;
                for t in list {
                    if !matches!(
                        t["status"].as_str(),
                        Some("pending" | "in_progress" | "completed")
                    ) || !t["content"].is_string()
                    {
                        return Err("invalid todo".into());
                    }
                }
                *todos = args["todos"].clone();
                let rendered = list
                    .iter()
                    .map(|t| {
                        format!(
                            "{{'content': {}, 'status': {}}}",
                            python_quote(t["content"].as_str().unwrap_or_default()),
                            python_quote(t["status"].as_str().unwrap_or_default())
                        )
                    })
                    .collect::<Vec<_>>()
                    .join(", ");
                format!("Updated todo list to [{rendered}]")
            }
            "read_file" => {
                let path = self.file_path(args["file_path"].as_str().ok_or("file path")?, false)?;
                let text = fs::read_to_string(path)?;
                let lines: Vec<_> = text.lines().collect();
                let offset = args["offset"].as_u64().unwrap_or(0) as usize;
                let end =
                    (offset + args["limit"].as_u64().unwrap_or(2000) as usize).min(lines.len());
                if offset >= lines.len() {
                    String::new()
                } else {
                    format!(
                        "@@ lines {}-{} of {} @@\n{}",
                        offset + 1,
                        end,
                        lines.len(),
                        lines[offset..end].join("\n")
                    )
                }
            }
            "write_file" => {
                let virtual_path = args["file_path"].as_str().ok_or("file path")?;
                let path = self.file_path(virtual_path, true)?;
                OpenOptions::new()
                    .create_new(true)
                    .write(true)
                    .open(path)?
                    .write_all(args["content"].as_str().ok_or("file content")?.as_bytes())?;
                format!("Updated file {virtual_path}")
            }
            "execute" => {
                self.sandbox(role, args["command"].as_str().ok_or("command")?)
                    .await?
            }
            "task" => {
                if role != "parent"
                    || args["subagent_type"] != "inspector"
                    || args["description"] != self.scenario["child_input"]
                {
                    return Err("invalid delegation".into());
                }
                self.event(
                    "subagent.start",
                    json!({"role":"inspector","parent":"parent","input":args["description"]}),
                )?;
                let result = Box::pin(self.run(
                    "inspector",
                    args["description"].as_str().ok_or("child input")?,
                ))
                .await?;
                let Outcome::Done(text) = result else {
                    return Err("child unexpectedly paused".into());
                };
                self.event(
                    "subagent.end",
                    json!({"role":"inspector","parent":"parent","output":text}),
                )?;
                text
            }
            _ => return Err("unsupported tool".into()),
        };
        self.event(
            "tool.end",
            json!({"role":role,"name":name,"output":output,"todos":todos}),
        )?;
        Ok(output)
    }

    #[cfg(feature = "native")]
    async fn run(&self, role: &str, input: &str) -> Result<Outcome> {
        #[derive(Serialize, serde::Deserialize)]
        struct State {
            messages: Vec<Value>,
            pending: Vec<Value>,
            turns: usize,
        }
        let saved = self.load(role)?;
        let (mut state, mut todos) = if let Some((s, t, d)) = saved {
            if let Some(done) = d {
                return Ok(Outcome::Done(done));
            }
            (serde_json::from_value::<State>(s)?, t)
        } else {
            (
                State {
                    messages: vec![
                        json!({"role":"system","content":self.profile[role]["system"]}),
                        json!({"role":"user","content":input}),
                    ],
                    pending: vec![],
                    turns: 0,
                },
                json!([]),
            )
        };
        self.event(
            "checkpoint.loaded",
            json!({"role":role,"next":["continue"],"todos":todos}),
        )?;
        self.event("agent.ready", json!({"role":role,"resources":resources()?}))?;
        loop {
            while !state.pending.is_empty() {
                let call = state.pending[0].clone();
                let name = call["function"]["name"].as_str().ok_or("tool name")?;
                let args: Value = serde_json::from_str(
                    call["function"]["arguments"]
                        .as_str()
                        .ok_or("tool arguments")?,
                )?;
                if self.should_pause(role, name, &args) {
                    self.save(role, &state, &todos, None)?;
                    self.event(
                        "process.paused",
                        json!({"todos":todos,"resources":resources()?}),
                    )?;
                    return Ok(Outcome::Paused(todos));
                }
                let output = self.tool(role, name, &args, &mut todos).await?;
                state
                    .messages
                    .push(json!({"role":"tool","tool_call_id":call["id"],"content":output}));
                state.pending.remove(0);
                self.save(role, &state, &todos, None)?;
            }
            if state.turns >= 20 {
                return Err("model turn budget".into());
            }
            state.turns += 1;
            self.event("model.start", json!({"role":role,"turn":state.turns}))?;
            let response:Value=self.client.post(format!("{}/chat/completions",std::env::var("OPENAI_BASE_URL")?))
                .json(&json!({"model":self.scenario["model"],"messages":state.messages,"tools":self.profile[role]["tools"],"stream":false})).send().await?.error_for_status()?.json().await?;
            let message = response["choices"][0]["message"].clone();
            if message.is_null() {
                return Err("missing assistant message".into());
            }
            state.messages.push(message.clone());
            state.pending = message["tool_calls"]
                .as_array()
                .cloned()
                .unwrap_or_default();
            self.event("model.end", json!({"role":role,"turn":state.turns}))?;
            if state.pending.is_empty() {
                let output = message["content"]
                    .as_str()
                    .ok_or("final output")?
                    .to_string();
                self.save(role, &state, &todos, Some(&output))?;
                self.event(
                    "process.completed",
                    json!({"role":role,"todos":todos,"resources":resources()?}),
                )?;
                return Ok(Outcome::Done(output));
            }
            self.save(role, &state, &todos, None)?;
        }
    }

    #[cfg(feature = "rig")]
    async fn run(&self, role: &str, input: &str) -> Result<Outcome> {
        use rig_agent::run::{
            AgentRun, AgentRunStep, ModelTurn, ModelTurnOutcome, PendingToolCall, TurnPolicy,
        };
        use rig_core::{
            completion::{CompletionRequest, ToolDefinition},
            tool::{ToolOutput, ToolResult},
        };
        let (mut run, mut todos) = if let Some((s, t, d)) = self.load(role)? {
            if let Some(done) = d {
                return Ok(Outcome::Done(done));
            }
            (serde_json::from_value::<AgentRun>(s)?, t)
        } else {
            (AgentRun::new(input).max_turns(20), json!([]))
        };
        self.event(
            "checkpoint.loaded",
            json!({"role":role,"next":["continue"],"todos":todos}),
        )?;
        let model = self.model.clone();
        let definitions = self.profile[role]["tools"]
            .as_array()
            .ok_or("tools")?
            .iter()
            .map(|t| {
                serde_json::from_value::<ToolDefinition>(t["function"].clone())
                    .map(std::sync::Arc::new)
            })
            .collect::<std::result::Result<Vec<_>, _>>()?;
        self.event("agent.ready", json!({"role":role,"resources":resources()?}))?;
        loop {
            match run.next_step()? {
                AgentRunStep::CallModel {
                    prompt,
                    history,
                    turn,
                } => {
                    self.event("model.start", json!({"role":role,"turn":turn}))?;
                    let response = model
                        .call(
                            CompletionRequest::new(prompt)
                                .messages(history)
                                .preamble(
                                    self.profile[role]["system"]
                                        .as_str()
                                        .ok_or("system")?
                                        .to_string(),
                                )
                                .tools(definitions.clone()),
                        )
                        .await?;
                    let names = definitions.iter().map(|t| t.name.to_string()).collect();
                    if let ModelTurnOutcome::NeedsResolution(_) =
                        run.model_response(ModelTurn::new(
                            response.head(),
                            response.choice.clone(),
                            response.usage,
                            TurnPolicy::new(names, None, None)?,
                            response.raw.clone(),
                        ))?
                    {
                        return Err("invalid model tool call".into());
                    }
                    self.event("model.end", json!({"role":role,"turn":turn}))?;
                    self.save(role, &run, &todos, None)?;
                }
                AgentRunStep::CallTools { calls } => {
                    for pending in calls {
                        let PendingToolCall::Execute(call) = pending else {
                            return Err("malformed tool call".into());
                        };
                        let name = call.name().to_string();
                        let args = call.arguments();
                        if self.should_pause(role, &name, &args) {
                            self.save(role, &run, &todos, None)?;
                            self.event(
                                "process.paused",
                                json!({"todos":todos,"resources":resources()?}),
                            )?;
                            return Ok(Outcome::Paused(todos));
                        }
                        let output = self.tool(role, &name, &args, &mut todos).await?;
                        run.answer(call.answer(ToolResult::success(ToolOutput::text(output))))?;
                        self.save(role, &run, &todos, None)?;
                    }
                }
                AgentRunStep::Done(response) => {
                    let output = response.output().to_string();
                    self.save(role, &run, &todos, Some(&output))?;
                    self.event(
                        "process.completed",
                        json!({"role":role,"todos":todos,"resources":resources()?}),
                    )?;
                    return Ok(Outcome::Done(output));
                }
            }
        }
    }
}

fn python_quote(text: &str) -> String {
    format!("'{}'", text.replace('\\', "\\\\").replace('\'', "\\'"))
}

fn main() -> Result<()> {
    let imported = resources()?;
    let args: Vec<String> = std::env::args().collect();
    let root = PathBuf::from(args.get(1).ok_or("workspace")?).canonicalize()?;
    let phase = args.get(2).ok_or("phase")?.clone();
    let profile: Value = serde_json::from_slice(&fs::read(args.get(3).ok_or("profile")?)?)?;
    let scenario: Value = serde_json::from_slice(&fs::read(args.get(4).ok_or("scenario")?)?)?;
    let db = Connection::open(root.join("checkpoint.sqlite"))?;
    db.execute_batch("PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; CREATE TABLE IF NOT EXISTS checkpoints(role TEXT PRIMARY KEY,engine TEXT NOT NULL,state TEXT NOT NULL,todos TEXT NOT NULL,done TEXT); CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY AUTOINCREMENT,role TEXT NOT NULL,state TEXT NOT NULL,todos TEXT NOT NULL,done TEXT);")?;
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(120))
        .build()?;
    #[cfg(feature = "rig")]
    let model = rig_core::providers::openai::OpenAIConfig::from_env()?
        .connect(rig_reqwest::ReqwestClient::from(client.clone()))
        .chat(scenario["model"].as_str().ok_or("model")?)
        .into();
    let framework = Framework {
        root,
        phase,
        profile,
        scenario,
        db,
        #[cfg(feature = "native")]
        client,
        #[cfg(feature = "rig")]
        model,
    };
    framework.event(
        "process.start",
        json!({"framework":ENGINE,"resources":imported,"sqlite_version":rusqlite::version()}),
    )?;
    if fs::read_to_string(framework.root.join("memory/AGENTS.md"))?
        != framework.scenario["memory_instructions"]
            .as_str()
            .ok_or("memory instructions")?
    {
        return Err("memory instructions differ from profile".into());
    }
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()?;
    let outcome = runtime.block_on(
        framework.run(
            "parent",
            framework.scenario["input_prompt"]
                .as_str()
                .ok_or("prompt")?,
        ),
    )?;
    let status = match outcome {
        Outcome::Paused(todos) => json!({"status":"paused","todos":todos}),
        Outcome::Done(_) => {
            let (_, todos, _) = framework.load("parent")?.ok_or("completed checkpoint")?;
            json!({"status":"completed","todos":todos})
        }
    };
    println!("{status}");
    std::io::stdout().flush()?;
    if status["status"] == "paused" {
        std::io::stdin().read_line(&mut String::new())?;
    }
    Ok(())
}
