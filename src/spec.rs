use crate::matcher::Rule;
use regex::RegexBuilder;
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashSet};

#[derive(Clone, Copy, Default, Deserialize, Serialize, JsonSchema, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum MatchMode {
    #[default]
    Line,
    Window,
}
#[derive(Clone, Deserialize, Serialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct RuleInput {
    pub name: String,
    pub pattern: String,
}

// Internal execution configuration. Only SubmitRequest is exposed through MCP.
pub struct OutputSpec {
    pub until: Vec<RuleInput>,
    pub streams: Vec<String>,
    pub match_mode: MatchMode,
    pub window_bytes: usize,
}
pub struct CommandSpec {
    pub program: String,
    pub args: Vec<String>,
    pub cwd: String,
    pub env: BTreeMap<String, String>,
    pub output: OutputSpec,
    pub log_dir: String,
}

pub fn validate_run(input: &CommandSpec) -> Result<(), String> {
    if input.program.is_empty() || input.program.contains('\0') {
        return Err("program must be nonempty without NUL".into());
    }
    if !std::path::Path::new(&input.cwd).is_absolute() {
        return Err("cwd must be absolute".into());
    }
    if !std::path::Path::new(&input.log_dir).is_absolute() {
        return Err("log_dir must be absolute".into());
    }
    Ok(())
}

pub fn validate_deadline(timeout_ms: Option<u64>) -> Result<(), String> {
    if timeout_ms.is_some_and(|ms| ms == 0 || ms > 31_536_000_000) {
        return Err("timeout_ms must be 1..31536000000, or null for no execution deadline".into());
    }
    Ok(())
}

pub fn validate_wait(input: &OutputSpec) -> Result<Vec<Rule>, String> {
    if !(256..=262144).contains(&input.window_bytes) {
        return Err("window_bytes must be in 256..262144".into());
    }
    if input.until.len() > 16 {
        return Err("until must contain at most 16 rules".into());
    }
    if input
        .streams
        .iter()
        .any(|s| !["stdout", "stderr"].contains(&s.as_str()))
    {
        return Err("streams must use stdout/stderr".into());
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

#[cfg(test)]
#[path = "spec_tests.rs"]
mod tests;
