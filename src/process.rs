use crate::{
    backend::{Backend, Event, SharedInput},
    logs::{Logs, STREAMS},
    matcher::{Hit, MatchBuffer, Normalizer, Rule, trim_tail},
    spec::{self, Action, Mode, RunRequest, WaitRequest, WaitSpec, WriteRequest},
};
use serde_json::{Value, json};
use std::{
    collections::{HashMap, VecDeque},
    sync::{Arc, Mutex},
    time::Instant,
};
use tokio::{
    sync::{Semaphore, watch},
    time::{Duration, sleep, timeout},
};
use tokio_util::sync::CancellationToken;

const HISTORY_LIMIT: usize = 1024 * 1024;
const MAX_SESSIONS: usize = 64;
#[derive(Clone)]
struct Record {
    cursor: u64,
    index: usize,
    text: String,
}
struct State {
    history: VecDeque<Record>,
    history_bytes: usize,
    floor: u64,
    cursor: u64,
    tails: [String; 3],
    bytes: u64,
    running: bool,
    exit_code: Option<i32>,
    error: Option<String>,
    log_dir: String,
    logs_rotated: bool,
}
struct Session {
    id: String,
    pid: u32,
    mode: Mode,
    started: Instant,
    state: Mutex<State>,
    changed: watch::Sender<u64>,
    stop: CancellationToken,
    done: watch::Sender<bool>,
    input: SharedInput,
}
struct Inner {
    sessions: Mutex<HashMap<String, Arc<Session>>>,
    slots: Arc<Semaphore>,
    logs: tempfile::TempDir,
}
#[derive(Clone)]
pub struct Manager(Arc<Inner>);
impl Manager {
    pub fn new() -> Result<Self, String> {
        let logs = tempfile::Builder::new()
            .prefix("terminalhook-")
            .tempdir()
            .map_err(|e| e.to_string())?;
        Ok(Self(Arc::new(Inner {
            sessions: Mutex::new(HashMap::new()),
            slots: Arc::new(Semaphore::new(8)),
            logs,
        })))
    }
    fn get(&self, id: &str) -> Result<Arc<Session>, String> {
        self.0
            .sessions
            .lock()
            .unwrap()
            .get(id)
            .cloned()
            .ok_or_else(|| "unknown or expired process_id in this connection".into())
    }
    pub async fn run(&self, input: RunRequest, ct: CancellationToken) -> Result<Value, String> {
        spec::validate_wait(&input.wait, input.mode)?;
        let duration = Some(Duration::from_millis(input.wait.timeout_ms));
        self.run_with_deadline(input, ct, duration).await
    }
    pub async fn run_with_deadline(
        &self,
        input: RunRequest,
        ct: CancellationToken,
        duration: Option<Duration>,
    ) -> Result<Value, String> {
        spec::validate_run(&input)?;
        let rules = if input.wait.until.is_empty() {
            Vec::new()
        } else {
            spec::validate_rules(&input.wait, input.mode)?
        };
        let permit = self
            .0
            .slots
            .clone()
            .try_acquire_owned()
            .map_err(|_| "at most 8 active processes per connection")?;
        let id = format!("proc_{}", uuid::Uuid::new_v4().simple());
        let parent = input
            .log_dir
            .as_deref()
            .map(std::path::Path::new)
            .unwrap_or(self.0.logs.path());
        let logs = Logs::new(parent, &id).await?;
        let backend = Backend::spawn(&input).await?;
        let session = Arc::new(Session {
            id: id.clone(),
            pid: backend.pid,
            mode: input.mode,
            started: Instant::now(),
            state: Mutex::new(State {
                history: VecDeque::new(),
                history_bytes: 0,
                floor: 0,
                cursor: 0,
                tails: Default::default(),
                bytes: 0,
                running: true,
                exit_code: None,
                error: None,
                log_dir: logs.directory.to_string_lossy().into_owned(),
                logs_rotated: false,
            }),
            changed: watch::channel(0).0,
            stop: CancellationToken::new(),
            done: watch::channel(false).0,
            input: backend.input.clone(),
        });
        let mut evicted = None;
        {
            let mut sessions = self.0.sessions.lock().unwrap();
            if sessions.len() >= MAX_SESSIONS {
                let oldest = sessions
                    .values()
                    .filter(|s| !s.state.lock().unwrap().running)
                    .min_by_key(|s| s.started)
                    .map(|s| s.id.clone());
                if let Some(oldest) = oldest {
                    sessions.remove(&oldest);
                    evicted = Some(oldest);
                }
            }
            sessions.insert(id, session.clone());
        }
        let mut guard = StopOnDrop(Some(session.stop.clone()));
        let actor = session.clone();
        tokio::spawn(async move {
            supervise(actor.clone(), backend, logs).await;
            drop(permit);
            actor.done.send_replace(true);
        });
        if let Some(oldest) = evicted {
            // Delete only our own generated per-process temporary directory. Never
            // recursively remove the caller's persistent log_dir.
            let root = self.0.logs.path();
            let path = root.join(oldest);
            if path.parent() == Some(root) && path.starts_with(root) {
                let _ = tokio::fs::remove_dir_all(path).await;
            }
        }
        let waited = wait_for_deadline(&session, 0, &input.wait, &rules, ct, duration).await;
        match waited {
            Ok(mut value) => {
                let terminate = match value["status"].as_str() {
                    Some("matched") => input.on_match == Action::Terminate,
                    Some("timeout") => input.on_timeout == Action::Terminate,
                    Some("error" | "cursor_expired") => true,
                    _ => false,
                };
                if terminate || value["process_running"] == false {
                    if terminate {
                        session.stop.cancel();
                    }
                    session.wait_done().await;
                    refresh(&session, &mut value);
                }
                guard.0.take();
                Ok(value)
            }
            Err(error) => {
                session.stop.cancel();
                session.wait_done().await;
                Err(error)
            }
        }
    }
    pub async fn wait_output(
        &self,
        input: WaitRequest,
        ct: CancellationToken,
    ) -> Result<Value, String> {
        let session = self.get(&input.process_id)?;
        let rules = spec::validate_wait(&input.wait, session.mode)?;
        wait_for(&session, input.cursor, &input.wait, &rules, ct).await
    }
    pub async fn write_input(
        &self,
        input: WriteRequest,
        ct: CancellationToken,
    ) -> Result<Value, String> {
        let session = self.get(&input.process_id)?;
        if input.data.len() > 65536 {
            return Err("input exceeds 65536 bytes".into());
        }
        if input.eof && session.mode == Mode::Pty {
            return Err("PTY EOF must be sent as an application-specific control character".into());
        }
        let rules = input
            .wait
            .as_ref()
            .map(|w| spec::validate_wait(w, session.mode))
            .transpose()?;
        // Capture cursor while holding the input lock, before any bytes can be sent.
        let mut writer = session.input.lock().await;
        let cursor = {
            let state = session.state.lock().unwrap();
            if !state.running {
                return Err("process has exited".into());
            }
            state.cursor
        };
        if ct.is_cancelled() {
            return Err("input request cancelled before sending".into());
        }
        let send = timeout(
            Duration::from_secs(5),
            writer.write(input.data.into_bytes(), input.eof),
        )
        .await;
        drop(writer);
        match send {
            Ok(result) => result?,
            Err(_) => {
                session.stop.cancel();
                session.wait_done().await;
                return Err(
                    "stdin write timed out; process terminated to cancel the pending write".into(),
                );
            }
        }
        match (&input.wait, &rules) {
            (Some(wait), Some(rules)) => wait_for(&session, cursor, wait, rules, ct).await,
            _ => Ok(session.value("written", cursor, None)),
        }
    }
    pub async fn stop(&self, id: &str) -> Result<Value, String> {
        let session = match self.get(id) {
            Ok(s) => s,
            Err(_) => return Ok(json!({"status":"not_running","process_id":id})),
        };
        let running = session.state.lock().unwrap().running;
        if running {
            session.stop.cancel();
        }
        session.wait_done().await;
        let mut value = session.value(
            if running { "stopped" } else { "not_running" },
            session.cursor(),
            None,
        );
        refresh(&session, &mut value);
        Ok(value)
    }
    pub async fn shutdown(&self) {
        let sessions: Vec<_> = self.0.sessions.lock().unwrap().values().cloned().collect();
        for s in &sessions {
            s.stop.cancel();
        }
        for s in sessions {
            s.wait_done().await;
        }
    }
}

struct StopOnDrop(Option<CancellationToken>);
impl Drop for StopOnDrop {
    fn drop(&mut self) {
        if let Some(ct) = &self.0 {
            ct.cancel();
        }
    }
}
impl Session {
    fn cursor(&self) -> u64 {
        self.state.lock().unwrap().cursor
    }
    async fn wait_done(&self) {
        let mut done = self.done.subscribe();
        loop {
            if *done.borrow_and_update() {
                break;
            }
            if done.changed().await.is_err() {
                break;
            }
        }
    }
    fn value(&self, status: &str, cursor: u64, hit: Option<Hit>) -> Value {
        let state = self.state.lock().unwrap();
        let mut value = json!({"status":status,"process_id":self.id,"os_pid":self.pid,"cursor":cursor,"latest_cursor":state.cursor,"oldest_cursor":state.floor,
            "process_running":state.running,"exit_code":state.exit_code,"elapsed_ms":self.started.elapsed().as_millis() as u64,"bytes_seen":state.bytes,
            "output_tail":{"stdout":state.tails[0],"stderr":state.tails[1],"terminal":state.tails[2]},"log_dir":state.log_dir,"logs_rotated":state.logs_rotated});
        if let Some(hit) = hit {
            value.as_object_mut().unwrap().extend(
                serde_json::to_value(hit)
                    .unwrap()
                    .as_object()
                    .unwrap()
                    .clone(),
            );
        }
        if let Some(error) = &state.error {
            value["message"] = json!(error);
        }
        value
    }
}
fn refresh(s: &Session, value: &mut Value) {
    let state = s.state.lock().unwrap();
    value["process_running"] = json!(state.running);
    value["exit_code"] = json!(state.exit_code);
    value["logs_rotated"] = json!(state.logs_rotated);
    if let Some(error) = &state.error {
        value["status"] = json!("error");
        value["message"] = json!(error);
    }
}

async fn wait_for(
    session: &Session,
    cursor: u64,
    spec: &WaitSpec,
    rules: &[Rule],
    ct: CancellationToken,
) -> Result<Value, String> {
    wait_for_deadline(
        session,
        cursor,
        spec,
        rules,
        ct,
        Some(Duration::from_millis(spec.timeout_ms)),
    )
    .await
}

async fn wait_for_deadline(
    session: &Session,
    mut cursor: u64,
    spec: &WaitSpec,
    rules: &[Rule],
    ct: CancellationToken,
    duration: Option<Duration>,
) -> Result<Value, String> {
    let started = Instant::now();
    let mut changes = session.changed.subscribe();
    let deadline = async {
        match duration {
            Some(duration) => sleep(duration).await,
            None => std::future::pending::<()>().await,
        }
    };
    tokio::pin!(deadline);
    let mut matchers: [MatchBuffer; 3] = Default::default();
    loop {
        let (records, running, error, floor, latest) = {
            let state = session.state.lock().unwrap();
            (
                state
                    .history
                    .iter()
                    .filter(|r| r.cursor > cursor)
                    .cloned()
                    .collect::<Vec<_>>(),
                state.running,
                state.error.clone(),
                state.floor,
                state.cursor,
            )
        };
        if cursor > latest {
            return Err("cursor is ahead of this process's output".into());
        }
        if cursor < floor {
            return Ok(session.value("cursor_expired", cursor, None));
        }
        for record in records {
            let start = record.cursor - record.text.len() as u64;
            let skip = cursor.saturating_sub(start) as usize;
            let text = record
                .text
                .get(skip..)
                .ok_or("cursor is not on a UTF-8 boundary")?;
            let stream = STREAMS[record.index];
            if !spec.streams.is_empty() && !spec.streams.iter().any(|s| s == stream) {
                cursor = record.cursor;
                continue;
            }
            match matchers[record.index].push(
                text,
                rules,
                stream,
                spec.match_mode,
                spec.window_bytes,
            ) {
                Ok(Some((hit, consumed))) => {
                    cursor = start + skip as u64 + consumed as u64;
                    let mut value = session.value("matched", cursor, Some(hit));
                    value["wait_elapsed_ms"] = json!(started.elapsed().as_millis() as u64);
                    return Ok(value);
                }
                Ok(None) => {}
                Err(error) => {
                    let mut value = session.value("error", cursor, None);
                    value["message"] = json!(error);
                    return Ok(value);
                }
            }
            cursor = record.cursor;
        }
        if let Some(error) = error {
            let mut value = session.value("error", cursor, None);
            value["message"] = json!(error);
            return Ok(value);
        }
        if !running {
            return Ok(session.value("exited_unmatched", cursor, None));
        }
        // Mark only after replay. If output raced with the snapshot, changed() stays ready.
        tokio::select! {
            biased;
            _ = ct.cancelled() => return Err("wait cancelled".into()),
            _ = &mut deadline => return Ok(session.value("timeout", cursor, None)),
            result = changes.changed() => if result.is_err() { return Err("session closed".into()); },
        }
    }
}

async fn supervise(session: Arc<Session>, mut backend: Backend, mut logs: Logs) {
    let mut normalizers: [Normalizer; 3] = Default::default();
    let mut eof = if backend.terminal() {
        [true, true, false]
    } else {
        [false, false, true]
    };
    let mut exited = false;
    let mut exit_code = None;
    let mut error = None;
    loop {
        tokio::select! {
            biased;
            _ = session.stop.cancelled() => break,
            result = backend.process.wait(), if !exited => {
                exited = true;
                match result { Ok(code) => exit_code = code, Err(e) => { error = Some(e); break; } }
                if let Err(e) = backend.terminate_tree().await { error = Some(e); break; }
                // Close ConPTY concurrently while continuing to consume its final output.
                if session.mode == Mode::Pty { backend.close_terminal(); }
                if eof.iter().all(|x| *x) { break; }
            }
            event = backend.events.recv(), if !eof.iter().all(|x| *x) => {
                let (index, data, ended) = match event {
                    Some(Event::Data(index, data)) => (index,data,false),
                    Some(Event::Eof(index)) => { eof[index] = true; (index,Vec::new(),true) },
                    Some(Event::Error(e)) => { error = Some(e); break; },
                    None => { error = Some("output channel closed before EOF".into()); break; }
                };
                if let Err(e) = logs.append(index, &data).await { error = Some(format!("log write failed: {e}")); break; }
                let text = normalizers[index].push(&data, ended);
                {
                    let mut state = session.state.lock().unwrap();
                    state.bytes += data.len() as u64;
                    state.logs_rotated = logs.rotated;
                    if !text.is_empty() {
                        state.tails[index].push_str(&text);
                        trim_tail(&mut state.tails[index], 4096);
                        state.cursor += text.len() as u64;
                        let cursor = state.cursor;
                        state.history_bytes += text.len();
                        state.history.push_back(Record { cursor,index,text });
                        while state.history_bytes > HISTORY_LIMIT {
                            if let Some(record) = state.history.pop_front() { state.history_bytes -= record.text.len(); state.floor = record.cursor; }
                        }
                    }
                }
                session.changed.send_modify(|n| *n += 1);
                if exited && eof.iter().all(|x| *x) { break; }
            }
        }
    }
    if let Err(e) = backend.finish().await {
        error.get_or_insert(e);
    }
    if let Err(e) = logs.flush().await {
        error.get_or_insert(e);
    }
    {
        let mut state = session.state.lock().unwrap();
        state.running = false;
        state.exit_code = exit_code;
        state.error = error;
    }
    session.changed.send_modify(|n| *n += 1);
}
