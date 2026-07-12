"""确定性根因归属：把分散评测信号收口为“该改哪里”。

该模块不调 LLM。所有结论都携带数字/产物证据；信号不足时输出 uncertain，
防止把统计现象包装成确定因果。
"""

from __future__ import annotations

import glob
import json
import os
from collections import Counter, defaultdict
from typing import Any


_LABELS = {
    "target_model": "外呼模型",
    "instruction": "SOP/任务指令",
    "judge": "裁判与判定链路",
    "test_data": "测试数据/模拟分布",
    "uncertain": "证据不足",
}

_OWNERS = {
    "target_model": "模型/对话策略工程",
    "instruction": "SOP 业务 owner",
    "judge": "评测算法 + 人工质检",
    "test_data": "质检运营/数据 owner",
    "uncertain": "人工复核",
}

_ACTIONS = {
    "target_model": [
        "针对高频失败检查点修改外呼模型指令遵循策略，不改评分尺",
        "使用同一 checklist 重跑回归，确认 fail→pass 且 P0 无退化",
    ],
    "instruction": [
        "先修复 lint 发现的冲突/不可行/缺失分支，由业务 owner 审核新 SOP",
        "SOP 改版后重新编译 checklist；新旧尺子不做模型能力直接 diff",
    ],
    "judge": [
        "先复核分裂票、规则冲突与高 NA 批次，不直接归责外呼模型",
        "将人工拍板案例进黄金集，预注册后修裁判口径并重跑校准",
    ],
    "test_data": [
        "核对 persona/真实用户分布，补充平淡场景与未触达分支",
        "保持检查尺不变，在调整后的数据分布上重测并比较 persona 切片",
    ],
    "uncertain": [
        "对低置信/证据不足项做人工复核，完成前不修模型或 SOP",
    ],
}


def _load_lint(task_id: str, lint_path: str | None = None) -> dict[str, Any] | None:
    candidates = [lint_path] if lint_path else []
    if not lint_path:
        candidates.extend(sorted(glob.glob(os.path.join("runs", "lint", f"*{task_id}*.json"))))
        aliases = {
            "official_01_feimaotui": "runs/lint/official_01_lint.json",
            "official_02_lowlatency": "runs/lint/official_02_lint.json",
            "real_recruit_rider": "runs/lint/real_recruit_rider.json",
        }
        if task_id in aliases:
            candidates.insert(0, aliases[task_id])
    for path in candidates:
        if not path or not os.path.isfile(path):
            continue
        try:
            data = json.load(open(path, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if isinstance(data, dict):
            data = dict(data)
            data["_source_path"] = path
            return data
    return None


def _root(
    category: str,
    confidence: str,
    score: int,
    evidence: list[str],
) -> dict[str, Any]:
    return {
        "category": category,
        "label": _LABELS[category],
        "confidence": confidence,
        "score": score,
        "owner": _OWNERS[category],
        "evidence": evidence,
        "actions": [
            {"owner": _OWNERS[category], "action": action, "verification": "同尺回归 + 人工审核"}
            for action in _ACTIONS[category]
        ],
    }


def analyze(
    checkpoints: list[dict[str, Any]],
    judgments: list[dict[str, Any]],
    summary: dict[str, Any],
    *,
    task_id: str,
    lint_path: str | None = None,
) -> dict[str, Any]:
    verdicts = Counter(str(j.get("verdict") or "na").lower() for j in judgments)
    judged = verdicts["pass"] + verdicts["fail"]
    fail_rate = verdicts["fail"] / judged if judged else 0.0
    na_rate = verdicts["na"] / len(judgments) if judgments else 1.0
    needs_review = sum(1 for j in judgments if j.get("needs_human_review"))
    rule_conflicts = sum(1 for j in judgments if j.get("rule_conflict"))
    disagreement = float(summary.get("avg_judge_disagreement_rate") or summary.get("judge_disagreement_rate") or 0.0)

    lint = _load_lint(task_id, lint_path)
    feasibility = None
    high_findings = 0
    if lint:
        try:
            feasibility = float(lint.get("feasibility_score"))
        except (TypeError, ValueError):
            feasibility = None
        high_findings = sum(
            1 for item in (lint.get("findings") or [])
            if isinstance(item, dict) and str(item.get("severity") or "").lower() == "high"
        )

    fail_by_persona: dict[str, int] = defaultdict(int)
    total_by_persona: dict[str, int] = defaultdict(int)
    for item in judgments:
        pid = str(item.get("persona_id") or "unknown")
        if str(item.get("verdict") or "na") in {"pass", "fail"}:
            total_by_persona[pid] += 1
        if str(item.get("verdict") or "na") == "fail":
            fail_by_persona[pid] += 1
    total_fail = sum(fail_by_persona.values())
    top_persona = max(fail_by_persona, key=fail_by_persona.get) if fail_by_persona else None
    concentration = fail_by_persona.get(top_persona, 0) / total_fail if top_persona and total_fail else 0.0

    roots: list[dict[str, Any]] = []
    if feasibility is not None and (feasibility < 60 or high_findings > 0):
        roots.append(
            _root(
                "instruction",
                "high" if feasibility < 40 or high_findings >= 2 else "medium",
                95 if feasibility < 40 else 80,
                [
                    f"lint 可行性分 {feasibility:g}/100",
                    f"高严重度指令问题 {high_findings} 项",
                    f"证据文件 {lint.get('_source_path')}",
                ],
            )
        )

    judge_score = 0
    judge_evidence: list[str] = []
    if na_rate >= 0.5:
        judge_score += 70
        judge_evidence.append(f"NA 占比 {na_rate:.1%}，判定链路无法提供足够有效结论")
        if na_rate >= 0.8:
            judge_score += 30  # 近乎整批无效时，判定链路是阻断性首因
    if disagreement >= 0.2:
        judge_score += 25
        judge_evidence.append(f"裁判平均分歧率 {disagreement:.1%}")
    if needs_review and judgments and needs_review / len(judgments) >= 0.2:
        judge_score += 20
        judge_evidence.append(f"待人工复核 {needs_review}/{len(judgments)} 项")
    if rule_conflicts:
        judge_score += 20
        judge_evidence.append(f"规则/LLM 冲突 {rule_conflicts} 项")
    if judge_score:
        roots.append(_root("judge", "high" if judge_score >= 60 else "medium", min(judge_score, 100), judge_evidence))

    if len(total_by_persona) >= 3 and total_fail >= 3 and concentration >= 0.7:
        roots.append(
            _root(
                "test_data",
                "medium",
                65,
                [f"{top_persona} 承担 {concentration:.1%} 的失败，失败过度集中", f"本批共 {len(total_by_persona)} 类 persona"],
            )
        )

    strong_instruction = any(r["category"] == "instruction" and r["confidence"] == "high" for r in roots)
    strong_judge = any(r["category"] == "judge" and r["confidence"] == "high" for r in roots)
    if judged >= 10 and fail_rate >= 0.2 and not strong_instruction and not strong_judge:
        roots.append(
            _root(
                "target_model",
                "high" if fail_rate >= 0.4 else "medium",
                min(90, round(50 + fail_rate * 50)),
                [f"有效判定 {judged} 项，外呼模型失败率 {fail_rate:.1%}", "未发现更强的指令/裁判故障信号"],
            )
        )
    elif judged >= 10 and fail_rate >= 0.4:
        roots.append(
            _root(
                "target_model",
                "low",
                40,
                [f"有效判定失败率 {fail_rate:.1%}，但存在更强的指令/裁判混杂因素"],
            )
        )

    if not roots:
        roots.append(
            _root(
                "uncertain",
                "low",
                20,
                [f"有效判定 {judged} 项、失败率 {fail_rate:.1%}、NA 占比 {na_rate:.1%}，未达到稳健归因阈值"],
            )
        )

    confidence_rank = {"high": 2, "medium": 1, "low": 0}
    roots.sort(key=lambda item: (confidence_rank[item["confidence"]], item["score"]), reverse=True)
    primary = roots[0]
    return {
        "schema_version": 1,
        "primary_category": primary["category"],
        "primary_label": primary["label"],
        "primary_confidence": primary["confidence"],
        "roots": roots,
        "signals": {
            "judgments": len(judgments),
            "judged": judged,
            "fail_rate": round(fail_rate, 4),
            "na_rate": round(na_rate, 4),
            "needs_human_review": needs_review,
            "rule_conflicts": rule_conflicts,
            "judge_disagreement_rate": disagreement,
            "instruction_feasibility": feasibility,
            "instruction_high_findings": high_findings,
            "persona_failure_concentration": round(concentration, 4),
        },
        "disclaimer": "根因为确定性信号归纳，不是因果证明；低/中置信结论必须人工复核后再修改生产配置。",
    }
