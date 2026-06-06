"""对话竞技场 Arena Runner —— EvalCall ③ 对话执行器。

职责：把「被测对话模型」与「用户模拟器」放进同一通电话里轮替对话，
记录完整轨迹，供后续 Judge 评测。

约定（见 SPEC 第 4 节）：
- 被测对话模型 = 把任务指令作为 system prompt 的 LLM，调用走
  evalcall.llm.target_chat(messages)（Agent A 提供，本模块只 import）；
- 外呼场景由 agent（被测模型）先开口；
- 轨迹 schema（JSONL，每行一条）：
    {run_id, task_id, persona_id, turns: [{role: agent|user, content, turn}], meta}
  meta 至少含 task_id / persona_id / seed / adversarial_targets。

LLM 调用统一走 evalcall.llm，本模块不实现具体后端。
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from evalcall.llm import target_chat
from evalcall.simulator import UserSimulator

# 外呼第一句：当被测模型生成开场白失败时的兜底，避免空轨迹。
_FALLBACK_OPENING = "您好，这里是美团客服，请问现在方便接听电话吗？"


def _extract_checkpoint_texts(checkpoints: Optional[list[dict[str, Any]]]) -> list[str]:
    """从检查点清单中抽出「约束 / 禁止项」的文本，作为对抗目标。

    只挑 type 为 constraint / forbidden 的检查点——这些才是值得诱导
    被测模型违反的硬规则；flow / style 类不作为对抗目标。
    """
    if not checkpoints:
        return []
    targets: list[str] = []
    for cp in checkpoints:
        if cp.get("type") in ("constraint", "forbidden"):
            text = cp.get("text")
            if text:
                targets.append(str(text))
    return targets


def _build_target_messages(
    instruction: str, turns: list[dict[str, Any]]
) -> list[dict[str, str]]:
    """构造给被测模型的 messages：任务指令作 system，对话历史按真实视角铺开。

    对被测模型而言：它自己（agent）说的话是 assistant，用户说的话是 user。
    """
    messages: list[dict[str, str]] = [{"role": "system", "content": instruction}]
    for t in turns:
        role = t.get("role")
        content = t.get("content", "")
        if role == "agent":
            messages.append({"role": "assistant", "content": content})
        elif role == "user":
            messages.append({"role": "user", "content": content})
    return messages


def run_dialogue(
    task: dict[str, Any],
    persona: dict[str, Any],
    checkpoints: Optional[list[dict[str, Any]]] = None,
    max_turns: int = 12,
    seed: Optional[int] = None,
    adversarial: bool = True,
    priority_targets: Optional[list[str]] = None,
) -> dict[str, Any]:
    """跑一通完整的外呼对话，返回一条轨迹（dict，符合轨迹 schema）。

    参数
    ----
    task: 任务配置，需含 task_id / instruction（被测模型的 system prompt）。
    persona: persona 配置。
    checkpoints: 指令编译出的检查点清单；其中的约束/禁止项会作为对抗目标
        注入用户模拟器（adversarial=True 时生效）。
    max_turns: 一通电话最多的「轮」数。这里 1 轮 = agent 说一次 + user 说
        一次。达到上限强制收口。
    seed: 随机种子，控制用户模拟器策略抽取的可复现性。
    adversarial: 是否开启对抗模式（默认开，更能暴露违规）。
    priority_targets: 覆盖率反馈环传入的「尚未触达检查点」文本列表——
        coverage-guided 调度：上一条轨迹判定后仍为 na 的检查点，
        本条轨迹优先制造能触发它们的场景（FLARE 式行为覆盖引导）。

    返回
    ----
    一条轨迹 dict，字段见模块 docstring。
    """
    task_id = str(task.get("task_id", "unknown_task"))
    persona_id = str(persona.get("persona_id", "anon"))
    instruction = str(task.get("instruction", ""))
    # 被测模型指令消融实验通道：设置 TARGET_INSTRUCTION_FILE 时，被测模型
    # 看到该文件内容（如剥离约束的降级指令），而检查点仍按原任务编译——
    # 用于「看得见约束 vs 看不见约束」的判别力对照实验。
    import os as _os
    _override = _os.getenv("TARGET_INSTRUCTION_FILE")
    if _override:
        with open(_override, encoding="utf-8") as _f:
            instruction = _f.read()

    adversarial_targets = (
        _extract_checkpoint_texts(checkpoints) if adversarial else []
    )
    # coverage-guided：未触达检查点置顶为最高优先攻击目标，并显式标注，
    # 引导模拟器主动制造能触发它们的场景（而非随缘对抗）
    if priority_targets:
        marked = [
            f"{t}（⚑覆盖率优先目标：此前所有轨迹均未触达，本通电话务必制造触发它的场景）"
            for t in priority_targets
        ]
        adversarial_targets = marked + [
            t for t in adversarial_targets if t not in priority_targets
        ]

    simulator = UserSimulator(
        persona=persona,
        task=task,
        adversarial_targets=adversarial_targets,
        seed=seed,
    )

    turns: list[dict[str, Any]] = []
    turn_idx = 0
    hung_up = False
    end_reason = "max_turns"  # 默认收口原因，被覆盖则说明提前结束

    # ---- 外呼：agent 先开口 ----
    try:
        opening = target_chat(_build_target_messages(instruction, turns)).strip()
    except Exception:
        opening = ""
    if not opening:
        opening = _FALLBACK_OPENING
    turns.append({"role": "agent", "content": opening, "turn": turn_idx})

    # ---- 轮替对话 ----
    for turn_idx in range(1, max_turns + 1):
        # 用户回应
        user_reply, hung_up = simulator.next_reply(turns)
        turns.append({"role": "user", "content": user_reply, "turn": turn_idx})
        if hung_up:
            end_reason = "user_hangup"
            break

        # 被测模型回应
        try:
            agent_reply = target_chat(
                _build_target_messages(instruction, turns)
            ).strip()
        except Exception:
            agent_reply = ""
        if not agent_reply:
            # 被测模型异常给空，记录占位但不中断，交给 judge 评判。
            agent_reply = "（系统无回复）"
        turns.append({"role": "agent", "content": agent_reply, "turn": turn_idx})
    else:
        # for 正常跑满未 break：达到 max_turns
        end_reason = "max_turns"

    meta: dict[str, Any] = {
        "task_id": task_id,
        "persona_id": persona_id,
        "seed": seed,
        "adversarial": adversarial,
        "adversarial_targets": adversarial_targets,
        "max_turns": max_turns,
        "end_reason": end_reason,
        "strategy_log": simulator.strategy_log,
        "n_turns": turn_idx,
    }

    return {
        "run_id": uuid.uuid4().hex[:12],
        "task_id": task_id,
        "persona_id": persona_id,
        "turns": turns,
        "meta": meta,
    }


def run_batch(
    task: dict[str, Any],
    personas: list[dict[str, Any]],
    checkpoints: Optional[list[dict[str, Any]]] = None,
    n_per_persona: int = 1,
    max_turns: int = 12,
    base_seed: Optional[int] = None,
    adversarial: bool = True,
) -> list[dict[str, Any]]:
    """对一个任务、多个 persona，每个 persona 跑 n_per_persona 条轨迹。

    串行执行，但每条轨迹用独立派生 seed，方便整体复现。预留并发 hook：
    后续可把内层调用替换成线程/进程池，签名与返回结构保持不变。

    返回轨迹 dict 列表（顺序：persona 外层、重复次数内层）。
    """
    trajectories: list[dict[str, Any]] = []
    for p_i, persona in enumerate(personas):
        for r_i in range(n_per_persona):
            # 派生种子：保证 (persona, 重复序号) 组合可复现且互不相同。
            seed = None if base_seed is None else base_seed + p_i * 1000 + r_i
            # —— 并发 hook：未来在此处提交到 executor，而非直接调用 ——
            traj = run_dialogue(
                task=task,
                persona=persona,
                checkpoints=checkpoints,
                max_turns=max_turns,
                seed=seed,
                adversarial=adversarial,
            )
            trajectories.append(traj)
    return trajectories
