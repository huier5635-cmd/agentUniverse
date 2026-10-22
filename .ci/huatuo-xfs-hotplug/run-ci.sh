#!/bin/sh
set -eu

cd /workspace
git config --global --add safe.directory /workspace
apk add --no-cache python3 util-linux xfsprogs bash
mkdir -p /logs
go version | tee /logs/go-version.txt
uname -a | tee /logs/kernel.txt
git rev-parse HEAD | tee /logs/upstream-head.txt
python3 - <<'PY'
import hashlib, json
from pathlib import Path
manifest = json.loads(Path('/evidence/validation-manifest.json').read_text())
for name, expected in manifest['original'].items():
    assert hashlib.sha256(Path(name).read_bytes()).hexdigest() == expected, name
for name, expected in manifest['patches'].items():
    assert hashlib.sha256((Path('/evidence') / name).read_bytes()).hexdigest() == expected, name
print('Pinned original source and every patch hash verified')
PY

make build > /logs/baseline-build.log 2>&1
set +e
make unit > /logs/baseline-full-unit.log 2>&1
baseline_unit=$?
set -e
git apply --check /evidence/regression-only.patch
git apply /evidence/regression-only.patch
set +e
go test -mod=vendor -json -run '^TestXFS' -count=1 ./core/metrics > /logs/baseline-focused.jsonl 2>&1
baseline_focused=$?
set -e
test "$baseline_focused" -eq 1
python3 /evidence/verify-ci.py /logs baseline
go test -mod=vendor -run '^$' -bench '^BenchmarkXFSUpdate$' -benchtime=200ms -count=3 -benchmem ./core/metrics > /logs/baseline-benchmark.log 2>&1

image=$(mktemp /tmp/huatuo-xfs-hotplug.XXXXXX.img)
device=
cleanup() {
    if [ -n "$device" ]; then
        target=$(findmnt -n -o TARGET --source "$device" || true)
        if [ -n "$target" ]; then
            umount "$target"
        fi
        backing=$(losetup -n -O BACK-FILE "$device")
        test "$(realpath "$backing")" = "$(realpath "$image")"
        losetup -d "$device"
        test -z "$(losetup -j "$image")"
        device=
    fi
    rm -f "$image"
    printf '%s\n' 'CLEANUP: private XFS loop detached; temporary image removed' | tee /logs/cleanup.txt
}
trap cleanup EXIT
truncate -s 512M "$image"
device=$(losetup --find --show "$image")
case "$device" in /dev/loop[0-9]*) ;; *) exit 1 ;; esac
backing=$(losetup -n -O BACK-FILE "$device")
test "$(realpath "$backing")" = "$(realpath "$image")"
mkfs.xfs -f "$device" > /logs/mkfs.log
set +e
HUATUO_XFS_HOTPLUG_DEVICE="$device" go test -mod=vendor -json -run '^TestXFSHotplugIntegration$' -count=1 ./core/metrics > /logs/baseline-live.jsonl 2>&1
baseline_live=$?
set -e
test "$baseline_live" -eq 1
python3 /evidence/verify-ci.py /logs baseline-live
test -z "$(findmnt -n -o TARGET --source "$device" || true)"

git apply --check /evidence/production.patch
git apply /evidence/production.patch
git apply --check /evidence/test-cache-migration.patch
git apply /evidence/test-cache-migration.patch
goimports -w -local github.com/ccfos/huatuo core/metrics/xfs.go core/metrics/xfs_test.go
gofumpt -w core/metrics/xfs.go core/metrics/xfs_test.go
python3 - <<'PY'
import hashlib, json
from pathlib import Path
manifest = json.loads(Path('/evidence/validation-manifest.json').read_text())
for name, expected in manifest['fixed'].items():
    assert hashlib.sha256(Path(name).read_bytes()).hexdigest() == expected, name
print('Exact proposed source and tests verified after repository formatters')
PY
go test -mod=vendor -race -json -run '^TestXFS' -count=1 ./core/metrics > /logs/fixed-focused.jsonl 2>&1
go test -mod=vendor -run '^$' -bench '^BenchmarkXFSUpdate$' -benchtime=200ms -count=3 -benchmem ./core/metrics > /logs/fixed-benchmark.log 2>&1
HUATUO_XFS_HOTPLUG_DEVICE="$device" go test -mod=vendor -race -json -run '^TestXFSHotplugIntegration$' -count=1 ./core/metrics > /logs/fixed-live.jsonl 2>&1
test -z "$(findmnt -n -o TARGET --source "$device" || true)"

make build > /logs/fixed-build.log 2>&1
git add core/metrics/xfs.go core/metrics/xfs_test.go
make check > /logs/make-check.log 2>&1
git diff --exit-code
git diff --cached --check
git write-tree | tee /logs/fixed-tree.txt
set +e
make unit > /logs/fixed-full-unit.log 2>&1
fixed_unit=$?
set -e
printf '{"baseline":%s,"fixed":%s}\n' "$baseline_unit" "$fixed_unit" > /logs/full-unit-exits.json
python3 /evidence/verify-ci.py /logs fixed
python3 - <<'PY'
import hashlib, json
from pathlib import Path
manifest = json.loads(Path('/evidence/validation-manifest.json').read_text())
for name, expected in manifest['fixed'].items():
    assert hashlib.sha256(Path(name).read_bytes()).hexdigest() == expected, name
print('Source remains frozen after complete build/check/unit execution')
PY
cat /logs/baseline-benchmark.log /logs/fixed-benchmark.log
cleanup
trap - EXIT
