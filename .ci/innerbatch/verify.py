import hashlib,json,shutil,subprocess,xml.etree.ElementTree as ET
from pathlib import Path
here=Path(__file__).resolve().parent
source=Path('source').resolve()
evidence=here/'evidence'
evidence.mkdir()
manifest=json.loads((here/'manifest.json').read_text())
assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=source,text=True).strip()==manifest['base']
for relative,expected in manifest['baseline'].items():
 assert hashlib.sha256((source/relative).read_bytes()).hexdigest()==expected['sha256']
def run(name,args):
 with (evidence/(name+'.txt')).open('w') as output:
  result=subprocess.run(['mvn','-B','-ntp','-Dstyle.color=never']+args,cwd=source,stdout=output,stderr=subprocess.STDOUT)
 print(name+': '+str(result.returncode),flush=True)
 if result.returncode:
  print('\n'.join((evidence/(name+'.txt')).read_text(errors='replace').splitlines()[-45:]),flush=True)
 return result.returncode
def reports(label,module):
 result=[]
 dest=evidence/label
 dest.mkdir(exist_ok=True)
 for file in (source/module/'target/surefire-reports').glob('TEST-*.xml'):
  shutil.copyfile(file,dest/file.name)
  xml=ET.parse(file).getroot()
  for case in xml.iter('testcase'):
   error=case.find('failure')
   if error is None: error=case.find('error')
   result.append({'name':case.get('name'),'class':case.get('classname'),
                  'failed':error is not None,'skipped':case.find('skipped') is not None,
                  'errorType':error.get('type') if error is not None else None,
                  'error':(error.text or '') if error is not None else None})
 return result
def apply(name):
 subprocess.run(['git','apply','--check','--whitespace=error',str(here/name)],cwd=source,check=True)
 subprocess.run(['git','apply','--whitespace=error',str(here/name)],cwd=source,check=True)
assert run('original-reactor-build',['-pl','broker','-am','-DskipTests','install'])==0
apply('tests.patch')
common=run('baseline-common',['-pl','common','-Dtest=MessageDecoderTest','test'])
common_reports=reports('baseline-common-reports','common')
assert common!=0
zero=next(x for x in common_reports if x['name']=='testCountInnerMsgNumRejectsZeroLength')
assert zero['failed'] and ('timed out' in (zero['error'] or '') or 'TestTimedOutException' in (zero['errorType'] or ''))
broker=run('baseline-broker',['-pl','broker','-Dtest=SendMessageProcessorTest','test'])
broker_reports=reports('baseline-broker-reports','broker')
assert broker!=0
zero=next(x for x in broker_reports if x['name']=='testInnerBatchRejectsZeroLength')
assert zero['failed'] and ('timed out' in (zero['error'] or '') or 'TestTimedOutException' in (zero['errorType'] or ''))
subprocess.run(['git','reset','--hard',manifest['base']],cwd=source,check=True,capture_output=True)
apply('complete.patch')
for relative,expected in manifest['fixed'].items():
 assert hashlib.sha256((source/relative).read_bytes()).hexdigest()==expected['sha256']
assert run('fixed-reactor-related-tests',['-pl','broker','-am','-Dtest=MessageDecoderTest,SendMessageProcessorTest',
                                        '-Dsurefire.failIfNoSpecifiedTests=false','test'])==0
fixed_common=reports('fixed-common-reports','common')
fixed_broker=reports('fixed-broker-reports','broker')
assert all(not x['failed'] and not x['skipped'] for x in fixed_common+fixed_broker)
assert run('fixed-common-complete-suite',['-pl','common','test'])==0
all_common=reports('fixed-complete-common-reports','common')
assert all(not x['failed'] for x in all_common)
subprocess.run(['git','diff','--check'],cwd=source,check=True)
assert set(subprocess.check_output(['git','diff','--name-only'],cwd=source,text=True).splitlines())==set(manifest['fixed'])
for relative,expected in manifest['fixed'].items():
 assert hashlib.sha256((source/relative).read_bytes()).hexdigest()==expected['sha256']
summary={'base':manifest['base'],'baselineCommonFailures':[x['name'] for x in common_reports if x['failed']],
         'baselineBrokerFailures':[x['name'] for x in broker_reports if x['failed']],
         'fixedRelatedTests':len(fixed_common)+len(fixed_broker),'fixedRelatedSkipped':0,
         'fixedFullCommonTests':len(all_common),'fixedFullCommonSkipped':sum(x['skipped'] for x in all_common),
         'fixedSourceHashes':manifest['fixed'],
         'scope':'Original reactor build/install; original zero-length timeouts in real common and broker code; fixed complete two related classes and full common suite, Maven default checkstyle. Not a full broker test suite or live broker deployment.'}
(evidence/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(summary),flush=True)
