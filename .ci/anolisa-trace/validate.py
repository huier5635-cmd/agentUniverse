import hashlib
import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT=Path.cwd()
DRIVER=ROOT/'.ci/anolisa-trace'
SOURCE=ROOT/'official'
PROJECT=SOURCE/'benchmark/swe-runner'
EVIDENCE=ROOT/'evidence'
EVIDENCE.mkdir(exist_ok=True)
manifest=json.loads((DRIVER/'manifest.json').read_text())
env=dict(os.environ,PYTHONPATH=str(PROJECT/'src'))
source,tests=[record['path'] for record in manifest['files']]
for record in manifest['files']:
    path=SOURCE/record['path']
    if record['original_sha256'] is None:
        assert not path.exists()
    else:
        assert hashlib.sha256(path.read_bytes()).hexdigest()==record['original_sha256']
assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=SOURCE,text=True).strip()==manifest['base']['sha']
shutil.copyfile(DRIVER/'files'/tests,SOURCE/tests)


def run(name,args,expected=0,cwd=PROJECT):
    p=subprocess.run(args,cwd=cwd,env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    (EVIDENCE/(name+'.log')).write_text(p.stdout)
    print(name,p.returncode,p.stdout[-500:])
    assert p.returncode==expected,(name,p.returncode)


def counts(path):
    root=ET.parse(path).getroot()
    return {'tests':len(list(root.iter('testcase'))),'failures':len(list(root.iter('failure'))),
            'errors':len(list(root.iter('error'))),'skipped':len(list(root.iter('skipped')))}


py=sys.executable
target='tests/unit/trace_extraction/test_openclaw_jsonl_encoding.py'
run('baseline',[py,'-m','pytest',target,'-q','--tb=short','--junitxml='+str(EVIDENCE/'baseline.xml')],1)
baseline=counts(EVIDENCE/'baseline.xml')
assert baseline=={'tests':8,'failures':4,'errors':0,'skipped':0},baseline
assert all('UnicodeDecodeError' in failure.text for failure in ET.parse(EVIDENCE/'baseline.xml').getroot().iter('failure'))
shutil.copyfile(DRIVER/'files'/source,SOURCE/source)
for record in manifest['files']:
    assert hashlib.sha256((SOURCE/record['path']).read_bytes()).hexdigest()==record['candidate_sha256']
run('lint',[py,'-m','ruff','check',source.removeprefix('benchmark/swe-runner/'),target])
run('format',[py,'-m','ruff','format','--check',source.removeprefix('benchmark/swe-runner/'),target])
run('focused',[py,'-m','pytest',target,'-q','--tb=short','--junitxml='+str(EVIDENCE/'focused.xml')])
focused=counts(EVIDENCE/'focused.xml')
assert focused=={'tests':8,'failures':0,'errors':0,'skipped':0},focused
run('full',[py,'-m','pytest','tests','-q','--tb=short','--junitxml='+str(EVIDENCE/'full.xml')])
full=counts(EVIDENCE/'full.xml')
assert full['tests']>=300 and not any(full[k] for k in ('failures','errors','skipped')),full
run('diff-check',['git','diff','--check'],cwd=SOURCE)
(EVIDENCE/'summary.json').write_text(json.dumps({'base':manifest['base']['sha'],'baseline':baseline,'focused':focused,
                                             'full':full,'files':manifest['files'],'python':sys.version,'platform':sys.platform},indent=2))
(EVIDENCE/'dependencies.txt').write_text(subprocess.check_output([py,'-m','pip','freeze'],text=True))
