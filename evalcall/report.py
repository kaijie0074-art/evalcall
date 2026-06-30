"""⑤ 评测报告 —— 数据聚合 + HTML 渲染（Agent C）。

读取一个 run 目录下的三个产物，聚合成评测报告所需的视图模型，
再用 jinja2 渲染 templates/report.html.j2 成单文件自包含 HTML。

run 目录约定的三个文件（schema 见 SPEC 第 4 节，本模块在其基础上做了
向下兼容的字段假定，详见模块末尾 _SCHEMA_NOTES）：

- transcripts.jsonl  每行一条对话轨迹
    {run_id, task_id, persona_id, turns:[{role, content, turn}], meta}
- judgments.json     逐检查点判定结果（list 或 {"judgments":[...]} 包裹）
    每条:{checkpoint_id, verdict, confidence, evidence:[{turn,quote}],
          judge_votes, method, ...（见下方假定字段）}
- summary.json       运行级元信息（可选，缺失时由本模块从前两者推导）

对外只暴露 build_report(run_dir) -> str（返回生成的 html 绝对路径）。
"""

from __future__ import annotations

import html
import json
import os
from collections import defaultdict
from datetime import datetime
from typing import Any, Optional

try:
    from jinja2 import Environment, FileSystemLoader, select_autoescape
except ModuleNotFoundError as exc:  # pragma: no cover - 安装期保护
    raise ModuleNotFoundError(
        "缺少 jinja2，请先安装：pip3 install --break-system-packages jinja2"
    ) from exc

# ---------------------------------------------------------------------------
# 常量：四维定义 + verdict / severity 元数据
# ---------------------------------------------------------------------------

# 四维评测维度 → 由哪些检查点 type 贡献。
# 流程完整度=flow / 约束遵循率=constraint / 异常处理=forbidden / 话术合规=style
# （异常处理用 forbidden 类近似：禁止项多与异常/越界行为相关。）
DIMENSIONS: list[dict[str, str]] = [
    {"key": "flow", "label": "流程完整度", "types": "flow"},
    {"key": "constraint", "label": "约束遵循率", "types": "constraint"},
    {"key": "exception", "label": "异常处理", "types": "forbidden"},
    {"key": "style", "label": "话术合规", "types": "style"},
]

# severity 加权（用于总分）。
# 单一事实来源：与 judge._SEVERITY_WEIGHT 同一张表——曾因 report/judge 各持
# 一张权重表（3/2/1 vs 5/3/1）+ 否决语义不一致，导致同一 run 的报告 HTML
# 与 summary.json 给出两个互相矛盾的总分（红队实测 51.6 vs 38.2）。
from evalcall.judge import _SEVERITY_WEIGHT as SEVERITY_WEIGHT  # noqa: E402

VERDICT_LABEL: dict[str, str] = {
    "pass": "通过",
    "fail": "未通过",
    "na": "不适用",
}


# ---------------------------------------------------------------------------
# 文件读取（带容错）
# ---------------------------------------------------------------------------

def _read_jsonl(path: str) -> list[dict[str, Any]]:
    """读 JSONL，逐行解析，跳过空行/坏行（坏行只警告不崩）。"""
    rows: list[dict[str, Any]] = []
    if not os.path.isfile(path):
        return rows
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                print(f"[report] 跳过坏行 {path}:{lineno}")
    return rows


def _read_json(path: str) -> Any:
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _load_judgments(run_dir: str) -> list[dict[str, Any]]:
    """judgments.json 允许两种形态：裸 list 或 {"judgments":[...]}。"""
    raw = _read_json(os.path.join(run_dir, "judgments.json"))
    if raw is None:
        return []
    if isinstance(raw, dict):
        return list(raw.get("judgments", []))
    if isinstance(raw, list):
        return raw
    return []


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _pct(num: float, den: float) -> float:
    """通过率百分比（保留 1 位），分母 0 时返回 0。"""
    return round(num / den * 100, 1) if den else 0.0


def _heat_color(rate: float | None) -> str:
    """通过率 → 热力背景色（红→黄→绿渐变），供模板内调用。"""
    if rate is None:
        return "#f4f4f4"
    r = max(0.0, min(100.0, float(rate))) / 100.0
    # 0% -> 红(#e02424)  50% -> 黄(#FFD100)  100% -> 绿(#16a34a)
    if r < 0.5:
        t = r / 0.5
        c1, c2 = (0xE0, 0x24, 0x24), (0xFF, 0xD1, 0x00)
    else:
        t = (r - 0.5) / 0.5
        c1, c2 = (0xFF, 0xD1, 0x00), (0x16, 0xA3, 0x4A)
    mix = tuple(round(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))
    # 调浅一点，文字才看得清
    light = tuple(round(v + (255 - v) * 0.45) for v in mix)
    return f"#{light[0]:02x}{light[1]:02x}{light[2]:02x}"


def _trajectory_key(j: dict[str, Any]) -> tuple[str, str, str]:
    """从一条判定里取出它所属轨迹的 (run_id, task_id, persona_id)。

    SPEC 的 judgment 单条 schema 未含轨迹定位字段，这里假定 judgments.json
    的每条额外带了 task_id / persona_id / run_id（由 CLI 落盘时补全）。
    缺失时归入 'unknown'，保证不崩。
    """
    return (
        str(j.get("run_id", "")),
        str(j.get("task_id", "unknown")),
        str(j.get("persona_id", "unknown")),
    )


# ---------------------------------------------------------------------------
# 聚合主逻辑
# ---------------------------------------------------------------------------

def _aggregate(
    transcripts: list[dict[str, Any]],
    judgments: list[dict[str, Any]],
    summary: dict[str, Any] | None,
) -> dict[str, Any]:
    """把三份原始数据聚合成模板视图模型。"""

    # --- transcript 索引：(task_id, persona_id) -> transcript ---
    tx_index: dict[tuple[str, str], dict[str, Any]] = {}
    persona_meta: dict[str, dict[str, Any]] = {}
    task_meta: dict[str, dict[str, Any]] = {}
    for tx in transcripts:
        tid = str(tx.get("task_id", "unknown"))
        pid = str(tx.get("persona_id", "unknown"))
        tx_index[(tid, pid)] = tx
        meta = tx.get("meta", {}) or {}
        if pid not in persona_meta:
            persona_meta[pid] = {
                "id": pid,
                "label": meta.get("persona_label", pid),
                "strategy": meta.get("persona_strategy", ""),
            }
        if tid not in task_meta:
            task_meta[tid] = {
                "id": tid,
                "label": meta.get("task_label", tid),
            }

    # --- 全局计数 ---
    verdict_counts: dict[str, int] = {"pass": 0, "fail": 0, "na": 0}
    # 四维：type -> {pass, fail}（na 不计入分母）
    dim_counts: dict[str, dict[str, int]] = defaultdict(lambda: {"pass": 0, "fail": 0})
    # persona × task 通过率：(pid, tid) -> {pass, fail}
    cell_counts: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {"pass": 0, "fail": 0}
    )
    # persona 切片：pid -> {pass, fail}
    persona_counts: dict[str, dict[str, int]] = defaultdict(lambda: {"pass": 0, "fail": 0})

    # 评分累加：Σ severity 加权（pass 得满权，fail 得 0，na 不计）
    score_earned = 0.0
    score_total = 0.0
    critical_fail = 0
    constraint_violations = 0  # 约束违反数（constraint+forbidden 的 fail）

    # 失败检查点（用于 TOP 列表）：聚合同名检查点的失败次数
    fail_agg: dict[str, dict[str, Any]] = {}
    # 全部检查点明细（逐条）
    detail_rows: list[dict[str, Any]] = []

    # judge 可靠性
    vote_unanimous = 0  # 投票一致（所有 votes 相同）
    vote_split = 0      # 投票分裂
    vote_total = 0
    rule_llm_conflict = 0  # 双轨冲突（同 checkpoint_id 规则与 LLM 结论不一致）
    method_verdict: dict[str, dict[str, str]] = defaultdict(dict)  # cp_id -> {method: verdict}

    # 模拟器覆盖率：检查点在某条轨迹中 pass|fail 即"触达"，na 即该轨迹未触达
    # cp_id -> {"meta": 检查点元信息, "covered_runs": 触达轨迹数, "total_runs": 出现轨迹数}
    coverage_by_cp: dict[str, dict[str, Any]] = {}
    # 每条轨迹自身的覆盖率：run_key -> {"covered": n, "total": n}
    coverage_by_run: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {"covered": 0, "total": 0}
    )

    for j in judgments:
        verdict = str(j.get("verdict", "na")).lower()
        if verdict not in verdict_counts:
            verdict = "na"
        verdict_counts[verdict] += 1

        cp_type = str(j.get("type", "flow")).lower()
        severity = str(j.get("severity", "minor")).lower()
        weight = SEVERITY_WEIGHT.get(severity, 1.0)

        _, tid, pid = _trajectory_key(j)

        # 四维 / persona / cell：只统计 pass|fail（na 跳过）
        if verdict in ("pass", "fail"):
            dim_counts[cp_type][verdict] += 1
            cell_counts[(pid, tid)][verdict] += 1
            persona_counts[pid][verdict] += 1

            score_total += weight
            if verdict == "pass":
                score_earned += weight
            else:  # fail
                if severity == "critical":
                    critical_fail += 1
                if cp_type in ("constraint", "forbidden"):
                    constraint_violations += 1

        # 证据规范化
        evidence = []
        for ev in j.get("evidence", []) or []:
            evidence.append(
                {
                    "turn": ev.get("turn", "?"),
                    "quote": str(ev.get("quote", "")),
                }
            )

        cp_id = str(j.get("checkpoint_id", ""))
        cp_text = str(j.get("text", j.get("checkpoint_text", cp_id)))
        source_quote = str(j.get("source_quote", ""))
        confidence = j.get("confidence", None)
        votes = j.get("judge_votes", None)
        method = str(j.get("method", "llm")).lower()

        # judge 可靠性：投票一致率
        if isinstance(votes, list) and votes:
            vote_total += 1
            # votes 元素是 {"verdict": ..., "confidence": ...} dict，只比较 verdict
            if len(set(
                str(v.get("verdict", v) if isinstance(v, dict) else v).lower()
                for v in votes
            )) == 1:
                vote_unanimous += 1
            else:
                vote_split += 1

        # 双轨冲突：双判合议后由 judge 直接打 rule_conflict 标记
        # （规则轨命中但 LLM 否决——子串误命中/引用，需人工复核）
        if j.get("rule_conflict"):
            rule_llm_conflict += 1

        # 覆盖率累计
        cov = coverage_by_cp.setdefault(
            cp_id or cp_text,
            {
                "checkpoint_id": cp_id,
                "text": cp_text,
                "type": cp_type,
                "severity": severity,
                "source_quote": source_quote,
                "covered_runs": 0,
                "total_runs": 0,
            },
        )
        cov["total_runs"] += 1
        run_cov = coverage_by_run[(pid, tid)]
        run_cov["total"] += 1
        # 触达口径：pass 即触达；fail 必须带原文证据才算触达——
        # 无证据的 fail 可能是裁判在场景未发生时的误判，计入会让覆盖率虚高
        if verdict == "pass" or (verdict == "fail" and evidence):
            cov["covered_runs"] += 1
            run_cov["covered"] += 1

        row = {
            "checkpoint_id": cp_id,
            "text": cp_text,
            "type": cp_type,
            "severity": severity,
            "source_quote": source_quote,
            "verdict": verdict,
            "verdict_label": VERDICT_LABEL.get(verdict, verdict),
            "confidence": confidence,
            "confidence_pct": (
                round(float(confidence) * 100) if isinstance(confidence, (int, float))
                and confidence <= 1 else confidence
            ),
            "evidence": evidence,
            "votes": votes,
            "method": method,
            "task_id": tid,
            "persona_id": pid,
            "task_label": task_meta.get(tid, {}).get("label", tid),
            "persona_label": persona_meta.get(pid, {}).get("label", pid),
        }
        detail_rows.append(row)

        # 失败聚合
        if verdict == "fail":
            agg = fail_agg.setdefault(
                cp_id or cp_text,
                {
                    "checkpoint_id": cp_id,
                    "text": cp_text,
                    "type": cp_type,
                    "severity": severity,
                    "source_quote": source_quote,
                    "count": 0,
                    "samples": [],
                },
            )
            agg["count"] += 1
            if len(agg["samples"]) < 3 and evidence:
                agg["samples"].append(
                    {
                        "task_label": task_meta.get(tid, {}).get("label", tid),
                        "persona_label": persona_meta.get(pid, {}).get("label", pid),
                        "evidence": evidence,
                    }
                )

    # --- 四维得分 ---
    dimensions = []
    for d in DIMENSIONS:
        c = dim_counts.get(d["types"], {"pass": 0, "fail": 0})
        total = c["pass"] + c["fail"]
        dimensions.append(
            {
                "key": d["key"],
                "label": d["label"],
                "pass": c["pass"],
                "fail": c["fail"],
                "total": total,
                "rate": _pct(c["pass"], total),
            }
        )

    # --- 总分：与 judge.summarize 同一套语义 ---
    # raw_score = severity 加权通过率（否决前）；存在 critical fail 时
    # final 总分按一票否决归 0（与 summary.json 口径一致），raw 保留供对照。
    base_score = _pct(score_earned, score_total)
    raw_score = base_score
    overall_score = 0.0 if critical_fail > 0 else base_score

    # --- persona × task 热力 ---
    persona_ids = sorted(persona_meta.keys())
    task_ids = sorted(task_meta.keys())
    heatmap_rows = []
    for pid in persona_ids:
        cells = []
        for tid in task_ids:
            c = cell_counts.get((pid, tid))
            if c is None:
                cells.append({"rate": None, "pass": 0, "fail": 0, "has": False})
            else:
                total = c["pass"] + c["fail"]
                cells.append(
                    {
                        "rate": _pct(c["pass"], total),
                        "pass": c["pass"],
                        "fail": c["fail"],
                        "has": True,
                    }
                )
        heatmap_rows.append(
            {
                "persona": persona_meta[pid],
                "cells": cells,
            }
        )

    # --- persona 切片汇总 ---
    persona_slices = []
    for pid in persona_ids:
        c = persona_counts.get(pid, {"pass": 0, "fail": 0})
        total = c["pass"] + c["fail"]
        persona_slices.append(
            {
                "persona": persona_meta[pid],
                "pass": c["pass"],
                "fail": c["fail"],
                "total": total,
                "rate": _pct(c["pass"], total),
            }
        )
    persona_slices.sort(key=lambda x: x["rate"])

    # --- 失败 TOP 列表 ---
    fail_top = sorted(
        fail_agg.values(),
        key=lambda x: (
            -x["count"],
            {"critical": 0, "major": 1, "minor": 2}.get(x["severity"], 3),
        ),
    )

    # --- 失败案例剖析：选 fail 占比高的轨迹做完整回放 ---
    case_studies = _build_case_studies(transcripts, judgments, tx_index, task_meta, persona_meta)

    # --- 模拟器覆盖率 ---
    never_covered = [
        c for c in coverage_by_cp.values() if c["covered_runs"] == 0
    ]
    never_covered.sort(
        key=lambda x: {"critical": 0, "major": 1, "minor": 2}.get(x["severity"], 3)
    )
    covered_unique = sum(1 for c in coverage_by_cp.values() if c["covered_runs"] > 0)
    run_coverage_rows = []
    for (pid, tid), c in sorted(coverage_by_run.items()):
        run_coverage_rows.append(
            {
                "persona_label": persona_meta.get(pid, {}).get("label", pid),
                "task_label": task_meta.get(tid, {}).get("label", tid),
                "covered": c["covered"],
                "total": c["total"],
                "rate": _pct(c["covered"], c["total"]),
            }
        )
    coverage = {
        "n_unique": len(coverage_by_cp),
        "n_covered_unique": covered_unique,
        "unique_rate": _pct(covered_unique, len(coverage_by_cp)),
        "never_covered": never_covered,
        "per_run": run_coverage_rows,
    }

    # --- judge 可靠性 ---
    reliability = {
        "vote_total": vote_total,
        "vote_unanimous": vote_unanimous,
        "vote_split": vote_split,
        "unanimous_rate": _pct(vote_unanimous, vote_total),
        "rule_llm_conflict": rule_llm_conflict,
        "total_judgments": len(judgments),
    }

    # --- 元信息 ---
    summary = summary or {}
    total_pf = verdict_counts["pass"] + verdict_counts["fail"]
    meta = {
        "run_id": summary.get("run_id", _first_run_id(transcripts)),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "started_at": summary.get("started_at", ""),
        "backend": summary.get("backend", os.environ.get("EVALCALL_BACKEND", "")),
        "target_model": summary.get("target_model", os.environ.get("EVALCALL_MODEL", "")),
        "judge_model": summary.get("judge_model", ""),
        "n_tasks": len(task_ids),
        "n_personas": len(persona_ids),
        "n_trajectories": len(transcripts),
        "n_checkpoints": len(judgments),
    }

    # --- 决策头条（P1-3 门禁 / C18 履约 / P1-5 复核）：从 summary.json 取聚合结果 ---
    # 这些是质检团队和评委第一眼要看的"能不能上线/活办成没/几条要人看"，不是分数。
    decision = {
        "gate": summary.get("gate", "—"),
        "gate_reasons": summary.get("gate_reasons", []),
        "blocked_runs": summary.get("blocked_runs", 0),
        "fulfillment_rate": summary.get("fulfillment_rate"),
        "fulfilled_runs": summary.get("fulfilled_runs", 0),
        "fulfillment_eval_runs": summary.get("fulfillment_eval_runs", 0),
        "needs_human_review_total": summary.get("needs_human_review_total", 0),
    }

    return {
        "meta": meta,
        "decision": decision,
        "overall_score": overall_score,
        "raw_score": raw_score,
        "critical_fail": critical_fail,
        "constraint_violations": constraint_violations,
        "verdict_counts": verdict_counts,
        "pass_rate": _pct(verdict_counts["pass"], total_pf),
        "dimensions": dimensions,
        "radar": _build_radar(dimensions),
        "heatmap": {"tasks": [task_meta[t] for t in task_ids], "rows": heatmap_rows},
        "persona_slices": persona_slices,
        "fail_top": fail_top,
        "detail_rows": detail_rows,
        "case_studies": case_studies,
        "reliability": reliability,
        "coverage": coverage,
    }


def _first_run_id(transcripts: list[dict[str, Any]]) -> str:
    for tx in transcripts:
        if tx.get("run_id"):
            return str(tx["run_id"])
    return "—"


def _build_case_studies(
    transcripts: list[dict[str, Any]],
    judgments: list[dict[str, Any]],
    tx_index: dict[tuple[str, str], dict[str, Any]],
    task_meta: dict[str, dict[str, Any]],
    persona_meta: dict[str, dict[str, Any]],
    max_cases: int = 3,
) -> list[dict[str, Any]]:
    """挑选失败最严重的若干条轨迹做完整对话回放，违规轮次高亮。"""
    # 按轨迹聚合 fail，记录违规轮次
    per_tx: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"fails": 0, "critical": 0, "bad_turns": set(), "fail_cps": []}
    )
    for j in judgments:
        if str(j.get("verdict", "")).lower() != "fail":
            continue
        _, tid, pid = _trajectory_key(j)
        rec = per_tx[(tid, pid)]
        rec["fails"] += 1
        if str(j.get("severity", "")).lower() == "critical":
            rec["critical"] += 1
        for ev in j.get("evidence", []) or []:
            if isinstance(ev.get("turn"), int):
                rec["bad_turns"].add(ev["turn"])
        rec["fail_cps"].append(
            {
                "text": str(j.get("text", j.get("checkpoint_id", ""))),
                "severity": str(j.get("severity", "minor")).lower(),
            }
        )

    ranked = sorted(
        per_tx.items(),
        key=lambda kv: (-kv[1]["critical"], -kv[1]["fails"]),
    )[:max_cases]

    cases = []
    for (tid, pid), rec in ranked:
        tx = tx_index.get((tid, pid))
        if not tx:
            continue
        bad_turns = rec["bad_turns"]
        turns = []
        for t in tx.get("turns", []):
            turns.append(
                {
                    "role": t.get("role", "agent"),
                    "content": str(t.get("content", "")),
                    "turn": t.get("turn", ""),
                    "bad": t.get("turn") in bad_turns,
                }
            )
        cases.append(
            {
                "task_label": task_meta.get(tid, {}).get("label", tid),
                "persona": persona_meta.get(pid, {"id": pid, "label": pid}),
                "fails": rec["fails"],
                "critical": rec["critical"],
                "fail_cps": rec["fail_cps"],
                "turns": turns,
            }
        )
    return cases


def _build_radar(dimensions: list[dict[str, Any]]) -> dict[str, Any]:
    """生成纯 SVG 雷达图所需的点坐标（4 个维度→正方形雷达）。

    画布 320x320，圆心 (160,160)，最大半径 120。
    角度从正上方开始顺时针均分。
    """
    import math

    cx, cy, r_max = 160.0, 160.0, 120.0
    n = len(dimensions)
    axes = []
    poly_points = []
    for i, d in enumerate(dimensions):
        angle = -math.pi / 2 + i * (2 * math.pi / n)  # 顶点起始
        # 轴端点（满分位置）
        ax = cx + r_max * math.cos(angle)
        ay = cy + r_max * math.sin(angle)
        # 数据点
        rr = r_max * (d["rate"] / 100.0)
        dx = cx + rr * math.cos(angle)
        dy = cy + rr * math.sin(angle)
        # 标签位置（轴外侧一点）
        lx = cx + (r_max + 24) * math.cos(angle)
        ly = cy + (r_max + 24) * math.sin(angle)
        axes.append(
            {
                "label": d["label"],
                "rate": d["rate"],
                "ax": round(ax, 1),
                "ay": round(ay, 1),
                "lx": round(lx, 1),
                "ly": round(ly, 1),
            }
        )
        poly_points.append(f"{round(dx,1)},{round(dy,1)}")
    # 同心网格圈（25/50/75/100%）
    grids = []
    for frac in (0.25, 0.5, 0.75, 1.0):
        pts = []
        for i in range(n):
            angle = -math.pi / 2 + i * (2 * math.pi / n)
            gx = cx + r_max * frac * math.cos(angle)
            gy = cy + r_max * frac * math.sin(angle)
            pts.append(f"{round(gx,1)},{round(gy,1)}")
        grids.append(" ".join(pts))
    return {
        "cx": cx,
        "cy": cy,
        "axes": axes,
        "polygon": " ".join(poly_points),
        "grids": grids,
    }


def _load_lint_warning(
    run_dir: str, transcripts: list[dict[str, Any]]
) -> Optional[dict[str, Any]]:
    """在 run_dir 的兄弟目录 lint/ 下查找本任务的指令体检结果。

    匹配规则：lint json 的 task_id 与轨迹 task_id 一致，且 feasibility_score < 50
    时返回警示数据（feasibility、top findings 摘要），否则返回 None。
    """
    task_ids = {str(tx.get("task_id")) for tx in transcripts if tx.get("task_id")}
    if not task_ids:
        return None
    lint_dir = os.path.join(os.path.dirname(run_dir), "lint")
    if not os.path.isdir(lint_dir):
        return None
    for fname in os.listdir(lint_dir):
        if not fname.endswith(".json"):
            continue
        data = _read_json(os.path.join(lint_dir, fname))
        if not isinstance(data, dict):
            continue
        lint_task = str(data.get("task_id", ""))
        # task_id 完全一致，或互为前缀（lint 文件可能用短名）
        hit = any(
            lint_task == t or t.startswith(lint_task) or lint_task.startswith(t)
            for t in task_ids
        )
        if not hit:
            continue
        score = data.get("feasibility_score")
        if not isinstance(score, (int, float)) or score >= 50:
            return None
        top = [
            f for f in (data.get("findings") or [])
            if isinstance(f, dict) and f.get("severity") == "high"
        ][:3]
        return {
            "feasibility_score": score,
            "n_findings": len(data.get("findings") or []),
            "top_findings": [
                {"dimension": f.get("dimension", ""), "analysis": str(f.get("analysis", ""))[:120]}
                for f in top
            ],
        }
    return None


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------

def build_report(run_dir: str) -> str:
    """聚合 run_dir 下的数据并渲染 HTML 报告，返回生成的 html 绝对路径。"""
    run_dir = os.path.abspath(run_dir)
    if not os.path.isdir(run_dir):
        raise FileNotFoundError(f"run 目录不存在：{run_dir}")

    transcripts = _read_jsonl(os.path.join(run_dir, "transcripts.jsonl"))
    judgments = _load_judgments(run_dir)
    summary = _read_json(os.path.join(run_dir, "summary.json"))
    if not isinstance(summary, dict):
        summary = None

    model = _aggregate(transcripts, judgments, summary)

    # 指令体检联动：若 runs/lint/ 下存在本任务的体检结果且可行性分过低，
    # 在报告头部挂归因警示——低分主要归因指令缺陷而非模型能力，
    # 避免「lint 说指令烂、主流程却把 0 分算在模型头上」的归因冲突。
    model["lint_warning"] = _load_lint_warning(run_dir, transcripts)

    tpl_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
    env = Environment(
        loader=FileSystemLoader(tpl_dir),
        autoescape=select_autoescape(["html", "j2"]),
    )
    env.globals["heat_color"] = _heat_color
    template = env.get_template("report.html.j2")
    rendered = template.render(**model)

    out_path = os.path.join(run_dir, "report.html")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(rendered)
    return out_path


# ---------------------------------------------------------------------------
# _SCHEMA_NOTES：本模块对 SPEC 第 4 节 schema 做的向下兼容假定
# ---------------------------------------------------------------------------
# 1. judgments.json：SPEC 只定义了单条 judgment 的字段，未定义整文件结构，
#    也未说明单条如何定位到所属轨迹与检查点元信息。本模块假定：
#    - 文件是裸 list 或 {"judgments":[...]}；
#    - 每条 judgment 额外携带 task_id / persona_id / run_id（轨迹定位）；
#    - 每条额外携带 type / severity / text / source_quote（检查点元信息，
#      由 CLI 在落盘时把 checklist 的字段并进 judgment）。
#    缺失时统一降级（type→flow、severity→minor、定位→unknown），保证不崩。
# 2. summary.json：SPEC 未定义字段。本模块当它是可选元信息表，可含
#    run_id / started_at / backend / target_model / judge_model；全缺也能从
#    transcripts 推导出最小元信息。
# 3. evidence[].turn 用于在失败案例回放里高亮违规轮次（与 transcript turn 对齐）。
