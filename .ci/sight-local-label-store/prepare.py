"""Format exactly one proposed source file, then publish its byte/tree identity."""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path.cwd()
PAYLOAD = ROOT / ".ci/sight-local-label-store"
UPSTREAM = ROOT / "official"
PROJECT = UPSTREAM / "src/agentsight"
OUT = ROOT / "prepared"
OUT.mkdir(exist_ok=True)
manifest = json.loads((PAYLOAD / "manifest.json").read_text(encoding="utf-8"))
target = UPSTREAM / manifest["sourceFile"]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def run(name, args, cwd=PROJECT):
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=False)
    (OUT / (name + ".log")).write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode:
        print((result.stdout + result.stderr)[-3000:])
        raise AssertionError(name + ": " + str(result.returncode))
    return result.stdout


assert run("head", ["git", "rev-parse", "HEAD"], UPSTREAM).strip() == manifest["base"]
assert digest(target.read_bytes()) == manifest["originalSHA256"]
candidate = (PAYLOAD / "candidate.rs").read_bytes()
assert digest(candidate) == manifest["unformattedSHA256"]
target.write_bytes(candidate)
run("format", ["cargo", "fmt", "--all"])
run("format-check", ["cargo", "fmt", "--all", "--", "--check"])
changed = run("changed-files", ["git", "diff", "--name-only"], UPSTREAM).splitlines()
assert changed == [manifest["sourceFile"]], changed
run("architecture", ["python3", "scripts/check-arch-boundaries.py"])
run("sqlite-self-test", ["python3", "scripts/check-sqlite-entrypoints.py", "--self-test"])
run("sqlite-entrypoints", ["python3", "scripts/check-sqlite-entrypoints.py"])
run("diff-check", ["git", "diff", "--check"], UPSTREAM)
run("stage", ["git", "add", "--", manifest["sourceFile"]], UPSTREAM)
manifest["sourceTree"] = run("tree", ["git", "write-tree"], UPSTREAM).strip()
manifest["fixedSHA256"] = digest(target.read_bytes())
shutil.copyfile(target, OUT / "candidate.rs")
patch = subprocess.check_output(["git", "diff", "--cached", "--binary"], cwd=UPSTREAM)
(OUT / "candidate.patch").write_bytes(patch)
(OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
print(json.dumps(manifest, indent=2))
