from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys

repo = Path(sys.argv[1]).resolve()
evidence = Path(sys.argv[2]).resolve()
evidence.mkdir(parents=True, exist_ok=True)
workspace = repo / "src/ws-ckpt/src"
payload = Path(__file__).resolve().parent
manifest = json.loads((payload / "manifest-final.json").read_text())
results = []
tree = None
coverage = ["cargo", "tarpaulin", "--workspace", "--out", "Stdout", "--fail-under", "45"]


def run(args, name, env=None, expected=0):
    with (evidence / (name + ".txt")).open("w") as stream:
        proc = subprocess.run(args, cwd=workspace, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=1500)
    output = (evidence / (name + ".txt")).read_text()
    results.append({"name": name, "exit": proc.returncode})
    if expected is not None:
        assert proc.returncode == expected, (name, proc.returncode, output[-5000:])
    return proc.returncode, output


try:
    assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip() == manifest["base"]
    # Observe the official base under the same parallel instrumentation first.
    code, output = run(coverage, "official-base-parallel-coverage", expected=None)
    if code:
        assert "the silent repair must be reported: []" in output, output[-5000:]
        assert "prune_chain_terminates_on_a_parent_cycle_below_the_head" in output
    # Keep all assertions and the coverage threshold; only serialize test threads.
    env = dict(os.environ, RUST_TEST_THREADS="1")
    run(coverage, "official-base-serial-coverage", env=env)
    subprocess.run(["git", "apply", "--check", str(payload / "candidate-final.patch")], cwd=repo, check=True)
    subprocess.run(["git", "apply", str(payload / "candidate-final.patch")], cwd=repo, check=True)
    for item in manifest["files"]:
        assert hashlib.sha256((repo / item["path"]).read_bytes()).hexdigest() == item["candidate_sha256"], item["path"]
    subprocess.run(["git", "add", "--", *[item["path"] for item in manifest["files"]]], cwd=repo, check=True)
    tree = subprocess.check_output(["git", "write-tree"], cwd=repo, text=True).strip()
    run(coverage, "candidate-serial-coverage", env=env)
    for item in manifest["files"]:
        assert hashlib.sha256((repo / item["path"]).read_bytes()).hexdigest() == item["candidate_sha256"], item["path"]
finally:
    (evidence / "summary.json").write_text(json.dumps({"base": manifest["base"], "source_tree": tree, "results": results, "extra_test_filters_or_ignores": False, "coverage_threshold": 45, "serialized_env": {"RUST_TEST_THREADS": "1"}}, indent=2))
