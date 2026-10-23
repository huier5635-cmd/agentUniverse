import json
import re
import subprocess
from pathlib import Path

ROOT = Path.cwd()
SOURCE = ROOT / "official"
PROJECT = SOURCE / "src/agentsight"
EVIDENCE = ROOT / "evidence"
EVIDENCE.mkdir(exist_ok=True)
base = "5351c892f2e89d6be799ebdbeea948c173d3d7c2"
assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=SOURCE, text=True).strip() == base
assert not subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=SOURCE, text=True).strip()
name = "storage::sqlite::genai::tokenless::tests::applied_modes_sources_filters_and_pagination"
with (EVIDENCE / "official-baseline-test.log").open("w") as log:
    p = subprocess.run(["cargo", "test", "--locked", "-p", "agentsight", "--lib", name,
                        "--", "--exact", "--nocapture"], cwd=PROJECT, stdout=log, stderr=subprocess.STDOUT)
text = (EVIDENCE / "official-baseline-test.log").read_text(errors="replace")
print(text[-4000:], flush=True)
assert p.returncode == 101
assert name in text
assert re.search(r"0 passed; 1 failed; 0 ignored", text)
assert re.search(r"left:\s*3\s+right:\s*1", text), "same deterministic 3-versus-1 assertion"
assert not subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=SOURCE, text=True).strip()
(EVIDENCE / "summary.json").write_text(json.dumps({
    "base": base, "clean_official_checkout": True, "test": name, "exit_code": p.returncode,
    "passed": 0, "failed": 1, "left": 3, "right": 1,
    "meaning": "Reproduces the candidate Rust-gate failure on pristine official code; does not certify a passing Rust workspace",
}, indent=2))
