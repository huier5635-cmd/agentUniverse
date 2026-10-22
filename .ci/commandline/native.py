import hashlib,json,os,shutil,subprocess,sys
from pathlib import Path
here=Path(__file__).resolve().parent
source=Path('source').resolve()
evidence=here/'evidence'
evidence.mkdir(exist_ok=True)
manifest=json.loads((here/'manifest.json').read_text())
assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=source,text=True).strip()==manifest['base']
for relative,expected in manifest['baseline'].items():
    data=(source/relative).read_bytes()
    assert hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()==expected
def run(name,cmd):
    with (evidence/(name+'.txt')).open('w') as log:
        result=subprocess.run(cmd,cwd=source,stdout=log,stderr=subprocess.STDOUT)
    print(name+': '+str(result.returncode),flush=True)
    return result.returncode
generation=run('native-generation',['make','gen-build'])
assert generation==0,'native generation failed'
rootenv=['sudo','env','PATH='+os.environ['PATH'],'GOPATH='+subprocess.check_output(['go','env','GOPATH'],text=True).strip(),
         'GOCACHE='+str(here/'root-go-cache'),'GOMAXPROCS=2',
         'GIT_CONFIG_COUNT=1','GIT_CONFIG_KEY_0=safe.directory',
         'GIT_CONFIG_VALUE_0='+str(source)]
packages=['./internal/bpf','./internal/cgroups','./internal/pcapfilter']
baseline=run('baseline-privileged-packages',rootenv+['go','test','-json','-count=1','-timeout=5m']+packages)
for relative,expected in manifest['fixed'].items():
    data=(here/'fixed'/relative).read_bytes()
    assert hashlib.sha256(data).hexdigest()==expected
    shutil.copyfile(here/'fixed'/relative,source/relative)
fixed=run('fixed-full-make-unit',rootenv+['make','unit'])
summary={'base':manifest['base'],'sourceHashes':manifest['fixed'],
         'nativeGenerationExit':generation,'baselineThreePrivilegedPackagesExit':baseline,
         'fixedFullMakeUnitExit':fixed}
(evidence/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(summary),flush=True)
sys.exit(fixed)
