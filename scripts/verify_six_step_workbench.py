#!/usr/bin/env python3
"""Verify the six-step product contract and write an auditable evidence record."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "site-deploy" / "app.html"
CACHE = ROOT / "site-deploy" / "demo-cache.json"

STEP_LABELS = [
    "配置测试任务",
    "建立评分标准",
    "测试外呼模型",
    "输出模型评测报告",
    "分析失败原因",
    "生成优化与回归计划",
]
EXPECTED_CONVERSATIONS = {
    "official01": 12,
    "t02": 10,
    "real_recruit": 10,
    "official02": 10,
}
SELECTABLE_SAMPLE_FILES = {
    "official01": ROOT / "样例选择包" / "01_骑手合同生效通知_对话_12通.jsonl",
    "t02": ROOT / "样例选择包" / "02_配送时间改约_对话_10通.jsonl",
    "real_recruit": ROOT / "样例选择包" / "03_骑手招聘外呼_对话_10通.jsonl",
    "official02": ROOT / "样例选择包" / "04_低延迟直播升级通知_对话_10通.jsonl",
}
LIVE_VERIFIED_RUNS = {
    "official01": ROOT / "runs" / "demo_live_official01_codex_20260713",
    "t02": ROOT / "runs" / "t02_delivery_baseline_v1_fixedusers_20260714",
    "real_recruit": ROOT / "runs" / "demo_live_real_recruit_codex_20260713",
    "official02": ROOT / "runs" / "demo_live_official02_codex_20260713",
}
LIVE_EXPECTED = {"official01": 12, "t02": 10, "real_recruit": 10, "official02": 10}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def jsonl_count(path: Path) -> int:
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def conversation_signature(path: Path) -> str:
    def normalize_text(value: object) -> str:
        text = str(value or "")
        text = re.sub(r"<ADDRESS_\d+>", "<ADDRESS>", text)
        text = re.sub(r"地址[^，。！？,.!?\\n]*", "地址<ADDRESS>", text)
        text = re.sub(r"地址<ADDRESS>[^。！？\\n]*", "地址<ADDRESS>", text)
        if "地址<ADDRESS>" in text:
            text = text.split("地址<ADDRESS>", 1)[0] + "地址<ADDRESS>"
        return text

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    semantic = [
        {
            "run_id": row.get("run_id"),
            "task_id": row.get("task_id"),
            "persona_id": row.get("persona_id"),
            "turns": [
                {"role": turn.get("role"), "content": normalize_text(turn.get("content"))}
                for turn in row.get("turns") or []
            ],
        }
        for row in rows
    ]
    payload = json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "runs" / "six_step_workbench_acceptance_20260713.json",
    )
    args = parser.parse_args()

    app_text = APP.read_text(encoding="utf-8")
    cache = json.loads(CACHE.read_text(encoding="utf-8"))
    checks: list[dict[str, object]] = []

    def record(name: str, passed: bool, actual: object, expected: object) -> None:
        checks.append({"name": name, "passed": passed, "actual": actual, "expected": expected})

    record(
        "six_plain_step_labels",
        all(label in app_text for label in STEP_LABELS),
        [label for label in STEP_LABELS if label in app_text],
        STEP_LABELS,
    )
    record(
        "truthful_mode_labels",
        "完整结果（已验证）" in app_text and "现场真跑（动态）" in app_text and "禁止缓存" in app_text,
        [label for label in ("完整结果（已验证）", "现场真跑（动态）", "禁止缓存") if label in app_text],
        ["完整结果（已验证）", "现场真跑（动态）", "禁止缓存"],
    )
    record(
        "dual_test_entries_visible",
        "模拟测试模式" in app_text and "已有日志质检模式" in app_text,
        [label for label in ("模拟测试模式", "已有日志质检模式") if label in app_text],
        ["模拟测试模式", "已有日志质检模式"],
    )

    presets = cache.get("presets") or {}
    record("preset_count", len(presets) == 4, len(presets), 4)
    for preset_id, expected in EXPECTED_CONVERSATIONS.items():
        steps = (presets.get(preset_id) or {}).get("steps") or {}
        record(f"{preset_id}_six_cached_steps", set(steps) == set("123456"), sorted(steps), list("123456"))
        actual = (steps.get("1") or {}).get("conversations")
        record(f"{preset_id}_historical_scope", actual == expected, actual, expected)
        record(
            f"{preset_id}_version_hashes",
            all(
                bool(value)
                for value in (
                    ((steps.get("1") or {}).get("hashes") or {}).get("sop_sha256"),
                    ((steps.get("3") or {}).get("hashes") or {}).get("transcripts_sha256"),
                    ((steps.get("3") or {}).get("hashes") or {}).get("checklist_sha256"),
                    (steps.get("3") or {}).get("target_model_fingerprint"),
                )
            ),
            {
                "sop": ((steps.get("1") or {}).get("hashes") or {}).get("sop_sha256"),
                "transcripts": ((steps.get("3") or {}).get("hashes") or {}).get("transcripts_sha256"),
                "checklist": ((steps.get("3") or {}).get("hashes") or {}).get("checklist_sha256"),
                "model": (steps.get("3") or {}).get("target_model_fingerprint"),
            },
            "all non-empty",
        )

    record(
        "main_demo_primary_target_model",
        (((presets.get("t02") or {}).get("steps") or {}).get("5") or {}).get("primary_category") == "target_model",
        (((presets.get("t02") or {}).get("steps") or {}).get("5") or {}).get("primary_category"),
        "target_model",
    )
    record(
        "instruction_contrast_primary_instruction",
        (((presets.get("official02") or {}).get("steps") or {}).get("5") or {}).get("primary_category") == "instruction",
        (((presets.get("official02") or {}).get("steps") or {}).get("5") or {}).get("primary_category"),
        "instruction",
    )
    main_plan = (((presets.get("t02") or {}).get("steps") or {}).get("6") or {})
    record(
        "model_plan_returns_step3_same_ruler",
        main_plan.get("return_step") == 3
        and main_plan.get("sop_changed") is False
        and main_plan.get("checklist_changed") is False
        and main_plan.get("sop_sha256_before") == main_plan.get("sop_sha256_for_regression")
        and main_plan.get("checklist_sha256_before") == main_plan.get("checklist_sha256_for_regression"),
        main_plan,
        "step 3 with identical SOP/checklist hashes",
    )
    actual_regression = main_plan.get("actual_regression") or {}
    regression_comparability = actual_regression.get("comparability") or {}
    regression_baseline = ((actual_regression.get("baseline") or {}).get("metrics") or {})
    regression_candidate = ((actual_regression.get("candidate") or {}).get("metrics") or {})
    record(
        "main_demo_actual_fixed_user_regression",
        regression_comparability.get("same_user_inputs") is True
        and regression_comparability.get("same_instruction") is True
        and regression_comparability.get("same_checklist") is True
        and regression_comparability.get("judgments_modified") is False
        and regression_baseline.get("gate") == "打回"
        and regression_candidate.get("gate") == "可上线"
        and regression_candidate.get("p0_triggered_runs") == 0
        and float(regression_candidate.get("fulfillment_rate") or 0)
        >= float(regression_baseline.get("fulfillment_rate") or 0),
        actual_regression,
        "same users/ruler, normal re-judge, candidate gate pass with zero P0",
    )

    selectable_counts = {key: jsonl_count(path) for key, path in SELECTABLE_SAMPLE_FILES.items()}
    record(
        "selectable_sample_batch_counts",
        selectable_counts == {"official01": 12, "t02": 10, "real_recruit": 10, "official02": 10},
        selectable_counts,
        {"official01": 12, "t02": 10, "real_recruit": 10, "official02": 10},
    )

    for preset_id, run_dir in LIVE_VERIFIED_RUNS.items():
        required = [run_dir / name for name in ("transcripts.jsonl", "checklist.json", "judgments.json", "summary.json", "report.html")]
        record(
            f"{preset_id}_live_verified_artifacts",
            all(path.is_file() for path in required),
            [path.name for path in required if path.is_file()],
            [path.name for path in required],
        )
        if all(path.is_file() for path in required):
            summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
            actual_runs = int(summary.get("total_runs") or 0)
            record(f"{preset_id}_live_verified_run_count", actual_runs == LIVE_EXPECTED[preset_id], actual_runs, LIVE_EXPECTED[preset_id])
            record(
                f"{preset_id}_live_input_semantic_match",
                conversation_signature(SELECTABLE_SAMPLE_FILES[preset_id]) == conversation_signature(run_dir / "transcripts.jsonl"),
                conversation_signature(run_dir / "transcripts.jsonl"),
                conversation_signature(SELECTABLE_SAMPLE_FILES[preset_id]),
            )

    result = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "PASS" if all(item["passed"] for item in checks) else "FAIL",
        "scope_note": "Every selectable sample, cache report and verified run uses the same full batch count.",
        "checks": checks,
        "artifacts": {
            str(APP.relative_to(ROOT)): sha256(APP),
            str(CACHE.relative_to(ROOT)): sha256(CACHE),
            **{str(path.relative_to(ROOT)): sha256(path) for path in SELECTABLE_SAMPLE_FILES.values()},
            **{
                str((run_dir / filename).relative_to(ROOT)): sha256(run_dir / filename)
                for run_dir in LIVE_VERIFIED_RUNS.values()
                for filename in ("summary.json", "report.html")
                if (run_dir / filename).is_file()
            },
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[evalcall] six-step verification: {result['status']} -> {args.out}")
    for item in checks:
        print(f"  {'PASS' if item['passed'] else 'FAIL'} {item['name']}")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
