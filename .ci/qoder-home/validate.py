from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

repo = Path(sys.argv[1]).resolve()
evidence = Path(sys.argv[2]).resolve()
evidence.mkdir(parents=True, exist_ok=True)
payload = Path(__file__).resolve().parent
manifest = json.loads((payload / "manifest.json").read_text())
hook_path = "src/tokenless/adapters/tokenless/qoder/hooks/run-hook.sh"
test_path = "src/tokenless/tests/test-qoder-adapter-install.sh"
results = []


def run(args, name, *, expected=0, timeout=180, cwd=repo):
    log = evidence / (name + ".txt")
    with log.open("w") as stream:
        proc = subprocess.run(args, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout)
    output = log.read_text()
    if expected is not None:
        assert proc.returncode == expected, (name, proc.returncode, output[-5000:])
    return proc.returncode, output


def raw_dispatch_probe(mode):
    # This second probe executes a byte-identical wrapper, with no FHS rewrites.
    for root in ("/usr/local/share/anolisa", "/usr/share/anolisa"):
        assert not (Path(root) / "adapters/tokenless/common/hooks/tool_ready_hook.sh").exists()
    observations = []
    with tempfile.TemporaryDirectory(prefix="qoder-raw-dispatch-") as temp:
        root = Path(temp).resolve()
        wrapper = root / "cache/hooks/run-hook.sh"
        wrapper.parent.mkdir(parents=True)
        wrapper.write_bytes((repo / hook_path).read_bytes())
        assert wrapper.read_bytes() == (repo / hook_path).read_bytes()
        cwd = root / "work"
        cwd.mkdir()
        marker = root / "probe-executed"
        for home in (None, "", "relative-home", "relative home", ".", "../parent-home", str(root / "absolute home")):
            marker.unlink(missing_ok=True)
            if home:
                base = Path(home)
                if not base.is_absolute():
                    base = cwd / base
                assert base.resolve().is_relative_to(root)
                target = base / ".local/share/anolisa/adapters/tokenless/common/hooks/tool_ready_hook.sh"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('printf executed > "$QODER_RAW_MARKER"\nprintf "probe:%s:%s\\n" "$#" "${1:-}"\n')
            env = dict(os.environ, QODER_RAW_MARKER=str(marker))
            env.pop("HOME", None)
            if home is not None:
                env["HOME"] = home
            proc = subprocess.run(["bash", str(wrapper), "tool_ready_hook.sh", "argument with spaces"], cwd=cwd, env=env, capture_output=True, text=True, timeout=15)
            should_execute = bool(home) and (mode == "baseline" or Path(home).is_absolute())
            assert proc.returncode == 0, proc.stderr
            assert marker.exists() == should_execute, (mode, home, marker.exists())
            assert proc.stdout == ("probe:1:argument with spaces\n" if should_execute else "{}\n"), (mode, home, proc.stdout)
            observations.append({"home": home, "exit": proc.returncode, "probe_executed": marker.exists(), "stdout": proc.stdout})
    (evidence / (mode + "-raw-dispatch.json")).write_text(json.dumps(observations, indent=2))


assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip() == manifest["base"]
for item in manifest["files"]:
    assert hashlib.sha256((repo / item["path"]).read_bytes()).hexdigest() == item["original_sha256"], item["path"]
original_hook = (repo / hook_path).read_bytes()
run(["git", "apply", "--check", str(payload / "candidate.patch")], "patch-check")
run(["git", "apply", str(payload / "candidate.patch")], "patch-apply")
fixed_hook = (repo / hook_path).read_bytes()
try:
    (repo / hook_path).write_bytes(original_hook)
    raw_dispatch_probe("baseline")
    code, output = run(["bash", test_path], "baseline-qoder-suite", expected=None)
    assert code != 0 and "FAILED (failures=4)" in output, output[-5000:]
    assert "home='relative-home'" in output and "home='../parent-home'" in output
    results.append({"phase": "original wrapper with regression suite", "exit": code, "relative_home_failures": 4})
finally:
    (repo / hook_path).write_bytes(fixed_hook)

raw_dispatch_probe("fixed")
_, output = run(["bash", test_path], "fixed-qoder-suite")
assert "Ran 5 tests" in output and "0 failed" in output
results.append({"phase": "fixed Qoder lifecycle and dispatch suite", "exit": 0, "embedded_methods": 5, "dispatch_scenarios": 9})
run(["bash", "-n", hook_path, test_path], "bash-syntax")
run(["shellcheck", hook_path, test_path], "shellcheck")
run(["git", "diff", "--check"], "whitespace")
paths = [item["path"] for item in manifest["files"]]
run(["git", "add", "--", *paths], "stage-candidate")
tree = subprocess.check_output(["git", "write-tree"], cwd=repo, text=True).strip()
assert tree == manifest["source_tree"], (tree, manifest["source_tree"])
run(["make", "-C", "src/tokenless", "test-adapters"], "full-adapter-suite", timeout=1500)
results.append({"phase": "make -C src/tokenless test-adapters", "exit": 0})
run(["bash", "scripts/docs-lint.sh"], "docs-lint")
run([sys.executable, "scripts/docs-link-check.py"], "docs-links", timeout=300)
for item in manifest["files"]:
    assert hashlib.sha256((repo / item["path"]).read_bytes()).hexdigest() == item["fixed_sha256"], item["path"]
run(["git", "status", "--short"], "post-build-worktree")
summary = {"base": manifest["base"], "source_tree": tree, "python": sys.version, "results": results, "files": manifest["files"]}
(evidence / "summary.json").write_text(json.dumps(summary, indent=2))
print(json.dumps(summary))
