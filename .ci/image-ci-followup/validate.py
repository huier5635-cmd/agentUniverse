import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path.cwd()
UPSTREAM = ROOT / "upstream"
PAYLOAD = Path(__file__).resolve().parent
OUT = ROOT / "evidence"
OUT.mkdir(exist_ok=True)
manifest = json.loads((PAYLOAD / "manifest.json").read_text())
summary = {"commands":{},"upstreamWorkflowTriggered":False,"sourcePublished":False}
environment = dict(os.environ,PYTHONDONTWRITEBYTECODE="1")


def run(name, commands):
    result = subprocess.run(["bash","-euo","pipefail","-c",commands],cwd=UPSTREAM,env=environment,
                            capture_output=True,text=True,check=False,timeout=900)
    output = result.stdout + result.stderr
    (OUT / (name+".log")).write_text(output,encoding="utf-8")
    summary["commands"][name] = result.returncode
    print(name,result.returncode,output[-2300:],flush=True)
    return result.returncode,output


def workflow_commands(text):
    anchor = "      - name: Verify raw package and action\n        run: |\n"
    assert text.count(anchor)==1
    commands=text.split(anchor,1)[1].splitlines()
    assert all(not line or line.startswith("          ") for line in commands)
    return [line[10:] for line in commands if line.strip()]


def triggered(text,path):
    block=text.split("    paths:\n",1)[1].split("  workflow_dispatch:",1)[0]
    patterns=re.findall(r"^      - '([^']+)'$",block,re.MULTILINE)
    assert patterns
    return any(path.startswith(p[:-2]) if p.endswith("/**") else path==p for p in patterns)


assert subprocess.check_output(["git","rev-parse","HEAD"],cwd=UPSTREAM,text=True).strip()==manifest["base"]
for f in manifest["files"]:
    assert hashlib.sha256((UPSTREAM/f["path"]).read_bytes()).hexdigest()==f["originalSHA256"]
    f["mode"]=subprocess.check_output(["git","ls-files","--stage","--",f["path"]],cwd=UPSTREAM,text=True).split()[0]
workflow_path=UPSTREAM/manifest["workflow"]
test_path=UPSTREAM/manifest["test"]
old_workflow=workflow_path.read_text()
old_test=test_path.read_text()
subprocess.run(["git","apply","--check",str(PAYLOAD/"candidate.patch")],cwd=UPSTREAM,check=True)
subprocess.run(["git","apply",str(PAYLOAD/"candidate.patch")],cwd=UPSTREAM,check=True)
new_workflow=workflow_path.read_text()
for f in manifest["files"]:
    assert hashlib.sha256((UPSTREAM/f["path"]).read_bytes()).hexdigest()==f["fixedSHA256"]
assert [ast.dump(n) for n in ast.parse(old_test).body[1:]]==[ast.dump(n) for n in ast.parse(test_path.read_text()).body[1:]]
old_commands=workflow_commands(old_workflow)
new_commands=workflow_commands(new_workflow)
assert new_commands==old_commands[:1]+["python3 "+manifest["test"]]+old_commands[1:]
assert not triggered(old_workflow,manifest["test"]) and triggered(new_workflow,manifest["test"])
assert triggered(old_workflow,manifest["script"]) and triggered(new_workflow,manifest["script"])
assert not triggered(new_workflow,"tests/os-skills/test_pdf_reader_tables.py")
summary["pathFilters"]={"oldTestOnlyChange":False,"newTestOnlyChange":True,"existingSourceChange":True,"unrelatedPdfTestChange":False}
script=UPSTREAM/manifest["script"]
original=script.read_bytes()
needle=b"        if error.code in _FALLBACK_STATUS:\n"
assert original.count(needle)==1
mutant=original.replace(needle,b"        if True:  # regression mutation: status guard removed\n")
try:
    script.write_bytes(mutant)
    # Packaging actions read HEAD through a worktree. Commit the owned mutation
    # and candidate locally so both command sets see the same clean revision.
    subprocess.run(["git","add","--",manifest["script"],*[f["path"] for f in manifest["files"]]],cwd=UPSTREAM,check=True)
    subprocess.run(["git","-c","user.name=CI mutation fixture","-c","user.email=ci-mutation@example.invalid",
                    "commit","--quiet","-m","ci: inject status-guard regression for validation"],cwd=UPSTREAM,check=True)
    summary["localMutationCommit"]=subprocess.check_output(["git","rev-parse","HEAD"],cwd=UPSTREAM,text=True).strip()
    assert summary["localMutationCommit"]!=manifest["base"]
    code,log=run("old-workflow-misses-status-regression","\n".join(old_commands))
    assert code==0 and "Ran 18 tests" not in log,log[-6000:]
    code,log=run("new-workflow-detects-status-regression","\n".join(new_commands))
    assert code!=0 and "FAILED (failures=32)" in log,log[-6000:]
    summary["mutationFailures"]=32
finally:
    # This is the disposable CI checkout; keep the candidate's unstaged files.
    subprocess.run(["git","reset","--mixed",manifest["base"]],cwd=UPSTREAM,check=True)
    script.write_bytes(original)
code,log=run("new-workflow-fixed","\n".join(new_commands))
assert code==0 and "Ran 18 tests" in log,log[-6000:]
summary["testGroups"]=re.findall(r"Ran (\d+) tests?",log)
assert script.read_bytes()==original
assert subprocess.check_output(["git","rev-parse","HEAD"],cwd=UPSTREAM,text=True).strip()==manifest["base"]
paths=[f["path"] for f in manifest["files"]]
changed=subprocess.check_output(["git","diff","--name-only"],cwd=UPSTREAM,text=True).splitlines()
assert sorted(changed)==sorted(paths),changed
subprocess.run(["git","diff","--check"],cwd=UPSTREAM,check=True)
subprocess.run(["git","add","--",*paths],cwd=UPSTREAM,check=True)
tree=subprocess.check_output(["git","write-tree"],cwd=UPSTREAM,text=True).strip()
manifest["sourceTree"]=tree
for f in manifest["files"]:
    target=OUT/"fixed"/f["path"]
    target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(UPSTREAM/f["path"],target)
(OUT/"manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
shutil.copyfile(PAYLOAD/"candidate.patch",OUT/"candidate.patch")
summary.update(status="passed",base=manifest["base"],candidateTree=tree,testMethods=18,testExecutableCodeUnchanged=True,
               scope="Actual original/new run blocks on Ubuntu; declared path patterns verified; no upstream event scheduler or self-hosted runner invoked; publication waits for8932 merge and capacity")
(OUT/"summary.json").write_text(json.dumps(summary,indent=2)+"\n")
print(json.dumps(summary,indent=2))
