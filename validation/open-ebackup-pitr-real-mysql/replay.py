"""Exercise the source restore method with real MySQL programs in test containers."""

from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import probe


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
fixture = Path(os.environ["PITR_FIXTURE"])
binlog_name = os.environ["PITR_BINLOG"]
snapshot_time = int(os.environ["PITR_SNAPSHOT"])
cutoff_time = int(os.environ["PITR_CUTOFF"])
target_password = os.environ["PITR_TARGET_PASSWORD"]
mode = sys.argv[1]
assert mode in {"original", "candidate"}, mode
application = probe.application
probe.load_functions(
    application / "mysql" / "src" / "utils" / "restore_func.py",
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


def restore(case):
    case_dir = fixture / case
    case_dir.mkdir(exist_ok=True)
    backup_info = case_dir / "xtrabackup_info"
    when = datetime.fromtimestamp(snapshot_time, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    backup_info.write_text(
        f"binlog_pos = filename '{binlog_name}', position '4'\nend_time = {when}\n",
        encoding="utf-8",
    )
    source_log = fixture / "valid" / binlog_name
    run_dir = case_dir / "binlogs"
    run_dir.mkdir(exist_ok=True)
    log_path = run_dir / binlog_name
    if case == "invalid":
        log_path.write_bytes(b"invalid MySQL binary log magic")
    else:
        log_path.write_bytes(source_log.read_bytes())
    (case_dir / "copy.meta").write_text(
        f"binlogs;{snapshot_time - 1}~{cutoff_time + 10}\n", encoding="utf-8"
    )
    param = SimpleNamespace(
        pid="mysql-integration",
        restore_time_stamp=str(cutoff_time),
        restore_copy_path=str(case_dir),
        restore_copy_id="copy",
        origin_log_path=str(case_dir),
        user="root",
        port=3306,
        mysql_ip="127.0.0.1",
        pass_wd=target_password,
    )
    task = probe.shared["LogRestore"]("job", "subjob", param, database="pitr_fixture")
    task.pre_log_restore = lambda: None
    task.get_skip_gtids_cmd = lambda: "--skip-gtids"
    try:
        task.exec_restore()
    except probe.RestoreError:
        return False
    return True


def restored_ids():
    return target_sql(
        "SELECT COALESCE(GROUP_CONCAT(id ORDER BY id), '') FROM pitr_fixture.events"
    )


target_sql("TRUNCATE TABLE pitr_fixture.events")
assert restore("valid-replay"), "the genuine binary log must replay"
ids = restored_ids()
assert ids == "1", f"expected only id=1 at the requested time, observed {ids!r}"
print("real MySQL point-in-time cutoff: passed")

expected_failure = mode == "candidate"
duplicate_status = restore("duplicate-key")
assert (not duplicate_status) == expected_failure, duplicate_status
assert restored_ids() == "1", "the duplicate-key replay must leave the restored row intact"
print(f"real MySQL duplicate key reported failure: {not duplicate_status}")

target_sql("TRUNCATE TABLE pitr_fixture.events")
invalid_status = restore("invalid")
assert (not invalid_status) == expected_failure, invalid_status
assert restored_ids() == "", "a malformed binary log must not change the target"
print(f"real mysqlbinlog invalid file reported failure: {not invalid_status}")
