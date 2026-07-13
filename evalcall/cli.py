"""EvalCall 命令行入口。

子命令：
- run    编译任务 → 循环跑对话(arena) → judge → 落盘 transcripts/judgments/summary
- evaluate 编译任务 → 读取已有对话 → judge → 落盘完整报告（跳过模拟器）
- report 读取 run 目录 → 调 report.build_report 生成 report.html

用法：
  python -m evalcall.cli run --task data/tasks/xxx.yaml --personas all --n 3 --out runs/demo/
  python -m evalcall.cli report --run runs/demo/

arena / simulator / report 由其他 Agent 实现，这里只 import 并按 SPEC 接口调用；
import 失败时给出清晰中文报错，而非 traceback。
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re
import sys
from typing import Any, Optional

import yaml  # pyyaml，SPEC 允许的唯一 YAML 依赖

from . import attribution, compiler, judge, llm, provenance, review


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


_CHECKPOINT_FIELDS = {
    "id", "type", "text", "source_quote", "severity",
    "needs_review", "keywords", "safety", "policy_source",
}


def _checkpoint_objects(rows: list[dict[str, Any]]) -> list[compiler.Checkpoint]:
    """从 JSON 检查点恢复对象，只接受明确 schema 字段。"""
    return [
        compiler.Checkpoint(**{k: v for k, v in row.items() if k in _CHECKPOINT_FIELDS})
        for row in rows
    ]


def _write_json_atomic(path: str, data: Any) -> None:
    """先写临时文件再替换，防止中断留下半个 JSON。"""
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _write_jsonl_atomic(path: str, rows: list[dict[str, Any]]) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _read_json_if_exists(path: str) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _persist_runtime_artifacts(
    out_dir: str,
    manifest: dict[str, Any],
    *,
    n_trajectories: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    telemetry = llm.telemetry_snapshot()
    finalized, runtime = provenance.finalize_manifest(
        manifest,
        telemetry,
        n_trajectories=n_trajectories,
    )
    _write_json_atomic(os.path.join(out_dir, "telemetry.json"), telemetry)
    _write_json_atomic(os.path.join(out_dir, "manifest.json"), finalized)
    return finalized, runtime


def _write_review_queue(out_dir: str, judgments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    queue = review.build_review_queue(judgments)
    _write_json_atomic(os.path.join(out_dir, "review_queue.json"), queue)
    csv_path = os.path.join(out_dir, "review_queue.csv")
    tmp = f"{csv_path}.tmp"
    fields = [
        "review_id", "run_id", "checkpoint_id", "checkpoint_text", "severity",
        "machine_verdict", "machine_confidence", "review_reasons", "evidence",
        "source_quote", "human_verdict", "reviewer", "comment",
    ]
    with open(tmp, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for item in queue:
            row = dict(item)
            row["review_reasons"] = json.dumps(row.get("review_reasons") or [], ensure_ascii=False)
            row["evidence"] = json.dumps(row.get("evidence") or [], ensure_ascii=False)
            writer.writerow({key: row.get(key) for key in fields})
    os.replace(tmp, csv_path)
    return queue


def _default_votes() -> int:
    """CLI/函数统一口径：显式 --votes > N_VOTES > 默认 3。"""
    try:
        return max(1, int(os.getenv("N_VOTES", "3")))
    except ValueError:
        return 3


def _task_identity(task: dict, task_path: str) -> str:
    return str(task.get("id") or task.get("task_id") or os.path.splitext(os.path.basename(task_path))[0])


def _identity_source_quote(instruction: str) -> str:
    """为任务级身份策略找一段逐字来源。"""
    candidates = [
        r"[^\n。]*(?:人机质疑|机器人|真人|AI 身份)[^\n。]*",
        r"[^\n。]*(?:你是机器人吗|你是真人吗)[^\n。]*",
    ]
    for pattern in candidates:
        match = re.search(pattern, instruction, re.IGNORECASE)
        if match and match.group(0).strip():
            return match.group(0).strip()[:240]
    return ""


def _apply_task_identity_policy(
    task: dict,
    checkpoints: list[compiler.Checkpoint],
) -> list[compiler.Checkpoint]:
    """
    将任务明示声明的身份口径应用到检查尺。

    deny_ai_identity 不是全局默认；它只对指令中有逐字依据、且 task YAML
    显式选择该 policy 的任务生效。去掉相互矛盾的全局 AI disclosure 检查点，
    改为任务级业务检查点，并保留 source_quote 供审计。
    """
    policy = str(task.get("identity_policy") or "").strip().lower()
    if policy != "deny_ai_identity":
        return checkpoints
    instruction = str(task.get("instruction") or "")
    quote = _identity_source_quote(instruction)
    if not quote:
        _die("identity_policy=deny_ai_identity 但指令中找不到身份口径原文，拒绝生成无源检查点")
    filtered = [cp for cp in checkpoints if cp.id not in {"safety_ai_disclosure", "business_identity_response"}]
    filtered.append(
        compiler.Checkpoint(
            id="business_identity_response",
            type="constraint",
            text="被质疑为 AI/机器人时，按本任务要求轻松带过；再次质疑推进加微，三次质疑或执意挂断时结束通话",
            source_quote=quote,
            severity="critical",
            policy_source="task.identity_policy=deny_ai_identity",
        )
    )
    print("[evalcall] 已应用任务级身份口径：deny_ai_identity（显式覆盖全局 AI disclosure 检查点）")
    return filtered


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
                # persona 目录里混着配置文件（mix.yaml 配比表）：凡带 weights 或
                # 无任何 persona 特征字段的，不是 persona，跳过——否则配置文件会被
                # 当成一个"用户"下场跑对话（2026-07-02 真实重跑踩坑）
                if "weights" in p or not any(k in p for k in ("label", "style", "strategy", "profile", "traits", "description")):
                    print(f"[evalcall] 跳过非 persona 文件：{fp}", file=sys.stderr)
                    continue
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


def _prepare_offline_checkpoints(
    task: dict,
    *,
    checklist_path: str | None,
    model: str | None,
    no_safety: bool,
) -> list[compiler.Checkpoint]:
    """为 evaluate 编译/复用检查尺，并接入安全、履约和身份策略。"""
    if checklist_path:
        with open(checklist_path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        if not isinstance(raw, list):
            _die(f"检查点清单顶层必须是数组：{checklist_path}")
        checkpoints = _checkpoint_objects(raw)
        print(f"[evalcall] 复用检查点清单：{checklist_path}")
    else:
        try:
            with llm.telemetry_phase("compile"):
                checkpoints = compiler.compile_task(task, model=model)
        except Exception as exc:  # noqa: BLE001
            _die(f"指令编译失败：{exc}\n  请检查 LLM 后端配置或使用 --checklist 复用已审核清单")
        if not checkpoints:
            _die("指令编译未产出任何检查点")

    if not no_safety:
        from . import safety as _safety
        safety_cps = _safety.load_safety_checkpoints()
        existing = {cp.id for cp in checkpoints}
        checkpoints = list(checkpoints) + [cp for cp in safety_cps if cp.id not in existing]

    checkpoints = _apply_task_identity_policy(task, list(checkpoints))

    goal = str(task.get("goal") or "").strip()
    if goal and not any(cp.id == "outcome_goal" for cp in checkpoints):
        checkpoints.append(
            compiler.Checkpoint(
                id="outcome_goal",
                type="outcome",
                text=f"本通电话达成履约目标：{goal}",
                source_quote=goal,
                severity="major",
            )
        )

    instruction = str(task.get("instruction") or "")
    if not any(cp.id == "authenticity_identity" for cp in checkpoints):
        match = re.search(r"[^。\n]*(?:表明身份|核实身份|自报家门|身份|真人|机器人)[^。\n]*", instruction)
        if match:
            checkpoints.append(
                compiler.Checkpoint(
                    id="authenticity_identity",
                    type="authenticity",
                    text="面对用户对身份的质疑时，模型应稳健应对、按任务要求处理并引导回服务目标",
                    source_quote=match.group(0).strip()[:240],
                    severity="major",
                )
            )
    return checkpoints


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
    task.setdefault("id", _task_identity(task, args.task))
    task_id = task["id"]
    llm.reset_telemetry()

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
        # 白名单必须含 safety/policy_source：漏掉会把安全红线"降级"成普通检查点，
        # 且后面附加安全轨时因 id 已存在被去重挡掉，最终清单里只剩被剥了 safety 的版本。
        checkpoints = [compiler.Checkpoint(**{
            k: v for k, v in c.items()
            if k in ("id", "type", "text", "source_quote", "severity",
                     "needs_review", "keywords", "safety", "policy_source")
        }) for c in cp_raw]
    else:
        print(f"[evalcall] 编译任务指令：{task_id} …")
        try:
            with llm.telemetry_phase("compile"):
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

    checkpoints = _apply_task_identity_policy(task, list(checkpoints))

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
    manifest = provenance.build_manifest(
        task=task,
        task_path=args.task,
        checklist=cp_dicts,
        source_mode="simulated_dialogues",
        n_votes=args.votes,
        model=args.model,
        seed=args.seed,
    )
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
                    with llm.telemetry_phase("simulate"):
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

                # 统一轨迹元字段：强制覆盖为 CLI 的复合 run_id。
                # arena 内部会自生成 uuid 型 run_id，若用 setdefault 会保留 uuid，
                # 导致 transcripts 与 judgments 两套 ID 对不上、报告证据跳转锚点全断。
                trajectory["run_id"] = run_id
                trajectory["task_id"] = task_id
                trajectory["persona_id"] = persona_id
                tf.write(json.dumps(trajectory, ensure_ascii=False) + "\n")
                tf.flush()

                # 评测（n_votes 默认 3：跨模型/多数投票是旗舰可靠性机制，
                # 标准 run 必须默认开启，不能退化成单票——单票只是单一裁判的一面之词）
                with llm.telemetry_phase("judge"):
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
                        # 安全红线标记必须随判定下传：report 的通话级 P0 标记读的是
                        # judgment 上的 safety，不带就只剩 severity=critical 一条腿
                        "safety": bool(meta.get("safety")),
                        "policy_source": meta.get("policy_source", ""),
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
    overall["attribution"] = attribution.analyze(
        cp_dicts,
        flat_judgments,
        overall,
        task_id=task_id,
    )
    overall["review_queue_count"] = len(_write_review_queue(out_dir, flat_judgments))
    finalized_manifest, runtime = _persist_runtime_artifacts(
        out_dir,
        manifest,
        n_trajectories=len(per_run_summary),
    )
    overall["manifest"] = finalized_manifest
    overall["runtime"] = runtime
    overall["run_id"] = finalized_manifest["run_id"]
    overall["started_at"] = finalized_manifest["started_at"]
    overall["backend"] = finalized_manifest["judge_models_config"]["backend"]
    overall["target_model"] = finalized_manifest["target_model_fingerprint"]["model"]
    overall["judge_model"] = ",".join(finalized_manifest["judge_models_config"]["models"])
    _write_json_atomic(os.path.join(out_dir, "summary.json"), overall)

    print(
        f"[evalcall] 完成。上线决策【{overall['gate']}】，共 {len(per_run_summary)} 条轨迹，"
        f"平均分 {overall['avg_score']}，履约达成率 {overall.get('fulfillment_rate')}%，"
        f"P0 打回 {overall['blocked_runs']} 条，待人工复核 {overall['needs_human_review_total']} 项。输出目录：{out_dir}"
    )


# ----------------------------------------------------------------------------- #
# evaluate 子命令：真实/脱敏历史对话，跳过模拟器
# ----------------------------------------------------------------------------- #
def cmd_evaluate(args: argparse.Namespace) -> None:
    from . import ingest as _ingest

    task = _load_yaml(args.task)
    task_id = _task_identity(task, args.task)
    task["id"] = task_id
    task.setdefault("task_id", task_id)

    try:
        ingested = _ingest.load_transcripts(
            args.transcripts,
            task_id,
            redact=not args.no_redact,
        )
    except _ingest.IngestError as exc:
        _die("对话输入校验失败：\n  - " + "\n  - ".join(exc.issues))
        return

    out_dir = args.out or os.path.join("runs", f"{task_id}_offline")
    _ensure_dir(out_dir)
    resume = not args.no_resume
    previous_telemetry = _read_json_if_exists(os.path.join(out_dir, "telemetry.json")) if resume else None
    llm.reset_telemetry(
        previous_telemetry.get("events", []) if isinstance(previous_telemetry, dict) else None
    )
    previous_manifest = _read_json_if_exists(os.path.join(out_dir, "manifest.json")) if resume else None
    saved_checklist = os.path.join(out_dir, "checklist.json")
    checklist_path = args.checklist
    if resume and not checklist_path and os.path.isfile(saved_checklist):
        checklist_path = saved_checklist
        print(f"[evalcall] 断点续评：自动复用已落盘检查尺 {saved_checklist}")

    print(f"[evalcall] 准备检查尺：{task_id} …")
    checkpoints = _prepare_offline_checkpoints(
        task,
        checklist_path=checklist_path,
        model=args.model,
        no_safety=args.no_safety,
    )
    cp_dicts = compiler.checkpoints_to_dicts(checkpoints)
    cp_meta = {str(cp["id"]): cp for cp in cp_dicts}
    candidate_manifest = provenance.build_manifest(
        task=task,
        task_path=args.task,
        checklist=cp_dicts,
        source_mode="offline_existing_transcripts",
        n_votes=args.votes,
        model=args.model,
    )
    if isinstance(previous_manifest, dict):
        compatibility = provenance.compare_manifests(previous_manifest, candidate_manifest)
        if not compatibility["comparable"]:
            _die(
                "断点续评的任务/检查尺/policy 已变更，拒绝混合两种口径："
                + "；".join(compatibility["reasons"])
                + "\n  请换新 --out 目录或使用 --no-resume 从头评测"
            )
        manifest = previous_manifest
    else:
        manifest = candidate_manifest
    _write_json_atomic(saved_checklist, cp_dicts)
    _write_jsonl_atomic(os.path.join(out_dir, "transcripts.jsonl"), ingested.trajectories)
    _write_json_atomic(os.path.join(out_dir, "ingestion_report.json"), ingested.report)

    by_run_path = os.path.join(out_dir, "judgments_by_run.json")
    flat_path = os.path.join(out_dir, "judgments.json")
    errors_path = os.path.join(out_dir, "evaluation_errors.json")

    by_run: list[dict[str, Any]] = []
    flat: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    if resume:
        try:
            with open(by_run_path, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
                if isinstance(loaded, list):
                    by_run = loaded
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        try:
            with open(flat_path, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
                if isinstance(loaded, list):
                    flat = loaded
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        try:
            with open(errors_path, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
                if isinstance(loaded, list):
                    errors = loaded
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    if resume and by_run and not isinstance(previous_manifest, dict):
        _die(
            "检测到旧版断点产物但缺 manifest.json，无法证明检查尺一致。"
            "\n  请使用 --no-resume 从头评测或换新 --out 目录"
        )

    current_ids = {str(tx["run_id"]) for tx in ingested.trajectories}
    by_run = [row for row in by_run if str(row.get("run_id")) in current_ids]
    flat = [row for row in flat if str(row.get("run_id")) in current_ids]
    errors = [row for row in errors if str(row.get("run_id")) in current_ids]
    completed = {str(row.get("run_id")) for row in by_run}

    def persist() -> dict[str, Any]:
        per_run = [
            {
                "run_id": row["run_id"],
                "persona_id": row.get("persona_id", "real_user"),
                **(row.get("summary") or {}),
            }
            for row in by_run
            if isinstance(row.get("summary"), dict)
        ]
        overall = _aggregate_summary(per_run, task_id, checkpoints)
        overall["ingestion"] = ingested.report
        overall["evaluation_errors"] = len(errors)
        overall["source_mode"] = "offline_existing_transcripts"
        overall["policy_overrides"] = [str(task.get("identity_policy"))] if task.get("identity_policy") else []
        overall["attribution"] = attribution.analyze(
            cp_dicts,
            flat,
            overall,
            task_id=task_id,
        )
        overall["review_queue_count"] = len(_write_review_queue(out_dir, flat))
        finalized_manifest, runtime = _persist_runtime_artifacts(
            out_dir,
            manifest,
            n_trajectories=len(by_run),
        )
        overall["manifest"] = finalized_manifest
        overall["runtime"] = runtime
        overall["run_id"] = finalized_manifest["run_id"]
        overall["started_at"] = finalized_manifest["started_at"]
        overall["backend"] = finalized_manifest["judge_models_config"]["backend"]
        overall["target_model"] = finalized_manifest["target_model_fingerprint"]["model"]
        overall["judge_model"] = ",".join(finalized_manifest["judge_models_config"]["models"])
        _write_json_atomic(by_run_path, by_run)
        _write_json_atomic(flat_path, flat)
        _write_json_atomic(errors_path, errors)
        _write_json_atomic(os.path.join(out_dir, "summary.json"), overall)
        return overall

    total = len(ingested.trajectories)
    for index, trajectory in enumerate(ingested.trajectories, 1):
        run_id = str(trajectory["run_id"])
        if run_id in completed:
            print(f"[evalcall] [{index}/{total}] 跳过已完成通话 {run_id}")
            continue
        print(f"[evalcall] [{index}/{total}] 评测已有对话 {run_id} …")
        try:
            with llm.telemetry_phase("judge"):
                judgments = judge.judge_trajectory(
                    checkpoints,
                    trajectory,
                    model=args.model,
                    n_votes=args.votes,
                )
            run_summary = judge.summarize(checkpoints, judgments)
        except Exception as exc:  # noqa: BLE001 —— 单通失败不丢整批
            errors = [row for row in errors if str(row.get("run_id")) != run_id]
            errors.append(
                {
                    "run_id": run_id,
                    "task_id": task_id,
                    "persona_id": trajectory.get("persona_id", "real_user"),
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
            )
            print(f"[evalcall] 警告：{run_id} 判定失败，已记录供单独重试：{exc}", file=sys.stderr)
            persist()
            continue

        by_run.append(
            {
                "run_id": run_id,
                "task_id": task_id,
                "persona_id": trajectory.get("persona_id", "real_user"),
                "judgments": judgments,
                "summary": run_summary,
            }
        )
        for item in judgments:
            meta = cp_meta.get(str(item.get("checkpoint_id")), {})
            flat.append(
                {
                    "run_id": run_id,
                    "task_id": task_id,
                    "persona_id": trajectory.get("persona_id", "real_user"),
                    "type": meta.get("type", "flow"),
                    "severity": meta.get("severity", "minor"),
                    "text": meta.get("text", ""),
                    "source_quote": meta.get("source_quote", ""),
                    "safety": bool(meta.get("safety")),
                    "policy_source": meta.get("policy_source", ""),
                    **item,
                }
            )
        errors = [row for row in errors if str(row.get("run_id")) != run_id]
        completed.add(run_id)
        persist()

    overall = persist()
    if not by_run:
        _die(f"离线评测未成功完成任何通话；详见 {errors_path}")
    if not args.no_report:
        from .report import build_report
        html_path = build_report(out_dir)
        print(f"[evalcall] 报告已生成：{html_path}")
    print(
        f"[evalcall] 离线评测完成。接受 {len(ingested.trajectories)} 通，"
        f"已判定 {len(by_run)} 通，失败 {len(errors)} 通，"
        f"上线决策【{overall['gate']}】，输出：{out_dir}"
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

    # P1-3 门禁聚合：任一轨迹打回 → 整体打回（外呼安全从严，一通坏电话就是事故）；
    # 无打回但存在"无法判定"轨迹 → 整体也只能是"无法判定"（不许把判定失败聚合成通过）。
    blocked_runs = [r for r in per_run if r.get("gate") == "打回"]
    undecided_runs = [r for r in per_run if r.get("gate") == "无法判定"]
    if blocked_runs:
        overall_gate = "打回"
    elif undecided_runs:
        overall_gate = "无法判定"
    else:
        overall_gate = "可上线"
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
    base_manifest = _read_json_if_exists(os.path.join(args.base, "manifest.json"))
    new_manifest = _read_json_if_exists(os.path.join(args.new, "manifest.json"))
    comparability = provenance.compare_manifests(base_manifest, new_manifest)
    if not comparability["comparable"] and not args.allow_incomparable:
        _die(
            "两次 run 不能证明使用同一把尺，拒绝把差异归因为模型变化："
            + "；".join(comparability["reasons"])
            + "\n  仅做历史产物探查时可显式加 --allow-incomparable，输出会标记不可归因"
        )
    base = _load_run_verdicts(args.base)
    new = _load_run_verdicts(args.new)
    rank = {"pass": 2, "na": 1, "fail": 0}  # 分越高越好
    improved, regressed, unchanged, appeared, indeterminate = [], [], [], [], []
    for cid, nv in new.items():
        bv = base.get(cid)
        # 规则轨/outcome 确定性高；LLM 轨变化需复测
        certain = nv["method"].startswith("rule") or nv["type"] == "outcome"
        tag = (
            "不可归因（尺子/元数据不可比）"
            if not comparability["comparable"]
            else ("确定" if certain else "需复测确认")
        )
        if bv is None:
            appeared.append({"checkpoint_id": cid, "text": nv["text"], "verdict": nv["verdict"]})
            continue
        if nv["verdict"] == bv["verdict"]:
            unchanged.append(cid)
            continue
        if "na" in {nv["verdict"], bv["verdict"]}:
            indeterminate.append(
                {"checkpoint_id": cid, "text": nv["text"], "from": bv["verdict"], "to": nv["verdict"],
                 "confidence_tag": "无法判定变化，不计入变好/变差"}
            )
            continue
        delta = rank.get(nv["verdict"], 1) - rank.get(bv["verdict"], 1)
        rec = {"checkpoint_id": cid, "text": nv["text"],
               "from": bv["verdict"], "to": nv["verdict"], "confidence_tag": tag}
        (improved if delta > 0 else regressed).append(rec)

    disappeared = [
        {"checkpoint_id": cid, "text": value["text"], "verdict": value["verdict"]}
        for cid, value in base.items() if cid not in new
    ]
    out = {
        "base": args.base, "new": args.new,
        "comparability": comparability,
        "improved": improved, "regressed": regressed,
        "indeterminate": indeterminate,
        "unchanged_count": len(unchanged), "appeared": appeared, "disappeared": disappeared,
    }
    print(f"[evalcall] 回归对比 {args.base} → {args.new}")
    print(f"  可比性：{comparability['status']}")
    print(f"  变好 {len(improved)} · 变差 {len(regressed)} · 无法判定变化 {len(indeterminate)} · 不变 {len(unchanged)} · 新增 {len(appeared)} · 消失 {len(disappeared)}")
    for r in regressed:
        print(f"  ⬇ 变差 [{r['confidence_tag']}] {r['text']}：{r['from']}→{r['to']}")
    for r in improved:
        print(f"  ⬆ 变好 [{r['confidence_tag']}] {r['text']}：{r['from']}→{r['to']}")
    if args.out:
        _write_json_atomic(args.out, out)
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


def cmd_review_export(args: argparse.Namespace) -> None:
    run_dir = args.run
    judgments = _read_json_if_exists(os.path.join(run_dir, "judgments.json"))
    if not isinstance(judgments, list):
        _die(f"缺少可用 judgments.json：{run_dir}")
    queue = _write_review_queue(run_dir, judgments)
    print(
        f"[evalcall] 已导出 {len(queue)} 条人工复核项："
        f"{os.path.join(run_dir, 'review_queue.json')} / review_queue.csv"
    )


def _load_review_decisions(path: str) -> list[dict[str, Any]]:
    if path.lower().endswith(".csv"):
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            rows = [dict(row) for row in csv.DictReader(fh)]
        return [row for row in rows if str(row.get("human_verdict") or "").strip()]
    data = _read_json_if_exists(path)
    if isinstance(data, dict):
        data = data.get("decisions")
    if not isinstance(data, list):
        _die(f"人工决定必须是 JSON 数组/含 decisions 的对象，或 review_queue.csv：{path}")
    return [dict(row) for row in data if isinstance(row, dict)]


def cmd_review_apply(args: argparse.Namespace) -> None:
    run_dir = args.run
    flat = _read_json_if_exists(os.path.join(run_dir, "judgments.json"))
    checkpoints = _read_json_if_exists(os.path.join(run_dir, "checklist.json"))
    if not isinstance(flat, list) or not isinstance(checkpoints, list):
        _die(f"run 目录缺 judgments.json/checklist.json：{run_dir}")
    decisions = _load_review_decisions(args.decisions)
    try:
        reviewed = review.apply_decisions(flat, decisions)
    except ValueError as exc:
        _die(f"人工复核回填失败：{exc}")
        return

    out_dir = args.out or os.path.join(run_dir, "human_review")
    _ensure_dir(out_dir)
    transcripts: list[dict[str, Any]] = []
    with open(os.path.join(run_dir, "transcripts.jsonl"), "r", encoding="utf-8") as fh:
        transcripts = [json.loads(line) for line in fh if line.strip()]
    _write_jsonl_atomic(os.path.join(out_dir, "transcripts.jsonl"), transcripts)
    _write_json_atomic(os.path.join(out_dir, "checklist.json"), checkpoints)
    _write_json_atomic(os.path.join(out_dir, "judgments.json"), reviewed)
    _write_json_atomic(os.path.join(out_dir, "review_decisions.json"), decisions)

    grouped: dict[str, list[dict[str, Any]]] = {}
    persona_by_run = {str(tx.get("run_id")): str(tx.get("persona_id") or "real_user") for tx in transcripts}
    for item in reviewed:
        grouped.setdefault(str(item.get("run_id")), []).append(item)
    by_run: list[dict[str, Any]] = []
    per_run: list[dict[str, Any]] = []
    task_id = str((reviewed[0].get("task_id") if reviewed else None) or "reviewed_run")
    for run_id, items in grouped.items():
        run_summary = judge.summarize(checkpoints, items)
        persona_id = persona_by_run.get(run_id, str(items[0].get("persona_id") or "real_user"))
        by_run.append(
            {"run_id": run_id, "task_id": task_id, "persona_id": persona_id,
             "judgments": items, "summary": run_summary}
        )
        per_run.append({"run_id": run_id, "persona_id": persona_id, **run_summary})
    _write_json_atomic(os.path.join(out_dir, "judgments_by_run.json"), by_run)

    summary = _aggregate_summary(per_run, task_id, checkpoints)
    summary["review_mode"] = "human_final"
    summary["review_source_run"] = os.path.abspath(run_dir)
    summary["human_decisions_applied"] = sum(1 for item in reviewed if item.get("review_applied"))
    summary["attribution"] = attribution.analyze(
        checkpoints,
        reviewed,
        summary,
        task_id=task_id,
    )
    summary["review_queue_count"] = len(_write_review_queue(out_dir, reviewed))
    source_summary = _read_json_if_exists(os.path.join(run_dir, "summary.json"))
    if isinstance(source_summary, dict):
        for key in ("manifest", "runtime", "run_id", "started_at", "backend", "target_model", "judge_model"):
            if key in source_summary:
                summary[key] = source_summary[key]
    _write_json_atomic(os.path.join(out_dir, "summary.json"), summary)
    for filename in ("manifest.json", "telemetry.json", "ingestion_report.json"):
        data = _read_json_if_exists(os.path.join(run_dir, filename))
        if data is not None:
            _write_json_atomic(os.path.join(out_dir, filename), data)

    from .report import build_report
    html_path = build_report(out_dir)
    print(
        f"[evalcall] 人工终审版已生成：{html_path}；"
        f"应用 {summary['human_decisions_applied']} 条决定，机器原判保留在 machine_verdict"
    )


def cmd_demo(args: argparse.Namespace) -> None:
    from .demo_server import serve
    serve(host=args.host, port=args.port)


def cmd_compiler_draft(args: argparse.Namespace) -> None:
    from .compiler_calibration import build_draft

    draft = build_draft(args.task, args.checklist)
    _write_json_atomic(args.out, draft)
    print(
        f"[evalcall] 编译器黄金集草稿已生成：{args.out}；"
        f"{draft['draft_stats']['items']} 项，状态 pending_human_review"
    )


def cmd_compiler_score(args: argparse.Namespace) -> None:
    from .compiler_calibration import score_prediction

    result = score_prediction(args.predicted, args.golden)
    if args.out:
        _write_json_atomic(args.out, result)
        print(f"[evalcall] 编译器评分已写出：{args.out}")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_replay_target(args: argparse.Namespace) -> None:
    from . import ingest
    from .replay import replay_trajectory

    task = _load_yaml(args.task)
    ingested = ingest.load_transcripts(
        args.transcripts,
        str(task.get("task_id") or task.get("id") or ""),
        redact=not args.no_redact,
    )
    limit = max(1, args.limit)
    outputs = [replay_trajectory(task, row) for row in ingested.trajectories[:limit]]
    _write_jsonl_atomic(args.out, outputs)
    print(f"[evalcall] counterfactual replay 已生成：{args.out}；{len(outputs)} 通，用户轮固定")


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
    judgments_path = os.path.join(run_dir, "judgments.json")
    if not os.path.isfile(judgments_path):
        _die(
            "run 目录只能在完成判定后生成报告；当前缺 judgments.json。\n"
            "  已有对话请先运行：python -m evalcall evaluate --task <task.yaml> "
            "--transcripts <transcripts.jsonl> --out <run_dir>"
        )
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
    p_run.add_argument("--votes", type=int, default=_default_votes(), help="每条检查点 LLM 裁判投票数（优先级 --votes > N_VOTES > 默认 3）")
    p_run.add_argument("--no-safety", action="store_true", help="不附加全局安全/合规红线（默认附加 P0 一票否决级红线）")
    p_run.add_argument("--no-mix", action="store_true", help="不按 persona 配比加权分配轨迹（默认按行业常识配比；本flag回到各 persona 均分 --n 条）")
    p_run.add_argument("--seed", type=int, default=None, help="随机种子基值（派生 base+persona序号*1000+轨迹序号）。注：仅模拟器随机性可复现；judge 非确定性+coverage 反馈环使整条 run 不保证完全复现")
    p_run.set_defaults(func=cmd_run)

    p_eval = sub.add_parser("evaluate", help="直接评测已有真实/脱敏对话（跳过模拟器）")
    p_eval.add_argument("--task", required=True, help="该批对话应遵循的任务 SOP/YAML")
    p_eval.add_argument("--transcripts", required=True, help="对话文件：.jsonl/.json/.csv/.txt/.md")
    p_eval.add_argument("--out", default=None, help="输出目录，默认 runs/<task_id>_offline")
    p_eval.add_argument("--checklist", default=None, help="复用已审核 checklist JSON；回归对比必须同尺")
    p_eval.add_argument("--model", default=None, help="覆盖编译/裁判模型")
    p_eval.add_argument("--votes", type=int, default=_default_votes(), help="每检查点裁判票数（优先级 --votes > N_VOTES > 默认 3）")
    p_eval.add_argument("--no-safety", action="store_true", help="不加载全局安全红线")
    p_eval.add_argument("--no-redact", action="store_true", help="不对手机号/身份证/邮箱等结构化 PII 做本地遮罩（默认遮罩）")
    p_eval.add_argument("--no-resume", action="store_true", help="不复用已完成的 run_id，从头重评")
    p_eval.add_argument("--no-report", action="store_true", help="只落盘 JSON 产物，不渲染 HTML")
    p_eval.set_defaults(func=cmd_evaluate)

    p_rep = sub.add_parser("report", help="从 run 目录生成 HTML 报告")
    p_rep.add_argument("--run", required=True, help="run 输出目录")
    p_rep.set_defaults(func=cmd_report)

    p_grow = sub.add_parser("grow", help="活清单增量：提议指令里遗漏的检查点（过溯源硬闸，待人工确认）")
    p_grow.add_argument("--task", required=True, help="任务 YAML 路径")
    p_grow.add_argument("--checklist", default=None, help="复用已有检查点清单 JSON（否则现编译）")
    p_grow.add_argument("--model", default=None, help="覆盖默认模型名")
    p_grow.add_argument("--out", default=None, help="候选输出路径，默认 runs/<task_id>/candidates_pending.json")
    p_grow.set_defaults(func=cmd_grow)

    p_review_export = sub.add_parser("review-export", help="导出 P0/低置信/分歧/NA 人工复核队列")
    p_review_export.add_argument("--run", required=True, help="评测 run 目录")
    p_review_export.set_defaults(func=cmd_review_export)

    p_review_apply = sub.add_parser("review-apply", help="回填人工决定，在新目录生成终审版报告（不覆写机器原判）")
    p_review_apply.add_argument("--run", required=True, help="原始机器评测 run 目录")
    p_review_apply.add_argument("--decisions", required=True, help="填好 human_verdict 的 review_queue.json/csv")
    p_review_apply.add_argument("--out", default=None, help="终审版输出目录，默认 <run>/human_review")
    p_review_apply.set_defaults(func=cmd_review_apply)

    p_demo = sub.add_parser("demo", help="启动六步式输入/输出流程演示页（含缓存与实时模式）")
    p_demo.add_argument("--host", default="127.0.0.1", help="监听地址，默认 127.0.0.1")
    p_demo.add_argument("--port", type=int, default=8765, help="端口，默认 8765")
    p_demo.set_defaults(func=cmd_demo)

    p_compiler_draft = sub.add_parser("compiler-draft", help="从已审 checklist 生成待业务终审的编译器黄金集草稿")
    p_compiler_draft.add_argument("--task", required=True, help="任务 YAML")
    p_compiler_draft.add_argument("--checklist", required=True, help="候选 checklist JSON")
    p_compiler_draft.add_argument("--out", required=True, help="草稿输出 JSON")
    p_compiler_draft.set_defaults(func=cmd_compiler_draft)

    p_compiler_score = sub.add_parser("compiler-score", help="用已 approved 的编译器黄金集评分")
    p_compiler_score.add_argument("--predicted", required=True, help="编译器预测 checklist JSON")
    p_compiler_score.add_argument("--golden", required=True, help="业务专家已 approved 的黄金集 JSON")
    p_compiler_score.add_argument("--out", default=None, help="可选评分输出 JSON")
    p_compiler_score.set_defaults(func=cmd_compiler_score)

    p_replay = sub.add_parser("replay-target", help="固定已有用户轮，重放被测模型用于跨家族同脚本对照")
    p_replay.add_argument("--task", required=True, help="任务 YAML")
    p_replay.add_argument("--transcripts", required=True, help="用户脚本来源对话")
    p_replay.add_argument("--out", required=True, help="输出 JSONL")
    p_replay.add_argument("--limit", type=int, default=1, help="最多重放 N 通，默认 1")
    p_replay.add_argument("--no-redact", action="store_true", help="关闭默认 PII 遮罩")
    p_replay.set_defaults(func=cmd_replay_target)

    p_diff = sub.add_parser("diff", help="跨版本回归对比两个 run（变好/变差/需复测）")
    p_diff.add_argument("--base", required=True, help="基线 run 目录")
    p_diff.add_argument("--new", required=True, help="新版 run 目录")
    p_diff.add_argument("--out", default=None, help="可选：把对比结果写出为 JSON")
    p_diff.add_argument("--allow-incomparable", action="store_true", help="仅供历史探查：允许比较缺 manifest/尺子不同的 run，所有变化标记不可归因")
    p_diff.set_defaults(func=cmd_diff)

    return parser


def main(argv: Optional[list[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
