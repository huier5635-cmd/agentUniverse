import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

root = Path.cwd()
component = root / "source/src/agent-memory"
evidence = root / "evidence"
evidence.mkdir(exist_ok=True)
source = component / "src/consolidation/heuristics.rs"
test = component / "tests/session_summary_tools_test.rs"
payload = root / "payload"
summary = {"source_base": "c894728f1c092c0e0552f02bc52143d2593dd4b9", "results": {}}

def run(name, args, expected=0):
    result = subprocess.run(args, cwd=component, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, check=False)
    (evidence / f"{name}.log").write_text(result.stdout, encoding="utf-8")
    summary["results"][name] = {"exit_code": result.returncode}
    print(f"{name}: exit {result.returncode}", flush=True)
    print(result.stdout[-2500:], flush=True)
    if expected is not None and result.returncode != expected:
        raise AssertionError(f"{name}: expected exit {expected}, got {result.returncode}")
    return result

def failures(output):
    return sorted(set(re.findall(r"^    ([A-Za-z_][A-Za-z_0-9:]+)$", output, re.MULTILINE)))

try:
    run("environment", ["rustc", "--version"])
    run("cargo-version", ["cargo", "--version"])
    (evidence / "original-heuristics.rs").write_bytes(source.read_bytes())
    baseline_all = run("baseline-all-tests", ["cargo", "test", "--locked",
                                             "--no-fail-fast"], expected=101)
    expected_host_failure = ["info_falls_back_with_strategy_auto_if_userns_unavailable"]
    assert failures(baseline_all.stdout) == expected_host_failure, failures(baseline_all.stdout)
    assert "/proc/self/setgroups: Permission denied" in baseline_all.stdout
    shutil.copyfile(payload / "session_summary_tools_test.rs", test)
    baseline = run("baseline", ["cargo", "test", "--locked", "--test",
                               "session_summary_tools_test"], expected=101)
    assert re.search(r"1 passed; 2 failed; 0 ignored", baseline.stdout), baseline.stdout[-4000:]
    assert "left == right" in baseline.stdout
    shutil.copyfile(payload / "heuristics.rs", source)
    run("format-check", ["cargo", "fmt", "--all", "--", "--check"])
    fixed = run("fixed", ["cargo", "test", "--locked", "--test",
                         "session_summary_tools_test"])
    assert re.search(r"3 passed; 0 failed; 0 ignored", fixed.stdout), fixed.stdout[-4000:]
    run("clippy", ["cargo", "clippy", "--all-targets", "--locked", "--", "-D", "warnings"])
    all_tests = run("all-tests", ["cargo", "test", "--locked", "--no-fail-fast"], expected=101)
    assert failures(all_tests.stdout) == expected_host_failure, failures(all_tests.stdout)
    assert "/proc/self/setgroups: Permission denied" in all_tests.stdout
    for name in ["generated_tools_round_trip_without_statistics",
                 "generated_single_tool_round_trips_without_byte_suffix",
                 "historical_summaries_keep_the_existing_tools_contract"]:
        assert f"test {name} ... ok" in all_tests.stdout
    summary["baseline_host_failure"] = expected_host_failure
    summary["full_suite_clean"] = False
    run("installer-tests", ["bash", "tests/test-openclaw-adapter-install.sh"])
    run("smoke-failure-detection", ["python3", "tests/test_smoke.py", "-v"])
    run("build-debug", ["cargo", "build", "--locked"])
    run("mcp-smoke", ["python3", "tests/smoke.py", "target/debug/agent-memory",
                      "--timeout", "60"])
    run("diff-check", ["git", "diff", "--check"])
    formatted = evidence / "formatted"
    formatted.mkdir(exist_ok=True)
    shutil.copyfile(source, formatted / "heuristics.rs")
    shutil.copyfile(test, formatted / "session_summary_tools_test.rs")
    summary["candidate_hashes"] = {
        "src/consolidation/heuristics.rs": hashlib.sha256(source.read_bytes()).hexdigest(),
        "tests/session_summary_tools_test.rs": hashlib.sha256(test.read_bytes()).hexdigest(),
    }
    summary["status"] = "focused-gates-passed;full-suite-has-one-identical-baseline-host-failure"
finally:
    if "status" not in summary:
        summary["status"] = "failed"
    (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
