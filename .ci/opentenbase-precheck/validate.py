"""Compare exact official and proposed precheck code with the same callers."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from urllib.request import urlopen

support = Path(__file__).resolve().parent
source = 'https://raw.githubusercontent.com/OpenTenBase/OpenTenBase/b612d77cbfd4d762f20c54c35f7caf09d57ef098/src/test/opentenbase_test/'
hashes = {'precheck.py':'5efa62e282ade50f6721e7461b78c42460a12ed596a34b060075093774e0f3d6',
          'common.py':'055ef423dfbdf26853b2b4ba773ef8d4f359def54027ad76df23b5bcbaf05dbf'}
summaries = []
with tempfile.TemporaryDirectory(prefix='otb-verify-') as temporary:
    root = Path(temporary)
    for profile in ['original','fixed']:
        target = root/profile
        target.mkdir()
        for name, expected in hashes.items():
            with urlopen(source+name, timeout=30) as response:
                content = response.read()
            assert hashlib.sha256(content).hexdigest() == expected
            (target/name).write_bytes(content)
        if profile == 'fixed':
            shutil.copyfile(support/'precheck.py', target/'precheck.py')
        shutil.copyfile(support/'test_precheck_paths.py', target/'test_precheck_paths.py')
        unit = subprocess.run([sys.executable,'-m','unittest','test_precheck_paths','-v'],cwd=target,capture_output=True,text=True)
        print('UNIT',profile,unit.returncode,flush=True)
        print(unit.stdout+unit.stderr,flush=True)
        assert 'Ran 10 tests' in unit.stdout+unit.stderr
        assert unit.returncode == (1 if profile=='original' else 0)
        if profile=='original':
            assert 'failures=3, errors=2' in unit.stderr
        native = subprocess.run([sys.executable,str(support/'native.py'),str(target)],capture_output=True,text=True)
        print('NATIVE',profile,native.returncode,flush=True)
        print(native.stdout+native.stderr,flush=True)
        result=json.loads(native.stdout.strip().splitlines()[-1])
        assert native.returncode == (1 if profile=='original' else 0)
        assert result['failed'] == (5 if profile=='original' else 0)
        summaries.append({'profile':profile,'unitCases':10,'native':result})
print('FINAL',json.dumps(summaries),flush=True)
