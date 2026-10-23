"""Check the real native build plus Linux tests, retaining the known main failure."""

import hashlib
import json
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()
UPSTREAM = ROOT / "official"
PROJECT = UPSTREAM / "src/agentsight"
PREPARED = ROOT / "prepared"
OUT = ROOT / "evidence"
OUT.mkdir(exist_ok=True)
manifest = json.loads((PREPARED / "manifest.json").read_text(encoding="utf-8"))
mode = sys.argv[1]
assert (mode == "native" and platform.system() == "Darwin") or (mode == "linux" and platform.system() == "Linux")
summary = {"mode": mode, "platform": platform.platform(), "commands": {}}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def run(name, args, cwd=PROJECT):
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=False)
    output = result.stdout + result.stderr
    (OUT / (name + ".log")).write_text(output, encoding="utf-8")
    summary["commands"][name] = result.returncode
    print(name, result.returncode, output[-2500:], flush=True)
    return result.returncode, re.sub(r"\x1b\[[0-9;]*m", "", output)


assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=UPSTREAM, text=True).strip() == manifest["base"]
for entry in manifest["files"]:
    assert digest((UPSTREAM / entry["path"]).read_bytes()) == entry["originalSHA256"]
cargo_original = (PROJECT / "Cargo.toml").read_bytes()
lock_original = (PROJECT / "Cargo.lock").read_bytes()
options = ["--locked", "-p", "agentsight"]
if mode == "native":
    code, log = run("pristine-main-check", ["cargo", "check", *options, "--bin", "agentsight"])
    assert code == 101 and "E0433" in log and "cpu_budget.rs" in log and "metrics" in log, log[-6000:]
subprocess.run(["git", "apply", "--check", str(PREPARED / "candidate.patch")], cwd=UPSTREAM, check=True)
subprocess.run(["git", "apply", str(PREPARED / "candidate.patch")], cwd=UPSTREAM, check=True)
for entry in manifest["files"]:
    assert digest((UPSTREAM / entry["path"]).read_bytes()) == entry["fixedSHA256"]
if mode == "native":
    code, log = run("fixed-check", ["cargo", "check", *options, "--bin", "agentsight"])
    assert code == 0, log[-5000:]
code, library_log = run("library", ["cargo", "test", *options, "--lib", "--", "--test-threads=2"])
if mode == "native":
    assert code == 0 and "test result: ok." in library_log, library_log[-6000:]
else:
    assert code == 101, library_log[-6000:]
    failures = re.findall(r"^test (\S+) \.\.\. FAILED$", library_log, re.MULTILINE)
    assert failures == [manifest["knownLinuxFailure"]], failures
    assert "3186 passed; 1 failed; 2 ignored" in library_log, library_log[-6000:]
    for test in ["utils::cpu_budget::tests::", "checkpoint_ticks_do_not_accelerate_cleanup", "yielded_cleanup_resumes", "disabled_tokenizer_feature_skips_global_lookup"]:
        assert test in library_log, test
    summary["knownFailure"] = failures[0]
    summary["knownFailureEvidenceRun"] = manifest["knownLinuxFailureEvidenceRun"]
clippy = ["cargo", "clippy", "--locked", "--workspace", "--all-targets", "--", "-D", "warnings"] if mode == "linux" else ["cargo", "clippy", *options, "--all-targets", "--", "-D", "warnings"]
code, log = run("clippy", clippy)
assert code == 0, log[-6000:]
code, _ = run("format", ["cargo", "fmt", "--all", "--", "--check"])
assert code == 0
assert (PROJECT / "Cargo.toml").read_bytes() == cargo_original
assert (PROJECT / "Cargo.lock").read_bytes() == lock_original
changed = subprocess.check_output(["git", "diff", "--name-only"], cwd=UPSTREAM, text=True).splitlines()
paths = [entry["path"] for entry in manifest["files"]]
assert sorted(changed) == sorted(paths), changed
subprocess.run(["git", "diff", "--check"], cwd=UPSTREAM, check=True)
subprocess.run(["git", "add", "--", *paths], cwd=UPSTREAM, check=True)
tree = subprocess.check_output(["git", "write-tree"], cwd=UPSTREAM, text=True).strip()
assert tree == manifest["sourceTree"], tree
summary.update({"status": "passed" if mode == "native" else "validated-with-known-main-test-failure", "base": manifest["base"], "sourceTree": tree, "cargoAndLockUnchanged": True, "testGroups": re.findall(r"test result: (?:ok|FAILED)\. (\d+) passed; (\d+) failed; (\d+) ignored", library_log), "scope": "Complete platform library and strict Clippy; Linux-specific tests executed on Linux; existing mode test failure is retained, not skipped"})
(OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
shutil.copyfile(PREPARED / "manifest.json", OUT / "manifest.json")
shutil.copyfile(PREPARED / "candidate.patch", OUT / "candidate.patch")
print(json.dumps(summary, indent=2))
