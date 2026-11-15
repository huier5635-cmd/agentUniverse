
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn local_user_scans_full_bash_rules_in_near_limit_frame() {
    const MAX_FRAME: usize = 4 * 1024 * 1024;
    let noise = concat!(
        "# setup step\nset -euo pipefail\n",
        "mkdir -p \"$HOME/work\" || true\n",
        "echo \"preparing\" >> \"$HOME/log.txt\"\n",
        "for f in *.txt; do\n",
        "    [ -f \"$f\" ] && cp \"$f\" \"$HOME/work/\" || echo \"skip $f\"\n",
        "done\n",
    );
    let encoded_block = serde_json::to_string(noise).unwrap().len() - 2;
    let mut code = noise.repeat((MAX_FRAME - 2048) / encoded_block);
    code.push_str("systemctl enable fixture.service\n");
    let request = json!({
        "method": "action.code_scan",
        "params": {"code": code, "language": "bash"},
    });
    let wire_bytes = serde_json::to_vec(&request).unwrap().len() + 1;
    assert!(wire_bytes <= MAX_FRAME && wire_bytes >= MAX_FRAME - 4096);
    let daemon = RunningDaemon::start(PrincipalRole::LocalUser).await;
    let started = std::time::Instant::now();
    let response = tokio::time::timeout(
        Duration::from_secs(90),
        support::request_json(&daemon.socket_path, &request),
    )
    .await
    .expect("daemon should return a result");
    eprintln!("near-frame daemon scan: {wire_bytes} wire bytes, {:?}", started.elapsed());
    assert_eq!(response["result"]["ok"], json!(true), "{response}");
    assert!(
        response["result"]["findings"].as_array().is_some_and(|findings| findings.iter().any(
            |finding| finding["rule_id"] == "shell-persistence"
                && finding["evidence"] == json!(["systemctl enable"])
        )),
        "expected final line in the daemon's evidence: {response}",
    );
    daemon.stop().await;
}
