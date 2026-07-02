"""定向重判工具：对指定 run 目录里的指定检查点（或全部）用 N 票多数决重判并回写。

用法: python3 rejudge_checkpoint.py <run_dir> <checkpoint_id|all> [n_votes]

场景：
- 单票 LLM 误判时只重判该检查点，避免全量重跑的时间成本
- judge 提示词升级后，传 all 从已有轨迹全量重判（无需重新跑对话模拟）
"""
import json
import sys

from evalcall import judge
from evalcall.cli import _aggregate_summary

if len(sys.argv) < 3:
    sys.exit("用法：python3 rejudge_checkpoint.py <run目录> <检查点id|all> [票数，默认3]\n"
             "  例：python3 rejudge_checkpoint.py runs/official01 forbidden_1 3")
run_dir, cp_id = sys.argv[1], sys.argv[2]
n_votes = int(sys.argv[3]) if len(sys.argv) > 3 else 3

checkpoints = json.load(open(f"{run_dir}/checklist.json"))
ALL = cp_id.lower() == "all"
target_cps = checkpoints if ALL else [next(c for c in checkpoints if c["id"] == cp_id)]
trajectories = {}
trajectories_by_persona = {}
with open(f"{run_dir}/transcripts.jsonl") as f:
    for line in f:
        t = json.loads(line)
        trajectories[t["run_id"]] = t
        # arena 生成的 run_id 是内部哈希，与判定文件的复合 ID 可能不一致，
        # 兜底按 persona_id 匹配（n=1 时一一对应）
        trajectories_by_persona.setdefault(t["persona_id"], []).append(t)

by_run = json.load(open(f"{run_dir}/judgments_by_run.json"))
flat = json.load(open(f"{run_dir}/judgments.json"))

per_run_summary = []
task_id = by_run[0]["task_id"]
# 按 persona 维护位置游标（.index(run) 是值匹配，两条判定全等的 run 会错配）
_persona_cursor: dict = {}
for run in by_run:
    rid = run["run_id"]
    traj = trajectories.get(rid)
    if traj is None:
        # 兜底1：persona 内唯一；兜底2：按出现顺序对位（cli 落盘顺序 =
        # judgments_by_run 顺序，均为 persona 外层、重复序号内层）
        cands = trajectories_by_persona.get(run["persona_id"], [])
        if len(cands) == 1:
            traj = cands[0]
        else:
            idx = _persona_cursor.get(run["persona_id"], 0)
            _persona_cursor[run["persona_id"]] = idx + 1
            if idx < len(cands):
                traj = cands[idx]
            else:
                raise SystemExit(f"无法匹配轨迹：{rid}（persona 候选 {len(cands)} 条，位置 {idx}）")
    print(f"[rejudge] {rid} / {cp_id} × {n_votes}票 …", flush=True)
    new_list = judge.judge_trajectory(target_cps, traj, n_votes=n_votes)
    new_by_id = {j["checkpoint_id"]: j for j in new_list}
    for nid, new_j in new_by_id.items():
        old = next((j for j in run["judgments"] if j["checkpoint_id"] == nid), None)
        old_v = old["verdict"] if old else "?"
        if old_v != new_j["verdict"]:
            print(f"  {nid}: {old_v} → {new_j['verdict']} (votes={[v['verdict'] for v in new_j.get('judge_votes', [])]})", flush=True)
    # 回写嵌套版
    run["judgments"] = [new_by_id.get(j["checkpoint_id"], j) for j in run["judgments"]]
    run["summary"] = judge.summarize(checkpoints, run["judgments"])
    # 回写扁平版（保留 cp 元信息字段）
    for fj in flat:
        if fj["run_id"] == rid and fj["checkpoint_id"] in new_by_id:
            new_j = new_by_id[fj["checkpoint_id"]]
            for k in ("verdict", "confidence", "evidence", "judge_votes", "method", "vote_agreement"):
                if k in new_j:
                    fj[k] = new_j[k]
    per_run_summary.append({"run_id": rid, "persona_id": run["persona_id"], **run["summary"]})

json.dump(by_run, open(f"{run_dir}/judgments_by_run.json", "w"), ensure_ascii=False, indent=2)
json.dump(flat, open(f"{run_dir}/judgments.json", "w"), ensure_ascii=False, indent=2)
overall = _aggregate_summary(per_run_summary, task_id, checkpoints)
json.dump(overall, open(f"{run_dir}/summary.json", "w"), ensure_ascii=False, indent=2)
print("[rejudge] 完成，summary 已更新:", json.dumps(overall, ensure_ascii=False)[:200], flush=True)
