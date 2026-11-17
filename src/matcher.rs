use crate::spec::MatchMode;
use regex::Regex;
use serde::Serialize;
pub struct Rule {
    pub name: String,
    pub regex: Regex,
}
#[derive(Debug, Serialize)]
pub struct Hit {
    pub rule: String,
    pub stream: String,
    #[serde(rename = "match")]
    pub text: String,
    pub captures: Vec<Option<String>>,
    /// Match/captures have a combined 8192-byte return budget.
    pub match_output_truncated: bool,
}
#[derive(Default)]
enum Ansi {
    #[default]
    Text,
    Escape,
    Csi,
    Osc,
    OscEscape,
}
#[derive(Default)]
pub struct Normalizer {
    pending: Vec<u8>,
    ansi: Ansi,
    cr: bool,
}
impl Normalizer {
    pub fn push(&mut self, bytes: &[u8], eof: bool) -> String {
        self.pending.extend_from_slice(bytes);
        let mut decoded = String::new();
        let mut used = 0;
        while used < self.pending.len() {
            match std::str::from_utf8(&self.pending[used..]) {
                Ok(s) => {
                    decoded.push_str(s);
                    used = self.pending.len();
                }
                Err(e) => {
                    let valid = e.valid_up_to();
                    decoded
                        .push_str(std::str::from_utf8(&self.pending[used..used + valid]).unwrap());
                    used += valid;
                    match e.error_len() {
                        Some(n) => {
                            decoded.push('\u{fffd}');
                            used += n;
                        }
                        None if eof => {
                            decoded.push('\u{fffd}');
                            used = self.pending.len();
                        }
                        None => break,
                    }
                }
            }
        }
        self.pending.drain(..used);
        let mut out = String::new();
        for ch in decoded.chars() {
            match self.ansi {
                Ansi::Escape => {
                    self.ansi = match ch {
                        '[' => Ansi::Csi,
                        ']' => Ansi::Osc,
                        _ => Ansi::Text,
                    };
                    continue;
                }
                Ansi::Csi => {
                    if ('\u{40}'..='\u{7e}').contains(&ch) {
                        self.ansi = Ansi::Text;
                    }
                    continue;
                }
                Ansi::Osc => {
                    if ch == '\u{7}' {
                        self.ansi = Ansi::Text;
                    } else if ch == '\u{1b}' {
                        self.ansi = Ansi::OscEscape;
                    }
                    continue;
                }
                Ansi::OscEscape => {
                    self.ansi = if ch == '\\' { Ansi::Text } else { Ansi::Osc };
                    continue;
                }
                Ansi::Text => {}
            }
            if ch == '\u{1b}' {
                self.ansi = Ansi::Escape;
                continue;
            }
            if ch == '\n' && self.cr {
                self.cr = false;
                continue;
            }
            self.cr = ch == '\r';
            out.push(if ch == '\r' { '\n' } else { ch });
        }
        out
    }
}
#[derive(Default)]
pub struct MatchBuffer {
    text: String,
}
impl MatchBuffer {
    pub fn push(
        &mut self,
        text: &str,
        rules: &[Rule],
        stream: &str,
        mode: MatchMode,
        window: usize,
    ) -> Result<Option<(Hit, usize)>, String> {
        if mode == MatchMode::Window {
            let previous = self.text.len();
            self.text.push_str(text);
            let before = self.text.len();
            trim_tail(&mut self.text, window);
            let removed = before - self.text.len();
            return Ok(self.find(rules, stream).map(|(hit, end)| {
                (
                    hit,
                    (end + removed).saturating_sub(previous).min(text.len()),
                )
            }));
        }
        let mut consumed = 0;
        for piece in text.split_inclusive('\n') {
            let previous = self.text.len();
            self.text
                .push_str(piece.strip_suffix('\n').unwrap_or(piece));
            if let Some((hit, end)) = self.find(rules, stream) {
                return Ok(Some((hit, consumed + end.saturating_sub(previous))));
            }
            if self.text.len() > 65536 {
                return Err(
                    "output line exceeds 65536 bytes; use bounded window mode for long output"
                        .into(),
                );
            }
            if piece.ends_with('\n') {
                self.text.clear();
            }
            consumed += piece.len();
        }
        Ok(None)
    }
    fn find(&self, rules: &[Rule], stream: &str) -> Option<(Hit, usize)> {
        rules.iter().find_map(|r| {
            r.regex.captures(&self.text).map(|c| {
                let mut budget = 8192;
                let text = take_prefix(&c[0], budget.min(4096));
                budget -= text.len();
                let mut truncated = text.len() != c[0].len();
                let captures = c
                    .iter()
                    .skip(1)
                    .map(|m| {
                        m.map(|m| {
                            let value = take_prefix(m.as_str(), budget);
                            budget -= value.len();
                            truncated |= value.len() != m.as_str().len();
                            value
                        })
                    })
                    .collect();
                (
                    Hit {
                        rule: r.name.clone(),
                        stream: stream.into(),
                        text,
                        captures,
                        match_output_truncated: truncated,
                    },
                    c.get(0).unwrap().end(),
                )
            })
        })
    }
}
fn take_prefix(s: &str, max: usize) -> String {
    let mut end = s.len().min(max);
    while !s.is_char_boundary(end) {
        end -= 1;
    }
    s[..end].to_owned()
}
pub fn trim_tail(s: &mut String, max: usize) {
    if s.len() <= max {
        return;
    }
    let mut start = s.len() - max;
    while !s.is_char_boundary(start) {
        start += 1;
    }
    s.drain(..start);
}
#[cfg(test)]
#[path = "matcher_tests.rs"]
mod tests;
