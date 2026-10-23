from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys

repo = Path(sys.argv[1]).resolve()
evidence = Path(sys.argv[2]).resolve()
gate = sys.argv[3]
evidence.mkdir(parents=True, exist_ok=True)
payload = Path(__file__).resolve().parent
manifest = json.loads((payload / "manifest-final.json").read_text())
results = []


def run(args, name, cwd=repo, timeout=1500):
    with (evidence / (name + ".txt")).open("w") as stream:
        proc = subprocess.run(args, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout)
    results.append({"name": name, "exit": proc.returncode})
    assert proc.returncode == 0, (name, proc.returncode, (evidence / (name + ".txt")).read_text()[-5000:])


assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip() == manifest["base"]
for item in manifest["files"]:
    path = repo / item["path"]
    if item["original_sha256"] is None:
        assert not path.exists(), item["path"]
    else:
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["original_sha256"], item["path"]
run(["git", "apply", "--check", str(payload / "candidate-final.patch")], "patch-check")
run(["git", "apply", str(payload / "candidate-final.patch")], "patch-apply")
for item in manifest["files"]:
    assert hashlib.sha256((repo / item["path"]).read_bytes()).hexdigest() == item["candidate_sha256"], item["path"]
    if item["path"].endswith(".rs"):
        assert item["candidate_sha256"] == manifest["native_validated_rust_hashes"][item["path"]]
paths = [item["path"] for item in manifest["files"]]
run(["git", "add", "--", *paths], "stage-source")
tree = subprocess.check_output(["git", "write-tree"], cwd=repo, text=True).strip()
workspace = repo / "src/ws-ckpt/src"
try:
    run(["cargo", "fmt", "--all", "--check"], "final-format", cwd=workspace)
    if gate == "package-docs":
        run(["make", "-C", "src/ws-ckpt", "test-package-raw"], "raw-package-tests")
        run(["make", "-C", "src/ws-ckpt", "test-openclaw-scripts"], "openclaw-script-tests")
        run(["bash", "scripts/docs-lint.sh"], "docs-lint")
        run([sys.executable, "scripts/docs-link-check.py"], "docs-links")
        run(["git", "diff", "--cached", "--check"], "whitespace")
    elif gate == "rust-coverage":
        version = subprocess.check_output(["cargo", "tarpaulin", "--version"], text=True).strip()
        assert version.endswith(" 0.37.2"), version
        (evidence / "coverage-tool.txt").write_text(version + "\n")
        run(["cargo", "tarpaulin", "--workspace", "--out", "Stdout", "--fail-under", "45"], "rust-coverage", cwd=workspace)
    elif gate == "plugins":
        plugin = repo / "src/ws-ckpt/src/plugins/openclaw"
        run(["npm", "install", "-g", "npm@10.9.0"], "npm-version")
        run(["npm", "ci", "--registry=https://registry.npmjs.org", "--replace-registry-host=always"], "openclaw-dependencies", cwd=plugin)
        run(["./node_modules/.bin/vitest", "run", "--coverage"], "openclaw-coverage", cwd=plugin)
        run([sys.executable, "-m", "pip", "install", "pytest", "pytest-cov", "pyyaml"], "hermes-dependencies")
        run([sys.executable, "-m", "pytest", "hermes/tests/", "--cov=hermes", "--cov-report=term", "--cov-fail-under=90"], "hermes-coverage", cwd=repo / "src/ws-ckpt/src/plugins")
    else:
        raise AssertionError(gate)
    for item in manifest["files"]:
        assert hashlib.sha256((repo / item["path"]).read_bytes()).hexdigest() == item["candidate_sha256"], item["path"]
    assert subprocess.check_output(["git", "write-tree"], cwd=repo, text=True).strip() == tree
finally:
    (evidence / "summary.json").write_text(json.dumps({"gate": gate, "base": manifest["base"], "source_tree": tree, "native_rust_validation_run": manifest["native_rust_validation_run"], "files": manifest["files"], "results": results}, indent=2))
