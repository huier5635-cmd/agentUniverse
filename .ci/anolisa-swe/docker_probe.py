"""Independent Linux service-lifecycle acceptance, outside the proposed PR."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from swe_runner.agents.cosh.adapter import CoshAdapter
from swe_runner.common.models import AgentConfig, Settings, SWEInstance
from swe_runner.run.workspace.docker import DockerManager


def command(*args):
    return subprocess.run(args, check=True, text=True, capture_output=True).stdout.strip()


mode = sys.argv[1]
image = 'anolisa-swe-cleanup-fixture:local'
results = []
with tempfile.TemporaryDirectory(prefix='anolisa-cleanup-') as temp:
    root = Path(temp)
    if mode == 'baseline':
        (root / 'seed').mkdir()
        (root / 'seed/fixture.txt').write_text('Lifecycle fixture\n')
        (root / 'Dockerfile').write_text('FROM alpine:3.21\nCOPY seed/ /testbed/\n')
        print(command('docker', 'build', '-t', image, str(root)))
    for scenario in ('invalid-prompt', 'success'):
        instance = SWEInstance(instance_id=f'ci-swe-cleanup-{mode}-{scenario}', repo='fixture/repo',
                               version='1', base_commit='fixture', problem_statement='fixture',
                               patch='', test_patch='', image_name=image)
        prompts = root / ('prompts-' + scenario)
        prompts.mkdir()
        (prompts / instance.instance_id).write_bytes(b'\xff' if scenario == 'invalid-prompt' else b'valid instructions')
        settings = Settings(agent=AgentConfig(name='cosh',per_case_prompt=True,prompts_dir=prompts))
        manager = DockerManager(image,instance_id=instance.instance_id,work_dir=root / ('work-' + scenario))

        def running():
            return manager.container_name in command('docker','ps','--format','{{.Names}}').splitlines()

        try:
            # The fixture image is built locally; all start/copy/stop calls remain real.
            with patch('swe_runner.agents.cosh.adapter.DockerManager',return_value=manager), \
                 patch('swe_runner.run.workspace.docker.pull_docker_image'):
                if scenario == 'invalid-prompt':
                    try:
                        CoshAdapter().prepare(instance,settings)
                    except UnicodeDecodeError:
                        pass
                    else:
                        raise AssertionError('real malformed UTF-8 prompt did not raise')
                    alive = running()
                    assert alive == (mode == 'baseline'), (mode,scenario,alive)
                    assert manager.work_dir.exists() == (mode == 'baseline')
                    results.append({'scenario':scenario,'container_running_after_error':alive,
                                    'workspace_exists_after_error':manager.work_dir.exists()})
                else:
                    prepared = CoshAdapter().prepare(instance,settings)
                    assert running() and prepared.work_dir.exists()
                    assert 'valid instructions' in prepared.prompt
                    prepared.cleanup()
                    prepared.cleanup()
                    assert not running() and not manager.work_dir.exists()
                    results.append({'scenario':scenario,'container_running_before_cleanup':True,
                                    'container_running_after_cleanup':False,'workspace_removed':True})
        finally:
            manager.cleanup(timeout=1)
            assert not running()
print(json.dumps({'mode':mode,'results':results}))
