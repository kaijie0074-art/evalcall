"""EvalCall 命令行入口。

子命令：
- run    编译任务 → 循环跑对话(arena) → judge → 落盘 transcripts/judgments/summary
- report 读取 run 目录 → 调 report.build_report 生成 report.html

用法：
  python -m evalcall.cli run --task data/tasks/xxx.yaml --personas all --n 3 --out runs/demo/
  python -m evalcall.cli report --run runs/demo/

arena / simulator / report 由其他 Agent 实现，这里只 import 并按 SPEC 接口调用；
import 失败时给出清晰中文报错，而非 traceback。
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from typing import Any, Optional

import yaml  # pyyaml，SPEC 允许的唯一 YAML 依赖

from . import compiler, judge


# ----------------------------------------------------------------------------- #
# 小工具
# ----------------------------------------------------------------------------- #
def _die(msg: str, code: int = 1) -> "NoReturn":  # type: ignore[name-defined]
    """打印中文错误并退出，不抛 traceback。"""
    print(f"[evalcall] 错误：{msg}", file=sys.stderr)
    sys.exit(code)


def _load_yaml(path: str) -> dict:
    """读取单个 YAML 文件为 dict。"""
    if not os.path.exists(path):
        _die(f"找不到文件：{path}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception as exc:  # noqa: BLE001
        _die(f"解析 YAML 失败（{path}）：{exc}")
    if not isinstance(data, dict):
        _die(f"YAML 顶层必须是对象（{path}）")
    return data  # type: ignore[return-value]


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _resolve_personas(personas_arg: str, persona_dir: str = "data/personas") -> list[dict]:
    """解析 --personas 参数。

    - "all"：加载 persona_dir 下所有 *.yaml
    - 逗号分隔的 id/文件名：逐个加载
    persona 文件不存在时不致命，仅警告并跳过；全空则用一个内置默认 persona 兜底。
    """
    personas: list[dict] = []
    if personas_arg.strip().lower() == "all":
        files = sorted(glob.glob(os.path.join(persona_dir, "*.yaml")) + glob.glob(os.path.join(persona_dir, "*.yml")))
        for fp in files:
            try:
                p = _load_yaml(fp)
                p.setdefault("id", os.path.splitext(os.path.basename(fp))[0])
                personas.append(p)
            except SystemExit:
                print(f"[evalcall] 跳过无法解析的 persona：{fp}", file=sys.stderr)
    else:
        for token in personas_arg.split(","):
            token = token.strip()
            if not token:
                continue
            # 尝试当作文件路径，再尝试 persona_dir 下同名
            candidates = [token, os.path.join(persona_dir, f"{token}.yaml"), os.path.join(persona_dir, f"{token}.yml")]
            found = next((c for c in candidates if os.path.exists(c)), None)
            if found is None:
                print(f"[evalcall] 警告：找不到 persona {token}，跳过", file=sys.stderr)
                continue
            p = _load_yaml(found)
            p.setdefault("id", token)
            personas.append(p)

    if not personas:
        print("[evalcall] 警告：未加载到任何 persona，使用内置默认配合型 persona", file=sys.stderr)
        personas = [{"id": "default_cooperative", "name": "默认配合用户", "style": "cooperative"}]
    return personas


# ----------------------------------------------------------------------------- #
# run 子命令
# ----------------------------------------------------------------------------- #
def cmd_run(args: argparse.Namespace) -> None:
    # 延迟 import arena，给出清晰报错（arena 由 Agent B 提供）
    try:
        from .arena import run_dialogue  # type: ignore
    except Exception as exc:  # noqa: BLE001
        _die(
            "无法导入 arena.run_dialogue（应由对话执行器模块提供）。"
            f"\n  详情：{exc}"
            "\n  请确认 evalcall/arena.py 已就位且提供 run_dialogue(task, persona, checkpoints, max_turns)。"
        )
        return  # 让类型检查器满意；_die 已退出

    task = _load_yaml(args.task)
    task.setdefault("id", task.get("id") or os.path.splitext(os.path.basename(args.task))[0])
    task_id = task["id"]

    out_dir = args.out or os.path.join("runs", task_id)
    _ensure_dir(out_dir)

    personas = _resolve_personas(args.personas)

    if getattr(args, "checklist", None):
        # 复用已固化的检查点清单——比较实验（A/B、消融、回归）必须用同一把尺子：
        # LLM 编译器非确定性，每次重编译的检查点数量/措辞/severity 都会漂移，
        # 跨 run 对比若各自编译，结论无效（实测踩坑后新增本通道）。
        print(f"[evalcall] 复用检查点清单：{args.checklist}")
        with open(args.checklist, encoding="utf-8") as f:
            cp_raw = json.load(f)
        checkpoints = [compiler.Checkpoint(**{
            k: v for k, v in c.items()
            if k in ("id", "type", "text", "source_quote", "severity", "needs_review", "keywords")
        }) for c in cp_raw]
    else:
        print(f"[evalcall] 编译任务指令：{task_id} …")
        try:
            checkpoints = compiler.compile_task(task, model=args.model)
        except Exception as exc:  # noqa: BLE001  —— LLM/解析失败给清晰报错而非 traceback
            _die(f"指令编译失败：{exc}\n  请检查 LLM 后端配置（EVALCALL_BACKEND / 模型 / key）或任务指令内容")
            return
    if not checkpoints:
        _die("指令编译未产出任何检查点，请检查任务指令内容或 LLM 后端配置")

    # 附加全局安全/合规红线（P0 一票否决级，来源 policy 而非指令，守 R-溯源）。
    # forbidden 类红线复用 judge 现有 forbidden 规则轨——单一规则源，不另立一套。
    if not getattr(args, "no_safety", False):
        from . import safety as _safety
        safety_cps = _safety.load_safety_checkpoints()
        if safety_cps:
            existing_ids = {c.id for c in checkpoints}
            added = [c for c in safety_cps if c.id not in existing_ids]
            checkpoints = list(checkpoints) + added
            print(f"[evalcall] 附加 {len(added)} 条全局安全/合规红线（P0 一票否决）")
        else:
            print("[evalcall] 警告：未加载到安全红线 policy（data/policy/safety_redlines.yaml），跳过", file=sys.stderr)

    # C18 履约达成检查点：从任务 goal 生成 outcome 检查点（goal 是任务指令级目标，
    # 故 source_quote 溯源回 goal，守 R-溯源）。评"这通电话有没有把活办成"——
    # 真实外呼运营第一 KPI，原系统全是质检机制、没人测履约达成（业务红队挖出的盲点）。
    goal = (task.get("goal") or "").strip()
    if goal and not any(getattr(c, "id", "") == "outcome_goal" for c in checkpoints):
        checkpoints = list(checkpoints) + [compiler.Checkpoint(
            id="outcome_goal",
            type="outcome",
            text=f"本通电话达成履约目标：{goal}",
            source_quote=goal,
            severity="major",
        )]
        print("[evalcall] 已加入履约达成（outcome）检查点")

    # P4-1 真实性/拟人度检查点（导师建议·用户拍板保留原意）：仅当指令涉及身份/话术处理时生成，
    # 守 R-溯源——source_quote 取指令中触发的身份相关片段，无指令依据不凭空生成（避免悬空检查点）。
    instruction_text = str(task.get("instruction") or "")
    if not getattr(args, "no_safety", False) and not any(
        getattr(c, "id", "") == "authenticity_identity" for c in checkpoints
    ):
        import re as _re
        m = _re.search(r"[^。\n]*(表明身份|核实身份|自报家门|身份|客服专员|真人|机器人)[^。\n]*", instruction_text)
        if m:
            checkpoints = list(checkpoints) + [compiler.Checkpoint(
                id="authenticity_identity",
                type="authenticity",
                text="面对用户对身份的质疑（如『你是不是机器人/真人』），模型应能稳健应对、按要求恰当处理身份并引导回服务（真实性/拟人度）",
                source_quote=m.group(0).strip()[:80],
                severity="major",
            )]
            print("[evalcall] 已加入真实性/拟人度（authenticity）检查点")

    cp_dicts = compiler.checkpoints_to_dicts(checkpoints)
    n_review = sum(1 for c in cp_dicts if c.get("needs_review"))
    print(f"[evalcall] 编译得到 {len(cp_dicts)} 条检查点（其中 {n_review} 条溯源待复核）")

    # 检查点元信息映射：扁平化 judgment 时把 type/severity/text/source_quote 并进每条判定，
    # 供 report.build_report 做四维切片、severity 加权、来源引文展示（对齐 Agent C schema 约定）。
    cp_meta = {c.get("id"): c for c in cp_dicts}

    # 落盘检查点清单
    with open(os.path.join(out_dir, "checklist.json"), "w", encoding="utf-8") as f:
        json.dump(cp_dicts, f, ensure_ascii=False, indent=2)

    transcripts_path = os.path.join(out_dir, "transcripts.jsonl")
    flat_judgments: list[dict[str, Any]] = []          # 扁平判定列表 → judgments.json（report 直接消费）
    by_run_judgments: list[dict[str, Any]] = []        # 按轨迹嵌套 → judgments_by_run.json（备查）
    per_run_summary: list[dict[str, Any]] = []

    # coverage-guided 调度状态：跨轨迹累计"已触达"检查点（pass/fail 即触达），
    # 每条新轨迹开跑前，把仍未触达的检查点作为优先攻击目标喂给模拟器，
    # 主动制造能触发它们的场景（FLARE 式行为覆盖引导，而非事后统计）。
    covered_cp_ids: set = set()

    # P4-2 persona 配比：默认按行业常识权重分配轨迹（平淡为主），可 --no-mix 关闭回到均分。
    # 总预算保持 n×persona数 不变，只改分布——防止极端 persona 被均分放大成过拟合诱因。
    persona_ids = [p.get("id", "persona") for p in personas]
    mix_meta = {"source": "均分（--no-mix 或无配比）", "counts": {pid: args.n for pid in persona_ids}}
    if not getattr(args, "no_mix", False):
        from . import persona_mix as _pm
        mix = _pm.load_mix()
        if mix["weights"]:
            counts = _pm.allocate(persona_ids, args.n * len(personas), mix["weights"])
            mix_meta = {"source": mix["source"], "counts": counts}
            print(f"[evalcall] persona 配比（{mix['source']}）：{counts}")
    persona_counts = mix_meta["counts"]

    with open(transcripts_path, "w", encoding="utf-8") as tf:
        for p_i, persona in enumerate(personas):
            persona_id = persona.get("id", "persona")
            for i in range(1, persona_counts.get(persona_id, 0) + 1):
                # 可复现性：seed 派生规则 (base + persona序号*1000 + 轨迹序号)，
                # 落进轨迹 meta——之前 arena 支持 seed 但主流程从不传，复现是空话
                traj_seed = (
                    args.seed + p_i * 1000 + i if args.seed is not None else None
                )
                run_id = f"{task_id}__{persona_id}__{i}"
                uncovered = [
                    c.get("text") for c in cp_dicts
                    if c.get("id") not in covered_cp_ids and c.get("text")
                ] if covered_cp_ids else []  # 首条轨迹无判定数据，不加先验
                if uncovered:
                    print(f"[evalcall] coverage-guided：{len(uncovered)} 个未触达检查点置为优先目标")
                print(f"[evalcall] 跑对话 {run_id} …")
                try:
                    trajectory = run_dialogue(
                        task=task,
                        persona=persona,
                        checkpoints=cp_dicts,  # arena 按 dict 访问（.get），传字典形式
                        max_turns=args.max_turns,
                        priority_targets=uncovered or None,
                        seed=traj_seed,
                    )
                except Exception as exc:  # noqa: BLE001  —— 单条失败不中断整批
                    print(f"[evalcall] 警告：对话 {run_id} 失败，跳过：{exc}", file=sys.stderr)
                    continue

                # 补齐轨迹元字段
                trajectory.setdefault("run_id", run_id)
                trajectory.setdefault("task_id", task_id)
                trajectory.setdefault("persona_id", persona_id)
                tf.write(json.dumps(trajectory, ensure_ascii=False) + "\n")
                tf.flush()

                # 评测（n_votes 默认 3：跨模型/多数投票是旗舰可靠性机制，
                # 标准 run 必须默认开启，不能退化成单票——单票只是单一裁判的一面之词）
                judgments = judge.judge_trajectory(
                    checkpoints, trajectory, model=args.model, n_votes=args.votes
                )
                summary = judge.summarize(checkpoints, judgments)

                # 覆盖率反馈：本条轨迹触达（pass/fail）的检查点计入已覆盖
                for j in judgments:
                    if str(j.get("verdict", "na")).lower() in ("pass", "fail"):
                        covered_cp_ids.add(j.get("checkpoint_id"))

                # 扁平化：每条判定补轨迹定位字段 + 检查点元信息，供报告切片/加权/引文展示
                for j in judgments:
                    meta = cp_meta.get(j.get("checkpoint_id"), {})
                    flat = {
                        "run_id": run_id,
                        "task_id": task_id,
                        "persona_id": persona_id,
                        "type": meta.get("type", "flow"),
                        "severity": meta.get("severity", "minor"),
                        "text": meta.get("text", ""),
                        "source_quote": meta.get("source_quote", ""),
                        **j,
                    }
                    flat_judgments.append(flat)

                by_run_judgments.append(
                    {"run_id": run_id, "task_id": task_id, "persona_id": persona_id,
                     "judgments": judgments, "summary": summary}
                )
                per_run_summary.append({"run_id": run_id, "persona_id": persona_id, **summary})

    # 落盘 judgments（扁平，report 直接消费）+ 按轨迹嵌套版本（备查）
    with open(os.path.join(out_dir, "judgments.json"), "w", encoding="utf-8") as f:
        json.dump(flat_judgments, f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "judgments_by_run.json"), "w", encoding="utf-8") as f:
        json.dump(by_run_judgments, f, ensure_ascii=False, indent=2)

    # 汇总 summary.json
    overall = _aggregate_summary(per_run_summary, task_id, checkpoints)
    overall["persona_mix"] = mix_meta  # P4-2 配比与口径，如实写进产物供报告声明
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(overall, f, ensure_ascii=False, indent=2)

    print(
        f"[evalcall] 完成。上线决策【{overall['gate']}】，共 {len(per_run_summary)} 条轨迹，"
        f"平均分 {overall['avg_score']}，履约达成率 {overall.get('fulfillment_rate')}%，"
        f"P0 打回 {overall['blocked_runs']} 条，待人工复核 {overall['needs_human_review_total']} 项。输出目录：{out_dir}"
    )


def _aggregate_summary(
    per_run: list[dict[str, Any]],
    task_id: str,
    checkpoints: list[Any],
) -> dict[str, Any]:
    """跨轨迹聚合：平均分、否决条数、平均违反率、平均分歧率，并按 persona 切片。"""
    if not per_run:
        return {
            "task_id": task_id,
            "total_runs": 0,
            "avg_score": 0.0,
            "gate": "无数据",
            "gate_reasons": [],
            "blocked_runs": 0,
            "needs_human_review_total": 0,
            "fulfillment_rate": None,
            "fulfilled_runs": 0,
            "fulfillment_eval_runs": 0,
            "critical_failed_runs": 0,
            "avg_violation_rate_per_100": 0.0,
            "avg_judge_disagreement_rate": 0.0,
            "by_persona": {},
            "total_checkpoints": len(checkpoints),
        }

    n = len(per_run)
    avg_score = round(sum(r["score"] for r in per_run) / n, 1)
    crit = sum(1 for r in per_run if r.get("critical_failed"))
    avg_vio = round(sum(r.get("violation_rate_per_100", 0.0) for r in per_run) / n, 1)
    avg_dis = round(sum(r.get("judge_disagreement_rate", 0.0) for r in per_run) / n, 3)

    # P1-3 门禁聚合：任一轨迹打回 → 整体打回（外呼安全从严，一通坏电话就是事故）。
    blocked_runs = [r for r in per_run if r.get("gate") == "打回"]
    overall_gate = "打回" if blocked_runs else "可上线"
    gate_reasons: list[dict[str, Any]] = []
    seen_cp: set = set()
    for r in per_run:
        for gr in r.get("gate_reasons", []):
            if gr.get("checkpoint_id") not in seen_cp:
                seen_cp.add(gr.get("checkpoint_id"))
                gate_reasons.append(gr)
    needs_review_total = sum(r.get("needs_human_review_count", 0) for r in per_run)

    # C18 履约达成率：有 outcome 判定的轨迹里，达成（outcome 全 pass 无 fail）的占比
    fulfill_runs = [r for r in per_run if (r.get("fulfillment", {}).get("pass", 0) + r.get("fulfillment", {}).get("fail", 0)) > 0]
    fulfilled_n = sum(1 for r in fulfill_runs if r.get("fulfilled"))
    fulfillment_rate = round(fulfilled_n / len(fulfill_runs) * 100.0, 1) if fulfill_runs else None

    by_persona: dict[str, dict[str, Any]] = {}
    for r in per_run:
        pid = r.get("persona_id", "?")
        b = by_persona.setdefault(pid, {"runs": 0, "score_sum": 0.0, "critical_failed_runs": 0})
        b["runs"] += 1
        b["score_sum"] += r["score"]
        if r.get("critical_failed"):
            b["critical_failed_runs"] += 1
    for pid, b in by_persona.items():
        b["avg_score"] = round(b["score_sum"] / b["runs"], 1)
        del b["score_sum"]

    return {
        "task_id": task_id,
        "total_runs": n,
        "avg_score": avg_score,
        "gate": overall_gate,                 # P1-3 整体上线决策
        "gate_reasons": gate_reasons,         # 触发打回的 P0 明细（去重）
        "blocked_runs": len(blocked_runs),
        "needs_human_review_total": needs_review_total,  # P1-5
        "fulfillment_rate": fulfillment_rate,            # C18 履约达成率(%)，无 outcome 时 None
        "fulfilled_runs": fulfilled_n,
        "fulfillment_eval_runs": len(fulfill_runs),
        "critical_failed_runs": crit,
        "avg_violation_rate_per_100": avg_vio,
        "avg_judge_disagreement_rate": avg_dis,
        "by_persona": by_persona,
        "total_checkpoints": len(checkpoints),
    }


# ----------------------------------------------------------------------------- #
# report 子命令
# ----------------------------------------------------------------------------- #
def _load_run_verdicts(run_dir: str) -> dict[str, dict]:
    """读取一个 run 目录的判定，返回 {checkpoint_id: {verdict, method, type, text}}。

    取每个 checkpoint 跨轨迹的代表判定：fail 优先（质检从严），其次 pass，再 na。
    """
    path = os.path.join(run_dir, "judgments.json")
    if not os.path.exists(path):
        _die(f"找不到判定文件：{path}")
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)
    priority = {"fail": 0, "pass": 1, "na": 2}
    best: dict[str, dict] = {}
    for j in rows:
        cid = j.get("checkpoint_id")
        if not cid:
            continue
        cur = best.get(cid)
        v = str(j.get("verdict", "na")).lower()
        if cur is None or priority.get(v, 3) < priority.get(cur["verdict"], 3):
            best[cid] = {
                "verdict": v,
                "method": str(j.get("method", "llm")).lower(),
                "type": str(j.get("type", "flow")).lower(),
                "text": j.get("text", cid),
            }
    return best


def cmd_diff(args: argparse.Namespace) -> None:
    """跨版本回归对比（P3-3）：对比 base/new 两个 run 的检查点判定。

    分轨标注可信度：规则轨/outcome 判定确定性高→变化直接采信；
    LLM 轨判定含裁判方差→变化标"需复测确认"，不把裁判噪声当模型退化。
    """
    base = _load_run_verdicts(args.base)
    new = _load_run_verdicts(args.new)
    rank = {"pass": 2, "na": 1, "fail": 0}  # 分越高越好
    improved, regressed, unchanged, appeared = [], [], [], []
    for cid, nv in new.items():
        bv = base.get(cid)
        # 规则轨/outcome 确定性高；LLM 轨变化需复测
        certain = nv["method"].startswith("rule") or nv["type"] == "outcome"
        tag = "确定" if certain else "需复测确认"
        if bv is None:
            appeared.append({"checkpoint_id": cid, "text": nv["text"], "verdict": nv["verdict"]})
            continue
        if nv["verdict"] == bv["verdict"]:
            unchanged.append(cid)
            continue
        delta = rank.get(nv["verdict"], 1) - rank.get(bv["verdict"], 1)
        rec = {"checkpoint_id": cid, "text": nv["text"],
               "from": bv["verdict"], "to": nv["verdict"], "confidence_tag": tag}
        (improved if delta > 0 else regressed).append(rec)

    out = {
        "base": args.base, "new": args.new,
        "improved": improved, "regressed": regressed,
        "unchanged_count": len(unchanged), "appeared": appeared,
    }
    print(f"[evalcall] 回归对比 {args.base} → {args.new}")
    print(f"  变好 {len(improved)} · 变差 {len(regressed)} · 不变 {len(unchanged)} · 新增检查点 {len(appeared)}")
    for r in regressed:
        print(f"  ⬇ 变差 [{r['confidence_tag']}] {r['text']}：{r['from']}→{r['to']}")
    for r in improved:
        print(f"  ⬆ 变好 [{r['confidence_tag']}] {r['text']}：{r['from']}→{r['to']}")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"[evalcall] 已写出对比结果：{args.out}")


def cmd_grow(args: argparse.Namespace) -> None:
    """活清单增量（P3-1）：提议指令里遗漏的检查点，过溯源硬闸，写入待人工确认区。

    候选**不自动并入正式清单**——只写 candidates_pending.json 等人工确认（守 R-反循环）。
    """
    from . import grow as _grow
    task = _load_yaml(args.task)
    task.setdefault("id", task.get("id") or os.path.splitext(os.path.basename(args.task))[0])
    # 已有清单：优先复用 --checklist，否则现编译
    if getattr(args, "checklist", None):
        with open(args.checklist, encoding="utf-8") as f:
            cp_raw = json.load(f)
        existing = [compiler.Checkpoint(**{k: v for k, v in c.items()
                    if k in ("id", "type", "text", "source_quote", "severity", "needs_review", "keywords", "safety", "policy_source")}) for c in cp_raw]
    else:
        try:
            existing = compiler.compile_task(task, model=args.model)
        except Exception as exc:  # noqa: BLE001
            _die(f"编译已有清单失败：{exc}")
            return
    result = _grow.mine_candidates(task, existing, model=args.model)
    if result.get("error"):
        _die(f"挖掘候选失败：{result['error']}")
    out_path = args.out or os.path.join("runs", task["id"], "candidates_pending.json")
    _ensure_dir(os.path.dirname(out_path) or ".")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    acc, rej = result["accepted"], result["rejected"]
    print(f"[evalcall] 活清单增量：候选 {len(acc)+len(rej)} 条 → 过溯源硬闸保留 {len(acc)} 条（待人工确认），拦截 {len(rej)} 条")
    for r in rej:
        print(f"  ⛔ 拦截：{r.get('text','?')[:30]} —— {r.get('_reason')}")
    print(f"[evalcall] 待确认候选写入：{out_path}（不自动并入正式清单）")


def cmd_report(args: argparse.Namespace) -> None:
    try:
        from .report import build_report  # type: ignore
    except Exception as exc:  # noqa: BLE001
        _die(
            "无法导入 report.build_report（应由报告模块提供）。"
            f"\n  详情：{exc}"
            "\n  请确认 evalcall/report.py 已就位且提供 build_report(run_dir) -> html路径。"
        )
        return

    run_dir = args.run
    if not os.path.isdir(run_dir):
        _die(f"run 目录不存在：{run_dir}")
    try:
        html_path = build_report(run_dir)
    except Exception as exc:  # noqa: BLE001
        _die(f"生成报告失败：{exc}")
        return
    print(f"[evalcall] 报告已生成：{html_path}")


# ----------------------------------------------------------------------------- #
# 参数解析
# ----------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evalcall", description="EvalCall 外呼对话指令遵循自动评测系统")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="编译任务并跑评测")
    p_run.add_argument("--task", required=True, help="任务 YAML 路径")
    p_run.add_argument("--personas", default="all", help="persona：all 或逗号分隔的 id/路径")
    p_run.add_argument("--n", type=int, default=3, help="每个 persona 跑的轨迹数")
    p_run.add_argument("--out", default=None, help="输出目录，默认 runs/<task_id>/")
    p_run.add_argument("--max-turns", type=int, default=12, help="单条对话最大轮数")
    p_run.add_argument("--model", default=None, help="覆盖默认模型名（评测/编译用）")
    p_run.add_argument("--checklist", default=None, help="复用已固化的检查点清单 JSON（A/B 对比实验必须同尺）")
    p_run.add_argument("--votes", type=int, default=3, help="每条检查点 LLM 裁判投票数（默认 3：多数投票/跨模型裁判团；配 JUDGE_MODELS 环境变量轮换不同模型消系统性偏差）")
    p_run.add_argument("--no-safety", action="store_true", help="不附加全局安全/合规红线（默认附加 P0 一票否决级红线）")
    p_run.add_argument("--no-mix", action="store_true", help="不按 persona 配比加权分配轨迹（默认按行业常识配比；本flag回到各 persona 均分 --n 条）")
    p_run.add_argument("--seed", type=int, default=None, help="随机种子基值（派生 base+persona序号*1000+轨迹序号）。注：仅模拟器随机性可复现；judge 非确定性+coverage 反馈环使整条 run 不保证完全复现")
    p_run.set_defaults(func=cmd_run)

    p_rep = sub.add_parser("report", help="从 run 目录生成 HTML 报告")
    p_rep.add_argument("--run", required=True, help="run 输出目录")
    p_rep.set_defaults(func=cmd_report)

    p_grow = sub.add_parser("grow", help="活清单增量：提议指令里遗漏的检查点（过溯源硬闸，待人工确认）")
    p_grow.add_argument("--task", required=True, help="任务 YAML 路径")
    p_grow.add_argument("--checklist", default=None, help="复用已有检查点清单 JSON（否则现编译）")
    p_grow.add_argument("--model", default=None, help="覆盖默认模型名")
    p_grow.add_argument("--out", default=None, help="候选输出路径，默认 runs/<task_id>/candidates_pending.json")
    p_grow.set_defaults(func=cmd_grow)

    p_diff = sub.add_parser("diff", help="跨版本回归对比两个 run（变好/变差/需复测）")
    p_diff.add_argument("--base", required=True, help="基线 run 目录")
    p_diff.add_argument("--new", required=True, help="新版 run 目录")
    p_diff.add_argument("--out", default=None, help="可选：把对比结果写出为 JSON")
    p_diff.set_defaults(func=cmd_diff)

    return parser


def main(argv: Optional[list[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
