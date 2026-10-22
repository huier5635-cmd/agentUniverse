import hashlib,json,os,subprocess,sys
from pathlib import Path
here=Path(__file__).resolve().parent
repo=Path('source').resolve()
manifest=json.loads((here/'manifest.json').read_text())
evidence=here/'evidence'
evidence.mkdir(exist_ok=True)
cache=Path(os.environ['RUNNER_TEMP'])/'huatuo-refresh-cache'
cache.mkdir(exist_ok=True)
summary={}
specs={
 '1377':(['./internal/storage/driver','./internal/storage/sqlite','./internal/storage/elasticsearch'],
          'TestNormalizeFilterJSONNumbers|TestSQLiteReadBackNumericPredicates|TestBuildClauseComparisons|TestBuildClauseRangePrecision'),
 '1379':(['./internal/storage/elasticsearch'],'^TestStorageTerms'),
 '1380':(['./internal/profiling/formatter/...','./internal/profiling/window'],'^TestFormatterFractional')}
for n,identity in manifest['candidates'].items():
 source=repo.parent/('validation-'+n)
 subprocess.run(['git','clone','--no-hardlinks',str(repo),str(source)],check=True,capture_output=True)
 assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=source,text=True).strip()==manifest['base']
 for relative,expected in identity['baseline'].items():
  assert hashlib.sha256((source/relative).read_bytes()).hexdigest()==expected
 case=evidence/n
 case.mkdir()
 docker=['docker','run','--rm','--network','host','-v',str(source)+':/workspace',
         '-v',str(cache/'build')+':/root/.cache/go-build',
         '-v',str(cache/'modules')+':/go/pkg/mod','-w','/workspace',
         '-e','GOMAXPROCS=2','-e','GOFLAGS=-p=2','-e','GOPROXY=https://proxy.golang.org,direct',
         '-e','HUATUO_TERMS_TEST_OPENSEARCH_URL=http://127.0.0.1:9200',
         'huatuo-three-refresh','sh','-ec']
 def run(label,command):
  with (case/(label+'.txt')).open('w') as output:
   result=subprocess.run(docker+['git config --global --add safe.directory /workspace; '+command],stdout=output,stderr=subprocess.STDOUT)
  print(n+' '+label+': '+str(result.returncode),flush=True)
  if result.returncode:
   lines=(case/(label+'.txt')).read_text(errors='replace').splitlines()
   print('\n'.join(lines[-35:]),flush=True)
  return result.returncode
 def apply(name):
  subprocess.run(['git','apply','--check','--whitespace=error',str(here/name)],cwd=source,check=True)
  subprocess.run(['git','apply','--whitespace=error',str(here/name)],cwd=source,check=True)
 assert run('generation','make gen-build')==0
 apply(n+'-tests.patch')
 packages,selection=specs[n]
 focused='go test -mod=vendor -count=1 -json -run '+"'"+selection+"' "+' '.join(packages)
 baseline=run('baseline',focused)
 events=[]
 for line in (case/'baseline.txt').read_text().splitlines():
  if line.startswith('{'):
   try: events.append(json.loads(line))
   except json.JSONDecodeError: pass
 failures=[e for e in events if e.get('Action')=='fail' and e.get('Test')]
 assert baseline==1 and failures,(n,'baseline must show behavioral test failures')
 if n=='1380':
  assert run('baseline-benchmark',"go test -mod=vendor -run '^$' -bench '^BenchmarkSummaryFormatter/chrometrace' -benchtime=1x -count=3 -benchmem ./internal/profiling/formatter")==0
 subprocess.run(['git','reset','--hard',manifest['base']],cwd=source,check=True,capture_output=True)
 apply(n+'.patch')
 for relative,expected in identity['files'].items():
  assert hashlib.sha256((source/relative).read_bytes()).hexdigest()==expected['sha256']
 subprocess.run(['git','add','--']+list(identity['files']),cwd=source,check=True)
 assert run('fixed-focused',focused)==0
 assert run('fixed-packages','go test -mod=vendor -count=1 -json '+' '.join(packages))==0
 assert run('fixed-race','go test -mod=vendor -race -count=1 '+' '.join(packages))==0
 assert run('fixed-vet','go vet -mod=vendor '+' '.join(packages))==0
 if n=='1380':
  assert run('fixed-benchmark',"go test -mod=vendor -run '^$' -bench '^BenchmarkSummaryFormatter/chrometrace' -benchtime=1x -count=3 -benchmem ./internal/profiling/formatter")==0
 assert run('build-and-required-check','make build; make check')==0
 for relative,expected in identity['files'].items():
  assert hashlib.sha256((source/relative).read_bytes()).hexdigest()==expected['sha256']
 subprocess.run(['git','diff','--check'],cwd=source,check=True)
 assert not subprocess.check_output(['git','diff','--name-only'],cwd=source,text=True).strip()
 actual_tree=subprocess.check_output(['git','write-tree'],cwd=source,text=True).strip()
 summary[n]={'baselineBehavioralFailures':[x['Test'] for x in failures],
             'fixedFocusedExit':0,'fixedPackagesExit':0,'raceExit':0,'vetExit':0,
             'makeBuildAndCheckExit':0,'tree':actual_tree,'sourceHashes':identity['files']}
 (evidence/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
 print(n+' verified tree '+actual_tree,flush=True)
