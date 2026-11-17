use std::path::{Path, PathBuf};
use tokio::io::AsyncWriteExt;
const LOG_LIMIT: usize = 2 * 1024 * 1024;

pub struct Logs {
    pub directory: PathBuf,
    pub rotated: bool,
    files: [Option<tokio::fs::File>; 2],
    sizes: [usize; 2],
}
pub const STREAMS: [&str; 2] = ["stdout", "stderr"];
impl Logs {
    pub async fn new(parent: &Path, id: &str) -> Result<Self, String> {
        let directory = parent.join(id);
        tokio::fs::create_dir_all(&directory)
            .await
            .map_err(|e| format!("cannot create log directory: {e}"))?;
        Ok(Self {
            directory,
            rotated: false,
            files: [None, None],
            sizes: [0; 2],
        })
    }
    pub async fn append(&mut self, index: usize, data: &[u8]) -> Result<(), String> {
        let name = self.directory.join(format!("{}.log", STREAMS[index]));
        if self.sizes[index] + data.len() > LOG_LIMIT {
            self.files[index].take();
            let backup = self.directory.join(format!("{}.log.1", STREAMS[index]));
            match tokio::fs::remove_file(&backup).await {
                Ok(()) => {}
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
                Err(e) => return Err(e.to_string()),
            }
            tokio::fs::rename(&name, &backup)
                .await
                .map_err(|e| e.to_string())?;
            self.sizes[index] = 0;
            self.rotated = true;
        }
        if self.files[index].is_none() {
            self.files[index] = Some(
                tokio::fs::File::create(name)
                    .await
                    .map_err(|e| e.to_string())?,
            );
        }
        self.files[index]
            .as_mut()
            .unwrap()
            .write_all(data)
            .await
            .map_err(|e| e.to_string())?;
        self.files[index]
            .as_mut()
            .unwrap()
            .flush()
            .await
            .map_err(|e| e.to_string())?;
        self.sizes[index] += data.len();
        Ok(())
    }
    pub async fn flush(&mut self) -> Result<(), String> {
        for file in self.files.iter_mut().flatten() {
            file.flush().await.map_err(|e| e.to_string())?;
        }
        Ok(())
    }
}
