"""裸裁判对照实验（B5，2026-07-02）：回答"你这跟直接拿 GPT 打个分有啥区别"。

对照组 = "裸 LLM 打分"：同一个后端模型，但**刻意去掉 EvalCall 的全部机制**——
- 无判定准则（无归属判定/自纠不洗白/语义等价从宽/噪声不豁免）
- 无证据引用强制（不要求引用第几轮原话）
- 无规则轨探测、无多数投票（单票）、无置信度校准
提示词就是普通人会写的那种"你是质检员，判断下面对话过没过"。

实验组 = EvalCall 完整裁判在同一黄金集上的已发布数字（runs/calibration/ 产物）。
同尺：同一份 32 例黄金集、同样的 pass/fail/na 三值口径。

用法：python3 bare_judge_baseline.py [--out runs/bare_judge_baseline]
产物：results.json（逐例判定+混淆矩阵+指标），供材料引用。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from evalcall import llm

_NAIVE_SYS = (
    "你是客服通话质检员。给你一通外呼对话记录和若干检查项，"
    "逐项判断被测客服（agent）是否满足：pass=满足，fail=不满足，na=本通对话没涉及。"
)

_SCHEMA = '{"results":[{"checkpoint_id":"...","verdict":"pass|fail|na"}]}'


def bare_judge_case(case: dict, checkpoints_by_id: dict) -> dict[str, str]:
    convo = "\n".join(
        f"[{t['role']}] {t['content']}" for t in case["turns"]
    )
    cps = [
        f"- id={cid}：{checkpoints_by_id[cid]['text']}"
        for cid in case["ground_truth"]
        if cid in checkpoints_by_id
    ]
    user = f"【对话记录】\n{convo}\n\n【检查项】\n" + "\n".join(cps)
    data = llm.chat_json(
        [{"role": "system", "content": _NAIVE_SYS}, {"role": "user", "content": user}],
        _SCHEMA,
    )
    out: dict[str, str] = {}
    for r in (data.get("results") or []):
        v = str(r.get("verdict", "na")).lower()
        out[str(r.get("checkpoint_id", ""))] = v if v in ("pass", "fail", "na") else "na"
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", default="data/calibration/golden_set.json")
    ap.add_argument("--out", default="runs/bare_judge_baseline")
    args = ap.parse_args(argv)

    golden = json.load(open(args.golden, encoding="utf-8"))
    cps_by_id = {c["id"]: c for c in golden["checkpoints"]}
    os.makedirs(args.out, exist_ok=True)

    rows = []
    for i, case in enumerate(golden["cases"], 1):
        print(f"[bare-judge] {i}/{len(golden['cases'])} {case['case_id']}", flush=True)
        try:
            preds = bare_judge_case(case, cps_by_id)
        except Exception as exc:  # noqa: BLE001 —— 单例失败记 error，不中断整批
            print(f"[bare-judge] 失败 {case['case_id']}：{exc}", file=sys.stderr, flush=True)
            preds = {}
        for cid, truth in case["ground_truth"].items():
            rows.append({
                "case_id": case["case_id"], "checkpoint_id": cid,
                "truth": truth, "pred": preds.get(cid, "missing"),
            })

    judged = [r for r in rows if r["pred"] != "missing"]
    correct = sum(1 for r in judged if r["pred"] == r["truth"])
    # 违规（fail 类）查准/查全——安全视角最关心的两个数
    tp = sum(1 for r in judged if r["truth"] == "fail" and r["pred"] == "fail")
    fp = sum(1 for r in judged if r["truth"] != "fail" and r["pred"] == "fail")
    fn = sum(1 for r in judged if r["truth"] == "fail" and r["pred"] != "fail")
    confusion: dict[str, int] = {}
    for r in judged:
        k = f"{r['truth']}->{r['pred']}"
        confusion[k] = confusion.get(k, 0) + 1

    metrics = {
        "protocol": "裸裁判：同后端模型，单票，无判定准则/无证据强制/无规则轨/无校准",
        "golden_set": golden["meta"].get("version", "?"),
        "total_labels": len(rows),
        "judged": len(judged),
        "missing": len(rows) - len(judged),
        "accuracy": round(correct / len(judged) * 100, 1) if judged else None,
        "fail_precision": round(tp / (tp + fp) * 100, 1) if (tp + fp) else None,
        "fail_recall": round(tp / (tp + fn) * 100, 1) if (tp + fn) else None,
        "confusion": confusion,
    }
    with open(os.path.join(args.out, "results.json"), "w", encoding="utf-8") as f:
        json.dump({"metrics": metrics, "rows": rows}, f, ensure_ascii=False, indent=2)
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
