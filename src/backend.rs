use crate::{
    spec::{Mode, RunRequest},
    tree::ProcessTree,
};
use std::{
    io::{Read, Write},
    sync::Arc,
};
use tokio::{
    io::{AsyncRead, AsyncReadExt, AsyncWriteExt},
    process::Child,
    sync::{Mutex, mpsc},
    task::JoinHandle,
};

pub enum Event {
    Data(usize, Vec<u8>),
    Eof(usize),
    Error(String),
}
pub enum Input {
    Pipe(Option<tokio::process::ChildStdin>),
    Pty(Option<Arc<std::sync::Mutex<Box<dyn Write + Send>>>>),
}
pub type SharedInput = Arc<Mutex<Input>>;
impl Input {
    pub async fn write(&mut self, data: Vec<u8>, eof: bool) -> Result<(), String> {
        match self {
            Self::Pipe(slot) => {
                let pipe = slot.as_mut().ok_or("stdin is closed")?;
                pipe.write_all(&data).await.map_err(|e| e.to_string())?;
                pipe.flush().await.map_err(|e| e.to_string())?;
                if eof {
                    slot.take();
                }
            }
            Self::Pty(slot) => {
                if eof {
                    return Err("PTY does not support closing stdin; send an application-specific control character".into());
                }
                let writer = slot.as_ref().ok_or("terminal input is closed")?.clone();
                tokio::task::spawn_blocking(move || {
                    let mut writer = writer.lock().unwrap();
                    writer
                        .write_all(&data)
                        .and_then(|_| writer.flush())
                        .map_err(|e| e.to_string())
                })
                .await
                .map_err(|e| e.to_string())??;
            }
        }
        Ok(())
    }
    fn close(&mut self) {
        match self {
            Self::Pipe(p) => {
                p.take();
            }
            Self::Pty(p) => {
                p.take();
            }
        }
    }
}

pub enum Process {
    Pipe(Box<Child>),
    Pty {
        wait: Option<JoinHandle<Result<Option<i32>, String>>>,
        killer: Box<dyn portable_pty::ChildKiller + Send + Sync>,
    },
}
pub struct Backend {
    #[cfg(windows)]
    gate: Option<crate::tree::Gate>,
    pub process: Process,
    tree: Option<ProcessTree>,
    master: Option<Box<dyn portable_pty::MasterPty + Send>>,
    closing: Option<JoinHandle<()>>,
    pub events: mpsc::Receiver<Event>,
    pub input: SharedInput,
    pub pid: u32,
    readers: Vec<JoinHandle<()>>,
}

fn pipe_reader<R: AsyncRead + Unpin + Send + 'static>(
    mut pipe: R,
    index: usize,
    tx: mpsc::Sender<Event>,
) -> JoinHandle<()> {
    tokio::spawn(async move {
        let mut buf = [0; 4096];
        loop {
            match pipe.read(&mut buf).await {
                Ok(0) => {
                    let _ = tx.send(Event::Eof(index)).await;
                    break;
                }
                Ok(n) => {
                    if tx
                        .send(Event::Data(index, buf[..n].to_vec()))
                        .await
                        .is_err()
                    {
                        break;
                    }
                }
                Err(e) => {
                    let _ = tx.send(Event::Error(e.to_string())).await;
                    break;
                }
            }
        }
    })
}

impl Backend {
    pub async fn spawn(input: &RunRequest) -> Result<Self, String> {
        #[cfg(windows)]
        let gate = crate::tree::Gate::new().map_err(|e| e.to_string())?;
        #[cfg(windows)]
        let (program, args) = gate
            .command(&input.program, &input.args)
            .map_err(|e| e.to_string())?;
        #[cfg(unix)]
        let (program, args) = (
            std::ffi::OsString::from(&input.program),
            input
                .args
                .iter()
                .map(std::ffi::OsString::from)
                .collect::<Vec<_>>(),
        );
        let (tx, rx) = mpsc::channel(32);
        let backend = if input.mode == Mode::Pipe {
            let mut command = tokio::process::Command::new(program);
            command
                .args(args)
                .current_dir(&input.cwd)
                .envs(&input.env)
                .stdin(std::process::Stdio::piped())
                .stdout(std::process::Stdio::piped())
                .stderr(std::process::Stdio::piped())
                .kill_on_drop(true);
            #[cfg(windows)]
            command.creation_flags(0x08000000);
            #[cfg(unix)]
            command.process_group(0);
            let mut child = command
                .spawn()
                .map_err(|e| format!("Failed to start {}: {e}", input.program))?;
            let pid = child.id().unwrap();
            #[cfg(windows)]
            let tree = ProcessTree::attach(child.raw_handle().unwrap());
            #[cfg(unix)]
            let tree = ProcessTree::attach(pid);
            let tree = match tree {
                Ok(t) => t,
                Err(e) => {
                    let _ = child.kill().await;
                    return Err(format!("Failed to contain process: {e}"));
                }
            };
            let stdin = child.stdin.take();
            let readers = vec![
                pipe_reader(child.stdout.take().unwrap(), 0, tx.clone()),
                pipe_reader(child.stderr.take().unwrap(), 1, tx),
            ];
            Self {
                #[cfg(windows)]
                gate: None,
                process: Process::Pipe(Box::new(child)),
                tree: Some(tree),
                master: None,
                closing: None,
                events: rx,
                input: Arc::new(Mutex::new(Input::Pipe(stdin))),
                pid,
                readers,
            }
        } else {
            let pair = portable_pty::native_pty_system()
                .openpty(portable_pty::PtySize {
                    rows: input.rows,
                    cols: input.cols,
                    pixel_width: 0,
                    pixel_height: 0,
                })
                .map_err(|e| e.to_string())?;
            let mut command = portable_pty::CommandBuilder::new(program);
            command.args(args);
            command.cwd(&input.cwd);
            for (key, value) in &input.env {
                command.env(key, value);
            }
            let mut child = pair
                .slave
                .spawn_command(command)
                .map_err(|e| e.to_string())?;
            let pid = child.process_id().ok_or("missing terminal process id")?;
            #[cfg(windows)]
            let tree = ProcessTree::attach(
                child
                    .as_raw_handle()
                    .ok_or("missing terminal process handle")?,
            );
            #[cfg(unix)]
            let tree = ProcessTree::attach(pid);
            let tree = match tree {
                Ok(t) => t,
                Err(e) => {
                    let _ = child.kill();
                    return Err(format!("Failed to contain terminal: {e}"));
                }
            };
            let killer = child.clone_killer();
            let mut reader = pair.master.try_clone_reader().map_err(|e| e.to_string())?;
            let writer = pair.master.take_writer().map_err(|e| e.to_string())?;
            let input_writer = Arc::new(std::sync::Mutex::new(writer));
            let reply_writer = input_writer.clone();
            drop(pair.slave);
            let read = tokio::task::spawn_blocking(move || {
                let mut buf = [0; 4096];
                let mut query = Vec::new();
                loop {
                    match reader.read(&mut buf) {
                        Ok(0) => {
                            let _ = tx.blocking_send(Event::Eof(2));
                            break;
                        }
                        Ok(n) => {
                            // Headless ConPTY requests an initial cursor position (DSR).
                            // Answer protocol queries locally; never involve the model.
                            for byte in &buf[..n] {
                                query.push(*byte);
                                if query.ends_with(b"\x1b[6n") {
                                    let _ = reply_writer.lock().unwrap().write_all(b"\x1b[1;1R");
                                    query.clear();
                                }
                                if query.len() > 8 {
                                    query.remove(0);
                                }
                            }
                            if tx.blocking_send(Event::Data(2, buf[..n].to_vec())).is_err() {
                                break;
                            }
                        }
                        Err(e) => {
                            // Unix PTYs conventionally return EIO when the slave closes.
                            #[cfg(unix)]
                            if e.raw_os_error() == Some(libc::EIO) {
                                let _ = tx.blocking_send(Event::Eof(2));
                                break;
                            }
                            let _ = tx.blocking_send(Event::Error(e.to_string()));
                            break;
                        }
                    }
                }
            });
            let wait = tokio::task::spawn_blocking(move || {
                child
                    .wait()
                    .map(|s| Some(s.exit_code() as i32))
                    .map_err(|e| e.to_string())
            });
            Self {
                #[cfg(windows)]
                gate: None,
                process: Process::Pty {
                    wait: Some(wait),
                    killer,
                },
                tree: Some(tree),
                master: Some(pair.master),
                closing: None,
                events: rx,
                input: Arc::new(Mutex::new(Input::Pty(Some(input_writer)))),
                pid,
                readers: vec![read],
            }
        };
        #[cfg(windows)]
        let mut backend = backend;
        #[cfg(windows)]
        {
            if let Err(e) = gate.release() {
                let _ = backend.finish().await;
                return Err(e.to_string());
            }
            // Keep the named event alive until the gate has opened it (and until exit).
            backend.gate = Some(gate);
        }
        Ok(backend)
    }
    pub async fn finish(&mut self) -> Result<(), String> {
        let cleanup = self.terminate_tree().await;
        match &mut self.process {
            Process::Pipe(child) => {
                let _ = child.kill().await;
            }
            Process::Pty { killer, wait } => {
                let _ = killer.kill();
                if let Some(wait) = wait.take() {
                    let _ = wait.await;
                }
            }
        }
        self.events.close(); // unblock a reader waiting to send into a full queue
        self.input.lock().await.close();
        if let Some(master) = self.master.take() {
            let _ = tokio::task::spawn_blocking(move || drop(master)).await;
        }
        if let Some(closing) = self.closing.take() {
            let _ = closing.await;
        }
        for reader in self.readers.drain(..) {
            if matches!(self.process, Process::Pipe(_)) {
                reader.abort();
            }
            let _ = reader.await;
        }
        cleanup
    }
    pub fn terminal(&self) -> bool {
        self.master.is_some()
    }
    pub async fn terminate_tree(&mut self) -> Result<(), String> {
        if let Some(tree) = self.tree.take() {
            tree.terminate().await?;
        }
        Ok(())
    }
    pub fn close_terminal(&mut self) {
        if let Some(master) = self.master.take() {
            self.closing = Some(tokio::task::spawn_blocking(move || drop(master)));
        }
    }
}
impl Process {
    pub async fn wait(&mut self) -> Result<Option<i32>, String> {
        match self {
            Self::Pipe(child) => child
                .wait()
                .await
                .map(|s| s.code())
                .map_err(|e| e.to_string()),
            Self::Pty { wait, .. } => {
                let result = wait
                    .as_mut()
                    .ok_or("terminal already reaped")?
                    .await
                    .map_err(|e| e.to_string())?;
                wait.take();
                result
            }
        }
    }
}
