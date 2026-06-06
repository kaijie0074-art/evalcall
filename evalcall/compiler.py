"""指令编译器 Instruction Compiler。

输入：任务指令文本（外呼脚本，含流程节点 + 硬约束 + 禁止项 + 话术要求）
输出：结构化「检查点清单」Checklist，每条可溯源到指令原文。

检查点 schema（SPEC 第 4 节）：
    {id, type: flow|constraint|forbidden|style, text, source_quote, severity: critical|major|minor}

带确定性后处理：校验 source_quote 是否确为指令原文子串（含模糊匹配容差），
否则标记 needs_review=True，供人工复核，保证可解释性根基不被模型幻觉污染。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from . import llm

# 合法枚举值
_VALID_TYPES = {"flow", "constraint", "forbidden", "style"}
_VALID_SEVERITY = {"critical", "major", "minor"}


@dataclass
class Checkpoint:
    """单条检查点。与 SPEC schema 对齐，额外带 needs_review 标志。"""

    id: str
    type: str  # flow | constraint | forbidden | style
    text: str  # 检查点的自然语言描述（"应该做到 / 不应出现 什么"）
    source_quote: str  # 溯源：对应的指令原文片段
    severity: str  # critical | major | minor
    needs_review: bool = False  # 溯源校验未通过时为 True
    keywords: list[str] = field(default_factory=list)  # forbidden 类的关键词/正则，供规则轨直接判

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ----------------------------------------------------------------------------- #
# 文本归一化 + 模糊子串匹配（确定性后处理）
# ----------------------------------------------------------------------------- #
def _normalize(s: str) -> str:
    """归一化：去除空白、全角标点转半角近似、转小写，用于模糊子串比对。"""
    s = s.strip().lower()
    # 去掉所有空白字符
    s = re.sub(r"\s+", "", s)
    # 常见全角/半角标点统一删除（只比内容字符）
    s = re.sub(r"[，。、；：！？,.;:!?\"'“”‘’（）()\[\]【】…—\-_~`]", "", s)
    return s


def _is_substring_fuzzy(quote: str, source: str, min_ratio: float = 0.85) -> bool:
    """判断 quote 是否（模糊地）是 source 的子串。

    策略：
    1. 归一化后若 quote 直接是 source 子串 → True
    2. 否则用滑动窗口找 source 中与 quote 最相似的等长片段，
       字符级相似度 >= min_ratio 视为命中（容忍模型轻微改写/漏字）。
    """
    nq = _normalize(quote)
    ns = _normalize(source)
    if not nq:
        return False
    if nq in ns:
        return True
    if len(nq) > len(ns):
        return False
    # 滑动窗口，窗口长度等于 quote 长度
    best = 0.0
    qlen = len(nq)
    # 步长 1，quote 一般不长，开销可接受
    for i in range(0, len(ns) - qlen + 1):
        window = ns[i : i + qlen]
        same = sum(1 for a, b in zip(nq, window) if a == b)
        ratio = same / qlen
        if ratio > best:
            best = ratio
            if best >= min_ratio:
                return True
    return False


# ----------------------------------------------------------------------------- #
# Prompt 构造
# ----------------------------------------------------------------------------- #
_SCHEMA_HINT = """\
输出一个 JSON 对象，含字段 "checkpoints"，值为检查点数组。每个检查点对象字段：
- id: 字符串，唯一短标识，如 "flow_1" / "forbid_2"
- type: 枚举之一 flow（流程节点：必须完成的步骤）/ constraint（硬约束：必须满足的条件，如必须播报某信息）/ forbidden（禁止项：绝不能出现的话术或行为）/ style（话术要求：语气/措辞规范）
- text: 字符串，对该检查点的清晰描述（"应当……" 或 "不得……"）
- source_quote: 字符串，从指令原文中【逐字摘抄】支撑本检查点的片段，必须是原文中真实存在的连续文字
- severity: 枚举之一 critical（违反则任务失败）/ major（重要）/ minor（次要）
- keywords: 字符串数组，仅当 type=forbidden 时填写——列出能直接命中违规的关键词或简单正则（如 ["保证收益","稳赚"]）；其它 type 留空数组

要求：source_quote 必须是指令原文的逐字片段，不要改写、不要自造。
示例：
{"checkpoints":[{"id":"flow_1","type":"flow","text":"开场需自报家门并说明来意","source_quote":"您好，我是XX外卖客服","severity":"major","keywords":[]}]}"""

_SYS_PROMPT = """\
你是资深的对话质检规则设计专家。你的任务是把一段【外呼任务指令】编译成结构化的「检查点清单」，\
用于后续自动评测对话模型是否遵循了该指令。要求覆盖全面、可溯源、可判定：
- 把指令拆成原子检查点，每条只考一件事
- 流程步骤→flow，必须满足的条件/必须播报的信息→constraint，禁止出现的话术/行为→forbidden，语气措辞规范→style
- 关键的、违反即失败的项标 critical
- 每条都要从原文摘抄 source_quote 作为依据"""


def _task_to_text(task: dict) -> tuple[str, str]:
    """从任务 dict 中提取用于编译的指令文本。

    兼容多种 YAML 字段名：instruction / script / prompt / content / text。
    若有 name/title/goal 也拼进去补充上下文。
    返回 (带上下文的完整文本, 仅指令正文)——正文用于溯源校验。
    """
    parts: list[str] = []
    for key in ("name", "title", "goal", "scenario"):
        v = task.get(key)
        if isinstance(v, str) and v.strip():
            parts.append(f"【{key}】{v.strip()}")
    body = ""
    for key in ("instruction", "script", "prompt", "content", "text"):
        v = task.get(key)
        if isinstance(v, str) and v.strip():
            body = v.strip()
            break
    if not body:
        # 兜底：把整个 task 序列化进去（除已用字段）
        leftover = {k: v for k, v in task.items() if k not in ("name", "title", "goal", "scenario")}
        body = "\n".join(f"{k}: {v}" for k, v in leftover.items())
    parts.append("【指令正文】\n" + body)
    return "\n".join(parts), body


# ----------------------------------------------------------------------------- #
# 后处理：归一化 + 溯源校验
# ----------------------------------------------------------------------------- #
def _coerce_checkpoint(raw: dict, idx: int, source_text: str) -> Checkpoint:
    """把模型返回的单条原始 dict 规整成 Checkpoint，并做枚举/溯源校验。"""
    cp_id = str(raw.get("id") or f"cp_{idx}")
    cp_type = str(raw.get("type") or "constraint").strip().lower()
    if cp_type not in _VALID_TYPES:
        cp_type = "constraint"
    severity = str(raw.get("severity") or "major").strip().lower()
    if severity not in _VALID_SEVERITY:
        severity = "major"
    text = str(raw.get("text") or "").strip()
    source_quote = str(raw.get("source_quote") or "").strip()

    kw_raw = raw.get("keywords") or []
    keywords = [str(k).strip() for k in kw_raw if str(k).strip()] if isinstance(kw_raw, list) else []

    # 确定性溯源校验：source_quote 必须能在原文中（模糊）找到
    needs_review = False
    if not source_quote:
        needs_review = True
    elif not _is_substring_fuzzy(source_quote, source_text):
        needs_review = True

    return Checkpoint(
        id=cp_id,
        type=cp_type,
        text=text,
        source_quote=source_quote,
        severity=severity,
        needs_review=needs_review,
        keywords=keywords,
    )


def compile_task(task: dict, model: Optional[str] = None) -> list[Checkpoint]:
    """把任务指令编译成检查点清单。

    流程：取指令文本 → chat_json 让模型产出 checkpoints → 确定性后处理（枚举归一 + 溯源校验）。
    """
    full_text, source_body = _task_to_text(task)

    messages = [
        {"role": "system", "content": _SYS_PROMPT},
        {"role": "user", "content": f"请把下面的外呼任务指令编译成检查点清单：\n\n{full_text}"},
    ]
    data = llm.chat_json(messages, schema_hint=_SCHEMA_HINT, model=model)

    # 兼容模型直接返回数组或返回 {"checkpoints": [...]}
    if isinstance(data, dict):
        raw_list = data.get("checkpoints") or data.get("checklist") or []
    elif isinstance(data, list):
        raw_list = data
    else:
        raw_list = []

    checkpoints: list[Checkpoint] = []
    seen_ids: set[str] = set()
    for i, raw in enumerate(raw_list, start=1):
        if not isinstance(raw, dict):
            continue
        cp = _coerce_checkpoint(raw, i, source_body)
        # 保证 id 唯一
        if cp.id in seen_ids:
            cp.id = f"{cp.id}_{i}"
        seen_ids.add(cp.id)
        checkpoints.append(cp)
    return checkpoints


def checkpoints_to_dicts(checkpoints: list[Checkpoint]) -> list[dict[str, Any]]:
    """便捷：把 Checkpoint 列表转成可 JSON 序列化的 dict 列表。"""
    return [cp.to_dict() for cp in checkpoints]
