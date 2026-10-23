"""Verify the pinned upstream tree against baseline, fix and guard mutation."""

import ast
import difflib
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PAYLOAD = Path(__file__).resolve().parent
MANIFEST = json.loads((PAYLOAD / "manifest.json").read_text())
UPSTREAM = Path(sys.argv[1]).resolve()
EVIDENCE = Path(sys.argv[2]).resolve()
EVIDENCE.mkdir(parents=True, exist_ok=True)
SOURCE, TEST = (entry["path"] for entry in MANIFEST["files"])
SUMMARY = {"python": platform.python_version(), "platform": platform.platform(), "commands": []}


def command(label, args, cwd, expected=0):
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=False)
    output = result.stdout + result.stderr
    (EVIDENCE / f"{label}.log").write_text(output, encoding="utf-8")
    SUMMARY["commands"].append({"label": label, "args": args, "returncode": result.returncode})
    if expected is not None and result.returncode != expected:
        print(output)
        raise AssertionError(f"{label}: expected {expected}, got {result.returncode}")
    return result, output


def scratch_tests(label, source):
    with tempfile.TemporaryDirectory(prefix=f"image-http-{label}-") as temporary:
        root = Path(temporary)
        for path, data in ((SOURCE, source), (TEST, (PAYLOAD / "fixed" / TEST).read_bytes())):
            destination = root / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        result, output = command(label, [sys.executable, "-B", TEST, "-v"], root, expected=1)
        assert "Ran 7 tests" in output and "FAILED (failures=14)" in output, output
        return result.returncode


head, _ = command("upstream-head", ["git", "rev-parse", "HEAD"], UPSTREAM)
assert head.stdout.strip() == MANIFEST["base"]
original = (UPSTREAM / SOURCE).read_bytes()
assert hashlib.sha256(original).hexdigest() == MANIFEST["files"][0]["original_sha256"]
scratch_tests("original", original)

command("patch-check", ["git", "apply", "--check", str(PAYLOAD / "candidate.patch")], UPSTREAM)
command("patch-apply", ["git", "apply", str(PAYLOAD / "candidate.patch")], UPSTREAM)
for entry in MANIFEST["files"]:
    data = (UPSTREAM / entry["path"]).read_bytes()
    assert hashlib.sha256(data).hexdigest() == entry["fixed_sha256"], entry["path"]
command("fixed", [sys.executable, "-B", TEST, "-v"], UPSTREAM)
fixed = (UPSTREAM / SOURCE).read_text()
guard = "if error.code in (404, 405, 501):"
assert fixed.count(guard) == 1
scratch_tests("guard-removed", fixed.replace(guard, "if True:").encode())

function = next(
    n for n in ast.parse(fixed).body if isinstance(n, ast.FunctionDef) and n.name == "_compat"
)
command(
    "black-handler",
    [
        sys.executable,
        "-m",
        "black",
        "--check",
        "--line-length",
        "100",
        "--line-ranges",
        f"{function.lineno}-{function.end_lineno}",
        SOURCE,
    ],
    UPSTREAM,
)
command(
    "black-test", [sys.executable, "-m", "black", "--check", "--line-length", "100", TEST], UPSTREAM
)
command(
    "isort",
    [
        sys.executable,
        "-m",
        "isort",
        "--check-only",
        "--profile",
        "black",
        "--line-length",
        "100",
        SOURCE,
        TEST,
    ],
    UPSTREAM,
)

old_lines, new_lines = original.decode().splitlines(), fixed.splitlines()
changed_lines = set()
for tag, _, _, start, end in difflib.SequenceMatcher(a=old_lines, b=new_lines).get_opcodes():
    if tag != "equal":
        changed_lines.update(range(start + 1, end + 1))
lint, _ = command(
    "ruff-raw",
    [
        sys.executable,
        "-m",
        "ruff",
        "check",
        "--isolated",
        "--select",
        "F,E,W,I,TID252",
        "--ignore",
        "E501",
        "--line-length",
        "100",
        "--output-format",
        "json",
        SOURCE,
        TEST,
    ],
    UPSTREAM,
    expected=None,
)
assert lint.returncode in (0, 1)
violations = json.loads(lint.stdout)
new_violations = [
    v
    for v in violations
    if Path(v["filename"]).resolve() != (UPSTREAM / SOURCE).resolve()
    or v["location"]["row"] in changed_lines
]
(EVIDENCE / "ruff-changed-lines.json").write_text(json.dumps(new_violations, indent=2) + "\n")
assert not new_violations, new_violations
command("diff-check", ["git", "diff", "--check"], UPSTREAM)
command("stage-source", ["git", "add", "--", SOURCE, TEST], UPSTREAM)
tree, _ = command("source-tree", ["git", "write-tree"], UPSTREAM)
assert tree.stdout.strip() == MANIFEST["sourceTree"], tree.stdout
SUMMARY.update(
    {
        "status": "passed",
        "base": MANIFEST["base"],
        "sourceTree": tree.stdout.strip(),
        "fixedMethods": 7,
        "originalFailures": 14,
        "mutationFailures": 14,
        "remoteModelCalls": 0,
        "scope": "urllib mocks plus real loopback HTTP; no paid generation or full monorepo suite",
        "formatterScope": MANIFEST["formatterScope"],
    }
)
(EVIDENCE / "summary.json").write_text(json.dumps(SUMMARY, indent=2) + "\n")
shutil.copyfile(PAYLOAD / "manifest.json", EVIDENCE / "manifest.json")
print(json.dumps(SUMMARY, indent=2))
