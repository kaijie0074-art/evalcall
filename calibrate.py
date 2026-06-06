#!/usr/bin/env python3
"""裁判自校准 / Judge Calibration via Known-Answer Injection。

目的：把 LLM 裁判当作"被测对象"，喂它一批已知真值（ground truth）的合成外呼对话，
对比裁判判定与真值，量化裁判自身的可靠性，让评测报告自带误差棒。

链路：
    golden_set.json（含 checkpoints + cases + 每条 case 的 ground_truth）
      → 对每条 case 构造 trajectory（turns）
      → judge.judge_trajectory(checkpoints, trajectory, n_votes=N)
      → 把裁判判定 verdict 与 ground_truth 逐检查点对齐
      → 计算 整体/分verdict 的 precision/recall/F1、3x3 混淆矩阵、
        置信度校准（判对 vs 判错的平均 confidence）、逐 case 明细
      → 写 runs/calibration/calibration.json + 打印人类可读摘要

用法：
    python3 calibrate.py                  # 全量 12 条
    python3 calibrate.py --limit 2        # 先试跑 2 条验证流程
    python3 calibrate.py --votes 3        # 每检查点投票次数（默认 3）
    python3 calibrate.py --model sonnet   # 指定裁判模型

注意：默认后端 claude-cli 很慢（单批 30-180s）。本脚本不修改 evalcall/ 任何文件，
只调用其公开接口 judge.judge_trajectory。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict
from typing import Any

# 让脚本能 import 同目录下的 evalcall 包
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from evalcall import judge  # noqa: E402

VERDICTS = ("pass", "fail", "na")

GOLDEN_PATH = os.path.join("data", "calibration", "golden_set.json")
OUT_DIR = os.path.join("runs", "calibration")
OUT_PATH = os.path.join(OUT_DIR, "calibration.json")


# --------------------------------------------------------------------------- #
# 指标计算
# --------------------------------------------------------------------------- #
def _prf(tp: int, fp: int, fn: int) -> dict[str, float]:
    """由 TP/FP/FN 算 precision / recall / f1。"""
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "support": tp + fn,  # 真值中该类的总数
        "tp": tp,
        "fp": fp,
        "fn": fn,
    }


def compute_metrics(pairs: list[dict]) -> dict[str, Any]:
    """pairs: [{case_id, checkpoint_id, truth, pred, correct, confidence, ...}]。

    返回 整体准确率、分 verdict 的 P/R/F1、3x3 混淆矩阵、宏平均 F1、置信度校准。
    """
    n = len(pairs)
    correct = sum(1 for p in pairs if p["correct"])
    accuracy = correct / n if n else 0.0

    # 3x3 混淆矩阵 confusion[truth][pred]
    confusion = {t: {p: 0 for p in VERDICTS} for t in VERDICTS}
    for p in pairs:
        t, pr = p["truth"], p["pred"]
        if t in confusion and pr in confusion[t]:
            confusion[t][pr] += 1

    # 分 verdict 的 one-vs-rest P/R/F1
    per_verdict: dict[str, dict] = {}
    for v in VERDICTS:
        tp = sum(1 for p in pairs if p["truth"] == v and p["pred"] == v)
        fp = sum(1 for p in pairs if p["truth"] != v and p["pred"] == v)
        fn = sum(1 for p in pairs if p["truth"] == v and p["pred"] != v)
        per_verdict[v] = _prf(tp, fp, fn)

    # 宏平均 F1（只对真值中出现过的类求平均，避免 support=0 的类拉低）
    present = [v for v in VERDICTS if per_verdict[v]["support"] > 0]
    macro_f1 = round(
        sum(per_verdict[v]["f1"] for v in present) / len(present), 4
    ) if present else 0.0

    # 置信度校准：判对 vs 判错 的平均 confidence（理想情况下判对的更自信）
    conf_correct = [p["confidence"] for p in pairs if p["correct"] and p["confidence"] is not None]
    conf_wrong = [p["confidence"] for p in pairs if not p["correct"] and p["confidence"] is not None]
    avg_conf_correct = round(sum(conf_correct) / len(conf_correct), 4) if conf_correct else None
    avg_conf_wrong = round(sum(conf_wrong) / len(conf_wrong), 4) if conf_wrong else None
    # 校准差：判对平均置信度 - 判错平均置信度，>0 说明裁判"自知之明"较好
    calib_gap = (
        round(avg_conf_correct - avg_conf_wrong, 4)
        if (avg_conf_correct is not None and avg_conf_wrong is not None)
        else None
    )

    # 按模型分别的置信度校准：跨模型混投时各模型置信度刻度不可比，
    # 必须按票面 model 字段分组各算 gap，可信度分层才有依据
    per_model: dict = {}
    for p in pairs:
        for v in p.get("judge_votes") or []:
            if not isinstance(v, dict) or "model" not in v:
                continue
            conf = v.get("confidence")
            if not isinstance(conf, (int, float)):
                continue
            vote_ok = str(v.get("verdict", "")).lower() == str(p["truth"]).lower()
            bucket = per_model.setdefault(v["model"], {"correct": [], "wrong": []})
            bucket["correct" if vote_ok else "wrong"].append(conf)
    per_model_calibration = {}
    for m, b in per_model.items():
        mc = round(sum(b["correct"]) / len(b["correct"]), 4) if b["correct"] else None
        mw = round(sum(b["wrong"]) / len(b["wrong"]), 4) if b["wrong"] else None
        per_model_calibration[m] = {
            "avg_confidence_when_correct": mc,
            "avg_confidence_when_wrong": mw,
            "calibration_gap": round(mc - mw, 4) if (mc is not None and mw is not None) else None,
            "n_correct": len(b["correct"]),
            "n_wrong": len(b["wrong"]),
        }

    return {
        "total_judgments": n,
        "correct": correct,
        "accuracy": round(accuracy, 4),
        "macro_f1": macro_f1,
        "per_verdict": per_verdict,
        "confusion_matrix": confusion,
        "confidence_calibration": {
            "avg_confidence_when_correct": avg_conf_correct,
            "avg_confidence_when_wrong": avg_conf_wrong,
            "calibration_gap": calib_gap,
            "n_correct": len(conf_correct),
            "n_wrong": len(conf_wrong),
        },
        "per_model_confidence_calibration": per_model_calibration,
    }


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def run(limit: int | None, votes: int, model: str | None) -> dict[str, Any]:
    with open(GOLDEN_PATH, "r", encoding="utf-8") as f:
        golden = json.load(f)

    checkpoints = golden["checkpoints"]
    cases = golden["cases"]
    if limit is not None:
        cases = cases[:limit]

    cp_index = {c["id"]: c for c in checkpoints}
    pairs: list[dict] = []
    case_details: list[dict] = []

    t0 = time.time()
    for i, case in enumerate(cases, 1):
        case_id = case["case_id"]
        truth_map = case["ground_truth"]
        trajectory = {
            "run_id": f"calib_{case_id}",
            "task_id": "calibration",
            "persona_id": "golden",
            "turns": case["turns"],
        }
        print(
            f"[{i}/{len(cases)}] 判定 {case_id} "
            f"（{len(truth_map)} 个埋值检查点，votes={votes}）...",
            file=sys.stderr,
            flush=True,
        )
        c0 = time.time()
        try:
            judgments = judge.judge_trajectory(
                checkpoints, trajectory, model=model, n_votes=votes
            )
        except Exception as exc:  # noqa: BLE001 —— 单 case 失败不拖垮全量
            print(f"    !! {case_id} 判定异常：{exc}", file=sys.stderr)
            judgments = []
        elapsed = time.time() - c0

        # 裁判判定按 checkpoint_id 索引
        jmap = {j["checkpoint_id"]: j for j in judgments}

        case_items: list[dict] = []
        case_correct = 0
        # 只评估这条 case 埋了真值的检查点
        for cid, truth in truth_map.items():
            j = jmap.get(cid, {})
            pred = j.get("verdict", "na")
            conf = j.get("confidence")
            correct = (pred == truth)
            if correct:
                case_correct += 1
            item = {
                "case_id": case_id,
                "checkpoint_id": cid,
                "checkpoint_type": cp_index.get(cid, {}).get("type"),
                "truth": truth,
                "pred": pred,
                "correct": correct,
                "confidence": conf,
                "method": j.get("method"),
                "vote_agreement": j.get("vote_agreement"),
                "evidence": j.get("evidence", []),
                # 保留票级明细（含每票投票模型）：跨模型置信度不在同一刻度，
                # 可信度分层必须按模型分别校准——票面数据是这项分析的原料
                "judge_votes": j.get("judge_votes", []),
            }
            pairs.append(item)
            case_items.append(item)

        case_details.append({
            "case_id": case_id,
            "description": case.get("description", ""),
            "n_checkpoints": len(truth_map),
            "n_correct": case_correct,
            "accuracy": round(case_correct / len(truth_map), 4) if truth_map else 0.0,
            "elapsed_sec": round(elapsed, 1),
            "items": case_items,
        })
        print(
            f"    -> {case_id} 命中 {case_correct}/{len(truth_map)}，耗时 {elapsed:.0f}s",
            file=sys.stderr,
            flush=True,
        )

    total_elapsed = time.time() - t0
    metrics = compute_metrics(pairs)

    # 分检查点类型的准确率（flow/constraint/forbidden/style）
    by_type: dict[str, dict] = defaultdict(lambda: {"correct": 0, "total": 0})
    for p in pairs:
        ct = p["checkpoint_type"] or "unknown"
        by_type[ct]["total"] += 1
        if p["correct"]:
            by_type[ct]["correct"] += 1
    by_type_out = {
        k: {
            "correct": v["correct"],
            "total": v["total"],
            "accuracy": round(v["correct"] / v["total"], 4) if v["total"] else 0.0,
        }
        for k, v in by_type.items()
    }

    result = {
        "meta": {
            "golden_set": GOLDEN_PATH,
            "backend": os.getenv("EVALCALL_BACKEND", "claude-cli"),
            "model": model or os.getenv("EVALCALL_MODEL", "sonnet"),
            # 溯源必须完整：裁判团配置不记录在产物里，事后无法自证
            # 「这次校准到底是同模型还是跨模型」（实测被评审质疑过一次）
            "judge_models": os.getenv("JUDGE_MODELS", "") or "(单模型)",
            "n_votes": votes,
            "n_cases": len(cases),
            "limit": limit,
            "total_elapsed_sec": round(total_elapsed, 1),
            "ground_truth_distribution": dict(Counter(p["truth"] for p in pairs)),
        },
        "metrics": metrics,
        "accuracy_by_checkpoint_type": by_type_out,
        "case_details": case_details,
    }
    return result


def print_summary(result: dict[str, Any]) -> None:
    m = result["metrics"]
    meta = result["meta"]
    print("\n" + "=" * 64)
    print("裁判自校准报告 (Judge Calibration)")
    print("=" * 64)
    print(f"后端/模型      : {meta['backend']} / {meta['model']}   投票数: {meta['n_votes']}")
    print(f"case 数        : {meta['n_cases']}   总判定数: {m['total_judgments']}   耗时: {meta['total_elapsed_sec']}s")
    print(f"真值分布       : {meta['ground_truth_distribution']}")
    print("-" * 64)
    print(f"整体准确率     : {m['accuracy']:.1%}  ({m['correct']}/{m['total_judgments']})")
    print(f"宏平均 F1      : {m['macro_f1']:.4f}")
    print("-" * 64)
    print("分 verdict 指标 (one-vs-rest):")
    print(f"  {'verdict':<8}{'P':>8}{'R':>8}{'F1':>8}{'support':>9}")
    for v in VERDICTS:
        pv = m["per_verdict"][v]
        print(f"  {v:<8}{pv['precision']:>8.3f}{pv['recall']:>8.3f}{pv['f1']:>8.3f}{pv['support']:>9}")
    print("-" * 64)
    print("混淆矩阵 (行=真值, 列=裁判判定):")
    print(f"  {'':<8}{'->pass':>8}{'->fail':>8}{'->na':>8}")
    for t in VERDICTS:
        row = m["confusion_matrix"][t]
        print(f"  {t:<8}{row['pass']:>8}{row['fail']:>8}{row['na']:>8}")
    print("-" * 64)
    cc = m["confidence_calibration"]
    print("置信度校准:")
    print(f"  判对时平均置信度: {cc['avg_confidence_when_correct']}  (n={cc['n_correct']})")
    print(f"  判错时平均置信度: {cc['avg_confidence_when_wrong']}  (n={cc['n_wrong']})")
    print(f"  校准差 (对-错)  : {cc['calibration_gap']}  (>0 越大说明裁判越有自知之明)")
    print("-" * 64)
    print("分检查点类型准确率:")
    for k, v in result["accuracy_by_checkpoint_type"].items():
        print(f"  {k:<12}{v['accuracy']:.1%}  ({v['correct']}/{v['total']})")
    print("-" * 64)
    print("逐 case 明细 (命中数 / 埋值数):")
    for cd in result["case_details"]:
        misses = [it for it in cd["items"] if not it["correct"]]
        miss_str = ""
        if misses:
            miss_str = "  误判: " + ", ".join(
                f"{it['checkpoint_id']}(真{it['truth']}->判{it['pred']})" for it in misses
            )
        print(f"  {cd['case_id']:<32}{cd['n_correct']}/{cd['n_checkpoints']}{miss_str}")
    print("=" * 64)


def main() -> int:
    ap = argparse.ArgumentParser(description="EvalCall 裁判自校准")
    ap.add_argument("--limit", type=int, default=None, help="只跑前 N 条 case（试跑用）")
    ap.add_argument("--votes", type=int, default=3, help="每检查点投票次数，默认 3")
    ap.add_argument("--model", type=str, default=None, help="裁判模型（默认走环境变量/sonnet）")
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    result = run(limit=args.limit, votes=args.votes, model=args.model)

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print_summary(result)
    print(f"\n已写入: {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
