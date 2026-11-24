#[test]
#[cfg(target_os = "linux")]
fn stats_clear_rejects_piped_confirmation_with_redirected_output() {
    use std::fs::File;
    use std::process::Stdio;

    let fixture = TempStatsDb::new().expect("seed a private statistics database");
    let redirected = fixture.dir.join("prompt.txt");
    let sink = File::create(&redirected).unwrap();
    let output = Command::new("sh")
        .args(["-c", r#"printf 'y\n' | "$1" stats clear"#, "sh"])
        .arg(fixture.command().get_program())
        .env("TOKENLESS_STATS_DB", &fixture.path)
        .env("TOKENLESS_SLS_ENABLED", "0")
        .stdout(Stdio::from(sink))
        .output()
        .unwrap();

    let diagnostic = String::from_utf8_lossy(&output.stderr);
    let recorder = StatsRecorder::new(&fixture.path).unwrap();
    assert!(
        recorder.record_by_id(fixture.record_id).unwrap().is_some(),
        "piped yes must preserve the seeded record (status: {}, stderr: {diagnostic})",
        output.status
    );
    assert_eq!(output.status.code(), Some(1), "{diagnostic}");
    assert!(diagnostic.contains("use --yes for automation"), "{diagnostic}");
    assert_eq!(
        std::fs::read_to_string(&redirected).unwrap(),
        "Are you sure you want to clear all statistics? [y/N] "
    );

    let forced = fixture
        .command()
        .env("TOKENLESS_SLS_ENABLED", "0")
        .args(["stats", "clear", "--yes"])
        .stdin(Stdio::null())
        .output()
        .unwrap();
    assert!(forced.status.success(), "{forced:?}");
    assert_eq!(recorder.count().unwrap(), 0);
}
