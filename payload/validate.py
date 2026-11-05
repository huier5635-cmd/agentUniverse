import hashlib
import json
import pathlib
import re
import subprocess

root = pathlib.Path.cwd()
workspace = root / "source/src/cosh-ng"
target = workspace / "crates/cosh-shell/src/ui/wrap.rs"
payload = root / "payload"
evidence = root / "evidence"
evidence.mkdir(exist_ok=True)

def run(name, args, expected=0):
    result = subprocess.run(args, cwd=workspace, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (evidence / (name + ".log")).write_text(result.stdout)
    print(result.stdout, flush=True)
    assert result.returncode == expected, (name, result.returncode)
    return result.stdout

baseline = target.read_text()
assert baseline == (payload / "original-wrap.rs").read_text(), "Pinned baseline differs from inspected file"
candidate = (payload / "wrap.rs").read_text()
target.write_text(candidate)
run("format", ["cargo", "fmt", "--all"])
formatted = target.read_text()
(evidence / "formatted-wrap.rs").write_text(formatted)
run("fmt-check", ["cargo", "fmt", "--all", "--", "--check"])

pattern = r"pub\(super\) fn strip_ansi_escape\(text: &str\) -> String \{.*?\n\}\n"
old = re.search(pattern, baseline, re.S)
assert old
negative = re.sub(pattern, lambda _: old.group(), formatted, count=1, flags=re.S)
assert negative != formatted
target.write_text(negative)
control = run("baseline-regressions", ["cargo", "test", "--locked", "-p", "cosh-shell", "--lib", "ui::wrap::tests", "--", "--nocapture"], expected=101)
assert "could not compile" not in control
assert "7 failed" in control and "1 passed" in control, "Baseline must fail all seven OSC tests and pass the compatibility control"
target.write_text(formatted)
focused = run("candidate-regressions", ["cargo", "test", "--locked", "-p", "cosh-shell", "--lib", "ui::wrap::tests", "--", "--nocapture"])
assert "8 passed" in focused and "0 failed" in focused
ui = run("ui-tests", ["cargo", "test", "--locked", "-p", "cosh-shell", "--lib", "ui::"])
run("clippy", ["cargo", "clippy", "--locked", "-p", "cosh-shell", "--all-targets", "--", "-D", "warnings"])
run("fmt-final", ["cargo", "fmt", "--all", "--", "--check"])
assert target.read_text() == formatted
summary = {
    "base": "b9516bedad144591970f0cb9b348ed1620806ee6",
    "platform": "ubuntu-24.04",
    "baseline": {"passed": 1, "failed": 7},
    "candidate": {"passed": 8, "failed": 0},
    "uiTestSummary": re.findall(r"test result:.*", ui),
    "clippy": "passed --all-targets -D warnings",
    "fmt": "passed --all",
    "formattedSha256": hashlib.sha256(formatted.encode()).hexdigest(),
    "fullWorkspaceSuiteRun": False,
    "realProviderOrTuiRun": False,
}
(evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
print(json.dumps(summary), flush=True)
