"""检查点编译器黄金集草稿与审核后评分。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from .provenance import sha256_file, sha256_json


def _load(path: str) -> Any:
    p = Path(path)
    if p.suffix.lower() in {".yaml", ".yml"}:
        return yaml.safe_load(p.read_text(encoding="utf-8"))
    return json.loads(p.read_text(encoding="utf-8"))


def build_draft(task_path: str, checklist_path: str) -> dict[str, Any]:
    task = _load(task_path) or {}
    checklist = _load(checklist_path) or []
    if not isinstance(task, dict) or not isinstance(checklist, list):
        raise ValueError("task 必须是对象，checklist 必须是数组")
    instruction = str(task.get("instruction") or "")
    items = []
    for cp in checklist:
        if not isinstance(cp, dict):
            continue
        quote = str(cp.get("source_quote") or "").strip()
        scope = "global_policy" if cp.get("safety") else "task_instruction"
        items.append(
            {
                "id": str(cp.get("id") or ""),
                "text": str(cp.get("text") or ""),
                "type": str(cp.get("type") or "constraint"),
                "severity": str(cp.get("severity") or "major"),
                "source_quote": quote,
                "source_scope": scope,
                "source_valid": bool(quote) and (scope == "global_policy" or quote in instruction),
                "review_note": "",
            }
        )
    return {
        "schema_version": 1,
        "status": "pending_human_review",
        "task_id": str(task.get("id") or task.get("task_id") or Path(task_path).stem),
        "task_file_hash": sha256_file(task_path),
        "candidate_checklist_hash": sha256_json(checklist),
        "review_instructions": (
            "业务专家逐项确认 text/type/severity/source_quote；可增删项。完成后把 status 改为 approved，"
            "填写 approved_by/approved_at，再作为编译器真值。"
        ),
        "items": items,
        "draft_stats": {
            "items": len(items),
            "source_valid": sum(1 for item in items if item["source_valid"]),
            "source_invalid": sum(1 for item in items if not item["source_valid"]),
        },
    }


def score_prediction(predicted_path: str, golden_path: str) -> dict[str, Any]:
    predicted = _load(predicted_path) or []
    golden = _load(golden_path) or {}
    if not isinstance(predicted, list) or not isinstance(golden, dict):
        raise ValueError("predicted 必须是 checklist 数组，golden 必须是审核对象")
    if golden.get("status") != "approved":
        raise ValueError("黄金集尚未 approved，不能把 Agent 草稿冒充人工真值")
    expected = {str(item.get("id")): item for item in golden.get("items") or [] if isinstance(item, dict)}
    actual = {str(item.get("id")): item for item in predicted if isinstance(item, dict)}
    matched = sorted(set(expected) & set(actual))
    recall = len(matched) / len(expected) if expected else 0.0
    source_missing = sum(1 for item in predicted if not str(item.get("source_quote") or "").strip())
    type_correct = sum(actual[cid].get("type") == expected[cid].get("type") for cid in matched)
    severity_correct = sum(actual[cid].get("severity") == expected[cid].get("severity") for cid in matched)
    return {
        "schema_version": 1,
        "golden_hash": sha256_file(golden_path),
        "predicted_hash": sha256_file(predicted_path),
        "expected_checkpoints": len(expected),
        "predicted_checkpoints": len(predicted),
        "matched_checkpoints": len(matched),
        "checkpoint_recall": round(recall, 4),
        "no_source_rate": round(source_missing / len(predicted), 4) if predicted else 0.0,
        "type_accuracy": round(type_correct / len(matched), 4) if matched else 0.0,
        "severity_accuracy": round(severity_correct / len(matched), 4) if matched else 0.0,
        "missing_ids": sorted(set(expected) - set(actual)),
        "unexpected_ids": sorted(set(actual) - set(expected)),
    }
