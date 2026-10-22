import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys

here = Path(__file__).resolve().parent
source = Path(sys.argv[1]).resolve()
manifest = json.loads((here / 'manifest.json').read_text())
evidence = here / 'evidence'
evidence.mkdir(exist_ok=True)
environment = dict(os.environ, GOMAXPROCS='2')

def run(arguments, name, expected=0, timeout=180):
    result = subprocess.run(arguments, cwd=source, env=environment,
                            capture_output=True, text=True, timeout=timeout)
    (evidence / (name + '.stdout')).write_text(result.stdout)
    (evidence / (name + '.stderr')).write_text(result.stderr)
    assert result.returncode == expected, (name, result.returncode, result.stdout[-5000:], result.stderr[-5000:])
    return result.stdout

assert run(['git', 'rev-parse', 'HEAD'], 'upstream-head').strip() == manifest['base']
for relative, expected in manifest['baseline'].items():
    data = (source / relative).read_bytes()
    assert hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest() == expected, relative
for relative, expected in manifest['fixed'].items():
    data = (here / 'fixed' / relative).read_bytes()
    assert hashlib.sha256(data).hexdigest() == expected, relative

test_path = 'internal/process/process_test.go'
shutil.copyfile(here / 'fixed' / test_path, source / test_path)

def tests(name, expected, full=False):
    command = ['go', 'test', '-p=2', '-mod=vendor', '-json', '-count=1']
    if full:
        command.append('-race')
    else:
        command += ['-run', '^TestCommandLine']
    output = run(command + ['./internal/process'], name, expected=expected)
    events = [json.loads(line) for line in output.splitlines() if line.startswith('{')]
    terminal = {e['Test']: e['Action'] for e in events
                if e.get('Test') and e['Action'] in ['pass', 'fail', 'skip']}
    selected = {key: value for key, value in terminal.items() if key.startswith('TestCommandLine')}
    leaves = {key: value for key, value in selected.items()
              if not any(other.startswith(key + '/') for other in selected)}
    failures = [key for key, value in leaves.items() if value == 'fail']
    assert len(leaves) == 10 and not any(value == 'skip' for value in leaves.values()), leaves
    assert failures == (['TestCommandLineStopsAtPrefix'] if expected else []), failures
    report = {'commandLineLeaves': leaves,
              'allPackageSkips': [key for key, value in terminal.items() if value == 'skip'],
              'allPackageFailures': [key for key, value in terminal.items() if value == 'fail']}
    (evidence / (name + '-summary.json')).write_text(json.dumps(report, indent=2))
    print(name + '_SUMMARY=' + json.dumps(report), flush=True)
    return report

def benchmark(name):
    output = run(['go', 'test', '-p=2', '-mod=vendor', '-run', '^$',
                  '-bench', '^BenchmarkCommandLinePrefix$', '-benchtime=100x',
                  '-count=3', '-benchmem', './internal/process'], name)
    rows = []
    for line in output.splitlines():
        match = re.match(r'BenchmarkCommandLinePrefix/(short|large)-\d+\s+\d+\s+([\d.]+) ns/op\s+(\d+) B/op\s+(\d+) allocs/op', line)
        if match:
            rows.append({'size': match[1], 'ns': float(match[2]),
                         'bytes': int(match[3]), 'allocs': int(match[4])})
    assert len(rows) == 6, output
    print(name + '_RESULT=' + json.dumps(rows), flush=True)
    return rows

baseline_tests = tests('baseline', 1)
baseline_benchmark = benchmark('baseline-benchmark')
for relative in manifest['fixed']:
    shutil.copyfile(here / 'fixed' / relative, source / relative)
fixed_tests = tests('fixed', 0, full=True)
fixed_benchmark = benchmark('fixed-benchmark')
run(['go', 'vet', '-p=2', '-mod=vendor', './internal/process'], 'vet')
run(['git', 'diff', '--check'], 'whitespace')
changed = set(run(['git', 'diff', '--name-only'], 'changed-files').splitlines())
assert changed == set(manifest['fixed']), changed
before = statistics.median(row['bytes'] for row in baseline_benchmark if row['size'] == 'large')
after = statistics.median(row['bytes'] for row in fixed_benchmark if row['size'] == 'large')
assert after < 16384 and after < before / 10, (before, after)
summary = {'base': manifest['base'], 'baseline': baseline_tests, 'fixed': fixed_tests,
           'baselineBenchmarks': baseline_benchmark, 'fixedBenchmarks': fixed_benchmark,
           'largeArgvAllocatedBytesBefore': before, 'largeArgvAllocatedBytesAfter': after,
           'fixedSHA256': manifest['fixed']}
(evidence / 'summary.json').write_text(json.dumps(summary, indent=2))
print('RESULT_SUMMARY=' + json.dumps(summary), flush=True)
