"""Format the platform repair and record its independent and combined trees."""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path.cwd()
PAYLOAD = ROOT / ".ci/sight-macos-boundaries"
UPSTREAM = ROOT / "official"
PROJECT = UPSTREAM / "src/agentsight"
OUT = ROOT / "prepared"
OUT.mkdir(exist_ok=True)
manifest = json.loads((PAYLOAD / "manifest.json").read_text(encoding="utf-8"))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def run(name, args, cwd=PROJECT):
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=False)
    (OUT / (name + ".log")).write_text(result.stdout + result.stderr, encoding="utf-8")
    assert result.returncode == 0, (name, (result.stdout + result.stderr)[-4000:])
    return result.stdout


assert run("head", ["git", "rev-parse", "HEAD"], UPSTREAM).strip() == manifest["base"]
for entry in manifest["files"]:
    assert digest((UPSTREAM / entry["path"]).read_bytes()) == entry["originalSHA256"]
run("apply-check", ["git", "apply", "--check", str(PAYLOAD / "candidate-unformatted.patch")], UPSTREAM)
run("apply", ["git", "apply", str(PAYLOAD / "candidate-unformatted.patch")], UPSTREAM)
for entry in manifest["files"]:
    assert digest((UPSTREAM / entry["path"]).read_bytes()) == entry["unformattedSHA256"]
run("format", ["cargo", "fmt", "--all"])
run("format-check", ["cargo", "fmt", "--all", "--", "--check"])
paths = [entry["path"] for entry in manifest["files"]]
assert sorted(run("changed-files", ["git", "diff", "--name-only"], UPSTREAM).splitlines()) == sorted(paths)
run("architecture", ["python3", "scripts/check-arch-boundaries.py"])
run("sqlite-self-test", ["python3", "scripts/check-sqlite-entrypoints.py", "--self-test"])
run("sqlite-entrypoints", ["python3", "scripts/check-sqlite-entrypoints.py"])
run("diff-check", ["git", "diff", "--check"], UPSTREAM)
run("stage", ["git", "add", "--", *paths], UPSTREAM)
manifest["sourceTree"] = run("tree", ["git", "write-tree"], UPSTREAM).strip()
(OUT / "candidate.patch").write_bytes(subprocess.check_output(["git", "diff", "--cached", "--binary"], cwd=UPSTREAM))
for entry in manifest["files"]:
    source = UPSTREAM / entry["path"]
    entry["fixedSHA256"] = digest(source.read_bytes())
    dest = OUT / "fixed" / entry["path"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, dest)

labels = json.loads((ROOT / "label-prepared/manifest.json").read_text(encoding="utf-8"))
label_path = labels["sourceFile"]
assert labels["base"] == manifest["base"] and label_path not in paths
run("label-apply-check", ["git", "apply", "--check", str(ROOT / "label-prepared/candidate.patch")], UPSTREAM)
run("label-apply", ["git", "apply", str(ROOT / "label-prepared/candidate.patch")], UPSTREAM)
run("label-stage", ["git", "add", "--", label_path], UPSTREAM)
manifest["withLabelTree"] = run("combined-tree", ["git", "write-tree"], UPSTREAM).strip()
run("restore-independent-scope", ["git", "restore", "--source=HEAD", "--staged", "--worktree", "--", label_path], UPSTREAM)
assert run("tree-restored", ["git", "write-tree"], UPSTREAM).strip() == manifest["sourceTree"]
(OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
print(json.dumps(manifest, indent=2))
