"""Run the real upstream pexpect launcher with MySQL and XtraBackup commands."""
import argparse
import ast
import logging
import os
from pathlib import Path
import shlex
import subprocess
from types import SimpleNamespace

import pexpect


KEY = "LD_LIBRARY_PATH"
PARENT_PATH = "/fixture/parent lib"
BACKUP_PATH = "/fixture/backup lib"
QUERY = (
    "SELECT GROUP_CONCAT(CONCAT(id, ':', label) ORDER BY id) "
    "FROM oe_fixture.events"
)
EXPECTED_ROWS = "11:alpha,22:beta"


def upstream_launcher(source):
    module = ast.parse(source.read_text(encoding="utf-8"))
    [function] = [
        member for member in module.body
        if isinstance(member, ast.FunctionDef) and member.name == "exec_cmd_spawn"
    ]
    namespace = {
        "os": os,
        "pexpect": pexpect,
        "log": logging.getLogger("open-eBackup fixture"),
        "CMDResultInt": SimpleNamespace(
            SUCCESS=SimpleNamespace(value=0), FAILED=SimpleNamespace(value=1)
        ),
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), namespace)
    return namespace["exec_cmd_spawn"]


def exercise(spawn, kind, container, argv, expected_value, expected_parent, **options):
    os.environ[KEY] = PARENT_PATH
    command = shlex.join([
        "docker", "exec", "-it", "--user", "0", "--env", KEY, container, "/bin/sh", "-ec",
        'case "$1" in mysql|xtrabackup) "$@" ;; *) exit 2 ;; esac; '
        'printf "LIBPATH_AT_COMMAND_EXIT=<%s>\\n" "$LD_LIBRARY_PATH"',
        "probe", *argv,
    ])
    code, output = spawn(command, os.environ["OE_TEST_PASSWORD"], **options)
    if code != 0:
        raise AssertionError(f"{kind} exited {code}: {output}")
    assert f"LIBPATH_AT_COMMAND_EXIT=<{expected_value}>" in output, output
    assert os.environ.get(KEY) == expected_parent, (kind, os.environ.get(KEY))
    inherited = subprocess.check_output(["printenv", KEY], text=True).rstrip("\n")
    assert inherited == expected_parent, (kind, inherited)
    print(f"{kind}: child library path and subsequent host process environment verified", flush=True)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("variant", choices=["baseline", "candidate"])
    args = parser.parse_args()
    spawn = upstream_launcher(args.source)
    original = args.variant == "baseline"
    mysql_result = exercise(
        spawn, f"{args.variant} MySQL SELECT", "oe-src",
        ["mysql", "--no-defaults", "--user=root", "--password",
         "--socket=/var/run/mysqld/mysqld.sock", "--batch", "--skip-column-names", "--execute", QUERY],
        "", "" if original else PARENT_PATH,
    )
    assert EXPECTED_ROWS in mysql_result, mysql_result
    xtra_result = exercise(
        spawn, f"{args.variant} XtraBackup", "oe-xtra",
        ["xtrabackup", "--backup", "--socket=/var/run/mysqld/mysqld.sock",
         "--user=root", "--password", "--datadir=/var/lib/mysql", f"--target-dir=/backup/{args.variant}"],
        BACKUP_PATH, BACKUP_PATH if original else PARENT_PATH,
        library_path=BACKUP_PATH, xtrabackup_cmd=True,
    )
    assert "completed OK!" in xtra_result, xtra_result
    if original:
        print("Original launcher leaked both changes into the parent", flush=True)
    else:
        print("Candidate launcher isolated both child environments and completed the physical backup", flush=True)


if __name__ == "__main__":
    main()
