"""pass^k 可靠性指标计算（τ-bench 口径）。

用法: python3 passk.py <run_dir> [k]

定义：对每个 persona 的 n 条独立轨迹，"单条 pass" = 该轨迹无 critical 级
fail（一票否决口径一致）。pass^k = 任取 k 条全部 pass 的概率（组合数估计）。
τ-bench (NeurIPS 2024) 用它度量"稳定做对"而非"偶尔做对"——
平均分高但 pass^k 低 = 模型不可靠。

同时输出每 persona 的均值±标准差（基于 raw_score，否决前原始分）。
"""
import json
import math
import sys
from collections import defaultdict

run_dir = sys.argv[1]
k_max = int(sys.argv[2]) if len(sys.argv) > 2 else 3

by_run = json.load(open(f"{run_dir}/judgments_by_run.json"))

per_persona = defaultdict(list)
for run in by_run:
    s = run["summary"]
    per_persona[run["persona_id"]].append({
        "pass": not s.get("critical_failed", False),
        "raw_score": s.get("raw_score", s.get("score", 0.0)),
    })


def comb(n, r):
    return math.comb(n, r) if r <= n else 0


print(f"{'persona':<28}{'n':>3}{'均值±std(raw)':>18}", end="")
for k in range(1, k_max + 1):
    print(f"{'pass^' + str(k):>9}", end="")
print()

overall = {"runs": [], }
for pid, runs in sorted(per_persona.items()):
    n = len(runs)
    c = sum(1 for r in runs if r["pass"])
    scores = [r["raw_score"] for r in runs]
    mean = sum(scores) / n
    # 样本标准差（n-1，Bessel 校正）——总体口径在 n=3 时低估约 18%
    std = (sum((x - mean) ** 2 for x in scores) / (n - 1)) ** 0.5 if n > 1 else 0.0
    print(f"{pid:<28}{n:>3}{f'{mean:.1f}±{std:.1f}':>18}", end="")
    row = {"persona_id": pid, "n": n, "mean_raw": round(mean, 1), "std_raw": round(std, 1)}
    for k in range(1, k_max + 1):
        # 无偏估计：C(c,k)/C(n,k)（τ-bench 同款）
        pk = comb(c, k) / comb(n, k) if comb(n, k) else float("nan")
        print(f"{pk:>9.2f}" if pk == pk else f"{'n/a':>9}", end="")
        row[f"pass^{k}"] = round(pk, 3) if pk == pk else None
    print()
    overall["runs"].append(row)

out = f"{run_dir}/passk.json"
json.dump(overall, open(out, "w"), ensure_ascii=False, indent=2)
print(f"\n已写入 {out}")
