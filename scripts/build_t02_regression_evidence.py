#!/usr/bin/env python3
"""Build machine-readable and visual evidence for the T02 fixed-user regression."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
from collections import Counter, defaultdict
from pathlib import Path


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _metrics(summary: dict) -> dict:
    total = int(summary["total_runs"])
    blocked = int(summary["blocked_runs"])
    return {
        "gate": summary["gate"],
        "total_runs": total,
        "blocked_runs": blocked,
        "call_block_rate": round(blocked / total * 100, 1) if total else 0.0,
        "p0_triggered_runs": int(summary["critical_failed_runs"]),
        "fulfillment_rate": float(summary["fulfillment_rate"]),
        "avg_score": float(summary["avg_score"]),
        "review_queue_count": int(summary.get("review_queue_count") or 0),
        "machine_judgments": int((summary.get("attribution") or {}).get("signals", {}).get("judgments") or 0),
    }


def _evidence_agent_reply(judgment: dict, transcript: dict) -> tuple[str, int | None]:
    roles = {int(turn.get("turn") or 0): str(turn.get("role") or "") for turn in transcript.get("turns") or []}
    candidates = []
    for item in judgment.get("evidence") or []:
        turn = int(item.get("turn") or 0)
        if roles.get(turn) == "agent" and item.get("quote"):
            candidates.append((turn, str(item["quote"])))
    if candidates:
        turn, quote = candidates[-1]
        return quote, turn
    return "未找到可展示的模型原文", None


def _preceding_user_input(transcript: dict, agent_turn: int | None) -> str:
    turns = transcript.get("turns") or []
    before = [turn for turn in turns if turn.get("role") == "user" and (agent_turn is None or int(turn.get("turn") or 0) < agent_turn)]
    return str(before[-1].get("content") or "") if before else "未找到对应用户输入"


def _strategy_for(checkpoint_id: str, text: str) -> str:
    explicit = {
        "safety_ai_disclosure": "加入 AI 身份真实性守卫：不得冒充真人，并提供 App 服务记录核验路径",
        "authenticity_identity": "把身份质疑识别为独立分支，先真实回应身份，再引导回改约任务",
        "constraint_2": "加入营业时间边界校验，超过 21:00 时拒绝提交并给出合法候选时段",
        "forbid_2": "把越界承诺升级为生成前硬约束，违规时改为解释限制并重新询问",
        "flow_2": "加入身份核验前置门：尾号未核验前禁止读取、透露或修改订单信息",
        "flow_5": "把地址与联系方式拆成必须显式确认的收尾状态",
        "flow_6": "统一收尾模板：复述时间、地址和联系方式后再结束通话",
    }
    return explicit.get(checkpoint_id, f"围绕“{text}”增加可观察的流程守卫与回复约束")


def _build_regression_detail(baseline_dir: Path, candidate_dir: Path) -> dict:
    baseline_judgments = _load(baseline_dir / "judgments.json")
    candidate_judgments = _load(candidate_dir / "judgments.json")
    baseline_transcripts = {row["run_id"]: row for row in _load_jsonl(baseline_dir / "transcripts.jsonl")}
    candidate_transcripts = {row["run_id"]: row for row in _load_jsonl(candidate_dir / "transcripts.jsonl")}
    baseline_index = {(row["run_id"], row["checkpoint_id"]): row for row in baseline_judgments}
    candidate_index = {(row["run_id"], row["checkpoint_id"]): row for row in candidate_judgments}
    transitions: dict[str, Counter] = defaultdict(Counter)
    checkpoint_meta: dict[str, dict] = {}
    case_candidates: list[dict] = []
    severity_rank = {"critical": 0, "major": 1, "minor": 2}

    for key, baseline in baseline_index.items():
        candidate = candidate_index.get(key)
        if not candidate:
            continue
        run_id, checkpoint_id = key
        before = str(baseline.get("verdict") or "na")
        after = str(candidate.get("verdict") or "na")
        transition = f"{before}_to_{after}"
        transitions[checkpoint_id][transition] += 1
        checkpoint_meta[checkpoint_id] = {
            "checkpoint_id": checkpoint_id,
            "text": baseline.get("text") or candidate.get("text") or checkpoint_id,
            "severity": baseline.get("severity") or candidate.get("severity") or "minor",
        }
        if transition not in {"fail_to_pass", "pass_to_fail"}:
            continue
        baseline_tx = baseline_transcripts.get(run_id, {})
        candidate_tx = candidate_transcripts.get(run_id, {})
        baseline_reply, baseline_turn = _evidence_agent_reply(baseline, baseline_tx)
        candidate_reply, candidate_turn = _evidence_agent_reply(candidate, candidate_tx)
        user_input = _preceding_user_input(candidate_tx, candidate_turn)
        if user_input == "未找到对应用户输入":
            user_input = _preceding_user_input(baseline_tx, baseline_turn)
        case_candidates.append({
            "case_id": f"{run_id}::{checkpoint_id}",
            "case_type": "fixed" if transition == "fail_to_pass" else "regressed",
            "run_id": run_id,
            "persona_id": baseline.get("persona_id") or candidate.get("persona_id"),
            "checkpoint_id": checkpoint_id,
            "checkpoint_text": checkpoint_meta[checkpoint_id]["text"],
            "severity": checkpoint_meta[checkpoint_id]["severity"],
            "user_input": user_input,
            "baseline_reply": baseline_reply,
            "candidate_reply": candidate_reply,
            "before_verdict": before,
            "after_verdict": after,
            "changed_strategy": _strategy_for(checkpoint_id, checkpoint_meta[checkpoint_id]["text"]),
        })

    checkpoint_transitions = []
    for checkpoint_id, counts in transitions.items():
        meta = checkpoint_meta[checkpoint_id]
        fixed = counts.get("fail_to_pass", 0)
        regressed = counts.get("pass_to_fail", 0)
        remaining = counts.get("fail_to_fail", 0)
        if fixed and regressed:
            status = "mixed"
        elif regressed:
            status = "regressed"
        elif fixed:
            status = "improved"
        elif remaining:
            status = "remaining"
        else:
            status = "stable"
        checkpoint_transitions.append({
            **meta,
            "status": status,
            "fail_to_pass": fixed,
            "pass_to_fail": regressed,
            "fail_to_fail": remaining,
            "pass_to_pass": counts.get("pass_to_pass", 0),
            "na_transitions": sum(value for key, value in counts.items() if key.startswith("na_") or key.endswith("_to_na")),
        })
    status_rank = {"improved": 0, "regressed": 1, "mixed": 2, "remaining": 3, "stable": 4}
    checkpoint_transitions.sort(key=lambda row: (status_rank[row["status"]], severity_rank.get(row["severity"], 9), row["checkpoint_id"]))
    case_candidates.sort(key=lambda row: (0 if row["case_type"] == "fixed" else 1, severity_rank.get(row["severity"], 9), row["checkpoint_id"], row["run_id"]))
    def unique_cases(kind: str, limit: int) -> list[dict]:
        selected: list[dict] = []
        seen: set[str] = set()
        for row in case_candidates:
            if row["case_type"] != kind or row["checkpoint_id"] in seen:
                continue
            selected.append(row)
            seen.add(row["checkpoint_id"])
            if len(selected) >= limit:
                break
        return selected

    fixed_cases = unique_cases("fixed", 5)
    regressed_cases = unique_cases("regressed", 2)
    transition_summary = {
        "fixed_judgments": sum(row["fail_to_pass"] for row in checkpoint_transitions),
        "regressed_judgments": sum(row["pass_to_fail"] for row in checkpoint_transitions),
        "remaining_failed_judgments": sum(row["fail_to_fail"] for row in checkpoint_transitions),
        "improved_checkpoints": sum(row["status"] == "improved" for row in checkpoint_transitions),
        "regressed_checkpoints": sum(row["status"] == "regressed" for row in checkpoint_transitions),
        "mixed_checkpoints": sum(row["status"] == "mixed" for row in checkpoint_transitions),
    }
    return {
        "case_diffs": fixed_cases + regressed_cases,
        "checkpoint_transitions": checkpoint_transitions,
        "transition_summary": transition_summary,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-run", required=True)
    parser.add_argument("--candidate-run", required=True)
    parser.add_argument("--comparability", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--html", required=True)
    args = parser.parse_args()

    baseline_dir = Path(args.baseline_run)
    candidate_dir = Path(args.candidate_run)
    comparability = _load(Path(args.comparability))
    baseline_summary = _load(baseline_dir / "summary.json")
    candidate_summary = _load(candidate_dir / "summary.json")
    baseline_manifest = _load(baseline_dir / "manifest.json")
    candidate_manifest = _load(candidate_dir / "manifest.json")

    assert baseline_summary["total_runs"] == candidate_summary["total_runs"] == comparability["run_count"]
    assert comparability["user_turns_identical"] is True
    assert comparability["judgments_modified"] is False
    for key in ("instruction_hash", "checklist_hash", "judge_policy_hash"):
        assert baseline_manifest[key] == candidate_manifest[key], key
    baseline_metrics = _metrics(baseline_summary)
    candidate_metrics = _metrics(candidate_summary)
    assert baseline_metrics["gate"] == "打回"
    assert candidate_metrics["gate"] == "可上线"
    assert candidate_metrics["p0_triggered_runs"] == 0
    assert candidate_metrics["fulfillment_rate"] >= baseline_metrics["fulfillment_rate"]

    regression_detail = _build_regression_detail(baseline_dir, candidate_dir)
    comparison = {
        "schema_version": 2,
        "comparison_id": "t02_delivery_fixedusers_v1_vs_guarded_v2_20260714",
        "method": comparability["method"],
        "comparability": {
            "same_user_inputs": True,
            "same_instruction": True,
            "same_checklist": True,
            "judgments_modified": False,
            "run_count": comparability["run_count"],
            "user_input_hash": comparability["user_input_hash"],
            "instruction_hash": baseline_manifest["instruction_hash"],
            "checklist_hash": baseline_manifest["checklist_hash"],
            "judge_policy_hash": baseline_manifest["judge_policy_hash"],
        },
        "baseline": {
            "version": comparability["baseline_version"],
            "run_dir": str(baseline_dir),
            "summary_hash": _sha256(baseline_dir / "summary.json"),
            "metrics": baseline_metrics,
        },
        "candidate": {
            "version": candidate_manifest["target_model_fingerprint"]["model"],
            "backend": candidate_manifest["target_model_fingerprint"]["backend"],
            "run_dir": str(candidate_dir),
            "summary_hash": _sha256(candidate_dir / "summary.json"),
            "transcript_source_hash": candidate_manifest.get("transcript_source_hash"),
            "metrics": candidate_metrics,
        },
        "capacity_evidence": {
            "unit": "machine_checkpoint_judgments",
            "baseline_machine_judgments": _metrics(baseline_summary)["machine_judgments"],
            "candidate_machine_judgments": _metrics(candidate_summary)["machine_judgments"],
            "interpretation": "系统批量完成逐项判定并生成复核队列；商业价值来自把人工工作从全量听审转为异常复核，不声明未经测量的节省金额。",
        },
        **regression_detail,
        "limitations": [
            "本证据只覆盖配送时间改约的10通固定用户输入。",
            "Judge为单模型单票；上线前应扩大样本并增加人工抽检。",
            "成本因模型未配置公开单价而标记为unpriced，不据此虚构ROI。",
        ],
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(comparison, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    b = comparison["baseline"]["metrics"]
    c = comparison["candidate"]["metrics"]
    short = lambda value: html.escape(str(value)[:12])
    page = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>EvalCall 同尺回归证据</title>
<style>
*{{box-sizing:border-box}} body{{margin:0;background:#f3f5f8;color:#14213d;font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif}}
.wrap{{width:1400px;height:800px;padding:58px 64px}} .eyebrow{{font-size:18px;font-weight:800;color:#a77b00;letter-spacing:.08em}}
h1{{margin:10px 0 12px;font-size:48px;line-height:1.1}} .sub{{font-size:21px;color:#667085}}
.grid{{display:grid;grid-template-columns:1fr 92px 1fr;gap:22px;margin-top:38px;align-items:stretch}}
.card{{background:white;border:1px solid #d8dee9;border-radius:24px;padding:30px;box-shadow:0 12px 38px rgba(20,33,61,.07)}}
.bad{{border-top:10px solid #cf342b}} .good{{border-top:10px solid #138a69}} .version{{font-size:20px;color:#667085;font-weight:700}}
.gate{{font-size:42px;font-weight:900;margin:8px 0 22px}} .bad .gate{{color:#cf342b}} .good .gate{{color:#138a69}}
.metrics{{display:grid;grid-template-columns:repeat(2,1fr);gap:14px}} .metric{{background:#f7f8fa;border-radius:16px;padding:15px 18px}}
.metric b{{display:block;font-size:32px}} .metric span{{font-size:15px;color:#667085}} .arrow{{display:flex;align-items:center;justify-content:center;font-size:52px;color:#ffcc00;font-weight:900}}
.hash{{margin-top:22px;padding:18px 22px;background:#14213d;color:white;border-radius:18px;font-family:Menlo,monospace;font-size:15px;line-height:1.8}}
.hash b{{color:#ffcc00}} .foot{{margin-top:18px;display:flex;justify-content:space-between;color:#667085;font-size:14px}}
</style></head><body><main class="wrap">
<div class="eyebrow">EVALCALL · FIXED-USER REGRESSION</div><h1>只改模型策略，不改评分尺：同一批用户输入的真实回归</h1>
<div class="sub">10 通用户轮逐字一致 · 重新走正常 Judge · judgments_modified = false</div>
<section class="grid">
<article class="card bad"><div class="version">{html.escape(comparison['baseline']['version'])}</div><div class="gate">{b['gate']}</div><div class="metrics">
<div class="metric"><b>{b['blocked_runs']}/10</b><span>通话打回</span></div><div class="metric"><b>{b['p0_triggered_runs']}</b><span>P0 触发通话</span></div>
<div class="metric"><b>{b['fulfillment_rate']:.0f}%</b><span>履约率</span></div><div class="metric"><b>{b['avg_score']:.1f}</b><span>平均分</span></div></div></article>
<div class="arrow">→</div>
<article class="card good"><div class="version">{html.escape(comparison['candidate']['version'])}</div><div class="gate">{c['gate']}</div><div class="metrics">
<div class="metric"><b>{c['blocked_runs']}/10</b><span>通话打回</span></div><div class="metric"><b>{c['p0_triggered_runs']}</b><span>P0 触发通话</span></div>
<div class="metric"><b>{c['fulfillment_rate']:.0f}%</b><span>履约率</span></div><div class="metric"><b>{c['avg_score']:.1f}</b><span>平均分</span></div></div></article>
</section>
<div class="hash"><b>USER</b> {short(comparison['comparability']['user_input_hash'])}… &nbsp; <b>SOP</b> {short(comparison['comparability']['instruction_hash'])}… &nbsp; <b>CHECKLIST</b> {short(comparison['comparability']['checklist_hash'])}… &nbsp; <b>JUDGE</b> {short(comparison['comparability']['judge_policy_hash'])}…<br>
<b>AUDIT</b> 同一输入 / 同一指令 / 同一Checklist / 正常Judge重评 / 不手改judgments</div>
<div class="foot"><span>证据：comparison.json + baseline/candidate summary.json + manifests</span><span>范围：T02 配送时间改约 · 2026-07-14</span></div>
</main></body></html>"""
    html_path = Path(args.html)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(page, encoding="utf-8")
    print(json.dumps({"comparison": str(out_path), "html": str(html_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
