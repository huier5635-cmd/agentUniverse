"""Exercise the upstream launcher through a real Linux PTY and child."""

import ast
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import pexpect


SOURCE = Path(sys.argv.pop(1))
KEY = "LD_LIBRARY_PATH"
CLIENT = """import json, os, sys
print("Enter password:", flush=True)
if sys.stdin.readline().strip() != "synthetic-password":
    raise SystemExit(9)
print("PAYLOAD:" + json.dumps([os.getenv("LD_LIBRARY_PATH"), sys.stdin.isatty()]), flush=True)
"""


def load_spawn():
    module = ast.parse(SOURCE.read_text(encoding="utf-8"))
    function = next(node for node in module.body
                    if isinstance(node, ast.FunctionDef) and node.name == "exec_cmd_spawn")
    namespace = {
        "os": os,
        "pexpect": pexpect,
        "log": Mock(),
        "CMDResultInt": SimpleNamespace(SUCCESS=SimpleNamespace(value=0),
                                        FAILED=SimpleNamespace(value=1)),
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), "exec"), namespace)
    return namespace["exec_cmd_spawn"]


class LinuxPtyTest(unittest.TestCase):
    def check_child(self, expected, **options):
        command = shlex.join([sys.executable, "-c", CLIENT])
        code, output = load_spawn()(command, "synthetic-password", **options)
        self.assertEqual(code, 0, output)
        match = re.search(r"PAYLOAD:(\[[^\r\n]+\])", output)
        self.assertIsNotNone(match, output)
        self.assertEqual(json.loads(match[1]), [expected, True])

    def check_next_child(self, expected):
        script = f"import json, os; print(json.dumps(os.environ.get({KEY!r})))"
        output = subprocess.check_output([sys.executable, "-c", script], text=True)
        self.assertEqual(json.loads(output), expected)

    def test_mysql_clear_is_not_inherited_by_later_child(self):
        with patch.dict(os.environ, {KEY: "/fixture/host lib"}):
            self.check_child("")
            self.check_next_child("/fixture/host lib")

    def test_xtrabackup_override_does_not_leak_to_no_override(self):
        with patch.dict(os.environ, {KEY: "/fixture/host lib"}):
            self.check_child("/fixture/backup lib", library_path="/fixture/backup lib",
                             xtrabackup_cmd=True)
            self.check_child("/fixture/host lib", xtrabackup_cmd=True)
            self.check_next_child("/fixture/host lib")

    def test_preserves_absent_inherited_variable(self):
        with patch.dict(os.environ):
            os.environ.pop(KEY, None)
            self.check_child(None, xtrabackup_cmd=True)
            self.check_next_child(None)

    def test_child_spawn_error_does_not_create_parent_variable(self):
        with patch.dict(os.environ):
            os.environ.pop(KEY, None)
            result = load_spawn()("/dev/null/unavailable-client", "synthetic-password")
            self.assertEqual(result, (1, "cmd error"))
            self.check_next_child(None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
