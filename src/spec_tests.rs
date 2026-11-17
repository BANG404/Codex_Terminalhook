use super::*;

fn output() -> OutputSpec {
    OutputSpec {
        until: vec![RuleInput {
            name: "ready".into(),
            pattern: "READY".into(),
        }],
        streams: Vec::new(),
        match_mode: MatchMode::Line,
        window_bytes: 65536,
    }
}
fn command() -> CommandSpec {
    let path = std::env::current_dir()
        .unwrap()
        .to_string_lossy()
        .into_owned();
    CommandSpec {
        program: "python".into(),
        args: vec![],
        cwd: path.clone(),
        env: BTreeMap::new(),
        output: output(),
        log_dir: path,
    }
}
#[test]
fn command_requires_a_program_and_absolute_paths() {
    assert!(validate_run(&command()).is_ok());
    for program in ["", "bad\0program"] {
        let mut input = command();
        input.program = program.into();
        assert!(validate_run(&input).is_err());
    }
    for path in ["", "relative/path"] {
        let mut input = command();
        input.cwd = path.into();
        assert!(validate_run(&input).is_err());
        let mut input = command();
        input.log_dir = path.into();
        assert!(validate_run(&input).is_err());
    }
}
#[test]
fn deadline_accepts_unbounded_or_inclusive_finite_limits() {
    for timeout in [None, Some(1), Some(31_536_000_000)] {
        assert!(validate_deadline(timeout).is_ok());
    }
    for timeout in [Some(0), Some(31_536_000_001), Some(u64::MAX)] {
        assert!(validate_deadline(timeout).is_err());
    }
}
#[test]
fn output_accepts_empty_rules_for_natural_exit_and_bounded_windows() {
    let mut input = output();
    assert_eq!(validate_wait(&input).unwrap().len(), 1);
    for window in [256, 262144] {
        input.window_bytes = window;
        assert!(validate_wait(&input).is_ok());
    }
    for window in [255, 262145] {
        input.window_bytes = window;
        assert!(validate_wait(&input).is_err());
    }
    input.window_bytes = 65536;
    input.until.clear();
    assert!(validate_wait(&input).unwrap().is_empty());
    input.streams = vec!["stdout".into(), "stderr".into()];
    assert!(validate_wait(&input).is_ok());
    for stream in ["terminal", "bogus"] {
        input.streams = vec![stream.into()];
        assert!(validate_wait(&input).is_err());
    }
}
#[test]
fn rule_names_and_counts_are_byte_bounded_and_unique() {
    for count in [0, 1, 16, 17] {
        let mut input = output();
        input.until = (0..count)
            .map(|i| RuleInput {
                name: format!("rule{i}"),
                pattern: "READY".into(),
            })
            .collect();
        assert_eq!(validate_wait(&input).is_ok(), count <= 16);
    }
    for (name, valid) in [
        (String::new(), false),
        ("a".repeat(128), true),
        ("a".repeat(129), false),
        ("汉".repeat(43), false),
    ] {
        let mut input = output();
        input.until[0].name = name;
        assert_eq!(validate_wait(&input).is_ok(), valid);
    }
    let mut input = output();
    input.until.push(input.until[0].clone());
    assert!(validate_wait(&input).is_err());
}
#[test]
fn regex_limits_reject_empty_invalid_and_oversized_expressions() {
    for pattern in ["", "a*", "^$", "[", "(?=READY)", r"(a)\1"] {
        let mut input = output();
        input.until[0].pattern = pattern.into();
        assert!(validate_wait(&input).is_err(), "{pattern}");
    }
    for (pattern, valid) in [
        ("a".repeat(4096), true),
        ("a".repeat(4097), false),
        ("(a)".repeat(32), true),
        ("(a)".repeat(33), false),
    ] {
        let mut input = output();
        input.until[0].pattern = pattern;
        assert_eq!(validate_wait(&input).is_ok(), valid);
    }
}
