from pathlib import Path
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys

root = Path(__file__).resolve().parents[2]
here = Path(__file__).resolve().parent
manifest = json.loads((here / 'manifest.json').read_text(encoding='utf-8'))
for entry in manifest:
    payload = (root / entry['path']).read_bytes()
    assert hashlib.sha256(payload).hexdigest() == entry['sha256'], entry['path']
    assert hashlib.sha1(f'blob {len(payload)}\0'.encode() + payload).hexdigest() == entry['gitBlob']
assert shutil.which('diff'), 'real diff is required; do not accept skipped cases'
print('Python:', sys.version, 'Platform:', platform.platform(), flush=True)
print(subprocess.check_output(['diff', '--version'], text=True).splitlines()[0], flush=True)
child = r'''
import json,sys,unittest,warnings
from pathlib import Path
sys.path.insert(0,sys.argv[1])
warnings.simplefilter('default',ResourceWarning)
suite=unittest.defaultTestLoader.discover(sys.argv[1],pattern='test_resultcompare*.py')
result=unittest.TextTestRunner(verbosity=2).run(suite)
data={'run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),
      'failureIDs':[t.id() for t,_ in result.failures],'errorIDs':[t.id() for t,_ in result.errors]}
Path(sys.argv[2]).write_text(json.dumps(data,indent=2),encoding='utf-8')
raise SystemExit(not result.wasSuccessful())
'''
summaries = {}
for phase, count in [('baseline', 11), ('fixed', 11), ('composed', 18)]:
    summary = here / (phase + '-summary.json')
    r = subprocess.run([sys.executable, '-c', child, str(here / phase), str(summary)],
                       capture_output=True, text=True)
    log = r.stdout + '\n' + r.stderr
    (here / (phase + '.log')).write_text(log, encoding='utf-8')
    print('\nPHASE:', phase, '\n' + log, flush=True)
    data = json.loads(summary.read_text(encoding='utf-8'))
    assert data['run'] == count and data['skipped'] == 0, data
    if phase == 'baseline':
        assert r.returncode == 1 and data['failures'] >= 1
        assert all(x.endswith('test_regex_mismatch_with_spaced_report_keeps_the_difference') for x in data['errorIDs'])
    else:
        assert r.returncode == 0 and data['errors'] == data['failures'] == 0, data
    summaries[phase] = data
print('RESULT_SUMMARY=' + json.dumps(summaries, sort_keys=True), flush=True)
