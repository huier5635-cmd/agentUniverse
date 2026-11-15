import json
import os
import shutil
import subprocess
from pathlib import Path

root = Path.cwd()
workspace = root / "source/src/agentsight"
file = root / "source/src/agentsight/crates/ebpf-ifc-engine/src/target_identity.rs"
test = "target_identity::tests::resolves_child_namespace_process_from_parent_procfs"
evidence = root / "evidence"
evidence.mkdir()

def run(name, args, expected=0):
    result = subprocess.run(args, cwd=workspace, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (evidence / (name + ".log")).write_text(result.stdout + result.stderr)
    if "json" not in name or result.returncode != expected:
        print(result.stdout, result.stderr, flush=True)
    assert result.returncode == expected, (name, result.returncode, expected)
    return result.stdout + result.stderr

def test_binary():
    data = run("compiled-json", ["cargo", "test", "--locked", "-p", "ebpf-ifc-engine", "--lib",
                                 "--no-run", "--message-format=json"])
    binaries = []
    for line in data.splitlines():
        if line.startswith("{"):
            item = json.loads(line)
            if item.get("reason") == "compiler-artifact" and item.get("executable") and item.get("target", {}).get("name") == "ebpf_ifc_engine":
                binaries.append(item["executable"])
    assert len(binaries) == 1, binaries
    return binaries[0]


import re

cmd = ["cargo", "clippy", "--locked", "-p", "ebpf-ifc-engine", "--all-targets", "--message-format=json", "--", "-D", "warnings"]
def diagnoses(name, source):
    shutil.copyfile(root / "payload" / source, file)
    result = subprocess.run(cmd, cwd=workspace, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    failures = []
    for line in result.stdout.splitlines():
        if line.startswith("{"):
            obj = json.loads(line)
            if obj.get("reason") == "compiler-message":
                msg = obj["message"]
                if msg["level"] == "error" and msg.get("spans"):
                    code = (msg.get("code") or {}).get("code")
                    for span in msg["spans"]:
                        if span["is_primary"]:
                            failures.append((code, msg["message"], span["file_name"], tuple(p["text"] for p in span["text"])))
    print(name, "exit", result.returncode, "diagnostics", failures, flush=True)
    (evidence / (name + ".json")).write_text(json.dumps(failures, indent=2))
    return result.returncode, sorted(set(failures))

base = diagnoses("baseline-clippy", "baseline.rs")
changed = diagnoses("candidate-clippy", "candidate.rs")
assert base == changed and len(base[1]) == 5, (base, changed)
