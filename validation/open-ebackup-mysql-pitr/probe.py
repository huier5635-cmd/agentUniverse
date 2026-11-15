from __future__ import annotations

import ast
from datetime import datetime
from enum import Enum
from functools import wraps
import json
import locale
import logging
import os
from pathlib import Path
import platform
import re
import shlex
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch


root = Path(sys.argv.pop(1))
application = root / "src" / "AppPlugins" / "database" / "applications"
mysql = application / "mysql" / "src"


class RestoreError(Exception):
    pass


class BaseService:
    def __init__(self, job_id, sub_job_id, pid):
        self.pid = pid

    def get_log_comm(self):
        return f"pid:{self.pid}"


shared = dict(globals(), log=logging.getLogger("pitr-fixture"),
              ErrCodeException=RestoreError, BaseService=BaseService,
              RestoreParam=SimpleNamespace, MySQLErrorCode=SimpleNamespace(SYSTEM_ERROR="system error"))


def load_functions(path, *names):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    selected = [node for node in tree.body if getattr(node, "name", None) in names]
    assert {node.name for node in selected} == set(names)
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"), shared)


load_functions(application / "common" / "const.py", "CMDResult")
load_functions(application / "common" / "common.py", "execute_cmd", "without_op_library")
load_functions(mysql / "utils" / "restore_func.py", "parse_time_stamp", "parse_xtrabackup_info",
               "convert_to_timestamp")
load_functions(mysql / "service" / "restore" / "log_restore.py", "LogRestore")


class PipelineTests(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.path = Path(work.name)
        self.binlog = self.path / "mysql-bin.000001"
        self.binlog.touch()
        (self.path / "xtrabackup_info").write_text(
            "binlog_pos = filename 'mysql-bin.000001', position '4'\n"
            "end_time = 2026-01-01 00:00:00\n", encoding="utf-8",
        )
        for name, script in {
            "mysqlbinlog": """
import json
import os
import sys
with open(os.environ["BINLOG_LOG"], "a", encoding="utf-8") as log:
    print(json.dumps(sys.argv[1:]), file=log)
print("SELECT 1;")
sys.exit(int(os.environ["BINLOG_STATUS"]))
""",
            "mysql": """
import json
import os
import sys
with open(os.environ["MYSQL_LOG"], "a", encoding="utf-8") as log:
    print(json.dumps({"args": sys.argv[1:], "sql": sys.stdin.read()}), file=log)
sys.exit(int(os.environ["MYSQL_STATUS"]))
""",
        }.items():
            exe = self.path / name
            exe.write_text("#!/usr/bin/env python3\n" + script, encoding="utf-8")
            exe.chmod(0o755)
        self.param = SimpleNamespace(
            pid="fixture", restore_time_stamp="1767225660", restore_copy_path=str(self.path),
            user="tester", port=3306, mysql_ip="127.0.0.1", pass_wd="not-a-real-password",
        )
        self.restore = shared["LogRestore"]("job", "subjob", self.param)
        self.restore.pre_log_restore = lambda: None
        self.restore.get_skip_gtids_cmd = lambda: "--skip-gtids"
        self.files = [str(self.binlog)]
        self.restore.get_log_restore_files = lambda *_: (True, self.files)

    def commands(self, tool):
        return [json.loads(line) for line in (self.path / f"{tool}.jsonl").read_text().splitlines()]

    def env(self, *, binlog=0, mysql=0):
        return patch.dict(os.environ, {
            "PATH": f"{self.path}{os.pathsep}{os.environ['PATH']}",
            "LD_LIBRARY_PATH": "/original-library",
            "BINLOG_STATUS": str(binlog),
            "MYSQL_STATUS": str(mysql),
            "BINLOG_LOG": str(self.path / "binlog.jsonl"),
            "MYSQL_LOG": str(self.path / "mysql.jsonl"),
        })

    def test_success_replays_sql(self):
        with self.env():
            self.restore.exec_restore()
            self.assertEqual(os.environ["LD_LIBRARY_PATH"], "/original-library")
        self.assertEqual(self.commands("mysql")[0]["sql"], "SELECT 1;\n")

    def test_decoder_failure_is_reported_and_stops_later_files(self):
        self.files.append(str(self.path / "mysql-bin.000002"))
        with self.env(binlog=2):
            with self.assertRaisesRegex(RestoreError, "system error"):
                self.restore.exec_restore()
            self.assertEqual(os.environ["LD_LIBRARY_PATH"], "/original-library")
        self.assertEqual(len(self.commands("binlog")), 1)

    def test_mysql_failure_is_reported(self):
        with self.env(mysql=7):
            with self.assertRaisesRegex(RestoreError, "system error"):
                self.restore.exec_restore()
            self.assertEqual(os.environ["LD_LIBRARY_PATH"], "/original-library")

    def test_shell_sensitive_arguments_are_preserved(self):
        path = self.path / "mysql raw $tmp.000001"
        path.touch()
        self.files[:] = [str(path)]
        self.restore.database = "unit test DB"
        self.param.pass_wd = "dummy'quote"
        with self.env():
            self.restore.exec_restore()
        self.assertIn(str(path), self.commands("binlog")[0])
        self.assertIn("--database=unit test DB", self.commands("binlog")[0])
        self.assertIn("-pdummy'quote", self.commands("mysql")[0]["args"])

    def test_unset_library_path_remains_unset(self):
        with self.env():
            os.environ.pop("LD_LIBRARY_PATH")
            self.restore.exec_restore()
            self.assertNotIn("LD_LIBRARY_PATH", os.environ)

    def test_library_path_is_restored_after_an_existing_exception(self):
        (self.path / "xtrabackup_info").unlink()
        with self.env():
            with self.assertRaisesRegex(RestoreError, "system error"):
                self.restore.exec_restore()
            self.assertEqual(os.environ["LD_LIBRARY_PATH"], "/original-library")


if __name__ == "__main__":
    unittest.main(verbosity=2)
