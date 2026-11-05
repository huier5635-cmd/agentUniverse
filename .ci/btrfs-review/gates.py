from pathlib import Path
import hashlib
import json
import os
import re
import subprocess
import sys

repo = Path(sys.argv[1]).resolve()
evidence = Path(sys.argv[2]).resolve()
gate = sys.argv[3]
evidence.mkdir(parents=True, exist_ok=True)
payload = Path(__file__).resolve().parent
manifest = json.loads((payload / 'manifest.json').read_text())
workspace = repo / 'src/ws-ckpt/src'
results = []


def run(args, name, cwd=workspace, expected=0, timeout=1800, env=None):
    with (evidence / (name + '.txt')).open('w') as log:
        process = subprocess.run(args, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                                 timeout=timeout, env=env)
    text = (evidence / (name + '.txt')).read_text(errors='replace')
    results.append({'name': name, 'command': args, 'exit': process.returncode})
    print(name, process.returncode, text[-1800:], flush=True)
    assert process.returncode == expected, (name, process.returncode, text[-6000:])
    return text


assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip() == manifest['base']
run(['git', 'apply', '--index', str(payload / 'candidate.patch')], 'apply', cwd=repo)
run(['cargo', 'fmt', '--all'], 'format')
paths = [row['path'] for row in manifest['files']]
run(['git', 'add', '--', *paths], 'stage', cwd=repo)
run(['git', 'diff', '--cached', '--check'], 'whitespace', cwd=repo)
tree = subprocess.check_output(['git', 'write-tree'], cwd=repo, text=True).strip()
hashes = {}
for path in paths:
    data = (repo / path).read_bytes()
    hashes[path] = hashlib.sha256(data).hexdigest()
    output = evidence / 'formatted' / path
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)


def mutant(name, path, transform, package, test):
    target = repo / path
    original = target.read_bytes()
    changed = transform(original.decode())
    assert changed != original.decode(), 'Mutation did not apply: ' + name
    try:
        target.write_bytes(changed.encode())
        output = run(['cargo', 'test', '-p', package, test, '--', '--nocapture'],
                     name, expected=101)
        assert 'test result: FAILED' in output, 'Control must compile and fail an assertion'
    finally:
        target.write_bytes(original)


try:
    if gate == 'rust-native':
        run(['cargo', 'fmt', '--all', '--check'], 'format-check')
        run(['cargo', 'clippy', '--workspace', '--all-targets', '--locked', '--', '-D', 'warnings'], 'clippy')
        run(['cargo', 'test', '--workspace', '--locked'], 'workspace-tests')
        run(['cargo', 'doc', '--workspace', '--no-deps', '--locked'], 'rustdoc')
        common = 'src/ws-ckpt/src/crates/daemon/src/backends/btrfs_common.rs'
        def abort_first(text):
            start = text.index('async fn select_valid_btrfs_mount_in')
            end = text.index('fn writable_btrfs_mounts_in', start)
            segment = text[start:end]
            assert segment.count('continue;') == 1
            return text[:start] + segment.replace('continue;', 'return Err(error);') + text[end:]
        mutant('first-invalid-aborts-mutation', common, abort_first, 'ws-ckpt-daemon', 'auto_selection_')
        util = 'src/ws-ckpt/src/crates/daemon/src/util.rs'
        def first_entry(text):
            changed, count = re.subn(r'content\s*\.lines\(\)\s*\.rev\(\)', 'content.lines()', text)
            assert count == 1
            return changed
        mutant('first-stacked-entry-mutation', util, first_entry, 'ws-ckpt-daemon',
               'fresh_selection_and_explicit_validation_agree_on_last_exact_stacked_entry')
        cli = 'src/ws-ckpt/src/crates/cli/src/main.rs'
        def omit_pin_diagnostic(text):
            start = text.index('    let saved_mount =', text.index('fn unlanded_global_settings'))
            end = text.index('    let (saved_size, landed_size)', start)
            return text[:start] + text[end:]
        mutant('missing-pin-diagnostic-mutation', cli, omit_pin_diagnostic, 'ws-ckpt-cli',
               'btrfs_pin_only_difference_names_saved_and_daemon_values')
        # Fixtures only format fresh loop-backed mappers proven to be owned by this run.
        import stat
        import tempfile
        def command(args, **kwargs):
            return subprocess.check_output(args, text=True, **kwargs).strip()
        exec((payload / 'mount_fixtures.py').read_text(), globals())
        fixtures = MountFixtures()
        try:
            env = fixtures.setup()
            run(['cargo', 'test', '-p', 'ws-ckpt-daemon', '--lib', 'startup::native_btrfs_',
                 '--', '--ignored', '--test-threads=1', '--skip', 'fresh_auto_'], 'native-restart-seven', env=env)
            first = Path(env['WS_CKPT_TEST_FIRST_MOUNT'])
            second = Path(env['WS_CKPT_TEST_PERSISTED_MOUNT'])
            for point, test in [
                (first, 'fresh_auto_uses_later_mount_after_first_is_overmounted'),
                (second, 'fresh_auto_falls_back_to_loop_when_all_candidates_are_overmounted'),
            ]:
                assert point.is_relative_to(fixtures.root)
                subprocess.run(['sudo', 'mount', '-t', 'tmpfs', '-o', 'size=1m', 'none', str(point)], check=True)
                fixtures.mounts.append(point)
                run(['cargo', 'test', '-p', 'ws-ckpt-daemon', '--lib', test,
                     '--', '--ignored', '--test-threads=1'], 'native-' + test, env=env)
        finally:
            fixtures.close()
    elif gate == 'coverage':
        version = subprocess.check_output(['cargo', 'tarpaulin', '--version'], text=True).strip()
        assert version.endswith(' 0.37.2'), version
        run(['cargo', 'tarpaulin', '--workspace', '--out', 'Stdout', '--fail-under', '45'], 'rust-coverage')
    elif gate == 'plugins-package-docs':
        run(['make', '-C', 'src/ws-ckpt', 'test-package-raw'], 'raw-package', cwd=repo)
        run(['make', '-C', 'src/ws-ckpt', 'test-openclaw-scripts'], 'openclaw-scripts', cwd=repo)
        run(['bash', 'scripts/docs-lint.sh'], 'docs-lint', cwd=repo)
        run([sys.executable, 'scripts/docs-link-check.py'], 'docs-links', cwd=repo)
        plugin = repo / 'src/ws-ckpt/src/plugins/openclaw'
        run(['npm', 'install', '-g', 'npm@10.9.0'], 'npm-version', cwd=plugin)
        run(['npm', 'ci', '--registry=https://registry.npmjs.org', '--replace-registry-host=always'], 'node-deps', cwd=plugin)
        run(['./node_modules/.bin/vitest', 'run', '--coverage'], 'openclaw-coverage', cwd=plugin)
        run([sys.executable, '-m', 'pip', 'install', 'pytest', 'pytest-cov', 'pyyaml'], 'python-deps')
        run([sys.executable, '-m', 'pytest', 'hermes/tests/', '--cov=hermes', '--cov-report=term', '--cov-fail-under=90'],
            'hermes-coverage', cwd=repo / 'src/ws-ckpt/src/plugins')
    else:
        raise AssertionError(gate)
    for path, sha in hashes.items():
        assert hashlib.sha256((repo / path).read_bytes()).hexdigest() == sha, path
    assert subprocess.check_output(['git', 'write-tree'], cwd=repo, text=True).strip() == tree
finally:
    (evidence / 'summary.json').write_text(json.dumps({
        'base': manifest['base'], 'gate': gate, 'source_tree': tree,
        'candidate_sha256': hashes, 'results': results,
        'no_extra_coverage_filters_or_serialization': True,
    }, indent=2))
