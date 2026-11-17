use super::*;
use serde_json::json;

fn wait() -> WaitSpec {
    serde_json::from_value(json!({"until":[{"name":"ready","pattern":"READY"}]})).unwrap()
}

fn run() -> RunRequest {
    serde_json::from_value(json!({
        "program":"python", "cwd":std::env::current_dir().unwrap(),
        "until":[{"name":"ready","pattern":"READY"}]
    }))
    .unwrap()
}

#[test]
fn run_accepts_absolute_paths_and_default_dimensions() {
    let mut request = run();
    assert_eq!((request.rows, request.cols), (24, 120));
    assert!(validate_run(&request).is_ok());
    request.log_dir = Some(request.cwd.clone());
    for edge in [1, 500] {
        request.rows = edge;
        request.cols = edge;
        assert!(validate_run(&request).is_ok());
    }
}

#[test]
fn run_rejects_invalid_program_paths_and_dimensions() {
    for program in ["", "bad\0program"] {
        let mut request = run();
        request.program = program.into();
        assert!(validate_run(&request).is_err());
    }
    for path in ["", "relative/path"] {
        let mut request = run();
        request.cwd = path.into();
        assert!(validate_run(&request).is_err());
        let mut request = run();
        request.log_dir = Some(path.into());
        assert!(validate_run(&request).is_err());
    }
    for edge in [0, 501] {
        let mut request = run();
        request.rows = edge;
        assert!(validate_run(&request).is_err());
        let mut request = run();
        request.cols = edge;
        assert!(validate_run(&request).is_err());
    }
}

#[test]
fn wait_enforces_inclusive_timeout_and_window_bounds() {
    let mut request = wait();
    assert_eq!((request.timeout_ms, request.window_bytes), (300_000, 65536));
    for timeout in [1, 600_000] {
        for window in [256, 262144] {
            request.timeout_ms = timeout;
            request.window_bytes = window;
            assert!(validate_wait(&request, Mode::Pipe).is_ok());
        }
    }
    for timeout in [0, 600_001] {
        request.timeout_ms = timeout;
        assert!(validate_wait(&request, Mode::Pipe).is_err());
    }
    request.timeout_ms = 1;
    for window in [255, 262145] {
        request.window_bytes = window;
        assert!(validate_wait(&request, Mode::Pipe).is_err());
    }
}

#[test]
fn stream_names_are_bound_to_process_mode() {
    for (mode, valid, invalid) in [
        (
            Mode::Pipe,
            vec!["stdout", "stderr"],
            vec!["terminal", "bogus"],
        ),
        (Mode::Pty, vec!["terminal"], vec!["stdout", "stderr"]),
    ] {
        let mut request = wait();
        assert!(validate_wait(&request, mode).is_ok());
        request.streams = valid.into_iter().map(String::from).collect();
        assert!(validate_wait(&request, mode).is_ok());
        for stream in invalid {
            request.streams = vec![stream.into()];
            assert!(validate_wait(&request, mode).is_err());
        }
    }
}

#[test]
fn rules_enforce_counts_and_unique_byte_bounded_names() {
    for count in [0, 1, 16, 17] {
        let mut request = wait();
        request.until = (0..count)
            .map(|i| RuleInput {
                name: format!("rule{i}"),
                pattern: "READY".into(),
            })
            .collect();
        assert_eq!(
            validate_wait(&request, Mode::Pipe).is_ok(),
            (1..=16).contains(&count)
        );
    }
    for (name, valid) in [
        (String::new(), false),
        ("a".repeat(128), true),
        ("a".repeat(129), false),
        ("汉".repeat(43), false),
    ] {
        let mut request = wait();
        request.until[0].name = name;
        assert_eq!(validate_wait(&request, Mode::Pipe).is_ok(), valid);
    }
    let mut request = wait();
    request.until.push(request.until[0].clone());
    assert!(validate_wait(&request, Mode::Pipe).is_err());
}

#[test]
fn regex_limits_reject_empty_invalid_and_oversized_expressions() {
    for pattern in ["", "a*", "^$", "[", "(?=READY)", r"(a)\1"] {
        let mut request = wait();
        request.until[0].pattern = pattern.into();
        assert!(validate_wait(&request, Mode::Pipe).is_err(), "{pattern}");
    }
    for (pattern, valid) in [
        ("a".repeat(4096), true),
        ("a".repeat(4097), false),
        ("(a)".repeat(32), true),
        ("(a)".repeat(33), false),
    ] {
        let mut request = wait();
        request.until[0].pattern = pattern;
        assert_eq!(validate_wait(&request, Mode::Pipe).is_ok(), valid);
    }
}
