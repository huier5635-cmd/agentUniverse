from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

repo = Path(sys.argv[1]).resolve()
payload = Path(__file__).resolve().parent
evidence = Path(sys.argv[2]).resolve()
evidence.mkdir(parents=True, exist_ok=True)
manifest = json.loads((payload / "manifest.json").read_text())


def run(args, name, expected=0, cwd=repo):
    proc = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    output = proc.stdout + proc.stderr
    (evidence / (name + ".txt")).write_text(output)
    if expected is not None:
        assert proc.returncode == expected, f"{name}: exit {proc.returncode}\n{output[-5000:]}"
    return proc.returncode, output


assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip() == manifest["base"]
for item in manifest["files"]:
    assert hashlib.sha256((repo / item["path"]).read_bytes()).hexdigest() == item["original_sha256"], item["path"]
run(["git", "apply", "--check", str(payload / "candidate.patch")], "patch-check")
run(["git", "apply", str(payload / "candidate.patch")], "apply")
fixed_sources = {}
for item in manifest["files"]:
    assert hashlib.sha256((repo / item["path"]).read_bytes()).hexdigest() == item["fixed_sha256"], item["path"]
    if item["path"].endswith(("/_v1.py", "/_v2.py")):
        fixed_sources[item["path"]] = (repo / item["path"]).read_bytes()
        (repo / item["path"]).write_bytes(subprocess.check_output(["git", "show", manifest["base"] + ":" + item["path"]], cwd=repo))

results = []
component = repo / "src/tokenless"
for file, cls, label in (
    ("test_agentscope_v1.py", "AgentScopeV1Test", "v1"),
    ("test_agentscope_middleware.py", "MiddlewareTest", "v2"),
):
    code, output = run([sys.executable, "tests/" + file, cls + ".test_core_post_tool_failure_retains_complete_result", cls + ".test_unrelated_post_tool_errors_remain_visible"], "baseline-" + label, expected=None, cwd=component)
    assert code != 0 and "stash backend unavailable" in output, label
    results.append({"phase": "baseline", "adapter": label, "exit": code, "expected_core_failure": True})
for path, contents in fixed_sources.items():
    (repo / path).write_bytes(contents)
for file, label in (("test_agentscope_v1.py", "v1"), ("test_agentscope_middleware.py", "v2")):
    _, output = run([sys.executable, "tests/" + file], "fixed-" + label, cwd=component)
    assert "Ran 13 tests" in output and "OK" in output, label
    results.append({"phase": "fixed", "adapter": label, "tests": 13, "exit": 0})
paths = [item["path"] for item in manifest["files"]]
run([sys.executable, "-m", "black", "--check", "--line-length", "100", *paths], "format")
run(["git", "diff", "--check"], "whitespace")
run(["git", "add", "--", *paths], "stage")
tree = subprocess.check_output(["git", "write-tree"], cwd=repo, text=True).strip()
summary = {"base": manifest["base"], "fixed_tree": tree, "files": manifest["files"], "results": results}
(evidence / "summary.json").write_text(json.dumps(summary, indent=2))
print(json.dumps(summary))
