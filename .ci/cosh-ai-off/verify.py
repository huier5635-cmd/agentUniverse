import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

ROOT=Path.cwd()
DRIVER=ROOT/'.ci/cosh-ai-off'
SOURCE=ROOT/'official'
PROJECT=SOURCE/'src/cosh-ng'
EVIDENCE=ROOT/'evidence'
EVIDENCE.mkdir(exist_ok=True)
manifest=json.loads((DRIVER/'manifest.json').read_text())
source,tests=[record['path'] for record in manifest['files']]
records=manifest['files']
results={}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(name,args,expected=0):
    with (EVIDENCE/(name+'.log')).open('w') as log:
        p=subprocess.run(args,cwd=PROJECT,text=True,stdout=log,stderr=subprocess.STDOUT)
    output=(EVIDENCE/(name+'.log')).read_text(errors='replace')
    print(name,p.returncode,output[-1000:],flush=True)
    assert p.returncode==expected,(name,p.returncode)
    summaries=re.findall(r'test result: (?:ok|FAILED)\. (\d+) passed; (\d+) failed; (\d+) ignored;',output)
    if summaries:
        results[name]=[{'passed':int(a),'failed':int(b),'ignored':int(c)} for a,b,c in summaries]
    return output


for record in records:
    assert digest(SOURCE/record['path'])==record['original_sha256']
assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=SOURCE,text=True).strip()==manifest['base']['sha']
shutil.copyfile(DRIVER/'files'/tests,SOURCE/tests)
run('format-tests',['rustfmt','--edition','2021','--check',str(SOURCE/tests)])
regression=['cargo','test','--locked','-p','cosh-shell','--test','raw_cli','startup::raw_cli_startup_upgrade_policy_',
            '--','--test-threads=4','--nocapture']
red=run('baseline',regression,101)
assert results['baseline']==[{'passed':4,'failed':2,'ignored':0}],results['baseline']
assert 'startup launched the background ownership probe' in red
shutil.copyfile(DRIVER/'files'/source,SOURCE/source)
for record in records:
    assert digest(SOURCE/record['path'])==record['candidate_sha256']
run('format',['cargo','fmt','--all','--','--check'])
run('fixed',regression)
assert results['fixed']==[{'passed':6,'failed':0,'ignored':0}],results['fixed']
candidate=(SOURCE/source).read_text()
needle='if cosh_config.ai_enabled {\n            inline_state\n                .startup_upgrade'
assert candidate.count(needle)==1,'precise startup guard mutation'
try:
    (SOURCE/source).write_text(candidate.replace(needle,'if true {\n            inline_state\n                .startup_upgrade',1))
    mutant=run('guard-removal-mutation',regression,101)
    assert results['guard-removal-mutation']==[{'passed':4,'failed':2,'ignored':0}],results['guard-removal-mutation']
    assert 'startup launched the background ownership probe' in mutant
finally:
    (SOURCE/source).write_text(candidate)
run('startup-controls',['cargo','test','--locked','-p','cosh-shell','--test','raw_cli','startup::',
                        '--','--test-threads=4'])
assert all(not row['failed'] and not row['ignored'] for row in results['startup-controls'])
run('upgrade-lifecycle',['cargo','test','--locked','-p','cosh-shell','--bin','cosh-shell','upgrade::check::',
                         '--','--test-threads=1'])
assert all(not row['failed'] and not row['ignored'] for row in results['upgrade-lifecycle'])
run('layout',['bash','crates/cosh-shell/scripts/check-layout.sh'])
run('clippy',['cargo','clippy','--locked','-p','cosh-shell','--all-targets','--','-D','warnings'])
run('build',['cargo','build','--locked','--bin','cosh-shell'])
run('diff-check',['git','diff','--check'])
for record in records:
    assert digest(SOURCE/record['path'])==record['candidate_sha256']
changed=subprocess.check_output(['git','diff','--name-only'],cwd=SOURCE,text=True).splitlines()
assert sorted(changed)==sorted(record['path'] for record in records),changed
summary={'base':manifest['base'],'tests':results,'files':records,
         'rustc':subprocess.check_output(['rustc','--version'],text=True).strip(),
         'scope':'source startup guard; real PTY with fixture package commands; no installed RPM or runtime cancellation claim'}
(EVIDENCE/'summary.json').write_text(json.dumps(summary,indent=2))
