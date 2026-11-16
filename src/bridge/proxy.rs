//! Own the proxy process and drain bounded diagnostics independently of RPC.
use std::{
    collections::VecDeque,
    ffi::OsString,
    process::Stdio,
    sync::{Arc, Mutex},
    time::Duration,
};
use tokio::{
    io::{AsyncReadExt, Join},
    process::{Child, ChildStdin, ChildStdout, Command},
    task::JoinHandle,
    time::timeout,
};

const STDERR_LIMIT: usize = 16 * 1024;
const DRAIN_GRACE: Duration = Duration::from_millis(250);

pub(super) struct ProxyCommand {
    pub program: OsString,
    pub args: Vec<OsString>,
    source: &'static str,
}

impl ProxyCommand {
    pub fn from_env() -> Result<Self, String> {
        Self::resolve(|key| std::env::var_os(key))
    }

    fn resolve(get: impl Fn(&str) -> Option<OsString>) -> Result<Self, String> {
        let mut result = if let Some(raw) = get("TERMINALHOOK_PROXY_COMMAND") {
            let argv: Vec<String> = serde_json::from_str(&raw.to_string_lossy())
                .map_err(|e| format!("invalid TERMINALHOOK_PROXY_COMMAND: {e}"))?;
            let first = argv
                .first()
                .filter(|arg| !arg.is_empty())
                .ok_or("TERMINALHOOK_PROXY_COMMAND must be a nonempty JSON argv array")?;
            Self {
                program: first.into(),
                args: argv[1..].iter().map(OsString::from).collect(),
                source: "TERMINALHOOK_PROXY_COMMAND",
            }
        } else {
            let (program, source) = if let Some(program) = get("TERMINALHOOK_CODEX_BIN") {
                if program.is_empty() {
                    return Err("TERMINALHOOK_CODEX_BIN must not be empty".into());
                }
                (program, "TERMINALHOOK_CODEX_BIN")
            } else if let Some(program) =
                get("CODEX_APP_RUNTIME_CLI_PATH").filter(|value| !value.is_empty())
            {
                (program, "CODEX_APP_RUNTIME_CLI_PATH")
            } else {
                ("codex".into(), "PATH")
            };
            Self {
                program,
                args: vec!["app-server".into(), "proxy".into()],
                source,
            }
        };
        if let Some(socket) = get("TERMINALHOOK_CODEX_SOCKET") {
            if socket.is_empty() {
                return Err("TERMINALHOOK_CODEX_SOCKET must not be empty".into());
            }
            result.args.extend(["--sock".into(), socket]);
        }
        Ok(result)
    }
}

#[derive(Default)]
struct StderrTail {
    bytes: VecDeque<u8>,
    truncated: bool,
}
impl StderrTail {
    fn append(&mut self, bytes: &[u8]) {
        self.bytes.extend(bytes);
        if self.bytes.len() > STDERR_LIMIT {
            self.bytes.drain(..self.bytes.len() - STDERR_LIMIT);
            self.truncated = true;
        }
    }
    fn describe(&self) -> String {
        let bytes: Vec<_> = self.bytes.iter().copied().collect();
        let text = String::from_utf8_lossy(&bytes);
        if text.is_empty() {
            "proxy stderr: (empty)".into()
        } else {
            format!(
                "proxy stderr{}:\n{}",
                if self.truncated {
                    " (last 16384 bytes; truncated)"
                } else {
                    ""
                },
                text.trim_end()
            )
        }
    }
}

pub(super) struct Proxy {
    #[cfg(windows)]
    gate: crate::tree::Gate,
    child: Child,
    tree: Option<crate::tree::ProcessTree>,
    tail: Arc<Mutex<StderrTail>>,
    reader: Option<JoinHandle<()>>,
    description: String,
}

impl Proxy {
    pub async fn spawn(
        spec: ProxyCommand,
    ) -> Result<(Self, Join<ChildStdout, ChildStdin>), String> {
        let description = format!(
            "proxy executable: {} (from {})",
            spec.program.to_string_lossy(),
            spec.source
        );
        #[cfg(windows)]
        let gate =
            crate::tree::Gate::new().map_err(|e| format!("cannot create Codex proxy gate: {e}"))?;
        #[cfg(windows)]
        let (program, args) = gate
            .command(
                &spec.program.to_string_lossy(),
                &spec
                    .args
                    .iter()
                    .map(|arg| arg.to_string_lossy().into_owned())
                    .collect::<Vec<_>>(),
            )
            .map_err(|e| format!("cannot prepare Codex proxy gate: {e}"))?;
        #[cfg(unix)]
        let (program, args) = (spec.program, spec.args);
        let mut command = Command::new(program);
        command
            .args(args)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true);
        #[cfg(windows)]
        command.creation_flags(0x08000000);
        #[cfg(unix)]
        command.process_group(0);
        let mut child = command
            .spawn()
            .map_err(|e| format!("cannot start Codex proxy: {e}\n{description}"))?;
        #[cfg(windows)]
        let tree = crate::tree::ProcessTree::attach(child.raw_handle().unwrap());
        #[cfg(unix)]
        let tree = crate::tree::ProcessTree::attach(child.id().unwrap());
        let tree = match tree {
            Ok(tree) => tree,
            Err(error) => {
                let _ = child.kill().await;
                return Err(format!(
                    "cannot contain Codex proxy: {error}\n{description}"
                ));
            }
        };
        let tail = Arc::new(Mutex::new(StderrTail::default()));
        let shared = tail.clone();
        let mut stderr = child.stderr.take().unwrap();
        let reader = tokio::spawn(async move {
            let mut buffer = [0; 4096];
            loop {
                match stderr.read(&mut buffer).await {
                    Ok(0) => break,
                    Ok(count) => shared.lock().unwrap().append(&buffer[..count]),
                    Err(error) => {
                        shared
                            .lock()
                            .unwrap()
                            .append(format!("\n[stderr read failed: {error}]").as_bytes());
                        break;
                    }
                }
            }
        });
        let stream = tokio::io::join(child.stdout.take().unwrap(), child.stdin.take().unwrap());
        let proxy = Self {
            #[cfg(windows)]
            gate,
            child,
            tree: Some(tree),
            tail,
            reader: Some(reader),
            description,
        };
        #[cfg(windows)]
        proxy
            .gate
            .release()
            .map_err(|e| format!("cannot release Codex proxy gate: {e}"))?;
        Ok((proxy, stream))
    }

    pub async fn failure(&mut self, reason: String) -> String {
        // Give a proxy that closed stdout time to publish its natural exit status.
        let natural = timeout(DRAIN_GRACE, self.child.wait())
            .await
            .ok()
            .and_then(Result::ok);
        let cleanup = self.stop().await;
        let status = natural.or_else(|| self.child.try_wait().ok().flatten());
        let status = match status {
            Some(status) => format!(
                "proxy exit status: {status}{}",
                if natural.is_none() {
                    " (stopped during cleanup)"
                } else {
                    ""
                }
            ),
            None => "proxy exit status: unavailable".into(),
        };
        let stderr = self.tail.lock().unwrap().describe();
        let hint = "Background delivery requires a reachable shared Codex daemon with this chat loaded. A stdio-only desktop host does not expose that endpoint. Select the existing host socket with TERMINALHOOK_CODEX_SOCKET or its proxy with TERMINALHOOK_PROXY_COMMAND; do not start a second app server.";
        format!(
            "{reason}\n{}\n{status}\n{stderr}\n{hint}{}",
            self.description,
            cleanup
                .err()
                .map(|error| format!("\nproxy cleanup: {error}"))
                .unwrap_or_default()
        )
    }

    pub async fn stop(&mut self) -> Result<(), String> {
        let cleanup = if let Some(tree) = self.tree.take() {
            tree.terminate().await
        } else {
            Ok(())
        };
        if timeout(DRAIN_GRACE, self.child.wait()).await.is_err() {
            let _ = self.child.start_kill();
        }
        if let Some(mut reader) = self.reader.take()
            && timeout(DRAIN_GRACE, &mut reader).await.is_err()
        {
            reader.abort();
        }
        cleanup
    }
}

impl Drop for Proxy {
    fn drop(&mut self) {
        if let Some(reader) = &self.reader {
            reader.abort();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn resolve(values: &[(&str, &str)]) -> ProxyCommand {
        ProxyCommand::resolve(|key| {
            values
                .iter()
                .find(|(name, _)| *name == key)
                .map(|(_, value)| OsString::from(value))
        })
        .unwrap()
    }
    #[test]
    fn runtime_cli_is_preferred_over_path_and_explicit_overrides_win() {
        let runtime = resolve(&[("CODEX_APP_RUNTIME_CLI_PATH", "host codex")]);
        assert_eq!(runtime.program, "host codex");
        assert_eq!(
            runtime.args,
            vec![OsString::from("app-server"), OsString::from("proxy")]
        );
        let explicit = resolve(&[
            ("CODEX_APP_RUNTIME_CLI_PATH", "host codex"),
            ("TERMINALHOOK_CODEX_BIN", "operator codex"),
        ]);
        assert_eq!(explicit.program, "operator codex");
        let custom = resolve(&[
            (
                "TERMINALHOOK_PROXY_COMMAND",
                r#"["custom proxy","argument with spaces"]"#,
            ),
            ("TERMINALHOOK_CODEX_BIN", "ignored"),
            ("TERMINALHOOK_CODEX_SOCKET", "existing host socket"),
        ]);
        assert_eq!(custom.program, "custom proxy");
        assert_eq!(
            custom.args,
            vec![
                OsString::from("argument with spaces"),
                OsString::from("--sock"),
                OsString::from("existing host socket")
            ]
        );
        assert_eq!(resolve(&[]).program, "codex");
    }
    #[test]
    fn stderr_tail_is_bounded_and_keeps_the_last_error() {
        let mut tail = StderrTail::default();
        tail.append(&vec![b'x'; STDERR_LIMIT + 100]);
        tail.append("\nconnection refused 中文\n".as_bytes());
        assert_eq!(tail.bytes.len(), STDERR_LIMIT);
        assert!(tail.describe().contains("truncated"));
        assert!(tail.describe().ends_with("connection refused 中文"));
        assert!(StderrTail::default().describe().contains("(empty)"));
    }
}
