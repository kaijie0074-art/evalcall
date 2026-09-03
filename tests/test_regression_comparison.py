from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_t02_regression_evidence.py"
SPEC = importlib.util.spec_from_file_location("build_t02_regression_evidence", SCRIPT)
assert SPEC and SPEC.loader
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


def test_regression_detail_contains_dialogue_and_checkpoint_evidence() -> None:
    detail = MOD._build_regression_detail(
        ROOT / "runs" / "t02_delivery_baseline_v1_fixedusers_20260714",
        ROOT / "runs" / "t02_delivery_guarded_v2_fixedusers_20260714",
    )

    assert detail["case_diffs"]
    assert detail["checkpoint_transitions"]
    assert detail["transition_summary"]["fixed_judgments"] > 0
    assert detail["transition_summary"]["regressed_judgments"] > 0
    assert any(case["case_type"] == "fixed" for case in detail["case_diffs"])
    assert any(case["case_type"] == "regressed" for case in detail["case_diffs"])
    assert all(case["user_input"] for case in detail["case_diffs"])
    assert all(case["baseline_reply"] for case in detail["case_diffs"])
    assert all(case["candidate_reply"] for case in detail["case_diffs"])


def test_deployed_cache_and_ui_expose_full_regression_evidence() -> None:
    cache = json.loads((ROOT / "site-deploy" / "demo-cache.json").read_text(encoding="utf-8"))
    regression = cache["presets"]["t02"]["steps"]["6"]["actual_regression"]
    html = (ROOT / "site-deploy" / "app.html").read_text(encoding="utf-8")

    assert regression["schema_version"] == 2
    assert regression["comparability"]["same_user_inputs"] is True
    assert regression["comparability"]["same_instruction"] is True
    assert regression["comparability"]["same_checklist"] is True
    assert regression["comparability"]["judgments_modified"] is False
    assert regression["artifact_url"] == "regression-t02.json"
    assert (ROOT / "site-deploy" / regression["artifact_url"]).is_file()

    for label in (
        "FIXED-USER REGRESSION · 优化效果证据",
        "核心指标状态迁移",
        "同一输入的回复对照",
        "检查点状态迁移",
        "证据边界",
        "门禁风险已清零，但履约率未提升",
    ):
        assert label in html
