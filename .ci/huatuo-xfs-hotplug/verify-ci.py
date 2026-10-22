import json
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
expected_new = {
    'TestXFSUpdateNewMount',
    'TestXFSUpdateRemountReloadsLogSize',
    'TestXFSUpdateDeviceNodeBecomesAvailable',
    'TestXFSUpdateConcurrentNewMount',
}
expected_old = {
    'TestXFSUpdate', 'TestXFSUpdateSnapshotIsolation',
    'TestXFSUpdateUnmountedDevices', 'TestXFSUpdateErrors',
    'TestXFSUpdateFullLog', 'TestXFSNoDevices', 'TestXFSLogFreeBytes',
}


def events(name):
    result = {}
    for line in (root / name).read_text().splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        test = event.get('Test')
        if test and '/' not in test and event.get('Action') in {'pass', 'fail', 'skip'}:
            result[test] = event['Action']
    return result


def expect(name, passed, failed, skipped):
    actual = events(name)
    expected = {**dict.fromkeys(passed, 'pass'), **dict.fromkeys(failed, 'fail'),
                **dict.fromkeys(skipped, 'skip')}
    assert actual == expected, (name, actual, expected)
    return actual


def full_unit(name):
    text = (root / name).read_text()
    return {
        'tests': sorted(set(re.findall(r'^--- FAIL: (\S+)', text, re.M))),
        'packages': sorted(set(re.findall(r'^FAIL\s+(github\.com/ccfos/huatuo/\S+)', text, re.M))),
    }


mode = sys.argv[2]
if mode == 'baseline':
    result = expect('baseline-focused.jsonl', expected_old, expected_new,
                    {'TestXFSHotplugIntegration'})
    print(json.dumps({'baseline_focused_parent_tests': result}, sort_keys=True))
elif mode == 'baseline-live':
    result = expect('baseline-live.jsonl', set(), {'TestXFSHotplugIntegration'}, set())
    print(json.dumps({'baseline_real_mount': result}, sort_keys=True))
elif mode == 'fixed':
    result = expect('fixed-focused.jsonl', expected_old | expected_new, set(),
                    {'TestXFSHotplugIntegration'})
    live = expect('fixed-live.jsonl', {'TestXFSHotplugIntegration'}, set(), set())
    before, after = full_unit('baseline-full-unit.log'), full_unit('fixed-full-unit.log')
    assert before == after, ('full repository failure set changed', before, after)
    statuses = json.loads((root / 'full-unit-exits.json').read_text())
    assert statuses['baseline'] == statuses['fixed'], statuses
    if statuses['fixed']:
        assert before['tests'] or before['packages'], 'Unclassified full-unit failure'
    result = {
        'fixed_focused_parent_tests': result,
        'fixed_real_mount': live,
        'full_unit': {'statuses': statuses, 'existing_failures': before},
        'limitations': [
            'Filesystem fixtures are distinct from the opt-in real Linux loop-device test.',
            'Legacy log gauge is unavailable on kernels with only the 6.11+ grant ABI; #1243 is separate.',
            'Polling cannot detect every removal/replacement entirely between scrapes.',
        ],
    }
    (root / 'verified-results.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, sort_keys=True))
else:
    raise SystemExit('unknown verification phase')
