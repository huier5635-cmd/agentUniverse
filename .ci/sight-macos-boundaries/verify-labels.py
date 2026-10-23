"""Exercise the real macOS module with owned SQLite stores and HTTP requests."""

import hashlib
import json
import platform
import re
import subprocess
from pathlib import Path

ROOT = Path.cwd()
UPSTREAM = ROOT / "official"
PROJECT = UPSTREAM / "src/agentsight"
PREPARED = ROOT / "prepared"
OUT = ROOT / "evidence"
OUT.mkdir(exist_ok=True)
manifest = json.loads((PREPARED / "manifest.json").read_text(encoding="utf-8"))
target = UPSTREAM / manifest["sourceFile"]
summary = {"platform": platform.platform(), "commands": {}}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def run(name, args, cwd=PROJECT):
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=False)
    output = result.stdout + result.stderr
    (OUT / (name + ".log")).write_text(output, encoding="utf-8")
    summary["commands"][name] = result.returncode
    print(name, result.returncode, output[-2000:], flush=True)
    return result.returncode, output


assert platform.system() == "Darwin", "The production module is macOS-specific"
assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=UPSTREAM, text=True).strip() == manifest["base"]
prerequisite = json.loads((ROOT / "platform-verified/manifest.json").read_text(encoding="utf-8"))
assert prerequisite["base"] == manifest["base"]
subprocess.run(["git", "apply", "--check", str(ROOT / "platform-verified/candidate.patch")], cwd=UPSTREAM, check=True)
subprocess.run(["git", "apply", str(ROOT / "platform-verified/candidate.patch")], cwd=UPSTREAM, check=True)
subprocess.run(["git", "add", "--", *[f["path"] for f in prerequisite["files"]]], cwd=UPSTREAM, check=True)
assert subprocess.check_output(["git", "write-tree"], cwd=UPSTREAM, text=True).strip() == prerequisite["sourceTree"]
manifest["prerequisiteTree"] = prerequisite["sourceTree"]
manifest["withPrerequisiteTree"] = prerequisite["withLabelTree"]
original = target.read_bytes()
assert digest(original) == manifest["originalSHA256"]
subprocess.run(["git", "apply", "--check", str(PREPARED / "candidate.patch")], cwd=UPSTREAM, check=True)
subprocess.run(["git", "apply", str(PREPARED / "candidate.patch")], cwd=UPSTREAM, check=True)
fixed = target.read_bytes()
assert digest(fixed) == manifest["fixedSHA256"]
marker = b"#[cfg(test)]"
assert fixed.count(marker) == original.count(marker) == 1
baseline = original.split(marker, 1)[0] + marker + fixed.split(marker, 1)[1]
options = ["--locked", "-p", "agentsight", "--no-default-features", "--features", "server"]
try:
    target.write_bytes(baseline)
    code, log = run("baseline-routes", ["cargo", "test", *options, "--lib", "local::server::trajectories::tests::", "--", "--test-threads=1"])
    assert code == 101 and "test result: FAILED." in log and "1 failed" in log, log[-5000:]
    assert "trajectory_label_store_failure_is_reported_and_recovers" in log
    assert "left: 200" in log and "right: 500" in log
finally:
    target.write_bytes(fixed)

code, log = run("fixed-routes", ["cargo", "test", *options, "--lib", "local::server::trajectories::tests::", "--", "--test-threads=1"])
assert code == 0 and "test result: ok." in log and "0 failed" in log, log[-5000:]
code, library_log = run("library", ["cargo", "test", *options, "--lib", "--", "--test-threads=2"])
assert code == 0, library_log[-5000:]
code, log = run("clippy", ["cargo", "clippy", *options, "--all-targets", "--", "-D", "warnings"])
assert code == 0, log[-5000:]
code, _ = run("format", ["cargo", "fmt", "--all", "--", "--check"])
assert code == 0
code, _ = run("diff-check", ["git", "diff", "--check"], UPSTREAM)
assert code == 0
assert digest(target.read_bytes()) == manifest["fixedSHA256"]
subprocess.run(["git", "add", "--", manifest["sourceFile"]], cwd=UPSTREAM, check=True)
tree = subprocess.check_output(["git", "write-tree"], cwd=UPSTREAM, text=True).strip()
assert tree == manifest["withPrerequisiteTree"], tree
summary.update({"status": "passed", "base": manifest["base"], "sourceTree": tree, "labelOnlyTree": manifest["sourceTree"], "prerequisiteTree": manifest["prerequisiteTree"], "fixedSHA256": manifest["fixedSHA256"], "rustc": subprocess.check_output(["rustc", "--version"], text=True).strip(), "testGroups": re.findall(r"test result: ok\. (\d+) passed; (\d+) failed; (\d+) ignored", library_log), "scope": "Explicit macOS platform-boundary prerequisite; real macOS local HTTP handlers, actual SQLite fault/repair, complete agentsight library and all-target clippy; no live model, browser or eBPF"})
(OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
(OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
print(json.dumps(summary, indent=2))
