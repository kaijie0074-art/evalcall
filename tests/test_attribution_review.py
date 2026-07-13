"""M3：根因归属、行动建议、人工复核导出与不覆写回填。"""

from __future__ import annotations

import json

import pytest

from evalcall import attribution, cli, review


def _judgment(cid, verdict, *, run="r", persona="p", severity="major", **extra):
    row = {
        "run_id": run,
        "task_id": "t",
        "persona_id": persona,
        "checkpoint_id": cid,
        "text": cid,
        "source_quote": "原文",
        "severity": severity,
        "type": "constraint",
        "verdict": verdict,
        "confidence": 0.9,
        "evidence": [],
    }
    row.update(extra)
    return row


class TestAttribution:
    def test_instruction_problem_is_primary_when_lint_is_low(self, tmp_path):
        lint = tmp_path / "lint.json"
        lint.write_text(
            json.dumps({"feasibility_score": 20, "findings": [{"severity": "high"}, {"severity": "high"}]}),
            encoding="utf-8",
        )
        judgments = [_judgment(f"c{i}", "fail" if i < 5 else "pass") for i in range(10)]
        out = attribution.analyze([], judgments, {}, task_id="t", lint_path=str(lint))
        assert out["primary_category"] == "instruction"
        assert out["primary_confidence"] == "high"
        assert "SOP" in out["primary_label"]

    def test_high_na_attributes_to_judge_not_target(self):
        judgments = [_judgment(f"c{i}", "na" if i < 8 else "fail") for i in range(10)]
        out = attribution.analyze([], judgments, {}, task_id="no_lint")
        assert out["primary_category"] == "judge"
        assert not any(root["category"] == "target_model" and root["confidence"] == "high" for root in out["roots"])

    def test_clean_high_failure_attributes_to_target_model(self):
        judgments = [_judgment(f"c{i}", "fail" if i < 5 else "pass") for i in range(12)]
        out = attribution.analyze([], judgments, {}, task_id="no_lint")
        assert out["primary_category"] == "target_model"

    def test_scratch_lint_files_are_not_auto_loaded(self, tmp_path, monkeypatch):
        lint_dir = tmp_path / "runs" / "lint"
        lint_dir.mkdir(parents=True)
        (lint_dir / "t_scratch_lint.json").write_text(
            json.dumps({"feasibility_score": 0, "findings": [{"severity": "high"}]}),
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        judgments = [_judgment(f"c{i}", "fail" if i < 5 else "pass") for i in range(12)]
        out = attribution.analyze([], judgments, {}, task_id="t_scratch")
        assert out["primary_category"] == "target_model"
        assert out["signals"]["instruction_feasibility"] is None

    def test_persona_concentration_is_secondary_signal(self):
        judgments = []
        for i in range(8):
            judgments.append(_judgment(f"a{i}", "fail", run=f"a{i}", persona="extreme"))
        judgments += [_judgment("b", "fail", run="b", persona="normal")]
        judgments += [_judgment("c", "pass", run="c", persona="quiet")]
        out = attribution.analyze([], judgments, {}, task_id="no_lint")
        assert any(root["category"] == "test_data" for root in out["roots"])
        assert out["primary_category"] == "test_data"

    def test_operational_failures_attribute_to_target_even_when_item_fail_rate_is_low(self):
        judgments = [
            _judgment(f"c{i}", "fail" if i < 13 else "pass", run=f"r{i % 6}", severity="critical" if i < 2 else "major")
            for i in range(100)
        ]
        summary = {
            "total_runs": 6,
            "blocked_runs": 2,
            "critical_failed_runs": 2,
            "fulfillment_rate": 50.0,
            "avg_judge_disagreement_rate": 0.02,
        }
        out = attribution.analyze([], judgments, summary, task_id="healthy_sop")
        assert out["primary_category"] == "target_model"
        assert out["signals"]["fail_rate"] == 0.13
        assert out["signals"]["call_block_rate"] == pytest.approx(1 / 3, abs=0.0001)
        assert out["signals"]["p0_trigger_rate"] == pytest.approx(1 / 3, abs=0.0001)
        assert out["signals"]["fulfillment_rate"] == 0.5

    def test_strong_instruction_problem_outweighs_model_symptoms(self, tmp_path):
        lint = tmp_path / "lint.json"
        lint.write_text(
            json.dumps({"feasibility_score": 30, "findings": [{"severity": "high"}, {"severity": "high"}]}),
            encoding="utf-8",
        )
        judgments = [_judgment(f"c{i}", "fail" if i < 20 else "pass", run=f"r{i % 10}") for i in range(50)]
        summary = {"total_runs": 10, "blocked_runs": 8, "critical_failed_runs": 8, "fulfillment_rate": 20.0}
        out = attribution.analyze([], judgments, summary, task_id="broken_sop", lint_path=str(lint))
        assert out["primary_category"] == "instruction"
        target = next(root for root in out["roots"] if root["category"] == "target_model")
        assert target["confidence"] == "low"

    def test_strong_judge_failure_outweighs_model_symptoms(self):
        judgments = [_judgment(f"c{i}", "na" if i < 60 else "fail" if i < 80 else "pass") for i in range(100)]
        summary = {
            "total_runs": 10,
            "blocked_runs": 5,
            "critical_failed_runs": 5,
            "fulfillment_rate": 30.0,
            "avg_judge_disagreement_rate": 0.25,
        }
        out = attribution.analyze([], judgments, summary, task_id="healthy_sop")
        assert out["primary_category"] == "judge"
        assert out["signals"]["judge_healthy"] is False

    def test_healthy_batch_remains_uncertain_instead_of_forcing_blame(self):
        judgments = [_judgment(f"c{i}", "pass") for i in range(20)]
        summary = {"total_runs": 4, "blocked_runs": 0, "critical_failed_runs": 0, "fulfillment_rate": 100.0}
        out = attribution.analyze([], judgments, summary, task_id="healthy_sop")
        assert out["primary_category"] == "uncertain"


class TestReview:
    def test_queue_selects_p0_na_and_split(self):
        judgments = [
            _judgment("p0", "fail", severity="critical"),
            _judgment("na", "na"),
            _judgment("split", "pass", needs_human_review=True),
            _judgment("clean", "pass"),
        ]
        queue = review.build_review_queue(judgments)
        assert {row["checkpoint_id"] for row in queue} == {"p0", "na", "split"}
        assert all(row["review_id"].startswith("rev_") for row in queue)

    def test_apply_preserves_machine_verdict(self):
        machine = [_judgment("c", "fail", severity="critical")]
        rid = review.review_id(machine[0])
        applied = review.apply_decisions(
            machine,
            [{"review_id": rid, "human_verdict": "pass", "reviewer": "qa", "comment": "误报"}],
        )
        assert machine[0]["verdict"] == "fail"
        assert applied[0]["machine_verdict"] == "fail"
        assert applied[0]["verdict"] == "pass"
        assert applied[0]["human_verdict"] == "pass"

    def test_unknown_review_id_rejected(self):
        with pytest.raises(ValueError, match="未知 review_id"):
            review.apply_decisions([_judgment("c", "fail")], [{"review_id": "bad", "human_verdict": "pass"}])

    def test_cli_apply_creates_separate_human_report(self, tmp_path):
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        checklist = [{"id": "c", "type": "constraint", "text": "要求", "source_quote": "原文", "severity": "critical"}]
        machine = [_judgment("c", "fail", severity="critical")]
        transcript = {"run_id": "r", "task_id": "t", "persona_id": "p", "turns": [
            {"role": "agent", "content": "您好", "turn": 1}, {"role": "user", "content": "你好", "turn": 2},
        ], "meta": {}}
        (run_dir / "checklist.json").write_text(json.dumps(checklist, ensure_ascii=False), encoding="utf-8")
        (run_dir / "judgments.json").write_text(json.dumps(machine, ensure_ascii=False), encoding="utf-8")
        (run_dir / "transcripts.jsonl").write_text(json.dumps(transcript, ensure_ascii=False) + "\n", encoding="utf-8")
        rid = review.review_id(machine[0])
        decisions = tmp_path / "decisions.json"
        decisions.write_text(json.dumps([{"review_id": rid, "human_verdict": "pass", "reviewer": "qa"}]), encoding="utf-8")
        out = tmp_path / "human"
        cli.main(["review-apply", "--run", str(run_dir), "--decisions", str(decisions), "--out", str(out)])
        assert (out / "report.html").exists()
        reviewed = json.loads((out / "judgments.json").read_text(encoding="utf-8"))
        summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        assert reviewed[0]["machine_verdict"] == "fail"
        assert reviewed[0]["verdict"] == "pass"
        assert summary["review_mode"] == "human_final"
        assert summary["human_decisions_applied"] == 1
        assert not (run_dir / "summary.json").exists()  # 原目录未被回填覆写
