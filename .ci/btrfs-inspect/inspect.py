from pathlib import Path
import json
import subprocess
import sys

repo = Path(sys.argv[1]).resolve()
evidence = Path(sys.argv[2]).resolve()
payload = Path(__file__).resolve().parent
evidence.mkdir(parents=True, exist_ok=True)
manifest = json.loads((payload / "manifest.json").read_text())
workspace = repo / "src/ws-ckpt/src"


def run(args, name):
    with (evidence / (name + ".txt")).open("w") as stream:
        p = subprocess.run(args, cwd=workspace, stdout=stream, stderr=subprocess.STDOUT, timeout=1200)
    return p.returncode


baseline = run(["cargo", "check", "--workspace", "--all-targets", "--locked"], "baseline-check")
baseline_format = run(["cargo", "fmt", "--all", "--", "--check"], "baseline-format")
subprocess.run(["git", "apply", "--check", str(payload / "candidate.patch")], cwd=repo, check=True)
subprocess.run(["git", "apply", str(payload / "candidate.patch")], cwd=repo, check=True)
formatted = run(["cargo", "fmt", "--all"], "format-candidate")
check = run(["cargo", "check", "--workspace", "--all-targets", "--locked"], "candidate-check")
for item in manifest["files"]:
    dest = evidence / "formatted" / item["path"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes((repo / item["path"]).read_bytes())
(evidence / "inspection-summary.json").write_text(json.dumps({"baseline_check": baseline, "baseline_format": baseline_format, "candidate_format": formatted, "candidate_check": check, "not_final_validation": True}, indent=2))
assert baseline == 0 and baseline_format == 0 and formatted == 0 and check == 0
