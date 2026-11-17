use crate::{spec::CommandSpec, tree::ProcessTree};
use tokio::{
    io::{AsyncRead, AsyncReadExt},
    process::Child,
    sync::mpsc,
    task::JoinHandle,
};

pub enum Event {
    Data(usize, Vec<u8>),
    Eof(usize),
    Error(String),
}
pub struct Backend {
    #[cfg(windows)]
    gate: Option<crate::tree::Gate>,
    pub process: Child,
    tree: Option<ProcessTree>,
    pub events: mpsc::Receiver<Event>,
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
    pub async fn spawn(input: &CommandSpec) -> Result<Self, String> {
        #[cfg(windows)]
        let gate = crate::tree::Gate::new().map_err(|e| e.to_string())?;
        #[cfg(windows)]
        let (program, args) = gate
            .command(&input.program, &input.args)
            .map_err(|e| e.to_string())?;
        #[cfg(unix)]
        let (program, args) = (&input.program, &input.args);
        let mut command = tokio::process::Command::new(program);
        command
            .args(args)
            .current_dir(&input.cwd)
            .envs(&input.env)
            .stdin(std::process::Stdio::null())
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
            Ok(tree) => tree,
            Err(error) => {
                let _ = child.kill().await;
                return Err(format!("Failed to contain process: {error}"));
            }
        };
        let (sender, events) = mpsc::channel(32);
        let readers = vec![
            pipe_reader(child.stdout.take().unwrap(), 0, sender.clone()),
            pipe_reader(child.stderr.take().unwrap(), 1, sender),
        ];
        let backend = Self {
            #[cfg(windows)]
            gate: None,
            process: child,
            tree: Some(tree),
            events,
            pid,
            readers,
        };
        #[cfg(windows)]
        let mut backend = backend;
        #[cfg(windows)]
        {
            if let Err(error) = gate.release() {
                let _ = backend.finish().await;
                return Err(error.to_string());
            }
            backend.gate = Some(gate);
        }
        Ok(backend)
    }

    pub async fn terminate_tree(&mut self) -> Result<(), String> {
        if let Some(tree) = self.tree.take() {
            tree.terminate().await?;
        }
        Ok(())
    }

    pub async fn finish(&mut self) -> Result<Option<i32>, String> {
        let cleanup = self.terminate_tree().await;
        if self.process.id().is_some() {
            let _ = self.process.kill().await;
        }
        let result = self
            .process
            .wait()
            .await
            .map(|s| s.code())
            .map_err(|e| e.to_string());
        self.events.close();
        for reader in self.readers.drain(..) {
            reader.abort();
            let _ = reader.await;
        }
        cleanup?;
        result
    }
}
