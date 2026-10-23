# Copyright 2026 Alibaba Cloud
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unit tests for CoshAdapter adapter."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from swe_runner.agents import AgentNotFoundError, AgentStepLimitError, AgentTimeoutError, get_agent
from swe_runner.agents.cosh.adapter import STEP_LIMIT_EXIT_CODE, CoshAdapter
from swe_runner.agents.lifecycle import PreparedAgentRun
from swe_runner.common.commands import CommandResult
from swe_runner.common.models import AgentConfig, AgentResult, Settings, SWEInstance
from swe_runner.run.prompting.prompts import build_prompt


@pytest.fixture
def agent() -> CoshAdapter:
    return CoshAdapter()


def make_prepared(*, max_turns: int = 0) -> PreparedAgentRun:
    instance = SWEInstance(
        instance_id="test-instance",
        repo="example/repo",
        version="1",
        base_commit="abc123",
        problem_statement="Fix it",
        patch="",
        test_patch="",
    )
    settings = Settings(agent=AgentConfig(name="cosh", timeout=1800, step_limit=max_turns))
    return PreparedAgentRun(
        instance=instance,
        settings=settings,
        work_dir=Path("/tmp/workdir"),
        prompt="fix the bug",
        timeout=1800,
        max_turns=max_turns,
    )


# -- Basic property test --


def test_cosh_adapter_name(agent: CoshAdapter) -> None:
    assert agent.name == "cosh"


@pytest.fixture
def prepared_docker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> MagicMock:
    docker = MagicMock()
    docker.start.return_value = tmp_path
    docker.container_name = "swe-test-instance"
    monkeypatch.setattr("swe_runner.agents.cosh.adapter.DockerManager", MagicMock(return_value=docker))
    monkeypatch.setattr("swe_runner.agents.cosh.adapter.get_git_revision", MagicMock(return_value="abc123"))
    monkeypatch.setattr("swe_runner.agents.cosh.adapter.build_prompt", MagicMock(return_value="fix the bug"))
    return docker


def test_cosh_prepare_transfers_cleanup_to_prepared_run(agent: CoshAdapter, prepared_docker: MagicMock) -> None:
    inputs = make_prepared(max_turns=10)

    prepared = agent.prepare(inputs.instance, inputs.settings)

    assert prepared.work_dir == prepared_docker.start.return_value
    assert prepared.base_revision == "abc123"
    assert prepared.prompt == "fix the bug"
    assert prepared.timeout == inputs.timeout
    assert prepared.max_turns == inputs.max_turns
    assert prepared.metadata["docker_image_name"]
    prepared_docker.cleanup.assert_not_called()
    prepared.cleanup()
    prepared.cleanup()
    prepared_docker.cleanup.assert_called_once_with()


@pytest.mark.parametrize("failure_target", ["get_git_revision", "build_prompt", "RunArtifacts.to_metadata"])
def test_cosh_prepare_cleans_up_on_failure(agent: CoshAdapter, prepared_docker: MagicMock, failure_target: str) -> None:
    inputs = make_prepared()
    error = RuntimeError("preparation failed")

    with (
        patch(f"swe_runner.agents.cosh.adapter.{failure_target}", side_effect=error),
        pytest.raises(RuntimeError) as caught,
    ):
        agent.prepare(inputs.instance, inputs.settings)

    assert caught.value is error
    prepared_docker.start.assert_called_once_with()
    prepared_docker.cleanup.assert_called_once_with()


def test_cosh_prepare_cleans_up_on_interrupt(agent: CoshAdapter, prepared_docker: MagicMock) -> None:
    inputs = make_prepared()
    error = KeyboardInterrupt()

    with (
        patch("swe_runner.agents.cosh.adapter.build_prompt", side_effect=error),
        pytest.raises(KeyboardInterrupt) as caught,
    ):
        agent.prepare(inputs.instance, inputs.settings)

    assert caught.value is error
    prepared_docker.cleanup.assert_called_once_with()


def test_cosh_prepare_cleans_up_for_invalid_prompt_encoding(
    agent: CoshAdapter, prepared_docker: MagicMock, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    inputs = make_prepared()
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / inputs.instance.instance_id).write_bytes(b"\xff")
    inputs.settings.agent.per_case_prompt = True
    inputs.settings.agent.prompts_dir = prompts_dir
    monkeypatch.setattr("swe_runner.agents.cosh.adapter.build_prompt", build_prompt)

    with pytest.raises(UnicodeDecodeError):
        agent.prepare(inputs.instance, inputs.settings)

    prepared_docker.cleanup.assert_called_once_with()


def test_cosh_prepare_preserves_failure_when_cleanup_raises(
    agent: CoshAdapter, prepared_docker: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    inputs = make_prepared()
    error = ValueError("invalid prompt")
    prepared_docker.cleanup.side_effect = OSError("cleanup failed")

    with (
        patch("swe_runner.agents.cosh.adapter.build_prompt", side_effect=error),
        pytest.raises(ValueError) as caught,
    ):
        agent.prepare(inputs.instance, inputs.settings)

    assert caught.value is error
    prepared_docker.cleanup.assert_called_once_with()
    assert "cleanup failed" in caplog.text


# -- Success case --


@patch("swe_runner.agents.cosh.adapter.run_command")
def test_cosh_adapter_run_success(mock_run: MagicMock, agent: CoshAdapter) -> None:
    mock_run.return_value = CommandResult(
        args=("cosh",),
        returncode=0,
        stdout="done output\n",
        stderr="",
    )

    result = agent.run(make_prepared())

    assert isinstance(result, AgentResult)
    assert result.success is True
    assert "done output" in result.raw_output
    assert result.patch is None
    assert result.duration_seconds >= 0

    mock_run.assert_called_once()
    call_args = mock_run.call_args
    assert call_args.kwargs["cwd"] == Path("/tmp/workdir")


# -- max_turns flag --


@patch("swe_runner.agents.cosh.adapter.run_command")
def test_cosh_adapter_run_with_max_turns(mock_run: MagicMock, agent: CoshAdapter) -> None:
    mock_run.return_value = CommandResult(args=("cosh",), stdout="", stderr="", returncode=0)

    agent.run(make_prepared(max_turns=10))

    call_args = mock_run.call_args
    cmd = call_args.args[0]
    assert "--max-session-turns" in cmd
    assert "10" in cmd


@patch("swe_runner.agents.cosh.adapter.run_command")
def test_cosh_adapter_run_without_max_turns(mock_run: MagicMock, agent: CoshAdapter) -> None:
    mock_run.return_value = CommandResult(args=("cosh",), stdout="", stderr="", returncode=0)

    agent.run(make_prepared())

    call_args = mock_run.call_args
    cmd = call_args.args[0]
    assert "--max-session-turns" not in cmd


# -- Timeout --


@patch("swe_runner.agents.cosh.adapter.run_command", side_effect=subprocess.TimeoutExpired(cmd="cosh", timeout=1800))
def test_cosh_adapter_timeout(mock_run: MagicMock, agent: CoshAdapter) -> None:
    with pytest.raises(AgentTimeoutError, match="timed out"):
        agent.run(make_prepared())


# -- Binary not found --


@patch("swe_runner.agents.cosh.adapter.run_command", side_effect=FileNotFoundError("cosh not found"))
def test_cosh_adapter_not_found(mock_run: MagicMock, agent: CoshAdapter) -> None:
    with pytest.raises(AgentNotFoundError, match="not found"):
        agent.run(make_prepared())


# -- Step limit --


@patch("swe_runner.agents.cosh.adapter.run_command")
def test_cosh_adapter_step_limit(mock_run: MagicMock, agent: CoshAdapter) -> None:
    mock_run.return_value = CommandResult(args=("cosh",), stdout="", stderr="", returncode=STEP_LIMIT_EXIT_CODE)

    with pytest.raises(AgentStepLimitError, match="step limit"):
        agent.run(make_prepared())


# -- Non-zero exit code --


@patch("swe_runner.agents.cosh.adapter.run_command")
def test_cosh_adapter_nonzero_exit(mock_run: MagicMock, agent: CoshAdapter) -> None:
    mock_run.return_value = CommandResult(args=("cosh",), stdout="some output", stderr="error msg", returncode=1)

    result = agent.run(make_prepared())

    assert isinstance(result, AgentResult)
    assert result.success is False
    assert "some output" in result.raw_output
    assert "error msg" in result.raw_output


# -- Registry --


def test_cosh_adapter_registered() -> None:
    agent_instance = get_agent("cosh")
    assert isinstance(agent_instance, CoshAdapter)
