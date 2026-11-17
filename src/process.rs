//! One contained command per durable worker; no connection-local process sessions.
use crate::{
    backend::{Backend, Event},
    logs::{Logs, STREAMS},
    matcher::{Hit, MatchBuffer, Normalizer, trim_tail},
    spec::{self, CommandSpec},
};
use serde_json::{Value, json};
use std::time::{Duration, Instant};
use tokio_util::sync::CancellationToken;

pub async fn execute(
    input: CommandSpec,
    cancellation: CancellationToken,
    duration: Option<Duration>,
) -> Result<Value, String> {
    spec::validate_run(&input)?;
    let rules = spec::validate_wait(&input.output)?;
    let started = Instant::now();
    let mut logs = Logs::new(
        std::path::Path::new(&input.log_dir),
        &format!("proc_{}", uuid::Uuid::new_v4().simple()),
    )
    .await?;
    let mut backend = Backend::spawn(&input).await?;
    let mut normalizers: [Normalizer; 2] = Default::default();
    let mut matchers: [MatchBuffer; 2] = Default::default();
    let mut tails: [String; 2] = Default::default();
    let mut eof = [false; 2];
    let mut exit_code = None;
    let mut exited = false;
    let mut bytes = 0;
    let mut hit: Option<Hit> = None;
    let mut error = None;
    let deadline = async {
        match duration {
            Some(duration) => tokio::time::sleep(duration).await,
            None => std::future::pending::<()>().await,
        }
    };
    tokio::pin!(deadline);
    let status = loop {
        tokio::select! {
            biased;
            _ = cancellation.cancelled() => break "cancelled",
            _ = &mut deadline => break "timeout",
            result = backend.process.wait(), if !exited => {
                exited = true;
                match result {
                    Ok(status) => exit_code = status.code(),
                    Err(e) => { error = Some(e.to_string()); break "error"; }
                }
                if let Err(e) = backend.terminate_tree().await { error = Some(e); break "error"; }
                if eof.iter().all(|ended| *ended) { break "exited"; }
            }
            event = backend.events.recv(), if !eof.iter().all(|ended| *ended) => {
                let (index, data, ended) = match event {
                    Some(Event::Data(index, data)) => (index, data, false),
                    Some(Event::Eof(index)) => { eof[index] = true; (index, Vec::new(), true) }
                    Some(Event::Error(e)) => { error = Some(e); break "error"; }
                    None => { error = Some("output channel closed before EOF".into()); break "error"; }
                };
                bytes += data.len();
                if let Err(e) = logs.append(index, &data).await { error = Some(e); break "error"; }
                let text = normalizers[index].push(&data, ended);
                tails[index].push_str(&text);
                trim_tail(&mut tails[index], 4096);
                if !rules.is_empty() && (input.output.streams.is_empty() || input.output.streams.iter().any(|s| s == STREAMS[index])) {
                    match matchers[index].push(&text, &rules, STREAMS[index], input.output.match_mode, input.output.window_bytes) {
                        Ok(Some((matched, _))) => { hit = Some(matched); break "matched"; }
                        Ok(None) => {}
                        Err(e) => { error = Some(e); break "error"; }
                    }
                }
                if exited && eof.iter().all(|ended| *ended) { break "exited"; }
            }
        }
    };
    match backend.finish().await {
        Ok(code) => exit_code = code,
        Err(e) => {
            error.get_or_insert(e);
        }
    }
    if let Err(e) = logs.flush().await {
        error.get_or_insert(e);
    }
    let mut result = json!({
        "status": if error.is_some() { "error" } else { status },
        "os_pid": backend.pid, "process_running": false, "exit_code": exit_code,
        "elapsed_ms": started.elapsed().as_millis() as u64, "bytes_seen": bytes,
        "output_tail": {"stdout": tails[0], "stderr": tails[1]},
        "log_dir": logs.directory, "logs_rotated": logs.rotated,
    });
    if let Some(hit) = hit {
        result.as_object_mut().unwrap().extend(
            serde_json::to_value(hit)
                .unwrap()
                .as_object()
                .unwrap()
                .clone(),
        );
    }
    if let Some(error) = error {
        result["message"] = json!(error);
    }
    Ok(result)
}
