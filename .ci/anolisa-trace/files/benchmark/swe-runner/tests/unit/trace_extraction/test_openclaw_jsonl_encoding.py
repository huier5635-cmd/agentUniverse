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

"""Keep usable session records when an individual JSONL line is damaged."""

import json
from pathlib import Path

import pytest

from swe_runner.trace_extraction import record_openclaw_jsonl_traces_in_window
from swe_runner.trace_extraction.helpers import parse_time_value
from swe_runner.trace_extraction.openclaw_jsonl import _read_jsonl

REPLY = "保留完整的中文和 emoji 🧪"


def session_lines(issue_id: str) -> list[bytes]:
    records = [
        {"type": "session", "id": issue_id, "timestamp": "2026-04-24T00:00:00Z"},
        {
            "type": "message",
            "timestamp": "2026-04-24T00:00:01Z",
            "message": {"role": "user", "content": [{"type": "text", "text": f"Issue ID: {issue_id}"}]},
        },
        {
            "type": "message",
            "timestamp": "2026-04-24T00:00:02Z",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": REPLY}],
                "usage": {"input": 11, "output": 7},
            },
        },
    ]
    return [json.dumps(record, ensure_ascii=False).encode("utf-8") for record in records]


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
@pytest.mark.parametrize("truncated_tail", [False, True])
def test_recorder_keeps_good_records_and_later_sessions(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, newline: bytes, truncated_tail: bool
) -> None:
    profiles = tmp_path / "profiles"
    sessions = profiles / "fixture" / "agents" / "fixture" / "sessions"
    sessions.mkdir(parents=True)
    damaged = sessions / "a-damaged.jsonl"
    healthy = sessions / "b-healthy.jsonl"
    lines = session_lines("damaged__case-1")
    if truncated_tail:
        payload = newline.join(lines) + newline + b'{"message":"\xe4\xb8'
        bad_line_number = 4
    else:
        payload = newline.join([*lines[:2], b'{"message":"\xff"}', lines[2]])
        bad_line_number = 3
    damaged.write_bytes(payload)
    healthy_payload = newline.join(session_lines("healthy__case-2"))
    healthy.write_bytes(healthy_payload)

    recorded = record_openclaw_jsonl_traces_in_window(
        parse_time_value("2026-04-24T00:00:00Z"),
        parse_time_value("2026-04-24T00:00:10Z"),
        profiles_root=profiles,
        trace_root=tmp_path / "traces",
    )

    traces = {}
    for path in recorded:
        data = json.loads(path.read_text(encoding="utf-8"))
        traces[data["issue_id"]] = data
    assert set(traces) == {"damaged__case-1", "healthy__case-2"}
    for trace in traces.values():
        assert trace["total_steps"] == 1
        assert trace["total_input_tokens"] == 11
        assert trace["total_output_tokens"] == 7
        assert trace["steps"][0]["assistant_output"] == [{"type": "text", "content": REPLY}]
    warnings = [record.getMessage() for record in caplog.records if record.levelname == "WARNING"]
    assert len(warnings) == 1
    assert str(damaged) in warnings[0]
    assert f"line={bad_line_number}" in warnings[0]
    assert damaged.read_bytes() == payload
    assert healthy.read_bytes() == healthy_payload


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_valid_unicode_and_unterminated_final_record_are_preserved(tmp_path: Path, newline: bytes) -> None:
    path = tmp_path / "session.jsonl"
    lines = session_lines("valid__case-3")
    path.write_bytes(newline.join([b"", *lines, b"null", b"[]", b" ", '{"last":"完整🧪"}'.encode()]))

    entries = _read_jsonl(path)

    assert entries == [*(json.loads(line.decode("utf-8")) for line in lines), {"last": "完整🧪"}]


def test_missing_session_is_still_an_io_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        _read_jsonl(tmp_path / "missing.jsonl")


def test_session_permission_error_is_not_suppressed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(*args: object, **kwargs: object) -> None:
        raise PermissionError("session cannot be read")

    monkeypatch.setattr(Path, "open", denied)

    with pytest.raises(PermissionError, match="session cannot be read"):
        _read_jsonl(tmp_path / "session.jsonl")
