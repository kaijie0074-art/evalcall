"""活清单增量（P3-1）：从真实对话暴露的覆盖盲区出发，提议指令里**已有但 v0 清单漏掉**的检查点。

核心红线（建模 R-溯源 / R-反循环）：
- 候选检查点的 `source_quote` **必须能（模糊）溯源回任务指令原文**，否则丢弃。
- 这道硬闸防的是"用被测模型/对话输出反推评判标准"的循环论证——对话只用来提示"哪里没测到"，
  检查点的**权威来源永远是指令**，不是模型说了什么。
- 候选一律进"待人工确认"区（needs_confirm=True），**不自动并入正式清单**。

可解释性的根基（compiler 的 source_quote 溯源）在增量阶段不被稀释。
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

from . import compiler, llm
from .compiler import Checkpoint, _is_substring_fuzzy, _task_to_text

_GROW_SYS = (
    "你是评测检查点的覆盖性审计员。给你一段外呼任务【指令原文】和一份【已有检查点清单】，"
    "请找出指令里明确要求、但已有清单**遗漏**的检查点。\n"
    "铁律：每条新检查点的 source_quote 必须是【指令原文】里真实存在的连续片段，逐字摘抄，"
    "不得改写、不得自造、不得来自对话内容或你的推测。找不到能逐字摘抄的指令依据就不要提。\n"
    '只输出 JSON：{"candidates":[{"type":"flow|constraint|forbidden|style","text":"检查点描述",'
    '"source_quote":"指令原文逐字片段","severity":"critical|major|minor"}]}'
)


def filter_traceable(
    candidates: list[dict],
    instruction: str,
    existing_texts: set[str],
) -> tuple[list[dict], list[dict]]:
    """硬闸：只保留 source_quote 能溯源回指令、且非重复的候选。

    返回 (accepted, rejected)，rejected 带 reason，便于审计"为什么被拦"。
    """
    accepted: list[dict] = []
    rejected: list[dict] = []
    for c in candidates:
        sq = str(c.get("source_quote") or "").strip()
        text = str(c.get("text") or "").strip()
        if not sq:
            rejected.append({**c, "_reason": "无 source_quote（悬空检查点）"})
            continue
        if not _is_substring_fuzzy(sq, instruction):
            # 关键硬闸：source_quote 不在指令原文里 = 可能从对话/模型输出反推 = 循环论证，拒绝
            rejected.append({**c, "_reason": "source_quote 无法溯源回指令原文（防循环论证）"})
            continue
        if text in existing_texts:
            rejected.append({**c, "_reason": "与已有检查点重复"})
            continue
        accepted.append({**c, "needs_confirm": True})  # 进待人工确认区，不自动入正式清单
    return accepted, rejected


def mine_candidates(
    task: dict,
    existing_checkpoints: list[Any],
    model: Optional[str] = None,
) -> dict[str, Any]:
    """调 LLM 提议遗漏检查点，再过溯源硬闸。返回 {accepted, rejected, instruction}。"""
    source_text, _ = _task_to_text(task)
    existing_dicts = [c if isinstance(c, dict) else c.to_dict() for c in existing_checkpoints]
    existing_texts = {str(c.get("text", "")).strip() for c in existing_dicts}
    existing_lines = "\n".join(f"- {c.get('text','')}" for c in existing_dicts)
    user = f"【指令原文】\n{source_text}\n\n【已有检查点清单】\n{existing_lines}"
    messages = [{"role": "system", "content": _GROW_SYS}, {"role": "user", "content": user}]
    try:
        data = llm.chat_json(messages, model=model)
        raw = data.get("candidates") if isinstance(data, dict) else data
        candidates = raw if isinstance(raw, list) else []
    except Exception as exc:  # noqa: BLE001
        return {"accepted": [], "rejected": [], "instruction": source_text, "error": str(exc)}
    accepted, rejected = filter_traceable(candidates, source_text, existing_texts)
    return {"accepted": accepted, "rejected": rejected, "instruction": source_text}
