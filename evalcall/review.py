"""人工复核队列和回填协议：永不覆写机器原判。"""

from __future__ import annotations

import hashlib
from typing import Any


_VALID = {"pass", "fail", "na"}


def review_id(judgment: dict[str, Any]) -> str:
    raw = f"{judgment.get('run_id')}|{judgment.get('checkpoint_id')}".encode("utf-8")
    return "rev_" + hashlib.sha256(raw).hexdigest()[:16]


def review_reasons(judgment: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    verdict = str(judgment.get("verdict") or "na")
    if judgment.get("needs_human_review"):
        reasons.append("低置信/投票分歧")
    if judgment.get("rule_conflict"):
        reasons.append("规则/LLM 冲突")
    if verdict == "na":
        reasons.append("无法判定")
    if verdict == "fail" and (judgment.get("safety") or str(judgment.get("severity")) == "critical"):
        reasons.append("P0 打回项")
    return reasons


def build_review_queue(judgments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    queue: list[dict[str, Any]] = []
    for item in judgments:
        reasons = review_reasons(item)
        if not reasons:
            continue
        queue.append(
            {
                "review_id": review_id(item),
                "run_id": item.get("run_id"),
                "checkpoint_id": item.get("checkpoint_id"),
                "checkpoint_text": item.get("text", ""),
                "severity": item.get("severity", "minor"),
                "machine_verdict": item.get("verdict", "na"),
                "machine_confidence": item.get("confidence"),
                "review_reasons": reasons,
                "evidence": item.get("evidence") or [],
                "source_quote": item.get("source_quote", ""),
                "human_verdict": None,
                "reviewer": None,
                "comment": None,
            }
        )
    return queue


def apply_decisions(
    judgments: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for decision in decisions:
        rid = str(decision.get("review_id") or "")
        verdict = str(decision.get("human_verdict") or "").lower()
        if not rid:
            raise ValueError("人工决定缺 review_id")
        if verdict not in _VALID:
            raise ValueError(f"{rid} human_verdict 必须是 pass/fail/na")
        by_id[rid] = decision

    known = {review_id(item) for item in judgments}
    unknown = sorted(set(by_id) - known)
    if unknown:
        raise ValueError(f"人工决定包含未知 review_id：{', '.join(unknown)}")

    result: list[dict[str, Any]] = []
    for item in judgments:
        out = dict(item)
        rid = review_id(item)
        decision = by_id.get(rid)
        out["machine_verdict"] = item.get("verdict", "na")
        if decision:
            out["human_verdict"] = str(decision["human_verdict"]).lower()
            out["verdict"] = out["human_verdict"]
            out["reviewer"] = decision.get("reviewer") or "human_reviewer"
            out["review_comment"] = decision.get("comment") or ""
            out["review_id"] = rid
            out["review_applied"] = True
        else:
            out["human_verdict"] = None
            out["review_applied"] = False
        result.append(out)
    return result
