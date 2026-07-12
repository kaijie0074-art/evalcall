"""M2：调用遥测、成本口径、run manifest 和可比性门禁。"""

from __future__ import annotations

import json

import pytest

from evalcall import cli, llm, provenance


class _UsageBackend:
    name = "usage-test"
    default_model = "priced-model"

    def __init__(self):
        self._last_usage = None
        self._last_model = None

    def chat(self, messages, model=None):
        self._last_usage = {"input_tokens": 100, "output_tokens": 20}
        self._last_model = model or self.default_model
        return "返回内容"


class TestTelemetry:
    def test_records_phase_usage_without_prompt_content(self):
        llm.reset_telemetry()
        backend = _UsageBackend()
        secret = "这是不应进遥测的对话原文"
        with llm.telemetry_phase("judge"):
            result = llm._call_backend(
                backend,
                [{"role": "user", "content": secret}],
                model=None,
                channel="main",
            )
        assert result == "返回内容"
        snapshot = llm.telemetry_snapshot()
        assert snapshot["totals"]["calls"] == 1
        assert snapshot["totals"]["total_tokens"] == 120
        assert snapshot["totals"]["actual_token_calls"] == 1
        assert snapshot["by_phase"]["judge"]["calls"] == 1
        assert secret not in json.dumps(snapshot, ensure_ascii=False)

    def test_estimates_when_backend_has_no_usage(self):
        class NoUsage:
            name = "no-usage"
            default_model = "x"

            def chat(self, messages, model=None):
                return "好的"

        llm.reset_telemetry()
        llm._call_backend(NoUsage(), [{"role": "user", "content": "你好"}], model=None, channel="main")
        snapshot = llm.telemetry_snapshot()
        assert snapshot["totals"]["estimated_token_calls"] == 1
        assert snapshot["totals"]["total_tokens"] > 0


class TestProvenance:
    def test_price_telemetry_priced_and_unknown(self):
        telemetry = {
            "totals": {"calls": 2},
            "events": [
                {"model": "priced-model", "input_tokens": 1_000_000, "output_tokens": 500_000, "token_source": "actual"},
                {"model": "unknown-model", "input_tokens": 10, "output_tokens": 2, "token_source": "estimated"},
            ],
        }
        prices = {
            "currency": "CNY",
            "models": {"priced-model": {"input_per_million": 2, "output_per_million": 4}},
            "source_file": "test",
        }
        out = provenance.price_telemetry(telemetry, prices)
        assert out["status"] == "partially_priced"
        assert out["total_cost"] == 4.0
        assert out["unknown_models"] == ["unknown-model"]
        assert out["token_measurement"] == "estimated_or_mixed"

    def test_manifest_hashes_and_comparability(self, tmp_path):
        task_path = tmp_path / "task.yaml"
        task_path.write_text("id: t\ninstruction: 必须问候\n", encoding="utf-8")
        task = {"id": "t", "instruction": "必须问候"}
        checklist = [{"id": "c", "type": "flow", "text": "问候", "source_quote": "必须问候", "severity": "major"}]
        one = provenance.build_manifest(
            task=task, task_path=str(task_path), checklist=checklist,
            source_mode="offline", n_votes=3, model="judge-x",
        )
        two = provenance.build_manifest(
            task=task, task_path=str(task_path), checklist=checklist,
            source_mode="offline", n_votes=3, model="judge-x",
        )
        assert one["instruction_hash"] == two["instruction_hash"]
        assert one["checklist_hash"] == two["checklist_hash"]
        assert provenance.compare_manifests(one, two)["comparable"] is True
        two["checklist_hash"] = "changed"
        result = provenance.compare_manifests(one, two)
        assert result["comparable"] is False
        assert "checklist_hash 不一致" in result["reasons"]

    def test_missing_manifest_is_not_comparable(self):
        out = provenance.compare_manifests(None, None)
        assert out["status"] == "unknown"
        assert out["comparable"] is False

    def test_offline_manifest_does_not_claim_a_target_model(self, tmp_path):
        task_path = tmp_path / "task.yaml"
        task_path.write_text("id: t\ninstruction: 必须问候\n", encoding="utf-8")
        manifest = provenance.build_manifest(
            task={"id": "t", "instruction": "必须问候"},
            task_path=str(task_path),
            checklist=[],
            source_mode="offline_existing_transcripts",
            n_votes=1,
            model="judge-x",
        )
        assert manifest["target_model_fingerprint"] == {
            "backend": "existing-transcript",
            "model": "not_applicable",
        }


class TestVotesPrecedence:
    def test_environment_sets_default_and_explicit_flag_wins(self, monkeypatch):
        monkeypatch.setenv("N_VOTES", "2")
        parser = cli.build_parser()
        assert parser.parse_args(["evaluate", "--task", "t", "--transcripts", "x"]).votes == 2
        assert parser.parse_args(["evaluate", "--task", "t", "--transcripts", "x", "--votes", "5"]).votes == 5

    def test_invalid_environment_falls_back_to_three(self, monkeypatch):
        monkeypatch.setenv("N_VOTES", "bad")
        parser = cli.build_parser()
        assert parser.parse_args(["run", "--task", "t"]).votes == 3


class TestDiffComparabilityGate:
    def test_missing_manifest_is_blocked_unless_explicit_override(self, tmp_path):
        base = tmp_path / "base"
        new = tmp_path / "new"
        base.mkdir()
        new.mkdir()
        judgments = [
            {"checkpoint_id": "c", "verdict": "pass", "method": "llm", "type": "flow", "text": "x"}
        ]
        (base / "judgments.json").write_text(json.dumps(judgments), encoding="utf-8")
        (new / "judgments.json").write_text(json.dumps(judgments), encoding="utf-8")
        with pytest.raises(SystemExit):
            cli.main(["diff", "--base", str(base), "--new", str(new)])
        cli.main(["diff", "--base", str(base), "--new", str(new), "--allow-incomparable"])
