from pathlib import Path
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile

repo = Path(sys.argv[1]).resolve()
evidence = Path(sys.argv[2]).resolve()
evidence.mkdir(parents=True, exist_ok=True)
payload = Path(__file__).resolve().parent
manifest = json.loads((payload / "manifest.json").read_text())
workspace = repo / "src/ws-ckpt/src"
results = []


def command(args, **kwargs):
    return subprocess.check_output(args, text=True, **kwargs).strip()


def run(args, name, expected=0, timeout=1200, env=None):
    with (evidence / (name + ".txt")).open("w") as stream:
        proc = subprocess.run(args, cwd=workspace, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout, env=env)
    output = (evidence / (name + ".txt")).read_text()
    results.append({"name": name, "exit": proc.returncode})
    if expected is not None:
        assert proc.returncode == expected, (name, proc.returncode, output[-6000:])
    return proc.returncode, output


class MountFixtures:
    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="wsckpt-native-", dir=os.environ["RUNNER_TEMP"])).resolve()
        self.loops = []
        self.mappers = []
        self.mounts = []

    def filesystem(self, key):
        image = self.root / (key + ".img")
        with image.open("wb") as stream:
            stream.truncate(512 * 1024 * 1024)
        assert image.resolve().is_relative_to(self.root)
        loop = command(["sudo", "losetup", "--find", "--show", str(image)])
        self.loops.append(loop)
        record = json.loads(command(["sudo", "losetup", "--json", loop]))["loopdevices"]
        assert len(record) == 1 and Path(record[0]["back-file"]).resolve() == image.resolve()
        assert loop.startswith("/dev/loop") and stat.S_ISBLK(os.stat(loop).st_mode)
        sectors = int(command(["sudo", "blockdev", "--getsz", loop]))
        assert sectors == 512 * 1024 * 1024 // 512
        name = "wsckpt-" + self.root.name + "-" + key
        device = Path("/dev/mapper") / name
        assert not device.exists(), "never reuse a pre-existing mapper"
        subprocess.run(["sudo", "dmsetup", "create", name, "--table", f"0 {sectors} linear {loop} 0"], check=True)
        self.mappers.append(name)
        subprocess.run(["sudo", "udevadm", "settle"], check=True)
        table = command(["sudo", "dmsetup", "table", name]).split()
        backing = os.stat(loop).st_rdev
        assert table == ["0", str(sectors), "linear", f"{os.major(backing)}:{os.minor(backing)}", "0"]
        # Format only a newly-created mapper proven to cover our own sparse file.
        subprocess.run(["sudo", "mkfs.btrfs", "-f", str(device)], check=True)
        point = self.root / (key + " mount")
        point.mkdir()
        subprocess.run(["sudo", "mount", str(device), str(point)], check=True)
        self.mounts.append(point)
        subprocess.run(["sudo", "chown", f"{os.getuid()}:{os.getgid()}", str(point)], check=True)
        return point

    def setup(self):
        eligible = [line for line in Path("/proc/mounts").read_text().splitlines() if len(line.split()) >= 4 and line.split()[2] == "btrfs" and line.split()[0].startswith("/dev/") and not line.split()[0].startswith("/dev/loop")]
        assert not eligible, "native negative control requires a disposable runner with no existing physical Btrfs mounts"
        first = self.filesystem("first")
        previous = self.filesystem("previous")
        readonly = self.root / "readonly mount"
        readonly.mkdir()
        subprocess.run(["sudo", "mount", "--bind", str(previous), str(readonly)], check=True)
        self.mounts.append(readonly)
        subprocess.run(["sudo", "mount", "-o", "remount,bind,ro", str(readonly)], check=True)
        non_btrfs = self.root / "other mount"
        non_btrfs.mkdir()
        subprocess.run(["sudo", "mount", "-t", "tmpfs", "-o", "size=1m", "none", str(non_btrfs)], check=True)
        self.mounts.append(non_btrfs)
        env = dict(os.environ, WS_CKPT_TEST_FIRST_MOUNT=str(first), WS_CKPT_TEST_PERSISTED_MOUNT=str(previous), WS_CKPT_TEST_READONLY_MOUNT=str(readonly), WS_CKPT_TEST_NON_BTRFS_MOUNT=str(non_btrfs))
        (evidence / "native-mounts.txt").write_text(Path("/proc/mounts").read_text())
        return env

    def close(self):
        errors = []
        for point in reversed(self.mounts):
            proc = subprocess.run(["sudo", "umount", str(point)], check=False)
            if proc.returncode:
                errors.append(str(point))
        if errors:
            raise RuntimeError("refusing device teardown while mounts remain: " + repr(errors))
        for name in reversed(self.mappers):
            subprocess.run(["sudo", "dmsetup", "remove", name], check=True)
        for loop in reversed(self.loops):
            record = json.loads(command(["sudo", "losetup", "--json", loop]))["loopdevices"]
            assert len(record) == 1 and Path(record[0]["back-file"]).resolve().is_relative_to(self.root)
            subprocess.run(["sudo", "losetup", "--detach", loop], check=True)
        # The runner removes the temporary directory after mount teardown;
        # mounted subvolumes and root-owned files are never recursively deleted.
        (evidence / "native-cleanup.txt").write_text("All owned mounts, mappers and loops released. Temporary files remain only in disposable RUNNER_TEMP.\n")


startup = repo / "src/ws-ckpt/src/crates/daemon/src/startup.rs"
stable_path = startup.parent / "startup/native_btrfs_tests.rs"
original_startup = startup.read_bytes()
fixtures = MountFixtures()
try:
    env = fixtures.setup()
    stable_path.parent.mkdir(exist_ok=True)
    stable_path.write_bytes((payload / "native_btrfs_tests.rs").read_bytes())
    startup.write_bytes(original_startup + b"\n#[cfg(test)]\nmod native_btrfs_tests;\n")
    try:
        code, output = run(["cargo", "test", "-p", "ws-ckpt-daemon", "--lib", "startup::native_btrfs_tests", "--", "--ignored", "--test-threads=1"], "original-native-restart", expected=101, env=env)
        assert "0 passed; 3 failed" in output, output[-6000:]
        assert "must refuse an unmounted persisted root" in output and "invalid state must not become a new first install" in output
        assert "restart_preserves_persisted_root_instead_of_first_detected_mount" in output
    finally:
        startup.write_bytes(original_startup)
        stable_path.unlink()
    subprocess.run(["git", "apply", "--check", str(payload / "candidate.patch")], cwd=repo, check=True)
    subprocess.run(["git", "apply", str(payload / "candidate.patch")], cwd=repo, check=True)
    run(["cargo", "fmt", "--all"], "format-candidate")
    run(["cargo", "check", "--workspace", "--all-targets", "--locked"], "candidate-check")
    run(["cargo", "clippy", "--workspace", "--all-targets", "--locked", "--", "-D", "warnings"], "candidate-clippy")
    run(["cargo", "test", "--workspace", "--locked"], "workspace-tests")
    _, output = run(["cargo", "test", "-p", "ws-ckpt-daemon", "--lib", "startup::native_btrfs_", "--", "--ignored", "--test-threads=1"], "fixed-native-restart", env=env)
    assert "7 passed; 0 failed" in output, output[-6000:]
    run(["cargo", "doc", "--workspace", "--no-deps", "--locked"], "public-api-docs")
finally:
    for item in manifest["files"]:
        source = repo / item["path"]
        if source.exists():
            dest = evidence / "formatted" / item["path"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(source.read_bytes())
    (evidence / "summary.json").write_text(json.dumps({"not_final_validation": True, "results": results}, indent=2))
    fixtures.close()
