"""Exercise the pinned open-eBackup methods with real Linux PTY and MySQL clients."""

import ast
from enum import IntEnum
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import NamedTemporaryFile
import time
from types import SimpleNamespace

import pexpect


class RestoreError(Exception):
    pass


class CMDResultInt(IntEnum):
    SUCCESS = 0
    FAILED = 1


def import_method(path, name, namespace, *, class_name=None):
    tree = ast.parse(path.read_bytes(), filename=str(path))
    owner = tree if class_name is None else next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    node = next(node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name == name)
    node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)


root = Path(sys.argv[1]) / 'src/AppPlugins/database/applications/mysql/src'
mode = sys.argv[2]
assert mode in ('review', 'follow-up')
fixture = Path(os.environ['PITR_FIXTURE'])
first_name, second_name = (os.environ[key] for key in ('PITR_BINLOG', 'PITR_SECOND_BINLOG'))
start_position = int(os.environ['PITR_START_POSITION'])
cutoff = int(os.environ['PITR_CUTOFF'])
target_password = os.environ['PITR_TARGET_PASSWORD']
target_port = os.environ['PITR_CLIENT_PORT']
restore_path = fixture / 'restore'
restore_path.mkdir(exist_ok=True)
first, second = (restore_path / name for name in (first_name, second_name))
for local in (first, second):
    shutil.copyfile(fixture / 'valid' / local.name, local)
(restore_path / 'xtrabackup_info').touch()


def admin_sql(statement):
    command = ['mysql', '-uroot', '-h127.0.0.1', f'-P{target_port}', '--batch', '--skip-column-names', '-e', statement]
    return subprocess.run(command, check=True, capture_output=True, text=True,
                          timeout=15, env={**os.environ, 'MYSQL_PWD': target_password}).stdout.strip()


spawn = pexpect.spawn
launches = []


def check_spawn(command, *args, **kwargs):
    arguments = kwargs.get('args') or []
    assert target_password not in command and all(target_password not in arg for arg in arguments)
    launches.append((command, arguments))
    return spawn(command, *args, **kwargs)


pexpect.spawn = check_spawn
namespace = {
    'os': os, 'log': logging.getLogger(), 'pexpect': pexpect, 'CMDResultInt': CMDResultInt,
    'NamedTemporaryFile': NamedTemporaryFile, 'ErrCodeException': RestoreError,
    'MySQLErrorCode': SimpleNamespace(SYSTEM_ERROR='restore failed'),
    'parse_time_stamp': lambda _: time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(cutoff)),
    'convert_to_timestamp': int,
    'parse_xtrabackup_info': lambda _: {'binlog_position': start_position, 'end_time': 1, 'binlog_filename': first_name},
}
import_method(root / 'utils/common_func.py', 'exec_cmd_spawn', namespace)
import_method(root / 'service/restore/log_restore.py', 'exec_restore', namespace, class_name='LogRestore')
task = SimpleNamespace(
    param=SimpleNamespace(restore_time_stamp=str(cutoff), restore_copy_path=str(restore_path),
                          user='root', port=int(target_port), mysql_ip='127.0.0.1', pass_wd=target_password),
    pre_log_restore=lambda: None, database='pitr_fixture', get_skip_gtids_cmd=lambda: '--skip-gtids',
    get_log_comm=lambda: 'PITR test fixture', get_log_restore_files=lambda *_: (True, [str(first), str(second)]),
)


def replay():
    launches.clear()
    try:
        namespace['exec_restore'](task)
    except RestoreError:
        return False
    return True


def rows():
    return admin_sql('SELECT GROUP_CONCAT(id ORDER BY id) FROM pitr_fixture.events')


admin_sql('DELETE FROM pitr_fixture.events WHERE id > 0')
if mode == 'review':
    assert not replay() and rows() == '0,1' and len(launches) == 2, (rows(), len(launches))
    print('PR #40 confirmed on MySQL: first transaction replayed, second session failed to find its temporary table')
else:
    assert replay() and rows() == '0,1,2' and len(launches) == 1, (rows(), len(launches))
    print('Follow-up on MySQL: one password prompt and one session replay both files, cutoff excludes id 3')
    assert not replay() and rows() == '0,1,2', 'duplicate-key failure must propagate'

    admin_sql('DELETE FROM pitr_fixture.events WHERE id > 0')
    task.get_log_restore_files = lambda *_: (True, [str(second)])
    assert not replay() and rows() == '0' and not launches, 'missing initial log must fail before starting MySQL'

    task.get_log_restore_files = lambda *_: (True, [str(first), str(second)])
    second.write_bytes(b'invalid second binlog fixture')
    assert not replay() and len(launches) == 1, 'decoder failure must propagate through pipefail'
    print('Duplicate key, missing initial log and damaged second log all reported failure')
