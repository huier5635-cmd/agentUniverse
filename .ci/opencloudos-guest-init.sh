#!/bin/sh
set -eu
export PATH=/bin
mkdir -p /proc /sys /dev
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
profile=unknown
for argument in $(cat /proc/cmdline); do
    case "$argument" in
        oc_profile=*) profile=${argument#oc_profile=} ;;
    esac
done
echo "OC_GUEST_PROFILE=$profile"
uname -r
cat /proc/cmdline
set +e
/stat-selftest
result=$?
set -e
echo "OC_SELFTEST_EXIT=$result"
case "$profile:$result" in
    original:4|fixed:0) ;;
    *) echo OC_GUEST_SELFTEST_FAILED; poweroff -f; exit 1 ;;
esac
/stat-benchmark
echo "OC_GUEST_DONE profile=$profile"
poweroff -f
