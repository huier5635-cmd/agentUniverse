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

shutil.copyfile(root / "payload/candidate.rs", file)
run("format-candidate", ["rustfmt", "--edition", "2021", str(file)])
shutil.copyfile(file, evidence / "formatted-target_identity.rs")
run("format", ["cargo", "fmt", "--all", "--", "--check"])

binary = test_binary()
run("live-preflight", ["sudo", "unshare", "--pid", "--fork", "--mount-proc", "true"])
args = ["--ignored", "--exact", "--nocapture", "--test-threads=1", test]
fixed = run("candidate-host", ["sudo", binary, *args])
assert "1 passed; 0 failed" in fixed
fixed_nested = run("candidate-parent-namespace", ["sudo", "unshare", "--pid", "--fork", "--mount-proc", binary, *args])
assert "1 passed; 0 failed" in fixed_nested

shutil.copyfile(root / "payload/baseline.rs", file)
baseline_bin = test_binary()
old = run("baseline-host", ["sudo", baseline_bin, *args], 101)
assert "is absent from PID namespace" in old and "1 failed" in old

shutil.copyfile(root / "payload/baseline.rs", file)
baseline_units = run("baseline-units", ["cargo", "test", "--locked", "-p", "ebpf-ifc-engine"], 101)
assert "tests::file_flow_does_not_enable_lsm_file_hooks_without_block_file ... FAILED" in baseline_units and "1 failed" in baseline_units
shutil.copyfile(evidence / "formatted-target_identity.rs", file)
run("clippy", ["cargo", "clippy", "--locked", "-p", "ebpf-ifc-engine", "--all-targets", "--", "-D", "warnings"])
