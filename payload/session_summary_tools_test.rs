//! Round-trip the generated session tool list through stored Markdown.

use agent_memory::config::{AppConfig, ConsolidationConfig};
use agent_memory::consolidation::fact::FactCategory;
use agent_memory::consolidation::heuristics::{OwnedAuditEntry, run_consolidation_owned};
use agent_memory::mount::MountStrategyKind;
use agent_memory::service::MemoryService;
use agent_memory::tools::session_history::memory_sessions;
use serde_json::Value;
use tempfile::{TempDir, tempdir};

fn service() -> (TempDir, TempDir, MemoryService) {
    let store = tempdir().unwrap();
    let sessions = tempdir().unwrap();
    let mut config = AppConfig::default();
    config.global.user_id = "session-tools-test".into();
    config.memory.paths.base_dir = store.path().to_string_lossy().into_owned();
    config.memory.session.base_dir = sessions.path().to_string_lossy().into_owned();
    config.memory.mount.strategy = MountStrategyKind::Userland;
    let service = MemoryService::new(config).unwrap();
    (store, sessions, service)
}

fn generated_tools(tools: &[(&str, bool)]) -> Value {
    let (_store, _sessions, service) = service();
    let entries: Vec<_> = tools
        .iter()
        .map(|(tool, ok)| OwnedAuditEntry {
            ts: "2026-10-23T10:00:00Z".into(),
            tool: (*tool).into(),
            path: "notes/example.md".into(),
            ok: *ok,
            bytes: Some(4096),
            error: (!ok).then(|| "test failure".into()),
            trace_id: None,
        })
        .collect();
    let config = ConsolidationConfig {
        min_tool_calls: 1,
        ..ConsolidationConfig::default()
    };
    let facts = run_consolidation_owned(&entries, "round-trip", &config);
    let summary = facts
        .iter()
        .find(|fact| fact.category == FactCategory::Summary)
        .expect("the real generator must emit a session summary");
    let directory = service.mount.root.join("facts/summary");
    std::fs::create_dir_all(&directory).unwrap();
    std::fs::write(directory.join("round-trip.md"), summary.to_markdown()).unwrap();

    let result: Value = serde_json::from_str(&memory_sessions(&service, 10).unwrap()).unwrap();
    assert_eq!(result.as_array().unwrap().len(), 1);
    result[0]["tools_used"].clone()
}

#[test]
fn generated_tools_round_trip_without_statistics() {
    assert_eq!(
        generated_tools(&[
            ("mem_write", true),
            ("bash", false),
            ("mem_read", true),
            ("mem_read", true),
        ]),
        serde_json::json!(["bash", "mem_read", "mem_write"])
    );
}

#[test]
fn generated_single_tool_round_trips_without_byte_suffix() {
    assert_eq!(
        generated_tools(&[("tool_123", true), ("tool_123", false)]),
        serde_json::json!(["tool_123"])
    );
}

#[test]
fn historical_summaries_keep_the_existing_tools_contract() {
    let (_store, _sessions, service) = service();
    let directory = service.mount.root.join("facts/summary");
    std::fs::create_dir_all(&directory).unwrap();
    for (id, body) in [
        ("english", "tools: `mem_read`, `bash`"),
        (
            "unstructured",
            "Session 共 2 次工具调用，使用工具类型: `mem_read`，总数据量 4096 bytes。",
        ),
        ("missing", "Session without a measured tool list."),
    ] {
        std::fs::write(
            directory.join(format!("{id}.md")),
            format!("---\nsession_id: {id}\n---\n\n{body}\n"),
        )
        .unwrap();
    }
    let result: Value = serde_json::from_str(&memory_sessions(&service, 10).unwrap()).unwrap();
    for summary in result.as_array().unwrap() {
        let expected = if summary["session_id"] == "english" {
            serde_json::json!(["mem_read", "bash"])
        } else {
            serde_json::json!([])
        };
        assert_eq!(summary["tools_used"], expected);
    }
}
