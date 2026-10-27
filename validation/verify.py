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

try:
    run("environment", ["rustc", "--version"])
    run("cargo-version", ["cargo", "--version"])
    original = source.read_bytes()
    (evidence / "original-heuristics.rs").write_bytes(original)
    shutil.copy2(payload / "session_summary_tools_test.rs", test)
    baseline = run("baseline", ["cargo", "test", "--locked", "--test",
                               "session_summary_tools_test"], expected=101)
    assert re.search(r"1 passed; 2 failed; 0 ignored", baseline.stdout), baseline.stdout[-4000:]
    assert "left == right" in baseline.stdout
    shutil.copy2(payload / "heuristics.rs", source)
    run("format-changed", ["rustfmt", "--edition", "2024", str(source), str(test)])
    formatted = evidence / "formatted"
    formatted.mkdir(exist_ok=True)
    shutil.copy2(source, formatted / "heuristics.rs")
    shutil.copy2(test, formatted / "session_summary_tools_test.rs")
    run("format-check", ["cargo", "fmt", "--all", "--", "--check"])
    fixed = run("fixed", ["cargo", "test", "--locked", "--test",
                         "session_summary_tools_test"])
    assert re.search(r"3 passed; 0 failed; 0 ignored", fixed.stdout), fixed.stdout[-4000:]
    run("clippy", ["cargo", "clippy", "--all-targets", "--locked", "--", "-D", "warnings"])
    run("all-tests", ["cargo", "test", "--locked"])
    run("installer-tests", ["bash", "tests/test-openclaw-adapter-install.sh"])
    run("smoke-failure-detection", ["python3", "tests/test_smoke.py", "-v"])
    run("build-debug", ["cargo", "build", "--locked"])
    run("mcp-smoke", ["python3", "tests/smoke.py", "target/debug/agent-memory",
                      "--timeout", "60"])
    run("diff-check", ["git", "diff", "--check"])
    summary["candidate_hashes"] = {
        "src/consolidation/heuristics.rs": hashlib.sha256(source.read_bytes()).hexdigest(),
        "tests/session_summary_tools_test.rs": hashlib.sha256(test.read_bytes()).hexdigest(),
    }
    summary["status"] = "passed"
finally:
    if "status" not in summary:
        summary["status"] = "failed"
    (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
