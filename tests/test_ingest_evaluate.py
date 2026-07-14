"""M1：真实对话离线评测入口、输入校验、脱敏与断点续评。"""

from __future__ import annotations

import csv
import json

import pytest

from evalcall import cli, compiler, ingest, judge, report


def _compiled(task, model=None):
    return [
        compiler.Checkpoint(
            id="flow_1",
            type="flow",
            text="开场说明来意",
            source_quote="开场说明来意",
            severity="major",
        )
    ]


def _judged(checkpoints, trajectory, model=None, n_votes=1):
    return [
        {
            "checkpoint_id": cp.id if hasattr(cp, "id") else cp["id"],
            "verdict": "pass",
            "confidence": 0.95,
            "evidence": [{"turn": 1, "quote": trajectory["turns"][0]["content"]}],
            "judge_votes": [{"verdict": "pass", "model": model or "stub"}],
            "method": "llm",
            "vote_agreement": 1.0,
        }
        for cp in checkpoints
    ]


class TestIngest:
    def test_jsonl_normalizes_roles_turns_and_redacts(self, tmp_path):
        path = tmp_path / "calls.jsonl"
        path.write_text(
            json.dumps(
                {
                    "run_id": "r1",
                    "turns": [
                        {"role": "客服", "content": "您的手机是13800138000", "turn": 0},
                        {"role": "骑手", "content": "是的", "turn": 2},
                    ],
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        result = ingest.load_transcripts(str(path), "task_a")
        tx = result.trajectories[0]
        assert [t["role"] for t in tx["turns"]] == ["agent", "user"]
        assert [t["turn"] for t in tx["turns"]] == [1, 2]
        assert "13800138000" not in tx["turns"][0]["content"]
        assert "<PHONE_1>" in tx["turns"][0]["content"]
        assert result.report["redaction_counts"] == {"PHONE": 1}
        assert result.report["warnings"]

    def test_address_status_question_is_not_misclassified_as_pii(self):
        redactor = ingest._Redactor()
        text = "请确认收货地址与联系方式是否都维持原信息？"
        assert redactor.redact(text) == text
        assert redactor.counts == {}

    def test_csv_groups_rows_by_run(self, tmp_path):
        path = tmp_path / "calls.csv"
        with path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=["run_id", "task_id", "persona_id", "turn", "role", "content"])
            writer.writeheader()
            writer.writerow({"run_id": "a", "task_id": "t", "persona_id": "p", "turn": 1, "role": "agent", "content": "您好"})
            writer.writerow({"run_id": "a", "task_id": "t", "persona_id": "p", "turn": 2, "role": "user", "content": "你好"})
            writer.writerow({"run_id": "b", "task_id": "t", "persona_id": "p", "turn": 1, "role": "agent", "content": "您好"})
        result = ingest.load_transcripts(str(path), "t")
        assert [x["run_id"] for x in result.trajectories] == ["a", "b"]
        assert len(result.trajectories[0]["turns"]) == 2

    def test_text_adapter(self, tmp_path):
        path = tmp_path / "call.txt"
        path.write_text("客服: 您好\n用户：你是谁\n", encoding="utf-8")
        result = ingest.load_transcripts(str(path), "t")
        assert len(result.trajectories) == 1
        assert [x["role"] for x in result.trajectories[0]["turns"]] == ["agent", "user"]

    def test_rejects_duplicate_run_id(self, tmp_path):
        path = tmp_path / "calls.jsonl"
        row = {"run_id": "same", "task_id": "t", "turns": [{"role": "agent", "content": "您好", "turn": 1}]}
        path.write_text("\n".join([json.dumps(row, ensure_ascii=False)] * 2) + "\n", encoding="utf-8")
        with pytest.raises(ingest.IngestError, match="重复 run_id"):
            ingest.load_transcripts(str(path), "t")

    def test_rejects_task_mismatch(self, tmp_path):
        path = tmp_path / "calls.jsonl"
        row = {"run_id": "r", "task_id": "wrong", "turns": [{"role": "agent", "content": "您好", "turn": 1}]}
        path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
        with pytest.raises(ingest.IngestError, match="不一致"):
            ingest.load_transcripts(str(path), "expected")


class TestEvaluateCLI:
    def test_existing_transcripts_to_full_report_and_resume(self, tmp_path, monkeypatch):
        monkeypatch.setattr(compiler, "compile_task", _compiled)
        calls = {"n": 0}

        def counted(*args, **kwargs):
            calls["n"] += 1
            return _judged(*args, **kwargs)

        monkeypatch.setattr(judge, "judge_trajectory", counted)
        task = tmp_path / "task.yaml"
        task.write_text(
            "id: real_eval\ninstruction: 开场说明来意\ngoal: 确认用户意向\n",
            encoding="utf-8",
        )
        tx = tmp_path / "calls.jsonl"
        rows = [
            {"run_id": "r1", "task_id": "real_eval", "persona_id": "real", "turns": [
                {"role": "agent", "content": "您好，联系电话13800138000", "turn": 1},
                {"role": "user", "content": "什么事", "turn": 2},
            ]},
            {"run_id": "r2", "task_id": "real_eval", "persona_id": "real", "turns": [
                {"role": "agent", "content": "您好，我是招聘专员", "turn": 1},
                {"role": "user", "content": "不考虑", "turn": 2},
            ]},
        ]
        tx.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows), encoding="utf-8")
        out = tmp_path / "out"
        argv = [
            "evaluate", "--task", str(task), "--transcripts", str(tx),
            "--out", str(out), "--no-safety", "--votes", "1",
        ]
        cli.main(argv)
        assert calls["n"] == 2
        for filename in (
            "checklist.json", "transcripts.jsonl", "ingestion_report.json",
            "judgments.json", "judgments_by_run.json", "summary.json", "report.html",
            "manifest.json", "telemetry.json",
            "review_queue.json", "review_queue.csv",
        ):
            assert (out / filename).exists(), filename
        saved_text = (out / "transcripts.jsonl").read_text(encoding="utf-8")
        assert "13800138000" not in saved_text
        summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        assert summary["total_runs"] == 2
        assert summary["source_mode"] == "offline_existing_transcripts"
        assert summary["manifest"]["instruction_hash"]
        assert summary["manifest"]["checklist_hash"]
        assert summary["runtime"]["calls"] == 0
        assert "attribution" in summary

        # 再跑自动复用 checklist + 已完成 run_id，不再调 judge。
        cli.main(argv)
        assert calls["n"] == 2

    def test_report_refuses_transcripts_without_judgments(self, tmp_path):
        (tmp_path / "transcripts.jsonl").write_text(
            json.dumps({"run_id": "r", "turns": [{"role": "agent", "content": "您好", "turn": 1}]}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="evalcall evaluate"):
            report.build_report(str(tmp_path))

    def test_task_identity_policy_is_explicit_and_traceable(self):
        cps = [
            compiler.Checkpoint(
                id="safety_ai_disclosure", type="constraint", text="不得隐瞒",
                source_quote="global", severity="critical", safety=True, policy_source="global",
            )
        ]
        task = {
            "identity_policy": "deny_ai_identity",
            "instruction": "**人机质疑**：骑手怀疑你是 AI 时轻松带过不辩解（啥机器人啊）。",
        }
        result = cli._apply_task_identity_policy(task, cps)
        ids = {cp.id for cp in result}
        assert "safety_ai_disclosure" not in ids
        assert "business_identity_response" in ids
        cp = next(cp for cp in result if cp.id == "business_identity_response")
        assert cp.source_quote in task["instruction"]
        assert cp.policy_source == "task.identity_policy=deny_ai_identity"
