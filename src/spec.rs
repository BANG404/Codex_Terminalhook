use crate::matcher::Rule;
use regex::RegexBuilder;
use schemars::JsonSchema;
use serde::Deserialize;
use std::collections::{BTreeMap, HashSet};

#[derive(Clone, Copy, Default, Deserialize, JsonSchema, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum Action {
    #[default]
    Terminate,
    KeepRunning,
}
#[derive(Clone, Copy, Default, Deserialize, JsonSchema, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum Mode {
    #[default]
    Pipe,
    Pty,
}
#[derive(Clone, Copy, Default, Deserialize, JsonSchema, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum MatchMode {
    #[default]
    Line,
    Window,
}
#[derive(Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct RuleInput {
    pub name: String,
    pub pattern: String,
}

#[derive(Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct WaitSpec {
    /// 1..16 named Rust regex patterns. For one-shot tasks, target the final outcome,
    /// not startup/progress messages. For natural exit, use a specific never-emitted marker.
    /// No lookaround/backreferences or empty matches.
    pub until: Vec<RuleInput>,
    /// stdout/stderr (pipe) or terminal (PTY). Omit for all available sources.
    #[serde(default)]
    pub streams: Vec<String>,
    /// 1..600000 ms, default 300000. Choose a task-sized wait below the client timeout,
    /// not short intervals for progress polling. Do not automatically repeat on timeout.
    #[serde(default = "default_timeout")]
    pub timeout_ms: u64,
    /// line (default) or a bounded multiline window.
    #[serde(default)]
    pub match_mode: MatchMode,
    /// Window mode capacity in normalized UTF-8 bytes, 256..262144.
    #[serde(default = "default_window")]
    pub window_bytes: usize,
}
fn default_timeout() -> u64 {
    300_000
}
fn default_window() -> usize {
    65536
}
fn default_rows() -> u16 {
    24
}
fn default_cols() -> u16 {
    120
}

#[derive(Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct RunRequest {
    /// Executable, not an implicit shell; invoke pwsh/cmd explicitly for shell syntax.
    /// Text I/O is UTF-8. In PowerShell, first set Console.InputEncoding,
    /// Console.OutputEncoding and $OutputEncoding to UTF8Encoding(false).
    pub program: String,
    #[serde(default)]
    pub args: Vec<String>,
    /// Required absolute working directory.
    pub cwd: String,
    #[serde(flatten)]
    pub wait: WaitSpec,
    /// Default terminate. Keep running for a service or a genuine intermediate action,
    /// not merely to return early and continue waiting for a one-shot task.
    #[serde(default)]
    pub on_match: Action,
    /// Default terminate. Keeping a process on timeout does not authorize repeated waits.
    #[serde(default)]
    pub on_timeout: Action,
    #[serde(default)]
    pub env: BTreeMap<String, String>,
    /// pipe (default) preserves stdout/stderr; pty uses ConPTY on Windows and a merged terminal stream.
    #[serde(default)]
    pub mode: Mode,
    #[serde(default = "default_rows")]
    pub rows: u16,
    #[serde(default = "default_cols")]
    pub cols: u16,
    /// Optional absolute parent for persistent rotating raw logs. Default: session-temporary logs.
    pub log_dir: Option<String>,
}

#[derive(Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct WaitRequest {
    pub process_id: String,
    /// Output-event cursor returned by the previous call; prevents replaying old matches.
    pub cursor: u64,
    #[serde(flatten)]
    pub wait: WaitSpec,
}
#[derive(Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct WriteRequest {
    pub process_id: String,
    /// UTF-8 input, at most 65536 bytes. Include newline explicitly (PTY typically uses \r).
    pub data: String,
    /// Pipe only: close stdin after writing. PTY EOF is application-specific.
    #[serde(default)]
    pub eof: bool,
    /// Include when a response is needed, avoiding a separate write-then-poll sequence.
    /// Registered before sending input. Timeout/cancellation leaves the process running;
    /// do not automatically resend input or repeat the wait.
    pub wait: Option<WaitSpec>,
}
#[derive(Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ProcessRequest {
    pub process_id: String,
}

pub fn validate_run(input: &RunRequest) -> Result<(), String> {
    if input.program.is_empty() || input.program.contains('\0') {
        return Err("program must be nonempty without NUL".into());
    }
    if !std::path::Path::new(&input.cwd).is_absolute() {
        return Err("cwd must be absolute".into());
    }
    if let Some(path) = &input.log_dir
        && !std::path::Path::new(path).is_absolute()
    {
        return Err("log_dir must be absolute".into());
    }
    if !(1..=500).contains(&input.rows) || !(1..=500).contains(&input.cols) {
        return Err("rows/cols must be in 1..500".into());
    }
    Ok(())
}
pub fn validate_wait(input: &WaitSpec, mode: Mode) -> Result<Vec<Rule>, String> {
    if !(1..=600_000).contains(&input.timeout_ms) {
        return Err("timeout_ms must be in 1..600000".into());
    }
    if !(256..=262144).contains(&input.window_bytes) {
        return Err("window_bytes must be in 256..262144".into());
    }
    if input.until.is_empty() || input.until.len() > 16 {
        return Err("until must contain 1..16 rules".into());
    }
    let valid: &[&str] = if mode == Mode::Pty {
        &["terminal"]
    } else {
        &["stdout", "stderr"]
    };
    if input.streams.iter().any(|s| !valid.contains(&s.as_str())) {
        return Err(format!("streams must use {valid:?} for this process mode"));
    }
    let mut names = HashSet::new();
    input
        .until
        .iter()
        .map(|r| {
            if r.name.is_empty() || r.name.len() > 128 || !names.insert(&r.name) {
                return Err("rule names must be unique and 1..128 bytes".into());
            }
            if r.pattern.len() > 4096 {
                return Err("regex exceeds 4096 bytes".into());
            }
            let regex = RegexBuilder::new(&r.pattern)
                .size_limit(2 * 1024 * 1024)
                .build()
                .map_err(|e| format!("Invalid regex {}: {e}", r.name))?;
            let hir = regex_syntax::Parser::new()
                .parse(&r.pattern)
                .map_err(|e| e.to_string())?;
            if hir.properties().minimum_len() == Some(0) {
                return Err("regex must not match an empty string".into());
            }
            if regex.captures_len() > 33 {
                return Err("regex supports at most 32 capture groups".into());
            }
            Ok(Rule {
                name: r.name.clone(),
                regex,
            })
        })
        .collect()
}
