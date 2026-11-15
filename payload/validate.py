from pathlib import Path
import hashlib
import json
import subprocess

root = Path.cwd()
workspace = root / "source/src/cosh-ng"
evidence = root / "evidence"
evidence.mkdir()
for name in ("wrap.rs", "wrap_tests.rs"):
    target = workspace / "crates/cosh-shell/src/ui" / name
    target.write_bytes((root / "payload" / name).read_bytes())

def run(name, command):
    result = subprocess.run(command, cwd=workspace, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    (evidence / (name + ".log")).write_text(result.stdout)
    print(result.stdout, flush=True)
    result.check_returncode()
    return result.stdout

run("fmt", ["cargo", "fmt", "--all", "--", "--check"])
run("layout", ["bash", "crates/cosh-shell/scripts/check-layout.sh"])
tests = run("wrapping", ["cargo", "test", "--locked", "-p", "cosh-shell", "--lib", "wrap_tests::"])
assert "14 passed" in tests and "0 failed" in tests, tests
inventory = run("inventory", ["bash", "scripts/check-test-inventory.sh"])
assert "test inventory audit passed" in inventory, inventory
assert "cosh-shell exact lib/bin overlap: 696 (ceiling 696)" in inventory, inventory
listing = run("bin-list", ["cargo", "test", "--locked", "-p", "cosh-shell", "--bin", "cosh-shell", "--", "--list"])
assert "strips_osc_bel_payloads" not in listing
summary = {name: hashlib.sha256((workspace / "crates/cosh-shell/src/ui" / name).read_bytes()).hexdigest() for name in ("wrap.rs", "wrap_tests.rs")}
(evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
