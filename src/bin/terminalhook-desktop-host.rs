//! Explicit Desktop startup adapter: one Codex core, JSONL for Desktop and a
//! private shared socket for TerminalHook. Never used as a preflight fallback.
use fs2::FileExt;
use futures_util::{SinkExt, StreamExt};
use std::{
    collections::HashSet,
    ffi::OsString,
    path::{Path, PathBuf},
    process::Stdio,
    sync::{Arc, Mutex},
    time::Duration,
};
use tokio::{
    io::{
        AsyncBufRead, AsyncBufReadExt, AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt,
        BufReader, Join,
    },
    process::{Child, ChildStdin, ChildStdout, Command},
    task::JoinHandle,
    time::{Instant, sleep, timeout},
};
use tokio_tungstenite::{WebSocketStream, tungstenite::Message};

type Socket = WebSocketStream<Join<ChildStdout, ChildStdin>>;
const STARTUP_TIMEOUT: Duration = Duration::from_secs(30);
const STDERR_LIMIT: usize = 16384;

#[cfg(windows)]
fn own_native_children() -> std::io::Result<()> {
    use windows_sys::Win32::{
        Foundation::CloseHandle,
        System::{
            JobObjects::{
                AssignProcessToJobObject, CreateJobObjectW, JOB_OBJECT_LIMIT_BREAKAWAY_OK,
                JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
                JobObjectExtendedLimitInformation, SetInformationJobObject,
            },
            Threading::GetCurrentProcess,
        },
    };
    unsafe {
        let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
        if job.is_null() {
            return Err(std::io::Error::last_os_error());
        }
        let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
        limits.BasicLimitInformation.LimitFlags =
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_BREAKAWAY_OK;
        if SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            &limits as *const _ as *const _,
            std::mem::size_of_val(&limits) as u32,
        ) == 0
            || AssignProcessToJobObject(job, GetCurrentProcess()) == 0
        {
            let error = std::io::Error::last_os_error();
            CloseHandle(job);
            return Err(error);
        }
        // Assign this adapter before spawning anything. Retain the unnamed,
        // non-inheritable handle until OS process teardown, rather than Rust
        // Drop: Desktop may use TerminateProcess. Core/proxy/delegated CLI
        // descendants die with the adapter; explicit detached workers may escape.
    }
    Ok(())
}

struct CapturedStderr {
    tail: Arc<Mutex<Vec<u8>>>,
    task: JoinHandle<()>,
}
impl CapturedStderr {
    fn start(mut stream: impl AsyncRead + Unpin + Send + 'static) -> Self {
        let tail = Arc::new(Mutex::new(Vec::new()));
        let shared = tail.clone();
        let task = tokio::spawn(async move {
            let mut buffer = [0; 4096];
            while let Ok(count @ 1..) = stream.read(&mut buffer).await {
                let mut bytes = shared.lock().unwrap();
                bytes.extend_from_slice(&buffer[..count]);
                if bytes.len() > STDERR_LIMIT {
                    let overflow = bytes.len() - STDERR_LIMIT;
                    bytes.drain(..overflow);
                }
            }
        });
        Self { tail, task }
    }
    fn text(&self) -> String {
        String::from_utf8_lossy(&self.tail.lock().unwrap()).into_owned()
    }
}
impl Drop for CapturedStderr {
    fn drop(&mut self) {
        self.task.abort();
    }
}

fn native_command(program: &Path) -> Command {
    let mut command = Command::new(program);
    #[cfg(windows)]
    command.creation_flags(0x08000000);
    command.kill_on_drop(true);
    command
}

async fn open_proxy(
    program: &Path,
    socket_path: &Path,
) -> Result<(Child, CapturedStderr, Socket), String> {
    let mut child = native_command(program)
        .args([
            OsString::from("app-server"),
            OsString::from("proxy"),
            OsString::from("--sock"),
            socket_path.as_os_str().to_owned(),
        ])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| e.to_string())?;
    let mut stderr = CapturedStderr::start(child.stderr.take().unwrap());
    let stream = tokio::io::join(child.stdout.take().unwrap(), child.stdin.take().unwrap());
    match timeout(
        Duration::from_secs(2),
        tokio_tungstenite::client_async("ws://localhost/", stream),
    )
    .await
    {
        Ok(Ok((socket, _))) => Ok((child, stderr, socket)),
        result => {
            let _ = child.kill().await;
            let _ = timeout(Duration::from_millis(200), &mut stderr.task).await;
            Err(format!(
                "proxy upgrade failed: {}\n{}",
                match result {
                    Ok(Err(error)) => error.to_string(),
                    _ => "timed out".into(),
                },
                stderr.text()
            ))
        }
    }
}

fn socket_path() -> Result<PathBuf, String> {
    if let Some(path) = std::env::var_os("TERMINALHOOK_DESKTOP_SOCKET") {
        return Ok(path.into());
    }
    let home = std::env::var_os("CODEX_HOME")
        .map(PathBuf::from)
        .or_else(|| {
            std::env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" })
                .map(|value| PathBuf::from(value).join(".codex"))
        })
        .ok_or("Cannot resolve CODEX_HOME")?;
    Ok(home.join("terminalhook/desktop-host/control.sock"))
}

fn server_args(args: &[OsString]) -> Result<Vec<OsString>, String> {
    let mut result = Vec::new();
    let mut args = args.iter();
    while let Some(arg) = args.next() {
        if arg == "--stdio" {
            continue;
        }
        if arg == "--listen" {
            if args.next().is_none_or(|value| value != "stdio://") {
                return Err("Desktop adapter requires a stdio caller".into());
            }
            continue;
        }
        if arg.to_string_lossy().starts_with("--listen=") {
            if arg != "--listen=stdio://" {
                return Err("Desktop adapter requires a stdio caller".into());
            }
            continue;
        }
        result.push(arg.clone());
    }
    Ok(result)
}

fn is_server(args: &[OsString]) -> bool {
    if args.iter().any(|arg| {
        ["--print-startup-config", "--help", "-h", "--version", "-V"]
            .iter()
            .any(|flag| arg == flag)
    }) {
        return false;
    }
    let mut index = 0;
    while let Some(arg) = args.get(index) {
        if [
            "-c",
            "--config",
            "-C",
            "--cd",
            "-p",
            "--profile",
            "--enable",
            "--disable",
        ]
        .iter()
        .any(|flag| arg == flag)
        {
            index += 2;
        } else if arg.to_string_lossy().starts_with('-') {
            index += 1;
        } else {
            return arg == "app-server"
                && args
                    .get(index + 1)
                    .is_none_or(|arg| arg.to_string_lossy().starts_with('-'));
        }
    }
    false
}

async fn relay<S, I, O>(
    socket: &mut WebSocketStream<S>,
    mut stdin: I,
    mut stdout: O,
) -> Result<(), String>
where
    S: AsyncRead + AsyncWrite + Unpin,
    I: AsyncBufRead + Unpin,
    O: AsyncWrite + Unpin,
{
    let mut line = Vec::new();
    let mut pending = HashSet::new();
    let mut input_closed = false;
    let mut drain_deadline = Instant::now() + STARTUP_TIMEOUT;
    loop {
        tokio::select! {
            read = stdin.read_until(b'\n', &mut line), if !input_closed => {
                if read.map_err(|e| e.to_string())? == 0 {
                    if pending.is_empty() { return Ok(()); }
                    input_closed = true;
                    drain_deadline = Instant::now() + STARTUP_TIMEOUT;
                    continue;
                }
                if line.len() > 32 * 1024 * 1024 { return Err("Desktop JSONL frame exceeds 32 MiB".into()); }
                let text = String::from_utf8(std::mem::take(&mut line)).map_err(|e| e.to_string())?;
                if text.trim().is_empty() { continue; }
                let message = serde_json::from_str::<serde_json::Value>(&text).map_err(|e| format!("Invalid Desktop JSONL: {e}"))?;
                if message.get("method").is_some() && let Some(id) = message.get("id") { pending.insert(id.to_string()); }
                socket.send(Message::Text(text.trim_end().to_owned().into())).await.map_err(|e| e.to_string())?;
            }
            received = socket.next() => match received {
                Some(Ok(Message::Text(text))) => {
                    let message: serde_json::Value = serde_json::from_str(&text).map_err(|e| e.to_string())?;
                    if message.get("method").is_none() && let Some(id) = message.get("id") { pending.remove(&id.to_string()); }
                    stdout.write_all(text.as_bytes()).await.map_err(|e| e.to_string())?;
                    stdout.write_all(b"\n").await.map_err(|e| e.to_string())?;
                    stdout.flush().await.map_err(|e| e.to_string())?;
                    if input_closed && pending.is_empty() { return Ok(()); }
                },
                Some(Ok(Message::Ping(bytes))) => socket.send(Message::Pong(bytes)).await.map_err(|e| e.to_string())?,
                Some(Ok(Message::Pong(_))) => {},
                Some(Ok(Message::Close(_))) | None => return Err("Shared Codex core closed the Desktop connection".into()),
                Some(Ok(_)) => return Err("Unsupported shared Codex WebSocket frame".into()),
                Some(Err(error)) => return Err(error.to_string()),
            },
            _ = tokio::time::sleep_until(drain_deadline), if input_closed => return Err("Timed out draining Desktop responses after stdin closed".into()),
        }
    }
}

async fn run(program: &Path, args: &[OsString]) -> Result<(), String> {
    let socket_path = socket_path()?;
    if !socket_path.is_absolute() {
        return Err("Desktop socket path must be absolute".into());
    }
    let directory = socket_path.parent().ok_or("Missing socket parent")?;
    #[cfg(windows)]
    std::fs::create_dir_all(
        directory
            .parent()
            .ok_or("Missing socket directory parent")?,
    )
    .map_err(|e| e.to_string())?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::create_dir_all(directory).map_err(|e| e.to_string())?;
        std::fs::set_permissions(directory, std::fs::Permissions::from_mode(0o700))
            .map_err(|e| e.to_string())?;
    }
    let lock = std::fs::OpenOptions::new()
        .create(true)
        .truncate(false)
        .read(true)
        .write(true)
        .open(directory.with_extension("lock"))
        .map_err(|e| e.to_string())?;
    lock.try_lock_exclusive()
        .map_err(|_| "A desktop shared host already owns this endpoint")?;
    let mut args = server_args(args)?;
    args.extend([
        OsString::from("--listen"),
        OsString::from(format!("unix://{}", socket_path.display())),
    ]);
    let mut server = native_command(program)
        .args(args)
        .env("TERMINALHOOK_CODEX_SOCKET", &socket_path)
        .env("TERMINALHOOK_CODEX_BIN", program)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("Cannot launch Desktop's Codex core: {e}"))?;
    let mut stderr = CapturedStderr::start(server.stderr.take().unwrap());
    let deadline = Instant::now() + STARTUP_TIMEOUT;
    let (mut proxy, _proxy_stderr, mut socket) = loop {
        if let Some(status) = server.try_wait().map_err(|e| e.to_string())? {
            let _ = timeout(Duration::from_millis(200), &mut stderr.task).await;
            return Err(format!(
                "Desktop's Codex core exited: {status}\n{}",
                stderr.text()
            ));
        }
        match open_proxy(program, &socket_path).await {
            Ok(connected) => break connected,
            Err(error) if Instant::now() >= deadline => {
                let _ = server.kill().await;
                let _ = timeout(Duration::from_millis(200), &mut stderr.task).await;
                return Err(format!(
                    "Desktop shared endpoint did not become ready: {error}\n{}",
                    stderr.text()
                ));
            }
            Err(_) => sleep(Duration::from_millis(100)).await,
        }
    };
    let result = tokio::select! {
        result = relay(&mut socket, BufReader::new(tokio::io::stdin()), tokio::io::stdout()) => result,
        status = server.wait() => Err(format!("Shared Codex core exited: {}", status.map_err(|e| e.to_string())?)),
    };
    let _ = timeout(Duration::from_secs(1), socket.close(None)).await;
    let _ = proxy.kill().await;
    let _ = server.kill().await;
    let _ = timeout(Duration::from_millis(200), &mut stderr.task).await;
    result.map_err(|error| format!("{error}\nCodex core stderr tail:\n{}", stderr.text()))
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    if std::env::args().any(|arg| arg == "--host-version") {
        println!("terminalhook-desktop-host {}", env!("TERMINALHOOK_VERSION"));
        return Ok(());
    }
    let program = PathBuf::from(
        std::env::var_os("TERMINALHOOK_DESKTOP_CODEX_BIN")
            .ok_or("Set TERMINALHOOK_DESKTOP_CODEX_BIN to the native Codex CLI")?,
    );
    if !program.is_absolute() || !program.is_file() {
        return Err("Desktop Codex CLI must be an existing absolute file".into());
    }
    if program.canonicalize()? == std::env::current_exe()?.canonicalize()? {
        return Err("Desktop host cannot delegate to itself".into());
    }
    #[cfg(windows)]
    own_native_children()
        .map_err(|error| format!("Cannot establish Desktop CLI process ownership: {error}"))?;
    let args: Vec<OsString> = std::env::args_os().skip(1).collect();
    if !is_server(&args) {
        let mut command = std::process::Command::new(program);
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000);
        }
        let status = command.args(args).status()?;
        std::process::exit(status.code().unwrap_or(1));
    }
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?;
    let result = runtime.block_on(run(&program, &args));
    // Tokio's blocking stdin reader cannot be cancelled when the core exits.
    // Do not wait indefinitely for another Desktop input line on failure.
    runtime.shutdown_timeout(Duration::from_millis(200));
    result.map_err(Into::into)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(values: &[&str]) -> Vec<OsString> {
        values.iter().map(OsString::from).collect()
    }

    #[test]
    fn preserves_desktop_configuration_and_removes_stdio_only() {
        let original = args(&[
            "app-server",
            "--analytics-default-enabled",
            "-c",
            "features.prefer_mxc=true",
        ]);
        assert_eq!(server_args(&original).unwrap(), original);
        let mut explicit = original.clone();
        explicit.extend(args(&["--listen", "stdio://", "--stdio"]));
        assert_eq!(server_args(&explicit).unwrap(), original);
        explicit = original.clone();
        explicit.push("--listen=stdio://".into());
        assert_eq!(server_args(&explicit).unwrap(), original);
    }

    #[test]
    fn recognizes_desktop_global_flags_and_delegates_subcommands() {
        assert!(is_server(&args(&[
            "-c",
            "features.code_mode_host=true",
            "app-server",
            "--analytics-default-enabled"
        ])));
        assert!(is_server(&args(&["app-server"])));
        assert!(!is_server(&args(&[
            "app-server",
            "proxy",
            "--sock",
            "/tmp/core.sock"
        ])));
        assert!(!is_server(&args(&["--version"])));
        assert!(!is_server(&args(&["plugin", "list"])));
        assert!(!is_server(&args(&["exec", "app-server"])));
        assert!(!is_server(&args(&["-c", "app-server"])));
        assert!(!is_server(&args(&[
            "app-server",
            "--print-startup-config",
            "--startup-client-info",
            "{}"
        ])));
        assert!(!is_server(&args(&["app-server", "--help"])));
    }

    #[test]
    fn rejects_existing_network_and_shared_listeners() {
        for value in ["ws://127.0.0.1:1234", "unix:///tmp/core.sock", "stdio"] {
            assert!(server_args(&args(&["app-server", "--listen", value])).is_err());
            assert!(server_args(&args(&["app-server", &format!("--listen={value}")])).is_err());
        }
        assert!(server_args(&args(&["app-server", "--listen"])).is_err());
    }

    #[tokio::test]
    async fn continuously_drains_and_retains_actual_stderr_tail() {
        let (mut writer, reader) = tokio::io::duplex(1024);
        let mut capture = CapturedStderr::start(reader);
        let output = format!("{}REAL_CORE_FAILURE_10061", "x".repeat(65536));
        writer.write_all(output.as_bytes()).await.unwrap();
        drop(writer);
        timeout(Duration::from_secs(2), &mut capture.task)
            .await
            .unwrap()
            .unwrap();
        assert_eq!(capture.text().len(), STDERR_LIMIT);
        assert!(capture.text().ends_with("REAL_CORE_FAILURE_10061"));
    }

    #[tokio::test]
    async fn forwards_notifications_and_drains_out_of_order_responses_after_eof() {
        use tokio_tungstenite::tungstenite::protocol::Role;
        let (client, server) = tokio::io::duplex(8192);
        let mut socket = WebSocketStream::from_raw_socket(client, Role::Client, None).await;
        let mut peer = WebSocketStream::from_raw_socket(server, Role::Server, None).await;
        let producer = tokio::spawn(async move {
            for expected in [serde_json::json!(1), serde_json::json!("second")] {
                let request = peer.next().await.unwrap().unwrap().into_text().unwrap();
                assert_eq!(
                    serde_json::from_str::<serde_json::Value>(&request).unwrap()["id"],
                    expected
                );
            }
            // Input is already at EOF before these responses arrive.
            sleep(Duration::from_millis(20)).await;
            for message in [
                r#"{"method":"core/ready","params":{}}"#,
                r#"{"id":"second","result":2}"#,
                r#"{"id":1,"result":1}"#,
            ] {
                peer.send(Message::Text(message.into())).await.unwrap();
            }
            // Keep the connection open: completion must depend on drained IDs.
            sleep(Duration::from_secs(2)).await;
        });
        let input = b"{\"id\":1,\"method\":\"initialize\"}\n{\"id\":\"second\",\"method\":\"config/read\"}\n";
        let mut output = Vec::new();
        timeout(
            Duration::from_secs(1),
            relay(&mut socket, &input[..], &mut output),
        )
        .await
        .unwrap()
        .unwrap();
        let values: Vec<serde_json::Value> = String::from_utf8(output)
            .unwrap()
            .lines()
            .map(|line| serde_json::from_str(line).unwrap())
            .collect();
        assert_eq!(values.len(), 3);
        assert_eq!(values[0]["method"], "core/ready");
        assert_eq!(values[1]["id"], "second");
        assert_eq!(values[2]["id"], 1);
        producer.abort();
    }
}
