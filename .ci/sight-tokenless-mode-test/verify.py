"""Verify the test-only mode compatibility repair on the pinned Rust workspace."""

import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path.cwd()
DRIVER = ROOT / ".ci/sight-tokenless-mode-test"
SOURCE = ROOT / "official"
PROJECT = SOURCE / "src/agentsight"
EVIDENCE = ROOT / "evidence"
EVIDENCE.mkdir(exist_ok=True)
manifest = json.loads((DRIVER / "manifest.json").read_text(encoding="utf-8"))
target = SOURCE / manifest["sourceFile"]
results = {}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def run(name, command):
    with (EVIDENCE / f"{name}.log").open("w", encoding="utf-8") as log:
        result = subprocess.run(
            command, cwd=PROJECT, stdout=log, stderr=subprocess.STDOUT, check=False
        )
    results[name] = result.returncode
    print(
        name,
        result.returncode,
        (EVIDENCE / f"{name}.log").read_text(errors="replace")[-1800:],
        flush=True,
    )
    return result.returncode


assert (
    subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=SOURCE, text=True).strip()
    == manifest["base"]
)
original = target.read_bytes()
assert digest(original) == manifest["originalSHA256"]
subprocess.run(["git", "apply", "--check", str(DRIVER / "candidate.patch")], cwd=SOURCE, check=True)
subprocess.run(["git", "apply", str(DRIVER / "candidate.patch")], cwd=SOURCE, check=True)
candidate = target.read_bytes()
assert digest(candidate) == manifest["fixedSHA256"]
assert digest(candidate.split(b"#[cfg(test)]")[0]) == manifest["productionSHA256"]

commands = [
    ("format", ["cargo", "fmt", "--all", "--", "--check"]),
    ("architecture", ["python3", "scripts/check-arch-boundaries.py"]),
    ("sqlite-self-test", ["python3", "scripts/check-sqlite-entrypoints.py", "--self-test"]),
    ("sqlite-entrypoints", ["python3", "scripts/check-sqlite-entrypoints.py"]),
    (
        "clippy",
        ["cargo", "clippy", "--workspace", "--all-targets", "--locked", "--", "-D", "warnings"],
    ),
    ("tests", ["cargo", "test", "--workspace", "--locked", "--", "--test-threads=2"]),
]
for name, command in commands:
    run(name, command)

# The expanded test must also detect a regression to the pre-compatibility rule.
old_filter = b"json_extract(record_json,'$.mode')='active'"
current_filter = (
    b"COALESCE(LOWER(json_extract(record_json,'$.mode')),'active') NOT IN ('dry-run','dryrun')"
)
assert candidate.count(current_filter) == 1
try:
    target.write_bytes(candidate.replace(current_filter, old_filter))
    run(
        "old-filter-mutation",
        [
            "cargo",
            "test",
            "--locked",
            "-p",
            "agentsight",
            "--lib",
            "storage::sqlite::genai::tokenless::tests::applied_modes_sources_filters_and_pagination",
            "--",
            "--exact",
            "--test-threads=2",
        ],
    )
finally:
    target.write_bytes(candidate)
assert digest(target.read_bytes()) == manifest["fixedSHA256"]
subprocess.run(["git", "diff", "--check"], cwd=SOURCE, check=True)
subprocess.run(["git", "add", "--", manifest["sourceFile"]], cwd=SOURCE, check=True)
tree = subprocess.check_output(["git", "write-tree"], cwd=SOURCE, text=True).strip()
assert tree == manifest["sourceTree"], tree
test_log = (EVIDENCE / "tests.log").read_text(errors="replace")
counts = [
    {"passed": int(p), "failed": int(f), "ignored": int(i)}
    for p, f, i in re.findall(
        r"test result: ok\. (\d+) passed; (\d+) failed; (\d+) ignored", test_log
    )
]
summary = {
    "base": manifest["base"],
    "sourceTree": tree,
    "commands": results,
    "rustc": subprocess.check_output(["rustc", "--version"], text=True).strip(),
    "testGroups": counts,
    "productionUnchanged": True,
    "fixtureModes": 9,
    "pristineBaselineRun": manifest["pristineBaselineRun"],
    "scope": "Rust workspace checks and real SQLite-backed tests; no live eBPF load or model call",
}
(EVIDENCE / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
(EVIDENCE / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
assert all(results[name] == 0 for name, _ in commands), results
mutation = (EVIDENCE / "old-filter-mutation.log").read_text(errors="replace")
assert results["old-filter-mutation"] == 101 and "1 failed" in mutation, mutation[-2000:]
assert counts and all(group["failed"] == 0 for group in counts), counts
assert "applied_modes_sources_filters_and_pagination ... ok" in test_log
print(json.dumps(summary, indent=2))
