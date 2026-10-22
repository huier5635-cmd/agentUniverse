import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

workspace = Path.cwd()
source = workspace / 'native-source'
evidence = workspace / '.ci/huatuo-xfs-hotplug'
logs = workspace / 'xfs-native-logs'
logs.mkdir(exist_ok=True)
manifest = json.loads((evidence / 'validation-manifest.json').read_text())
env = os.environ.copy()
env['PATH'] = '/usr/lib/llvm-15/bin:' + env['PATH']
env['GOMAXPROCS'] = '2'
gopath = subprocess.check_output(['go', 'env', 'GOPATH'], text=True).strip()
gocache = subprocess.check_output(['go', 'env', 'GOCACHE'], text=True).strip()
privileged = ['sudo', 'env', 'PATH=' + env['PATH'], 'GOPATH=' + gopath,
              'GOCACHE=' + gocache, 'GOMAXPROCS=2']


def run(command, name, cwd=source):
    with (logs / name).open('w') as log:
        result = subprocess.run(command, cwd=cwd, env=env, stdout=log,
                                stderr=subprocess.STDOUT)
    if result.returncode:
        print((logs / name).read_text()[-10000:])
        raise SystemExit(f'{name} failed: {result.returncode}')


def verify(section):
    for path, expected in manifest[section].items():
        assert hashlib.sha256((source / path).read_bytes()).hexdigest() == expected, path


verify('original')
head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip()
assert head == manifest['base'], head
run(['make', 'gen-build'], 'native-generation.log')
run(privileged + ['git', 'config', '--global', '--add', 'safe.directory', str(source)], 'safe-directory.log')
run(privileged + ['make', 'unit'], 'baseline-full-unit.log')
for name in ['regression-only.patch', 'production.patch', 'test-cache-migration.patch']:
    path = evidence / name
    assert hashlib.sha256(path.read_bytes()).hexdigest() == manifest['patches'][name]
    subprocess.run(['git', 'apply', '--check', str(path)], cwd=source, check=True)
    subprocess.run(['git', 'apply', str(path)], cwd=source, check=True)
verify('fixed')
run(privileged + ['make', 'unit'], 'fixed-full-unit.log')
verify('fixed')
summary = {'base': head, 'baseline_full_unit_exit': 0, 'fixed_full_unit_exit': 0}
for variant in ['baseline', 'fixed']:
    text = (logs / (variant + '-full-unit.log')).read_text()
    events = re.findall(r'^--- (PASS|SKIP|FAIL): (\S+)', text, re.M)
    summary[variant] = {
        'passed_parent_tests': sum(action == 'PASS' for action, _ in events),
        'skipped_parent_tests': [name for action, name in events if action == 'SKIP'],
        'failed_parent_tests': [name for action, name in events if action == 'FAIL'],
        'passing_packages': len(re.findall(r'^ok\s+github\.com/ccfos/huatuo', text, re.M)),
    }
    assert not summary[variant]['failed_parent_tests']
(logs / 'verified-native-unit.json').write_text(json.dumps(summary, indent=2) + '\n')
print(json.dumps(summary, sort_keys=True))
