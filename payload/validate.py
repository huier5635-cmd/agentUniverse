from pathlib import Path
import subprocess
import sys
import shutil

source=Path.cwd()
payload=source.parent/"payload"
test="tests/test_mnist_device_placement.py"
trainer="example/qvae_mnist/trainer/trainer.py"
shutil.copyfile(payload / test, source / test)
cmd=[sys.executable,"-m","pytest","-q",test,"-k","empty_test_dataset_during_setup"]
before=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
print(before.stdout,flush=True)
assert before.returncode==1 and "DID NOT RAISE" in before.stdout and "1 failed" in before.stdout
shutil.copyfile(payload / trainer, source / trainer)
after=subprocess.run([sys.executable,"-m","pytest","-q",test],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
print(after.stdout,flush=True)
assert after.returncode==0 and "passed" in after.stdout
