import argparse
import atexit
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--tools-version", required=True)
args = parser.parse_args()
ROOT = Path.cwd()
DRIVER = ROOT / ".ci/sight-dsh-status"
SOURCE = ROOT / "official"
PROJECT = SOURCE / "src/agentsight/dsh-plugin"
EVIDENCE = ROOT / "evidence"
EVIDENCE.mkdir(exist_ok=True)
manifest = json.loads((DRIVER / "manifest.json").read_text())
records = manifest["files"]
source = "src/agentsight/dsh-plugin/src/index.ts"
package = "src/agentsight/dsh-plugin/package.json"

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def run(name, command, expected=0):
    with (EVIDENCE / (name + ".log")).open("w") as log:
        p = subprocess.run(command, cwd=PROJECT, text=True, stdout=log, stderr=subprocess.STDOUT)
    output = (EVIDENCE / (name + ".log")).read_text(errors="replace")
    print(name, p.returncode, output[-1400:], flush=True)
    assert p.returncode == expected, (name, p.returncode)
    return output

def summary(output):
    return {name: int(re.search(r"^# " + name + r" (\d+)$", output, re.M)[1])
            for name in ("tests", "pass", "fail", "cancelled", "skipped")}

import re
assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=SOURCE, text=True).strip() == manifest["base"]
for row in records:
    if row["original_sha256"]:
        assert digest(SOURCE / row["path"]) == row["original_sha256"]
    else:
        assert not (SOURCE / row["path"]).exists()
    if row["path"] != source:
        target = SOURCE / row["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(DRIVER / "files" / row["path"], target)
candidate_package = (SOURCE / package).read_bytes()
install_package = json.loads(candidate_package)
install_package["devDependencies"].update({
    "@deepseek-ai/cordis": "4.0.5",
    "@deepseek-ai/dsh-tools": args.tools_version,
    "@deepseek-ai/dsh-system-prompt": args.tools_version,
    "@types/node": "22.20.5",
    "typescript": "5.9.3",
})
(SOURCE / package).write_text(json.dumps(install_package, indent=2) + "\n")
# pnpm verifies dependency pins before running a package script.
atexit.register((SOURCE / package).write_bytes, candidate_package)
run("install", ["pnpm", "install", "--ignore-scripts"])
shutil.copyfile(PROJECT / "pnpm-lock.yaml", EVIDENCE / "resolved-dependencies.yaml")
versions = {name: json.loads((PROJECT / "node_modules" / name / "package.json").read_text())["version"]
            for name in install_package["devDependencies"]}
versions["node"] = subprocess.check_output(["node", "--version"], text=True).strip()
versions["pnpm"] = subprocess.check_output(["pnpm", "--version"], text=True).strip()
(EVIDENCE / "versions.json").write_text(json.dumps(versions, indent=2))
assert versions["@deepseek-ai/dsh-tools"] == args.tools_version
build = run("baseline-build", ["node", "node_modules/typescript/bin/tsc"], 2)
assert "TS2322" in build and "ContentBlock[]" in build and "TS2307" not in build
test_command = ["node", "--test", "--test-reporter=tap", "test/status.test.mjs"]
red = summary(run("baseline-tests", test_command, 1))
assert red == {"tests": 4, "pass": 1, "fail": 3, "cancelled": 0, "skipped": 0}, red
assert "AgentSight vundefined" in (EVIDENCE / "baseline-tests.log").read_text()
shutil.copyfile(DRIVER / "files" / source, SOURCE / source)
run("fixed-build", ["node", "node_modules/typescript/bin/tsc"])
green = summary(run("fixed-tests", test_command))
assert green == {"tests": 4, "pass": 4, "fail": 0, "cancelled": 0, "skipped": 0}
candidate = (SOURCE / source).read_bytes()
needle = b"const result = value as"
assert candidate.count(needle) == 1
try:
    (SOURCE / source).write_bytes(candidate.replace(needle, b"const result = _args as"))
    run("wrong-argument-mutation-build", ["node", "node_modules/typescript/bin/tsc"])
    mutant = summary(run("wrong-argument-mutation-tests", test_command, 1))
    assert mutant == red, mutant
finally:
    (SOURCE / source).write_bytes(candidate)
run("public-test-command", ["pnpm", "test"])
(SOURCE / package).write_bytes(candidate_package)
for row in records:
    assert digest(SOURCE / row["path"]) == row["candidate_sha256"]
run("diff-check", ["git", "-C", str(SOURCE), "diff", "--check"])
subprocess.run(["git", "add", "--", *[row["path"] for row in records]], cwd=SOURCE, check=True)
run("staged-diff-check", ["git", "-C", str(SOURCE), "diff", "--cached", "--check"])
actual_tree = subprocess.check_output(["git", "write-tree"], cwd=SOURCE, text=True).strip()
assert actual_tree == manifest["source_tree"], (actual_tree, manifest["source_tree"])
result = {"base": manifest["base"], "files": records, "versions": versions,
          "baseline": red, "fixed": green, "compiling_wrong_argument_mutation": mutant,
          "scope": "real Cordis plugin loading and dsh ToolRuntime execution; no full CLI/model/eBPF claim"}
(EVIDENCE / "summary.json").write_text(json.dumps(result, indent=2))
print(json.dumps(result), flush=True)

