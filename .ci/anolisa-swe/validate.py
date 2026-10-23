import hashlib
import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path.cwd()
DRIVER = ROOT / '.ci/anolisa-swe'
SOURCE = ROOT / 'official'
PROJECT = SOURCE / 'benchmark/swe-runner'
EVIDENCE = ROOT / 'evidence'
EVIDENCE.mkdir(exist_ok=True)
manifest = json.loads((DRIVER / 'manifest.json').read_text())
env = dict(os.environ, PYTHONPATH=str(PROJECT / 'src'))
adapter, tests = [record['path'] for record in manifest['files']]
for record in manifest['files']:
    assert hashlib.sha256((SOURCE / record['path']).read_bytes()).hexdigest() == record['original_sha256']
assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=SOURCE,text=True).strip() == manifest['base']['sha']
shutil.copyfile(DRIVER / 'files' / tests, SOURCE / tests)


def run(name, args, expected=0, cwd=PROJECT):
    result = subprocess.run(args,cwd=cwd,env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    (EVIDENCE / (name+'.log')).write_text(result.stdout)
    print(name, result.returncode, result.stdout[-400:])
    if result.returncode != expected:
        raise AssertionError(f'{name}: {result.returncode} != {expected}')


def counts(path):
    suite=ET.parse(path).getroot()
    cases=list(suite.iter('testcase'))
    return {'tests':len(cases),'failures':len(list(suite.iter('failure'))),
            'errors':len(list(suite.iter('error'))),'skipped':len(list(suite.iter('skipped')))}


py=sys.executable
target='tests/unit/agents/test_cosh_adapter.py'
run('baseline',[py,'-m','pytest',target,'-q','--tb=short','--junitxml='+str(EVIDENCE/'baseline.xml')],expected=1)
baseline=counts(EVIDENCE/'baseline.xml')
assert baseline == {'tests':16,'failures':6,'errors':0,'skipped':0}, baseline
failures=list(ET.parse(EVIDENCE/'baseline.xml').getroot().iter('failure'))
assert all("Expected 'cleanup' to be called once. Called 0 times." in failure.text for failure in failures)
run('docker-baseline',[py,str(DRIVER/'docker_probe.py'),'baseline'])
shutil.copyfile(DRIVER / 'files' / adapter, SOURCE / adapter)
for record in manifest['files']:
    assert hashlib.sha256((SOURCE / record['path']).read_bytes()).hexdigest() == record['candidate_sha256']
run('lint',[py,'-m','ruff','check','src/swe_runner/agents/cosh/adapter.py',target])
run('format',[py,'-m','ruff','format','--check','src/swe_runner/agents/cosh/adapter.py',target])
run('fixed',[py,'-m','pytest',target,'-q','--tb=short','--junitxml='+str(EVIDENCE/'fixed.xml')])
fixed=counts(EVIDENCE/'fixed.xml')
assert fixed == {'tests':16,'failures':0,'errors':0,'skipped':0}, fixed
related_paths=[target,'tests/unit/agents/test_agent_protocol.py','tests/unit/run/test_docker_manager.py',
               'tests/unit/run/test_prompt_builder.py','tests/unit/run/test_git.py',
               'tests/unit/common/test_models.py','tests/unit/run/test_run_artifacts.py']
run('related',[py,'-m','pytest',*related_paths,'-q','--tb=short','--junitxml='+str(EVIDENCE/'related.xml')])
related=counts(EVIDENCE/'related.xml')
assert related['tests'] >= 91 and not any(related[k] for k in ('failures','errors','skipped')), related
run('docker-fixed',[py,str(DRIVER/'docker_probe.py'),'fixed'])
run('diff-check',['git','diff','--check'],cwd=SOURCE)
summary={'base':manifest['base']['sha'],'baseline':baseline,'fixed':fixed,'related':related,
         'real_docker':{'baseline_failure_leaks':True,'fixed_failure_cleans':True,'success_ownership_preserved':True},
         'files':manifest['files'],'python':sys.version,'platform':sys.platform}
(EVIDENCE/'summary.json').write_text(json.dumps(summary,indent=2))
(EVIDENCE/'dependencies.txt').write_text(subprocess.check_output([py,'-m','pip','freeze'],text=True))
print(json.dumps(summary))
