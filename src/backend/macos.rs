//! The coordinator owns persistence. Codex owns the command and its sandbox.
use super::Event;
use crate::{
    bridge::{Bridge, RPC_TIMEOUT},
    spec::CommandSpec,
};
use data_encoding::BASE64;
use serde_json::{Value, json};
use std::{collections::VecDeque, time::Duration};
use tokio::time::{Instant, timeout_at};

const TERMINATE_REJECTION_GRACE: Duration = Duration::from_secs(1);

pub struct Backend {
    bridge: Bridge,
    program: String,
    process_id: String,
    request_id: u64,
    finished: bool,
    exit_code: Option<i32>,
    completion: VecDeque<Event>,
}

impl Backend {
    pub async fn spawn(input: &CommandSpec) -> Result<Self, String> {
        let mut bridge = Bridge::connect().await?;
        let process_id = uuid::Uuid::new_v4().to_string();
        let command: Vec<_> = std::iter::once(&input.program).chain(&input.args).collect();
        // No sandbox or permission override. The host applies its default policy.
        let request_id = bridge
            .request(
                "command/exec",
                json!({
                    "command": command, "cwd": input.cwd, "env": input.env,
                    "processId": process_id, "streamStdoutStderr": true,
                    "disableOutputCap": true, "disableTimeout": true,
                }),
            )
            .await?;
        Ok(Self {
            bridge,
            program: input.program.clone(),
            process_id,
            request_id,
            finished: false,
            exit_code: None,
            completion: VecDeque::new(),
        })
    }

    // App Server reports a connection-scoped process ID and no OS PID.
    pub fn pid(&self) -> Option<u32> {
        None
    }

    fn completed(&mut self, message: &Value) -> Result<(), String> {
        self.finished = true;
        if let Some(error) = message.get("error") {
            return Err(format!(
                "Failed to start {}: Codex command/exec rejected: {error}",
                self.program
            ));
        }
        let code = message["result"]["exitCode"]
            .as_i64()
            .and_then(|code| i32::try_from(code).ok())
            .ok_or("Codex command/exec did not report an exit code")?;
        self.exit_code = Some(code);
        Ok(())
    }

    async fn receive_event(&mut self) -> Result<Event, String> {
        if let Some(event) = self.completion.pop_front() {
            return Ok(event);
        }
        loop {
            let message = self.bridge.receive().await?;
            if message["id"].as_u64() == Some(self.request_id) {
                self.completed(&message)?;
                self.completion
                    .extend([Event::Eof(1), Event::Exited(self.exit_code)]);
                return Ok(Event::Eof(0));
            }
            let output = &message["params"];
            if message["method"] != "command/exec/outputDelta"
                || output["processId"].as_str() != Some(&self.process_id)
            {
                continue;
            }
            let index = match output["stream"].as_str() {
                Some("stdout") => 0,
                Some("stderr") => 1,
                _ => return Err("Codex command/exec sent an unknown output stream".into()),
            };
            let encoded = output["deltaBase64"]
                .as_str()
                .ok_or("Codex output delta is missing")?;
            let bytes = BASE64
                .decode(encoded.as_bytes())
                .map_err(|e| format!("Invalid Codex output delta: {e}"))?;
            return Ok(Event::Data(index, bytes));
        }
    }

    pub async fn next(&mut self) -> Option<Event> {
        Some(match self.receive_event().await {
            Ok(event) => event,
            Err(error) => Event::Error(error),
        })
    }

    async fn terminate(&mut self) -> Result<(), String> {
        let mut deadline = Instant::now() + RPC_TIMEOUT;
        let mut diagnostic = "Codex did not confirm the command stopped".to_string();
        let terminate_id = timeout_at(
            deadline,
            self.bridge.request(
                "command/exec/terminate",
                json!({"processId": self.process_id}),
            ),
        )
        .await
        .map_err(|_| diagnostic.clone())?
        .map_err(|error| format!("{diagnostic}: {error}"))?;
        loop {
            let message = timeout_at(deadline, self.bridge.receive())
                .await
                .map_err(|_| diagnostic.clone())?
                .map_err(|error| format!("{diagnostic}: {error}"))?;
            let id = message["id"].as_u64();
            if id == Some(self.request_id) {
                return self.completed(&message);
            }
            // Acknowledging termination only confirms receipt. A rejection can
            // race a natural exit whose command result is still in transit.
            if id == Some(terminate_id)
                && let Some(error) = message.get("error")
            {
                diagnostic = format!(
                    "Codex command/exec/terminate rejected: {error}; command exit unconfirmed"
                );
                deadline = deadline.min(Instant::now() + TERMINATE_REJECTION_GRACE);
            }
        }
    }

    pub async fn finish(mut self) -> Result<Option<i32>, String> {
        let cleanup = if self.finished {
            Ok(())
        } else {
            self.terminate().await
        };
        self.bridge.close().await;
        cleanup?;
        Ok(self.exit_code)
    }
}
