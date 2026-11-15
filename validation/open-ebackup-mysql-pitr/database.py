from datetime import datetime
import logging
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

from probe import RestoreError, shared

logging.basicConfig(level=logging.INFO, format="%(message)s")
fixture = Path(sys.argv[1])
container = sys.argv[2]
variant = sys.argv[3]
password = os.environ["MYSQL_FIXTURE_PASSWORD"]


def sql(statement):
    return subprocess.check_output([
        "docker", "exec", container, "mysql", "-uroot", f"-p{password}",
        "-N", "-B", "-e", statement,
    ], text=True, stderr=subprocess.DEVNULL).strip()


def reset_target():
    sql("SET SESSION sql_log_bin=0; DROP DATABASE IF EXISTS pitr_fixture")


def restore(file, password):
    config = SimpleNamespace(
        pid="real-fixture", restore_time_stamp=str(int(datetime.now().timestamp()) + 300),
        restore_copy_path=str(fixture), user="root", port=3306, mysql_ip="localhost",
        pass_wd=password,
    )
    runner = shared["LogRestore"]("real-job", "subjob", config)
    runner.pre_log_restore = lambda: None
    runner.get_skip_gtids_cmd = lambda: "--skip-gtids"
    runner.get_log_restore_files = lambda *_: (True, [str(fixture / file)])
    runner.exec_restore()


(fixture / "xtrabackup_info").write_text(
    "binlog_pos = filename 'pitr-valid.binlog', position '4'\n"
    f"end_time = {datetime.now():%Y-%m-%d %H:%M:%S}\n",
    encoding="utf-8",
)
reset_target()
restore("pitr-valid.binlog", password)
rows = sql("SELECT GROUP_CONCAT(id ORDER BY id) FROM pitr_fixture.example")
assert rows == "7,42", f"Unexpected restored rows: {rows}"
print(f"{variant}: actual binlog replay restored rows {rows}")

for name, file, secret in (
    ("corrupt binlog", "pitr-corrupt.binlog", password),
    ("rejected MySQL login", "pitr-valid.binlog", "invalid-fixture-password"),
):
    reset_target()
    try:
        restore(file, secret)
    except RestoreError:
        status = "rejected"
    else:
        status = "reported success"
    expected = "rejected" if variant == "fixed" else "reported success"
    assert status == expected, f"{variant} {name}: expected {expected}, observed {status}"
    assert sql("SHOW DATABASES LIKE 'pitr_fixture'") == ""
    print(f"{variant}: {name}: {status}")
