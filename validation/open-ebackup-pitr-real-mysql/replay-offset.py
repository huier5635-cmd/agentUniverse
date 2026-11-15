"""Check the backup offset at a real MySQL binary-log rotation boundary."""

from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import probe


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
fixture = Path(os.environ["PITR_FIXTURE"])
first_name = os.environ["PITR_BINLOG"]
second_name = os.environ["PITR_SECOND_BINLOG"]
start_position = int(os.environ["PITR_START_POSITION"])
snapshot_time = int(os.environ["PITR_SNAPSHOT"])
cutoff_time = int(os.environ["PITR_CUTOFF"])
target_password = os.environ["PITR_TARGET_PASSWORD"]
mode = sys.argv[1]
assert mode in {"original", "candidate"}, mode

probe.load_functions(
    probe.application / "mysql" / "src" / "utils" / "restore_func.py",
    "parse_log_meta",
    "parse_num",
    "get_bin_log_names",
    "convert_restore_binlog_files_str",
)


def target_sql(statement):
    return subprocess.run(
        ["mysql", "-uroot", "-h127.0.0.1", f"-p{target_password}",
         "--batch", "--skip-column-names", "-e", statement],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


root = fixture / "restore"
logs = root / "binlogs"
logs.mkdir(parents=True, exist_ok=True)
for name in (first_name, second_name):
    shutil.copyfile(fixture / "valid" / name, logs / name)
assert (logs / first_name).stat().st_size > start_position > (logs / second_name).stat().st_size

when = datetime.fromtimestamp(snapshot_time, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
(root / "xtrabackup_info").write_text(
    f"binlog_pos = filename '{first_name}', position '{start_position}'\nend_time = {when}\n",
    encoding="utf-8",
)
(root / "copy.meta").write_text(
    f"binlogs;{snapshot_time - 1}~{cutoff_time + 10}\n", encoding="utf-8"
)
param = SimpleNamespace(
    pid="mysql-rotated-binlogs",
    restore_time_stamp=str(cutoff_time),
    restore_copy_path=str(root),
    restore_copy_id="copy",
    origin_log_path=str(root),
    user="root",
    port=3306,
    mysql_ip="127.0.0.1",
    pass_wd=target_password,
)
task = probe.shared["LogRestore"]("job", "subjob", param, database="pitr_fixture")
task.pre_log_restore = lambda: None
task.get_skip_gtids_cmd = lambda: "--skip-gtids"
target_sql("DELETE FROM pitr_fixture.events WHERE id > 0")
success = True
try:
    task.exec_restore()
except probe.RestoreError:
    success = False
ids = target_sql("SELECT GROUP_CONCAT(id ORDER BY id) FROM pitr_fixture.events")
print(f"rotated replay ({mode}): returned_success={success}, ids={ids}")
if mode == "original":
    assert ids == "0,1", f"expected the later log to be skipped with the original offset, got {ids!r}"
else:
    assert success and ids == "0,1,2", (success, ids)
