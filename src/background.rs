//! Durable, detached jobs with a completion outbox owned by the originating chat.
use crate::{
    bridge::{self, Bridge},
    process::Manager,
    spec::{self, Action, MatchMode, Mode, RuleInput, RunRequest, WaitSpec},
};
use fs2::FileExt;
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    collections::BTreeMap,
    fs::{self, File, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
    time::Duration,
};
use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader},
    net::{TcpListener, TcpStream},
    time::timeout,
};
use tokio_util::sync::CancellationToken;
#[cfg(windows)]
mod windows;

#[derive(Clone, Deserialize, Serialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct SubmitRequest {
    /// Stable key, 1..128 bytes, unique within this chat. Reusing the key returns the existing task without rerunning it.
    pub task_key: String,
    pub program: String,
    #[serde(default)]
    pub args: Vec<String>,
    /// Required absolute working directory.
    pub cwd: String,
    #[serde(default)]
    pub env: BTreeMap<String, String>,
    /// Background execution deadline in milliseconds. Omit/null to wait until match, exit, or explicit cancellation. Independent of the MCP call timeout.
    pub timeout_ms: Option<u64>,
    /// Optional output conditions. Omit/[] to wait for natural exit. A match terminates the managed process tree before delivering the result.
    #[serde(default)]
    pub until: Vec<RuleInput>,
    #[serde(default)]
    pub streams: Vec<String>,
    #[serde(default)]
    pub match_mode: MatchMode,
}

impl SubmitRequest {
    fn run_request(&self, logs: &Path) -> RunRequest {
        RunRequest {
            program: self.program.clone(),
            args: self.args.clone(),
            cwd: self.cwd.clone(),
            env: self.env.clone(),
            mode: Mode::Pipe,
            rows: 24,
            cols: 120,
            wait: WaitSpec {
                until: self.until.clone(),
                streams: self.streams.clone(),
                timeout_ms: 300_000,
                match_mode: self.match_mode,
                window_bytes: 65536,
            },
            on_match: Action::Terminate,
            on_timeout: Action::Terminate,
            log_dir: Some(logs.to_string_lossy().into_owned()),
        }
    }
    fn validate(&self) -> Result<(), String> {
        if self.task_key.is_empty() || self.task_key.len() > 128 {
            return Err("task_key must be 1..128 bytes".into());
        }
        // Duration construction accepts large values; keep an explicit finite deadline within Tokio's supported range.
        if self
            .timeout_ms
            .is_some_and(|ms| ms == 0 || ms > 31_536_000_000)
        {
            return Err(
                "timeout_ms must be 1..31536000000, or null for no execution deadline".into(),
            );
        }
        let run = self.run_request(Path::new(&self.cwd));
        spec::validate_run(&run)?;
        if !self.until.is_empty() {
            spec::validate_rules(&run.wait, Mode::Pipe)?;
        } else if self
            .streams
            .iter()
            .any(|s| !["stdout", "stderr"].contains(&s.as_str()))
        {
            return Err("streams must use stdout/stderr".into());
        }
        Ok(())
    }
}

#[derive(Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct TaskRequest {
    pub task_id: String,
}

#[derive(Deserialize, Serialize)]
struct Job {
    task_id: String,
    thread_id: String,
    request: SubmitRequest,
}

fn err(e: impl std::fmt::Display) -> String {
    e.to_string()
}
fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}
fn private_dir(path: &Path) -> Result<(), String> {
    fs::create_dir_all(path).map_err(err)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o700)).map_err(err)?;
    }
    Ok(())
}
fn write_json(path: &Path, value: &impl Serialize) -> Result<(), String> {
    let parent = path.parent().ok_or("missing parent directory")?;
    let mut file = tempfile::NamedTempFile::new_in(parent).map_err(err)?;
    serde_json::to_writer_pretty(&mut file, value).map_err(err)?;
    file.write_all(b"\n").map_err(err)?;
    file.as_file().sync_all().map_err(err)?;
    let mut attempts = 0;
    loop {
        match file.persist(path) {
            Ok(_) => break,
            Err(failure)
                if cfg!(windows)
                    && matches!(failure.error.raw_os_error(), Some(5 | 32 | 33))
                    && attempts < 100 =>
            {
                // A Windows reader/scanner can temporarily prevent replacement.
                // Keep the same fully synced file; never regenerate or replay work.
                file = failure.file;
                attempts += 1;
                std::thread::sleep(Duration::from_millis(10));
            }
            Err(failure) => return Err(err(failure)),
        }
    }
    #[cfg(unix)]
    File::open(parent)
        .and_then(|directory| directory.sync_all())
        .map_err(err)?;
    Ok(())
}
fn read_json(path: &Path) -> Result<Value, String> {
    let mut attempts = 0;
    let file = loop {
        match File::open(path) {
            Ok(file) => break file,
            Err(error)
                if cfg!(windows)
                    && matches!(error.raw_os_error(), Some(5 | 32 | 33))
                    && attempts < 20 =>
            {
                // Windows can briefly deny a path lookup during atomic replacement.
                // Retry file access locally, without polling the model or replaying work.
                attempts += 1;
                std::thread::sleep(Duration::from_millis(5));
            }
            Err(error) => return Err(err(error)),
        }
    };
    serde_json::from_reader(file).map_err(err)
}
fn lock(path: &Path) -> Result<File, String> {
    let file = OpenOptions::new()
        .create(true)
        .truncate(false)
        .read(true)
        .write(true)
        .open(path)
        .map_err(err)?;
    file.try_lock_exclusive()
        .map_err(|_| "task already has an active owner".to_string())?;
    Ok(file)
}
fn task_path(id: &str) -> Result<PathBuf, String> {
    let suffix = id.strip_prefix("task_").ok_or("invalid task_id")?;
    if suffix.len() != 32 || !suffix.bytes().all(|c| c.is_ascii_hexdigit()) {
        return Err("invalid task_id".into());
    }
    Ok(bridge::task_root()?.join(id))
}
fn owned_job(id: &str, thread_id: &str) -> Result<(PathBuf, Job), String> {
    let path = task_path(id)?;
    let job: Job = serde_json::from_value(read_json(&path.join("request.json"))?).map_err(err)?;
    if job.thread_id != thread_id {
        return Err("task belongs to a different chat".into());
    }
    Ok((path, job))
}

pub async fn submit(
    request: SubmitRequest,
    thread_id: &str,
    cancellation: CancellationToken,
) -> Result<Value, String> {
    request.validate()?;
    let task_id = format!(
        "task_{}",
        uuid::Uuid::new_v5(
            &uuid::Uuid::NAMESPACE_URL,
            format!("terminalhook:{thread_id}:{}", request.task_key).as_bytes()
        )
        .simple()
    );
    let path = task_path(&task_id)?;
    private_dir(path.parent().unwrap())?;
    // Serialize submission and process launch; a lost response must never duplicate the command.
    let _submission = lock(&path.parent().unwrap().join("submission.lock"))?;
    if path.exists() {
        let (_, job) = owned_job(&task_id, thread_id)?;
        if serde_json::to_value(&job.request).map_err(err)?
            != serde_json::to_value(&request).map_err(err)?
        {
            return Err("task_key already exists with different arguments; inspect it before submitting new work".into());
        }
        let mut result = get(&task_id, thread_id).await?;
        result["reused"] = json!(true);
        return Ok(result);
    }
    let mut host = tokio::select! {
        result=async {let mut host=Bridge::connect().await?;host.verify_thread(thread_id).await?;Ok::<_,String>(host)}=>result?,
        _=cancellation.cancelled()=>return Err("background submission cancelled before command launch".into()),
    };
    if cancellation.is_cancelled() {
        return Err("background submission cancelled before command launch".into());
    }
    private_dir(&path)?;
    let job = Job {
        task_id: task_id.clone(),
        thread_id: thread_id.into(),
        request,
    };
    write_json(&path.join("request.json"), &job)?;
    let mut state = json!({"task_id":task_id,"thread_id":thread_id,"status":"queued","delivery":"pending","created_at_ms":now_ms()});
    write_json(&path.join("state.json"), &state)?;
    // Ask the existing host to launch outside the MCP server's process containment.
    // In particular, Codex's Windows MCP Job Object deliberately forbids breakaway.
    let launched = host.launch_worker(&path).await;
    host.close().await;
    match launched {
        Ok(pid) => {
            state["worker_pid"] = json!(pid);
            state["status"] = json!("submitted");
            state["state_dir"] = json!(path);
            Ok(state)
        }
        Err(error) => {
            write_json(&path.join("launch-error.json"), &json!({"error":error}))?;
            Err(format!(
                "background launch was not acknowledged; inspect the existing task {} at {} before retrying; never replay the command: {error}",
                job.task_id,
                path.display()
            ))
        }
    }
}

pub fn spawn_worker(path: &Path) -> Result<u32, String> {
    #[cfg(windows)]
    {
        windows::spawn(path)
    }
    #[cfg(unix)]
    {
        use std::process::Stdio;
        let diagnostics = OpenOptions::new()
            .create(true)
            .append(true)
            .open(path.join("worker.log"))
            .map_err(err)?;
        let mut command = std::process::Command::new(std::env::current_exe().map_err(err)?);
        command
            .arg("--background-worker")
            .arg(path)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(diagnostics);
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            unsafe {
                command.pre_exec(|| {
                    if libc::setsid() == -1 {
                        return Err(std::io::Error::last_os_error());
                    }
                    Ok(())
                });
            }
        }
        command.spawn().map(|child| child.id()).map_err(err)
    }
}

pub async fn get(id: &str, thread_id: &str) -> Result<Value, String> {
    let (path, _) = owned_job(id, thread_id)?;
    let mut state = read_json(&path.join("state.json"))?;
    state["state_dir"] = json!(path);
    if path.join("launch-error.json").exists() {
        state["launch_error"] = read_json(&path.join("launch-error.json"))?;
    }
    if path.join("result.json").exists() {
        state["result"] = read_json(&path.join("result.json"))?;
    }
    if matches!(state["status"].as_str(), Some("running" | "queued")) {
        state["worker_live"] = json!(control(&path, "ping").await.is_ok());
        let mut tails = json!({});
        for entry in fs::read_dir(&path).map_err(err)?.flatten() {
            if !entry.file_name().to_string_lossy().starts_with("proc_")
                || !entry.file_type().map_err(err)?.is_dir()
            {
                continue;
            }
            for stream in ["stdout", "stderr"] {
                if let Ok(mut file) = File::open(entry.path().join(format!("{stream}.log"))) {
                    use std::io::{Read, Seek, SeekFrom};
                    let size = file.metadata().map_err(err)?.len();
                    file.seek(SeekFrom::Start(size.saturating_sub(8192)))
                        .map_err(err)?;
                    let mut bytes = Vec::new();
                    file.take(8192).read_to_end(&mut bytes).map_err(err)?;
                    tails[stream] = json!(String::from_utf8_lossy(&bytes));
                }
            }
        }
        state["output_tail"] = tails;
    }
    Ok(state)
}

pub async fn list(thread_id: &str) -> Result<Value, String> {
    let root = bridge::task_root()?;
    let mut tasks = Vec::new();
    if root.exists() {
        for entry in fs::read_dir(root).map_err(err)?.flatten() {
            if !entry.file_type().map_err(err)?.is_dir() {
                continue;
            }
            let id = entry.file_name().to_string_lossy().into_owned();
            let Ok((_, job)) = owned_job(&id, thread_id) else {
                continue;
            };
            let mut state = read_json(&entry.path().join("state.json"))?;
            state["task_key"] = json!(job.request.task_key);
            state["program"] = json!(job.request.program);
            state["cwd"] = json!(job.request.cwd);
            state["timeout_ms"] = json!(job.request.timeout_ms);
            if entry.path().join("result.json").exists() {
                let result = read_json(&entry.path().join("result.json"))?;
                state["exit_code"] = result["exit_code"].clone();
                state["elapsed_ms"] = result["elapsed_ms"].clone();
            }
            tasks.push(state);
        }
    }
    tasks.sort_by_key(|task| std::cmp::Reverse(task["created_at_ms"].as_u64().unwrap_or(0)));
    // Avoid an unbounded model result; full individual results stay available through get_task.
    let total = tasks.len();
    tasks.truncate(100);
    Ok(json!({"tasks":tasks,"total":total,"truncated":total>100}))
}

async fn control(path: &Path, action: &str) -> Result<(), String> {
    let endpoint = read_json(&path.join("control.json"))?;
    let address = endpoint["address"]
        .as_str()
        .ok_or("worker endpoint missing")?;
    let address: std::net::SocketAddr = address.parse().map_err(err)?;
    if !address.ip().is_loopback() {
        return Err("worker control endpoint must be loopback".into());
    }
    timeout(Duration::from_secs(3), async {
        let stream = TcpStream::connect(address).await.map_err(err)?;
        let mut stream = BufReader::new(stream);
        stream
            .write_all(
                format!("{}\n", json!({"token":endpoint["token"],"action":action})).as_bytes(),
            )
            .await
            .map_err(err)?;
        let mut line = String::new();
        stream.read_line(&mut line).await.map_err(err)?;
        if line.trim() != "ok" {
            return Err("worker did not acknowledge control request".into());
        }
        Ok(())
    })
    .await
    .map_err(|_| "worker control timed out".to_string())?
}

pub async fn cancel(id: &str, thread_id: &str) -> Result<Value, String> {
    let (path, _) = owned_job(id, thread_id)?;
    let state = read_json(&path.join("state.json"))?;
    if !matches!(state["status"].as_str(), Some("queued" | "running")) {
        return Ok(state);
    }
    write_json(&path.join("cancel.request"), &json!({"cancel":true}))?;
    // A worker not yet ready will read the durable cancellation before launch.
    if let Err(error) = control(&path, "cancel").await
        && state["status"] != "queued"
    {
        return Err(error);
    }
    Ok(json!({"task_id":id,"status":"cancellation_requested"}))
}

pub async fn retry_delivery(id: &str, thread_id: &str) -> Result<Value, String> {
    let (path, job) = owned_job(id, thread_id)?;
    let initial = read_json(&path.join("state.json"))?;
    if initial["delivery"] == "delivered" {
        return Ok(initial);
    }
    if matches!(initial["delivery"].as_str(), Some("sending" | "uncertain")) {
        return Err("delivery is active or uncertain; verify the original chat before retrying, and never rerun the command".into());
    }
    let _lock = lock(&path.join("worker.lock"))?;
    let mut state = read_json(&path.join("state.json"))?;
    if state["delivery"] == "delivered" {
        return Ok(state);
    }
    if !matches!(state["delivery"].as_str(), Some("pending" | "failed"))
        || !path.join("result.json").exists()
    {
        return Err("delivery is active or uncertain; verify the original chat before retrying, and never rerun the command".into());
    }
    deliver(&path, &job, &mut state).await?;
    Ok(state)
}

async fn deliver(path: &Path, job: &Job, state: &mut Value) -> Result<(), String> {
    let prepared = async {
        let mut bridge = Bridge::connect().await?;
        bridge.verify_thread(&job.thread_id).await?;
        Ok::<_, String>(bridge)
    }
    .await;
    let mut bridge = match prepared {
        Ok(bridge) => bridge,
        Err(error) => {
            state["delivery"] = json!("failed");
            state["delivery_error"] = json!(error);
            write_json(&path.join("state.json"), state)?;
            return Ok(());
        }
    };
    let result = read_json(&path.join("result.json"))?;
    // Commit intent before sending. No automatic replay after an ambiguous transport failure.
    state["delivery"] = json!("sending");
    write_json(&path.join("state.json"), state)?;
    match bridge.deliver(&job.thread_id, &job.task_id, &result).await {
        Ok(receipt) => {
            state["delivery"] = json!("delivered");
            state["receipt"] = receipt;
            state.as_object_mut().unwrap().remove("delivery_error");
        }
        Err(error) => {
            state["delivery"] = json!("uncertain");
            state["delivery_error"] = json!(error);
        }
    }
    write_json(&path.join("state.json"), state)?;
    bridge.close().await;
    Ok(())
}

pub async fn worker(path: &Path) -> Result<(), String> {
    let _lock = lock(&path.join("worker.lock"))?;
    let result = run_worker(path).await;
    if let Err(error) = &result
        && let Ok(mut state) = read_json(&path.join("state.json"))
    {
        if !path.join("result.json").exists() {
            state["status"] = json!("worker_failed");
            state["error"] = json!(error);
        }
        if state["delivery"] == "sending" {
            state["delivery"] = json!("uncertain");
        }
        let _ = write_json(&path.join("state.json"), &state);
    }
    result
}

async fn run_worker(path: &Path) -> Result<(), String> {
    let job: Job = serde_json::from_value(read_json(&path.join("request.json"))?).map_err(err)?;
    let mut state = read_json(&path.join("state.json"))?;
    if state["status"] != "queued" {
        return Err("task already started; execution must never be replayed".into());
    }
    let listener = TcpListener::bind("127.0.0.1:0").await.map_err(err)?;
    let token = uuid::Uuid::new_v4().to_string();
    write_json(
        &path.join("control.json"),
        &json!({"address":listener.local_addr().map_err(err)?.to_string(),"token":token}),
    )?;
    state["status"] = json!("running");
    state["started_at_ms"] = json!(now_ms());
    state["worker_pid"] = json!(std::process::id());
    write_json(&path.join("state.json"), &state)?;
    let cancellation = CancellationToken::new();
    let signal = cancellation.clone();
    let control_task = tokio::spawn(async move {
        while let Ok((stream, _)) = listener.accept().await {
            let token = token.clone();
            let signal = signal.clone();
            // Sequential, bounded control input; never execute a command from this channel.
            let _ = timeout(Duration::from_secs(2), async move {
                let mut stream = BufReader::new(stream);
                let mut bytes = Vec::new();
                use tokio::io::AsyncReadExt;
                (&mut stream)
                    .take(1024)
                    .read_until(b'\n', &mut bytes)
                    .await
                    .map_err(err)?;
                let value: Value = serde_json::from_slice(&bytes).map_err(err)?;
                if value["token"] != token {
                    return Err("invalid control token".into());
                }
                if value["action"] == "cancel" {
                    signal.cancel();
                } else if value["action"] != "ping" {
                    return Err("invalid control action".into());
                }
                stream.write_all(b"ok\n").await.map_err(err)
            })
            .await;
        }
    });
    let manager = Manager::new()?;
    let result = if path.join("cancel.request").exists() {
        cancellation.cancel();
        Ok(json!({"status":"cancelled","message":"cancelled before command launch"}))
    } else {
        manager
            .run_with_deadline(
                job.request.run_request(path),
                cancellation.clone(),
                job.request.timeout_ms.map(Duration::from_millis),
            )
            .await
    };
    manager.shutdown().await;
    control_task.abort();
    let result = match result {
        Ok(result) => result,
        Err(error) => {
            json!({"status":if cancellation.is_cancelled(){"cancelled"}else{"error"},"message":error})
        }
    };
    write_json(&path.join("result.json"), &result)?;
    state["status"] = json!(if cancellation.is_cancelled() {
        "cancelled"
    } else if result["status"] == "timeout" {
        "timed_out"
    } else if result["status"] == "error"
        || (result["status"] == "exited_unmatched" && result["exit_code"] != 0)
    {
        "failed"
    } else {
        "completed"
    });
    state["finished_at_ms"] = json!(now_ms());
    write_json(&path.join("state.json"), &state)?;
    deliver(path, &job, &mut state).await
}
