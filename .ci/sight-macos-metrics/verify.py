"""Verify a shared dependency repair on pristine native macOS source."""

import hashlib
import json
import platform
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path.cwd()
PAYLOAD = ROOT / ".ci/sight-macos-metrics"
UPSTREAM = ROOT / "official"
PROJECT = UPSTREAM / "src/agentsight"
OUT = ROOT / "build-evidence"
OUT.mkdir(exist_ok=True)
manifest = json.loads((PAYLOAD / "manifest.json").read_text(encoding="utf-8"))
target = UPSTREAM / manifest["sourceFile"]
summary = {"platform": platform.platform(), "commands": {}}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def run(name, args, cwd=PROJECT):
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=False)
    output = result.stdout + result.stderr
    (OUT / (name + ".log")).write_text(output, encoding="utf-8")
    summary["commands"][name] = result.returncode
    print(name, result.returncode, output[-2500:], flush=True)
    return result.returncode, output


assert platform.system() == "Darwin"
assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=UPSTREAM, text=True).strip() == manifest["base"]
assert digest(target.read_bytes()) == manifest["originalSHA256"]
assert subprocess.check_output(["git", "diff", "--name-only"], cwd=UPSTREAM, text=True) == ""
lock = PROJECT / "Cargo.lock"
lock_digest = digest(lock.read_bytes())
options = ["--locked", "-p", "agentsight"]
code, log = run("pristine-main-check", ["cargo", "check", *options, "--bin", "agentsight"])
assert code == 101 and "E0433" in log and "cpu_budget.rs" in log and "metrics" in log, log[-6000:]
fixed = (PAYLOAD / "Cargo.toml").read_bytes()
assert digest(fixed) == manifest["fixedSHA256"]
target.write_bytes(fixed)
commands = [
    ("fixed-check", ["cargo", "check", *options, "--bin", "agentsight"]),
    ("library", ["cargo", "test", *options, "--lib", "--", "--test-threads=2"]),
    ("clippy", ["cargo", "clippy", *options, "--all-targets", "--", "-D", "warnings"]),
    ("format", ["cargo", "fmt", "--all", "--", "--check"]),
]
outputs = {}
for name, args in commands:
    _, outputs[name] = run(name, args)
assert all(summary["commands"][name] == 0 for name, _ in commands), summary["commands"]
assert "utils::cpu_budget::tests::" in outputs["library"]
assert digest(lock.read_bytes()) == lock_digest
changed = subprocess.check_output(["git", "diff", "--name-only"], cwd=UPSTREAM, text=True).splitlines()
assert changed == [manifest["sourceFile"]], changed
subprocess.run(["git", "diff", "--check"], cwd=UPSTREAM, check=True)
subprocess.run(["git", "add", "--", manifest["sourceFile"]], cwd=UPSTREAM, check=True)
tree = subprocess.check_output(["git", "write-tree"], cwd=UPSTREAM, text=True).strip()
assert tree == manifest["sourceTree"], tree
summary.update({"status": "passed", "base": manifest["base"], "sourceTree": tree, "fixedSHA256": manifest["fixedSHA256"], "lockUnchanged": True, "testGroups": re.findall(r"test result: ok\. (\d+) passed; (\d+) failed; (\d+) ignored", outputs["library"]), "scope": "Pristine main native macOS binary check, manifest-only repair, complete library including CPU budget tests, all-target clippy; no local-label patch"})
(OUT / "summary.json").write_text(json.dumps(summary, indent=2)+"\n", encoding="utf-8")
(OUT / "manifest.json").write_text(json.dumps(manifest, indent=2)+"\n", encoding="utf-8")
shutil.copyfile(target, OUT / "Cargo.toml")
(OUT / "candidate.patch").write_bytes(subprocess.check_output(["git", "diff", "--cached", "--binary"], cwd=UPSTREAM))
print(json.dumps(summary, indent=2))
