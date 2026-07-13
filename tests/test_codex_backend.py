"""M5：Codex CLI 模型后端。"""

from __future__ import annotations

import pytest

from evalcall import llm
from evalcall import judge


def test_codex_backend_is_readonly_ephemeral_and_parses_provider_tokens(monkeypatch):
    captured = {}

    class FakePopen:
        returncode = 0
        pid = 123

        def __init__(self, cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs

        def communicate(self, input=None, timeout=None):
            captured["input"] = input
            captured["timeout"] = timeout
            return '{"ok":true}\n', "tokens used\n17,800\n"

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return FakePopen(cmd, **kwargs)

    monkeypatch.setattr(llm.subprocess, "Popen", fake_popen)
    backend = llm.CodexCliBackend(model="gpt-5.6-sol")
    result = backend.chat([{"role": "system", "content": "只输出 JSON"}, {"role": "user", "content": "开始"}])
    assert result == '{"ok":true}'
    assert "--ephemeral" in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("--sandbox") + 1] == "read-only"
    assert "--ignore-user-config" in captured["cmd"]
    assert "--ignore-rules" in captured["cmd"]
    assert captured["cmd"][-1] == "-"
    assert captured["kwargs"]["start_new_session"] is True
    assert "[SYSTEM]" in captured["input"]
    assert backend._last_provider_total_tokens == 17800


def test_codex_backend_error_is_fail_closed(monkeypatch):
    class FailedPopen:
        returncode = 1
        pid = 123

        def communicate(self, input=None, timeout=None):
            return "", "model unavailable"

    monkeypatch.setattr(llm.subprocess, "Popen", lambda *args, **kwargs: FailedPopen())
    with pytest.raises(llm.LLMError, match="model unavailable"):
        llm.CodexCliBackend().chat([{"role": "user", "content": "x"}])


def test_codex_timeout_kills_entire_process_group(monkeypatch):
    calls = []

    class TimedOutPopen:
        returncode = -9
        pid = 456

        def communicate(self, input=None, timeout=None):
            if timeout is not None:
                raise llm.subprocess.TimeoutExpired("codex", timeout)
            calls.append("drained")
            return "", ""

        def kill(self):
            calls.append("kill")

    monkeypatch.setattr(llm.subprocess, "Popen", lambda *args, **kwargs: TimedOutPopen())
    monkeypatch.setattr(llm.os, "killpg", lambda pid, sig: calls.append((pid, sig)))
    backend = llm.CodexCliBackend()
    backend.timeout = 1
    with pytest.raises(llm.LLMError, match="调用超时"):
        backend.chat([{"role": "user", "content": "x"}])
    assert (456, llm.signal.SIGKILL) in calls
    assert "drained" in calls


def test_codex_provider_total_is_preserved_in_telemetry(monkeypatch):
    backend = llm.CodexCliBackend()

    def fake_chat(messages, model=None):
        backend._last_model = "gpt-5.6-sol"
        backend._last_provider_total_tokens = 1234
        return "ok"

    monkeypatch.setattr(backend, "chat", fake_chat)
    llm.reset_telemetry()
    llm._call_backend(backend, [{"role": "user", "content": "test"}], model=None, channel="main")
    snapshot = llm.telemetry_snapshot()
    assert snapshot["totals"]["provider_total_token_calls"] == 1
    assert snapshot["totals"]["provider_total_tokens"] == 1234
    assert snapshot["events"][0]["provider_total_source"] == "actual"


def test_judge_vote_records_environment_model_when_argument_is_omitted(monkeypatch):
    monkeypatch.setenv("EVALCALL_BACKEND", "codex-cli")
    monkeypatch.setenv("EVALCALL_MODEL", "gpt-5.6-sol")
    monkeypatch.setattr(
        judge,
        "_judge_batch_llm",
        lambda batch, transcript, model: {
            cp["id"]: {"verdict": "pass", "confidence": 0.9, "evidence": []} for cp in batch
        },
    )
    rows = judge.judge_trajectory(
        [{"id": "c", "type": "flow", "text": "问候", "severity": "major"}],
        {"turns": [{"turn": 0, "role": "agent", "content": "你好"}]},
        n_votes=1,
    )
    assert rows[0]["judge_votes"][0]["model"] == "gpt-5.6-sol"
