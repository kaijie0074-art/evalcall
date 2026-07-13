#!/usr/bin/env python3
"""Verify the six-step product contract and write an auditable evidence record."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "site-deploy" / "app.html"
CACHE = ROOT / "site-deploy" / "demo-cache.json"

STEP_LABELS = [
    "上传评测材料",
    "生成评分标准",
    "检查每通电话",
    "查看评测结果",
    "定位问题原因",
    "生成修复方案",
]
EXPECTED_CONVERSATIONS = {
    "official01": 12,
    "t02": 6,
    "real_recruit": 1,
    "official02": 6,
}
SELECTABLE_SAMPLE_FILES = {
    "official01": ROOT / "样例选择包" / "01_骑手合同生效通知_对话_12通.jsonl",
    "t02": ROOT / "样例选择包" / "02_配送时间改约_对话_10通.jsonl",
    "real_recruit": ROOT / "样例选择包" / "03_骑手招聘外呼_对话_10通.jsonl",
    "official02": ROOT / "样例选择包" / "04_低延迟直播升级通知_对话_10通.jsonl",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def jsonl_count(path: Path) -> int:
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


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
        "历史产物 · 非现场调用" in app_text and "本地服务 · 六步真实执行" in app_text,
        ["历史产物 · 非现场调用", "本地服务 · 六步真实执行"],
        ["历史产物 · 非现场调用", "本地服务 · 六步真实执行"],
    )

    presets = cache.get("presets") or {}
    record("preset_count", len(presets) == 4, len(presets), 4)
    for preset_id, expected in EXPECTED_CONVERSATIONS.items():
        steps = (presets.get(preset_id) or {}).get("steps") or {}
        record(f"{preset_id}_six_cached_steps", set(steps) == set("123456"), sorted(steps), list("123456"))
        actual = (steps.get("1") or {}).get("conversations")
        record(f"{preset_id}_historical_scope", actual == expected, actual, expected)

    selectable_counts = {key: jsonl_count(path) for key, path in SELECTABLE_SAMPLE_FILES.items()}
    record(
        "selectable_sample_batch_counts",
        selectable_counts == {"official01": 12, "t02": 10, "real_recruit": 10, "official02": 10},
        selectable_counts,
        {"official01": 12, "t02": 10, "real_recruit": 10, "official02": 10},
    )

    result = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "PASS" if all(item["passed"] for item in checks) else "FAIL",
        "scope_note": (
            "Historical cache scope is intentionally different from selectable live sample scope "
            "for real_recruit and official02; the UI labels the modes separately."
        ),
        "checks": checks,
        "artifacts": {
            str(APP.relative_to(ROOT)): sha256(APP),
            str(CACHE.relative_to(ROOT)): sha256(CACHE),
            **{str(path.relative_to(ROOT)): sha256(path) for path in SELECTABLE_SAMPLE_FILES.values()},
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
