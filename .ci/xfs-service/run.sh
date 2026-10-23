#!/usr/bin/env bash
set -euo pipefail
cd /workspace
git config --global --add safe.directory /workspace
apk add --no-cache python3 util-linux xfsprogs bash coreutils kmod
mkdir -p /logs
go version > /logs/environment.txt
uname -a >> /logs/environment.txt
git rev-parse HEAD >> /logs/environment.txt
test "$(git rev-parse HEAD)" = f1b141432275429cb094ce6e5381dc0ec7703225
test "$(git rev-parse HEAD^)" = bdcee2d10d872c9c693a87994cf1f63f9b4340dd
git apply --check --whitespace=error /evidence/review.patch
git apply --whitespace=error /evidence/review.patch
chmod +x integration/test_metrics_xfs_hotplug.sh
bash -n integration/test_metrics_xfs_hotplug.sh
shfmt -i 0 -bn -sr -w integration/test_metrics_xfs_hotplug.sh
cp integration/test_metrics_xfs_hotplug.sh /logs/formatted-test_metrics_xfs_hotplug.sh
python3 - <<'PY'
import hashlib,json
from pathlib import Path
m=json.loads(Path('/evidence/manifest.json').read_text())
for path,h in m['files'].items():
 assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==h['sha256'], path
print('Exact revision sources verified after shfmt')
PY

check_cleanup() {
	if losetup -ln -O NAME,BACK-FILE | grep -E '/tmp/huatuo-test\.[^/]+/[^/]+/xfs-hotplug.img'; then
		echo 'Private loop image remains attached' >&2
		return 1
	fi
	printf 'CLEANUP VERIFIED: %s\n' "$1" >> /logs/cleanup.txt
}

# Use the identical new service test with the original production collector.
git show HEAD^:core/metrics/xfs.go > core/metrics/xfs.go
make build > /logs/baseline-build.log 2>&1
set +e
bash integration/run.sh test_metrics_xfs_hotplug.sh > /logs/baseline-service.log 2>&1
baseline_status=$?
set -e
test "${baseline_status}" -eq 1
grep -F 'service ready before mounting' /logs/baseline-service.log
grep -F 'XFS hotplug: mounted' /logs/baseline-service.log
grep -F 'missing or invalid XFS metrics for mounted device' /logs/baseline-service.log
grep -F 'XFS hotplug cleanup: mount released, loop detached, image removed' /logs/baseline-service.log
grep -F 'summary: total=1 passed=0 skipped=0 failed=1' /logs/baseline-service.log
check_cleanup baseline-assertion-failure

git restore --source=HEAD -- core/metrics/xfs.go
make build > /logs/fixed-build.log 2>&1
bash integration/run.sh test_metrics_xfs_hotplug.sh > /logs/fixed-service.log 2>&1
grep -F 'summary: total=1 passed=1 skipped=0 failed=0' /logs/fixed-service.log
grep -F 'metric names, types and device/host/region labels verified, round 2' /logs/fixed-service.log
grep -F 'metrics removed after unmount, round 2' /logs/fixed-service.log
grep -F 'XFS hotplug cleanup: mount released, loop detached, image removed' /logs/fixed-service.log
check_cleanup fixed-success

# Exercise the same EXIT trap on an explicit failure immediately after mount.
python3 - <<'PY'
from pathlib import Path
p=Path('integration/test_metrics_xfs_hotplug.sh')
t=p.read_text()
anchor='\tlog_info "XFS hotplug: mounted ${device_name}, round ${round}"\n'
assert t.count(anchor)==1
Path('integration/test_metrics_xfs_hotplug_cleanup.sh').write_text(
 t.replace(anchor,anchor+'\tfatal "injected failure after private mount"\n'))
PY
set +e
bash integration/run.sh test_metrics_xfs_hotplug_cleanup.sh > /logs/fixed-failure-cleanup.log 2>&1
injected_status=$?
set -e
rm integration/test_metrics_xfs_hotplug_cleanup.sh
test "${injected_status}" -eq 1
grep -F 'injected failure after private mount' /logs/fixed-failure-cleanup.log
grep -F 'XFS hotplug cleanup: mount released, loop detached, image removed' /logs/fixed-failure-cleanup.log
grep -F 'summary: total=1 passed=0 skipped=0 failed=1' /logs/fixed-failure-cleanup.log
check_cleanup fixed-injected-failure

go test -mod=vendor -race -json -run '^TestXFS' -count=1 ./core/metrics > /logs/fixed-focused.jsonl 2>&1
git add core/metrics/xfs.go core/metrics/xfs_test.go integration/test_metrics_xfs_hotplug.sh
make check > /logs/make-check.log 2>&1
git diff --exit-code
git diff --cached --check
git write-tree > /logs/fixed-tree.txt
python3 - <<'PY'
import hashlib,json,subprocess
from pathlib import Path
m=json.loads(Path('/evidence/manifest.json').read_text())
for path,h in m['files'].items():
 assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==h['sha256'], path
events=[json.loads(l) for l in Path('/logs/fixed-focused.jsonl').read_text().splitlines() if l.startswith('{')]
tests=[e for e in events if e.get('Test') and '/' not in e['Test'] and e.get('Action') in ['pass','fail','skip']]
assert len(tests)==11 and all(e['Action']=='pass' for e in tests)
assert not any(e['Action'] in ['fail','skip'] for e in events)
assert set(subprocess.check_output(['git','diff','--cached','--name-only'],text=True).splitlines())=={
 'core/metrics/xfs_test.go','integration/test_metrics_xfs_hotplug.sh'}
summary={'head':m['old_head'],'production_unchanged_from_published_pr':True,
 'baseline_service_exit':1,'baseline_failure':'newly mounted device missing metrics',
 'fixed_service_exit':0,'fixed_mount_rounds':2,'fixed_unmount_rounds':2,
 'integration_skipped':0,'cleanup_checked':['baseline assertion failure','fixed success','fixed injected failure'],
 'focused_race_parent_tests':len(tests),'make_check_exit':0,
 'fixed_tree':Path('/logs/fixed-tree.txt').read_text().strip(),'files':m['files']}
Path('/logs/summary.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(summary))
PY
