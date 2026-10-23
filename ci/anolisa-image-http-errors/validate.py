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
import textwrap
import re
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


def scratch_tests(label, source, test=None, exact=None):
    with tempfile.TemporaryDirectory(prefix=f"image-http-{label}-") as temporary:
        root = Path(temporary)
        for path, data in ((SOURCE, source), (TEST, test or (PAYLOAD / "fixed" / TEST).read_bytes())):
            destination = root / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        result, output = command(label, [sys.executable, "-B", TEST, "-v"], root, expected=1)
        assert "Ran " in output and "FAILED" in output, output
        if exact:
            assert exact in output, output
        SUMMARY[label] = re.search(r"FAILED \(.*?\)", output).group(0)
        return result.returncode


head, _ = command("upstream-head", ["git", "rev-parse", "HEAD"], UPSTREAM)
assert head.stdout.strip() == MANIFEST["base"]
original = (UPSTREAM / SOURCE).read_bytes()
assert hashlib.sha256(original).hexdigest() == MANIFEST["files"][0]["original_sha256"]
if sys.version_info >= (3, 11):
    scratch_tests("original", original, (PAYLOAD / "original-test.py").read_bytes(), "FAILED (failures=14)")
else:
    SUMMARY["original"] = "legacy seven-method baseline retained on Python3.11"
scratch_tests("previous-review-head", (PAYLOAD / "previous.py").read_bytes())

command("patch-check", ["git", "apply", "--check", str(PAYLOAD / "candidate.patch")], UPSTREAM)
command("patch-apply", ["git", "apply", str(PAYLOAD / "candidate.patch")], UPSTREAM)
for entry in MANIFEST["files"]:
    data = (UPSTREAM / entry["path"]).read_bytes()
    assert hashlib.sha256(data).hexdigest() == entry["fixed_sha256"], entry["path"]
command("fixed", [sys.executable, "-B", TEST, "-v"], UPSTREAM)
fixed = (UPSTREAM / SOURCE).read_text()
guard = "if error.code in _FALLBACK_STATUS:"
assert fixed.count(guard) == 1
scratch_tests("guard-removed", fixed.replace(guard, "if True:").encode())

helper = next(n for n in ast.parse(fixed).body if isinstance(n, ast.FunctionDef) and n.name == "_http_error_detail")
read_guard = next(n for n in helper.body if isinstance(n, ast.Try))
mutant_lines = fixed.splitlines(keepends=True)
mutant_lines[read_guard.lineno - 1:read_guard.end_lineno] = ["    raw = error.read(_ERROR_DETAIL_LIMIT + 1)\n"]
scratch_tests("read-guard-removed", "".join(mutant_lines).encode())
assert int(re.search(r"errors=(\d+)", SUMMARY["previous-review-head"]).group(1)) >= 20
assert int(re.search(r"errors=(\d+)", SUMMARY["read-guard-removed"]).group(1)) >= 20


# Format new logic independently so legacy request/success lines stay intact.
if sys.version_info >= (3, 11):
    parsed = ast.parse(fixed)
    helper = next(n for n in parsed.body if isinstance(n, ast.FunctionDef) and n.name == "_http_error_detail")
    compat = next(n for n in parsed.body if isinstance(n, ast.FunctionDef) and n.name == "_compat")
    handler = next(n for n in ast.walk(compat) if isinstance(n, ast.ExceptHandler))
    lines = fixed.splitlines(keepends=True)
    snippets = {
        "helper": "".join(lines[helper.lineno - 1:helper.end_lineno]),
        "handler": "try:\n    pass\n" + textwrap.dedent("".join(lines[handler.lineno - 1:handler.end_lineno])),
    }
    for name, snippet in snippets.items():
        path = EVIDENCE / (name + "-format.py")
        path.write_text(snippet, encoding="utf-8")
        command("black-" + name, [sys.executable, "-m", "black", "--check", "--target-version", "py38", "--line-length", "100", str(path)], UPSTREAM)
    command("black-test", [sys.executable, "-m", "black", "--check", "--target-version", "py38", "--line-length", "100", TEST], UPSTREAM)
    command("isort", [sys.executable, "-m", "isort", "--check-only", "--profile", "black", "--line-length", "100", SOURCE, TEST], UPSTREAM)

old_lines, new_lines = original.decode().splitlines(), fixed.splitlines()
changed_lines = set()
for tag, _, _, start, end in difflib.SequenceMatcher(a=old_lines, b=new_lines).get_opcodes():
    if tag != "equal":
        changed_lines.update(range(start + 1, end + 1))
if sys.version_info >= (3, 11):
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
        "fixedMethods": 18,
        "originalFailures": 14 if sys.version_info >= (3, 11) else None,
        "remoteModelCalls": 0,
        "scope": "urllib mocks plus real loopback HTTP; no paid generation or full monorepo suite",
        "formatterScope": MANIFEST["formatterScope"],
    }
)
(EVIDENCE / "summary.json").write_text(json.dumps(SUMMARY, indent=2) + "\n")
shutil.copyfile(PAYLOAD / "manifest.json", EVIDENCE / "manifest.json")
print(json.dumps(SUMMARY, indent=2))
