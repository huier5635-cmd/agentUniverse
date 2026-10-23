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
    accepted=expected if isinstance(expected,tuple) else (expected,)
    assert p.returncode in accepted,(name,p.returncode)
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
startup=['cargo','test','--locked','-p','cosh-shell','--test','raw_cli','startup::','--','--test-threads=1']
baseline_startup=run('baseline-startup-controls',startup,101)
failure_names=lambda text:set(re.findall(r'^\s+(startup::[a-zA-Z0-9_]+)\s*$',text,re.M))
expected_regressions={'startup::raw_cli_startup_upgrade_policy_ai_off_blocks_probe',
                      'startup::raw_cli_startup_upgrade_policy_ai_off_preserves_cache_and_rpm_lookup'}
known_zsh={'startup::raw_cli_shell_arg_can_select_zsh_raw_host',
           'startup::raw_cli_startup_banner_reports_selected_zsh_shell',
           'startup::raw_cli_upgrade_notice_skips_the_session_after_zsh_bindkey_typeahead',
           'startup::raw_cli_upgrade_notice_skips_the_zsh_session_after_typed_input'}
baseline_failures=failure_names(baseline_startup)
assert expected_regressions <= baseline_failures,baseline_failures
assert baseline_failures-expected_regressions <= known_zsh,baseline_failures
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
fixed_startup=run('startup-controls',startup,(0,101))
fixed_startup_failures=failure_names(fixed_startup)
assert fixed_startup_failures==baseline_failures-expected_regressions,(baseline_failures,fixed_startup_failures)
assert len(results['startup-controls'])==1 and not results['startup-controls'][0]['ignored']
assert results['startup-controls'][0]['passed']+results['startup-controls'][0]['failed']>=50
run('upgrade-lifecycle',['cargo','test','--locked','-p','cosh-shell','--bin','cosh-shell','upgrade::check::',
                         '--','--test-threads=1'])
assert all(not row['failed'] and not row['ignored'] for row in results['upgrade-lifecycle'])
assert sum(row['passed'] for row in results['upgrade-lifecycle'])>=17
run('layout',['bash','crates/cosh-shell/scripts/check-layout.sh'])
run('clippy',['cargo','clippy','--locked','-p','cosh-shell','--all-targets','--','-D','warnings'])
run('build',['cargo','build','--locked','--bin','cosh-shell'])
run('diff-check',['git','diff','--check'])
for record in records:
    assert digest(SOURCE/record['path'])==record['candidate_sha256']
changed=subprocess.check_output(['git','diff','--name-only'],cwd=SOURCE,text=True).splitlines()
assert sorted(changed)==sorted(record['path'] for record in records),changed
summary={'base':manifest['base'],'tests':results,'files':records,
         'baseline_startup_failures':sorted(baseline_failures),
         'fixed_startup_failures':sorted(fixed_startup_failures),
         'rustc':subprocess.check_output(['rustc','--version'],text=True).strip(),
         'scope':'source startup guard; real PTY with fixture package commands; no installed RPM or runtime cancellation claim'}
(EVIDENCE/'summary.json').write_text(json.dumps(summary,indent=2))
