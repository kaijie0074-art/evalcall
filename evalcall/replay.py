"""固定用户话术的 counterfactual replay，用于跨家族被测模型同脚本对照。"""

from __future__ import annotations

import uuid
from typing import Any

from .llm import target_chat


def _messages(instruction: str, turns: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows = [{"role": "system", "content": instruction}]
    for turn in turns:
        role = "assistant" if turn.get("role") == "agent" else "user"
        rows.append({"role": role, "content": str(turn.get("content") or "")})
    return rows


def replay_trajectory(task: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    """把 source 的用户轮固定，重新生成被测模型轮。

    这是受控的反事实脚本，不冒充自然互动：后续用户台词可能不完全贴合新模型回复，
    但两个模型面对的诱因完全一致，适合测指令遵循差异。
    """
    instruction = str(task.get("instruction") or "")
    task_id = str(task.get("task_id") or task.get("id") or "unknown_task")
    scripted_users = [turn for turn in source.get("turns") or [] if turn.get("role") == "user"]
    turns: list[dict[str, Any]] = []
    opening = target_chat(_messages(instruction, turns)).strip()
    turns.append({"role": "agent", "content": opening or "（系统无回复）", "turn": 0})
    for index, user_turn in enumerate(scripted_users, 1):
        turns.append({"role": "user", "content": str(user_turn.get("content") or ""), "turn": index})
        reply = target_chat(_messages(instruction, turns)).strip()
        turns.append({"role": "agent", "content": reply or "（系统无回复）", "turn": index})
    return {
        "run_id": uuid.uuid4().hex[:12],
        "task_id": task_id,
        "persona_id": str(source.get("persona_id") or "scripted_replay"),
        "turns": turns,
        "meta": {
            "counterfactual_replay": True,
            "source_run_id": source.get("run_id"),
            "scripted_user_turns": len(scripted_users),
            "limitation": "用户轮固定，可能与新模型上一轮不完全衔接；只用于同诱因指令遵循对照。",
        },
    }
