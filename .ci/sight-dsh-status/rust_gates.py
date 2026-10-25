import hashlib
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path.cwd()
DRIVER = ROOT / ".ci/sight-dsh-status"
SOURCE = ROOT / "official"
PROJECT = SOURCE / "src/agentsight"
EVIDENCE = ROOT / "evidence"
EVIDENCE.mkdir(exist_ok=True)
manifest = json.loads((DRIVER / "manifest.json").read_text())
assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=SOURCE, text=True).strip() == manifest["base"]
for row in manifest["files"]:
    path = SOURCE / row["path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(DRIVER / "files" / row["path"], path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == row["candidate_sha256"]
subprocess.run(["git", "add", "--", *[row["path"] for row in manifest["files"]]], cwd=SOURCE, check=True)
subprocess.run(["git", "diff", "--cached", "--check"], cwd=SOURCE, check=True)
actual_tree = subprocess.check_output(["git", "write-tree"], cwd=SOURCE, text=True).strip()
assert actual_tree == manifest["source_tree"], (actual_tree, manifest["source_tree"])
commands = [
    ("format", ["cargo", "fmt", "--all", "--", "--check"]),
    ("architecture", ["python3", "scripts/check-arch-boundaries.py"]),
    ("sqlite-self-test", ["python3", "scripts/check-sqlite-entrypoints.py", "--self-test"]),
    ("sqlite-entrypoints", ["python3", "scripts/check-sqlite-entrypoints.py"]),
    ("clippy", ["cargo", "clippy", "--workspace", "--all-targets", "--locked", "--", "-D", "warnings"]),
    ("tests", ["cargo", "test", "--workspace", "--locked", "--", "--test-threads=2"]),
]
results = {}
for name, command in commands:
    with (EVIDENCE / (name + ".log")).open("w") as log:
        p = subprocess.run(command, cwd=PROJECT, stdout=log, stderr=subprocess.STDOUT)
    results[name] = p.returncode
    print(name, p.returncode, (EVIDENCE / (name + ".log")).read_text(errors="replace")[-1800:], flush=True)
(EVIDENCE / "summary.json").write_text(json.dumps({
    "base": manifest["base"], "source_tree": actual_tree, "files": manifest["files"], "commands": results,
    "rustc": subprocess.check_output(["rustc", "--version"], text=True).strip(),
    "scope": "unchanged Rust workspace gates; no live eBPF load claimed",
}, indent=2))
assert all(code == 0 for code in results.values()), results

