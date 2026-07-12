"""M5：编译器黄金集草稿与 sealed holdout 防泄漏。"""

from __future__ import annotations

import json

import pytest

import calibrate
from evalcall.compiler_calibration import build_draft, score_prediction


def test_compiler_draft_stays_pending_and_checks_sources(tmp_path):
    task = tmp_path / "task.yaml"
    checklist = tmp_path / "checklist.json"
    task.write_text("id: t\ninstruction: 必须问候并说明来意\n", encoding="utf-8")
    checklist.write_text(
        json.dumps([
            {"id": "a", "text": "问候", "type": "flow", "severity": "major", "source_quote": "必须问候"},
            {"id": "b", "text": "编造", "type": "flow", "severity": "minor", "source_quote": "不存在"},
        ], ensure_ascii=False),
        encoding="utf-8",
    )
    draft = build_draft(str(task), str(checklist))
    assert draft["status"] == "pending_human_review"
    assert draft["draft_stats"] == {"items": 2, "source_valid": 1, "source_invalid": 1}


def test_compiler_score_requires_human_approval(tmp_path):
    pred = tmp_path / "pred.json"
    gold = tmp_path / "gold.json"
    pred.write_text('[{"id":"a","type":"flow","severity":"major","source_quote":"x"}]', encoding="utf-8")
    gold.write_text(json.dumps({"status": "pending_human_review", "items": [{"id": "a"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="尚未 approved"):
        score_prediction(str(pred), str(gold))
    data = json.loads(gold.read_text())
    data["status"] = "approved"
    data["items"][0].update({"type": "flow", "severity": "major"})
    gold.write_text(json.dumps(data), encoding="utf-8")
    score = score_prediction(str(pred), str(gold))
    assert score["checkpoint_recall"] == 1.0
    assert score["type_accuracy"] == 1.0


def test_sealed_calibration_omits_case_truth(monkeypatch, tmp_path):
    fixture = {
        "checkpoints": [{"id": "a", "type": "flow", "severity": "major"}],
        "cases": [{"case_id": "secret_case", "description": "secret", "turns": [], "ground_truth": {"a": "pass"}}],
    }
    secret = tmp_path / "private.json"
    secret.write_text(json.dumps(fixture), encoding="utf-8")
    monkeypatch.setattr(calibrate, "GOLDEN_PATH", str(secret))
    monkeypatch.setattr(
        calibrate.judge,
        "judge_trajectory",
        lambda *args, **kwargs: [{"checkpoint_id": "a", "verdict": "pass", "confidence": 1.0}],
    )
    result = calibrate.run(limit=None, votes=1, model="judge", sealed=True)
    raw = json.dumps(result)
    assert result["meta"]["golden_set"] == "private_sealed_input"
    assert "case_details" not in result
    assert "secret_case" not in raw
