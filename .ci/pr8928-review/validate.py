from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

repo = Path(sys.argv[1]).resolve()
evidence = Path(sys.argv[2]).resolve()
payload = Path(__file__).resolve().parent
evidence.mkdir(parents=True, exist_ok=True)
manifest = json.loads((payload / "manifest.json").read_text())
component = repo / "src/tokenless"
results = []


def run(args, name, *, expected=0, cwd=repo):
    proc = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=120)
    output = proc.stdout + proc.stderr
    (evidence / (name + ".txt")).write_text(output)
    assert proc.returncode == expected if expected is not None else True, (name, proc.returncode, output[-4000:])
    return proc.returncode, output


assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip() == manifest["base"]
for item in manifest["files"]:
    assert hashlib.sha256((repo / item["path"]).read_bytes()).hexdigest() == item["original_sha256"], item["path"]
run(["git", "apply", "--check", str(payload / "candidate.patch")], "patch-check")
run(["git", "apply", str(payload / "candidate.patch")], "patch-apply")
sources = {
    item["path"]: (repo / item["path"]).read_text()
    for item in manifest["files"] if item["path"].endswith(("/_v1.py", "/_v2.py"))
}
suites = [
    ("test_agentscope_v1.py", "AgentScopeV1Test", "v1", 16),
    ("test_agentscope_middleware.py", "MiddlewareTest", "v2", 15),
]
methods = [
    "test_sdk_post_tool_failure_retains_complete_result",
    "test_older_request_constructor_retains_original_result",
    "test_invalid_core_wire_response_retains_original_result",
    "test_post_tool_cancellation_propagates",
]

try:
    for name, source in sources.items():
        old = "            except Exception:\n                # SDK/version"
        assert source.count(old) == 1
        (repo / name).write_text(source.replace(old, "            except TokenlessError:\n                # SDK/version", 1))
    for file, cls, label, count in suites:
        selected = [cls + "." + method for method in methods]
        if label == "v1":
            selected.append(cls + ".test_uninstalled_integration_error_remains_visible")
        code, output = run([sys.executable, "tests/" + file, *selected], "narrow-handler-" + label, expected=None, cwd=component)
        assert code != 0 and "AttributeError" not in output
        for error in ("ValueError", "TypeError", "KeyError", "JSONDecodeError"):
            assert error in output, (label, error)
        results.append({"phase": "TokenlessError-only mutation", "adapter": label, "exit": code, "errors": int(re.search(r"FAILED \(errors=(\d+)\)", output).group(1))})
finally:
    for name, source in sources.items():
        (repo / name).write_text(source)

try:
    for name, source in sources.items():
        (repo / name).write_text(source.replace("            except Exception:\n                # SDK/version", "            except BaseException:\n                # SDK/version", 1))
    for file, cls, label, count in suites:
        code, output = run([sys.executable, "tests/" + file, cls + ".test_post_tool_cancellation_propagates"], "swallowed-cancellation-" + label, expected=None, cwd=component)
        assert code != 0 and "CancelledError not raised" in output
        results.append({"phase": "BaseException mutation", "adapter": label, "exit": code, "failures": 1})
finally:
    for name, source in sources.items():
        (repo / name).write_text(source)

for file, cls, label, count in suites:
    _, output = run([sys.executable, "tests/" + file], "fixed-" + label, cwd=component)
    assert f"Ran {count} tests" in output and "OK" in output
    results.append({"phase": "fixed full suite", "adapter": label, "tests": count, "exit": 0})
paths = [item["path"] for item in manifest["files"]]
run([sys.executable, "-m", "black", "--check", "--line-length", "100", *[p for p in paths if p.endswith(".py")]], "format")
run(["git", "diff", "--check"], "whitespace")
for item in manifest["files"]:
    assert hashlib.sha256((repo / item["path"]).read_bytes()).hexdigest() == item["fixed_sha256"], item["path"]
run(["git", "add", "--", *paths], "stage")
tree = subprocess.check_output(["git", "write-tree"], cwd=repo, text=True).strip()
assert tree == manifest["source_tree"], (tree, manifest["source_tree"])
summary = {"base": manifest["base"], "fixed_tree": tree, "python": sys.version, "files": manifest["files"], "results": results}
(evidence / "summary.json").write_text(json.dumps(summary, indent=2))
print(json.dumps(summary))
