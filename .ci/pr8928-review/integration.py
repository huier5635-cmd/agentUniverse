from pathlib import Path
import hashlib
import json
import subprocess
import sys

repo = Path(sys.argv[1]).resolve()
payload = Path(__file__).resolve().parent
evidence = Path(sys.argv[2]).resolve()
evidence.mkdir(parents=True, exist_ok=True)
manifest = json.loads((payload / "manifest.json").read_text())
assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip() == manifest["base"]
subprocess.run(["git", "apply", "--check", str(payload / "candidate.patch")], cwd=repo, check=True)
subprocess.run(["git", "apply", str(payload / "candidate.patch")], cwd=repo, check=True)
for item in manifest["files"]:
    assert hashlib.sha256((repo / item["path"]).read_bytes()).hexdigest() == item["fixed_sha256"]
with (evidence / "make-test-integration.txt").open("w") as log:
    proc = subprocess.run(["make", "-C", "src/tokenless", "test-integration"], cwd=repo, stdout=log, stderr=subprocess.STDOUT, timeout=1500)
result = {"base": manifest["base"], "command": "make -C src/tokenless test-integration", "exit": proc.returncode, "python": sys.version}
(evidence / "integration-summary.json").write_text(json.dumps(result, indent=2))
if proc.returncode:
    print((evidence / "make-test-integration.txt").read_text()[-6000:])
assert proc.returncode == 0, result
print(json.dumps(result))
