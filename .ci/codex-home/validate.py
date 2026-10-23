import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()
UPSTREAM = ROOT / "upstream"
PAYLOAD = Path(__file__).resolve().parent
OUT = ROOT / "evidence"
OUT.mkdir(exist_ok=True)
manifest = json.loads((PAYLOAD / "manifest.json").read_text(encoding="utf-8"))
summary = {"platform":platform.platform(),"python":sys.version,"commands":{}}
environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def run(name, args, cwd=UPSTREAM, timeout=300):
    result = subprocess.run(args, cwd=str(cwd), env=environment, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, universal_newlines=True, check=False, timeout=timeout)
    output = result.stdout + result.stderr
    (OUT / (name + ".log")).write_text(output, encoding="utf-8")
    summary["commands"][name] = result.returncode
    print(name, result.returncode, output[-2400:], flush=True)
    return result.returncode, output


assert subprocess.check_output(["git","rev-parse","HEAD"],cwd=str(UPSTREAM),universal_newlines=True).strip() == manifest["base"]
for f in manifest["files"]:
    assert digest((UPSTREAM / f["path"]).read_bytes()) == f["originalSHA256"]
    mode = subprocess.check_output(["git","ls-files","--stage","--",f["path"]],cwd=str(UPSTREAM),universal_newlines=True).split()[0]
    assert mode in ("100644","100755")
    f["mode"] = mode
original = {p:(UPSTREAM / p).read_bytes() for p in manifest["discoverySources"]}
subprocess.run(["git","apply","--check",str(PAYLOAD / "candidate.patch")],cwd=str(UPSTREAM),check=True)
subprocess.run(["git","apply",str(PAYLOAD / "candidate.patch")],cwd=str(UPSTREAM),check=True)
for f in manifest["files"]:
    assert digest((UPSTREAM / f["path"]).read_bytes()) == f["fixedSHA256"]
fixed = {p:(UPSTREAM / p).read_bytes() for p in manifest["discoverySources"]}
try:
    for p, content in original.items(): (UPSTREAM / p).write_bytes(content)
    code, log = run("baseline-codex",["bash",manifest["test"]])
    assert code != 0 and "selected executable:" in log and "canary:home:argument with spaces" in log, log[-6000:]
    summary["baselineRelativeCanaryObserved"] = True
finally:
    for p, content in fixed.items(): (UPSTREAM / p).write_bytes(content)
code, log = run("fixed-codex",["bash",manifest["test"]])
assert code == 0 and "Ran 10 tests" in log, log[-6000:]
counts = re.search(r"Codex adapter tests: (\d+) passed, 0 failed, 0 skipped",log)
assert counts and int(counts.group(1)) > 0, log[-6000:]
summary["codexShellChecksPassed"] = int(counts.group(1))
summary["embeddedMethodsPassed"] = 10
for p in [manifest["common"],manifest["detect"],manifest["test"]]:
    code, log = run("syntax-"+Path(p).name,["bash","-n",p])
    assert code == 0, log[-4000:]
if platform.system() == "Linux":
    code, log = run("shellcheck",["shellcheck",manifest["common"],manifest["detect"],manifest["test"]])
    assert code == 0, log[-6000:]
if os.environ.get("FULL_ADAPTER_GATES") == "1":
    for name, command in [
        ("codex-response-diagnostics",[sys.executable,"src/tokenless/tests/test_codex_response_diagnostics.py"]),
        ("codex-rewrite-hook",[sys.executable,"src/tokenless/tests/test_codex_rewrite_hook.py"]),
        ("full-adapters",["make","-C","src/tokenless","test-adapters"]),
        ("docs-lint",["bash","scripts/docs-lint.sh"]),
        ("docs-links",[sys.executable,"scripts/docs-link-check.py"]),
    ]:
        code, log = run(name,command,timeout=1500)
        assert code == 0, log[-6000:]
paths = [f["path"] for f in manifest["files"]]
changed = subprocess.check_output(["git","diff","--name-only"],cwd=str(UPSTREAM),universal_newlines=True).splitlines()
assert sorted(changed) == sorted(paths), changed
for f in manifest["files"]:
    assert digest((UPSTREAM / f["path"]).read_bytes()) == f["fixedSHA256"]
subprocess.run(["git","diff","--check"],cwd=str(UPSTREAM),check=True)
subprocess.run(["git","add","--",*paths],cwd=str(UPSTREAM),check=True)
tree = subprocess.check_output(["git","write-tree"],cwd=str(UPSTREAM),universal_newlines=True).strip()
manifest["sourceTree"] = tree
for f in manifest["files"]:
    target = OUT / "fixed" / f["path"]
    target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(UPSTREAM / f["path"],target)
(OUT / "manifest.json").write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8")
shutil.copyfile(PAYLOAD / "candidate.patch",OUT / "candidate.patch")
summary.update(status="passed",base=manifest["base"],sourceTree=tree,fullAdapterGates=os.environ.get("FULL_ADAPTER_GATES")=="1",
               scope="Real Bash resolver and owned executable canaries; guarded FHS relocation plus raw-source priority controls; no real Codex registration or user-home writes")
(OUT / "summary.json").write_text(json.dumps(summary,indent=2)+"\n",encoding="utf-8")
print(json.dumps(summary,indent=2))
