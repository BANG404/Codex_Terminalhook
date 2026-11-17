use super::*;
fn rules(p: &str) -> Vec<Rule> {
    vec![Rule {
        name: "ready".into(),
        regex: Regex::new(p).unwrap(),
    }]
}
#[test]
fn split_utf8_and_ansi() {
    let mut n = Normalizer::default();
    let bytes = "\x1b[32m就绪:5173\x1b[0m".as_bytes();
    assert_eq!(n.push(&bytes[..7], false), "");
    let text = n.push(&bytes[7..], false);
    assert_eq!(text, "就绪:5173");
    let hit = MatchBuffer::default()
        .push(
            &text,
            &rules("就绪:(\\d+)"),
            "stdout",
            MatchMode::Line,
            65536,
        )
        .unwrap()
        .unwrap();
    assert_eq!(hit.0.captures, vec![Some("5173".into())]);
}
#[test]
fn line_and_window_semantics() {
    let r = rules("REA\\nDY");
    assert!(
        MatchBuffer::default()
            .push("REA\nDY", &r, "stdout", MatchMode::Line, 65536)
            .unwrap()
            .is_none()
    );
    assert!(
        MatchBuffer::default()
            .push("REA\nDY", &r, "stdout", MatchMode::Window, 65536)
            .unwrap()
            .is_some()
    );
}
#[test]
fn crlf_osc_invalid_utf8() {
    let mut n = Normalizer::default();
    assert_eq!(
        n.push(b"old\r\n\x1b]0;title\x1b\\READY\r\xff\xe4", true),
        "old\nREADY\n\u{fffd}\u{fffd}"
    );
}
#[test]
fn bounded_output_and_unicode() {
    let mut s = "汉".repeat(24000);
    let hit = MatchBuffer::default()
        .push(&s, &rules("(.+)"), "stdout", MatchMode::Window, 262144)
        .unwrap()
        .unwrap();
    assert!(hit.0.match_output_truncated);
    assert!(hit.0.text.len() + hit.0.captures[0].as_ref().unwrap().len() <= 8192);
    trim_tail(&mut s, 4096);
    assert!(s.len() <= 4096);
}
