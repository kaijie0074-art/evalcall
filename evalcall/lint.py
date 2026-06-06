"""指令体检 Instruction Lint。

评测系统的差异化能力：不只评测「模型是否遵循指令」，还评测「指令本身是否可被遵循」。
很多外呼任务的低分根因不是模型差，而是【指令本身有缺陷】——自相矛盾、不可行约束、
歧义表述、流程缺失分支。本模块用 2-3 次 LLM 调用对一条任务指令做体检，产出可直接
给运营/产品照着改的 findings 报告。

核心函数：
    lint_instruction(task: dict, model=None) -> dict
返回：
    {
      task_id,
      findings: [ {id, dimension, severity, quote_a, quote_b, analysis, suggestion}, ... ],
      feasibility_score: 0-100,   # 指令整体可遵循度（确定性公式从 findings 扣分得出）
      summary,                    # 一句话总结
    }

四个体检维度（dimension）：
- conflict       自相矛盾：两条约束/要求物理上无法同时满足
- infeasible     不可行：单条约束在该场景下根本无法执行
- ambiguous      歧义：不同执行者会理解不一致，导致评测无客观标准
- missing_branch 缺失分支：流程图存在路径却未规定处理方式

设计原则：
1. 提示词只描述「如何找问题的方法论」，不硬编码任何具体结论（不许泄题）。
2. 把「冲突类」与「单条缺陷类」拆成两次调用，各自专注，降低漏检。
3. 所有 LLM 输出都做 schema 归一 + 溯源校验（quote 必须能在原文找到），
   失败兜底为空列表而非崩溃，保证一次坏输出不影响整体。
4. feasibility_score 由确定性公式计算，不交给 LLM 拍脑袋，保证可复现、可解释。
"""

from __future__ import annotations

import re
from typing import Any, Optional

from . import llm
from .compiler import _is_substring_fuzzy, _task_to_text  # 复用归一化/溯源工具

# 合法枚举
_VALID_DIMENSIONS = {"conflict", "infeasible", "ambiguous", "missing_branch"}
_VALID_SEVERITY = {"high", "medium", "low"}

# 各严重度扣分权重（用于 feasibility_score 确定性计算）
_SEVERITY_WEIGHT = {"high": 28, "medium": 12, "low": 5}
# conflict / infeasible 是硬伤，额外加权（同 severity 下比歧义/缺分支更致命）
_DIMENSION_FACTOR = {
    "conflict": 1.0,
    "infeasible": 1.0,
    "ambiguous": 0.6,
    "missing_branch": 0.6,
}


# ----------------------------------------------------------------------------- #
# Prompt：冲突 + 不可行（约束之间 / 单条约束）
# ----------------------------------------------------------------------------- #
_SYS_CONFLICT = """\
你是资深的对话脚本审核专家，专门给企业外呼任务指令做「可执行性体检」。
你的任务：找出指令中【两条要求互相冲突】或【单条要求根本无法执行】的硬缺陷。

方法论（务必逐条比对，不要凭印象）：
1. 先把指令里所有「约束/风格要求」和所有「必须传达的信息/必须完成的步骤」分别列在心里。
2. 逐一两两交叉比对：是否存在一条约束，使得另一条要求物理上无法被满足？
   典型冲突来源：
   - 字数/篇幅上限 与 必须讲清楚的信息量（要讲的内容塞不进字数限制里）
   - 语气/风格要求 与 必须执行的动作（如要求「随意简短」却要求逐步念多步操作引导）
   - 「每次回复只做一件事/暂停等待」与「一次性传达多项内容」
   评估冲突时，请估算「要传达的信息」大致需要多少字，与字数上限做量级对比，
   只要量级明显塞不下，就是 conflict。
3. 再逐条检查单条约束：在这个外呼场景下，执行者有没有能力真正做到？
   做不到的（依赖对方看不见的信息、依赖电话里无法完成的动作等）记为 infeasible。

严格要求：
- quote_a / quote_b 必须是指令原文里【逐字连续摘抄】的片段，不得改写、不得自造。
- analysis 要讲清「为什么冲突/为什么不可行」，让没看过指令的运营也能秒懂。
- suggestion 要给【具体可照抄的改法】（如「把字数上限放宽到 X 字」「拆成多轮，每轮只讲一个区别」），不要泛泛说「优化一下」。
- 只报真实存在的硬缺陷，不要为凑数编造；没有就返回空数组。"""

_SCHEMA_CONFLICT = """\
输出一个 JSON 对象，含字段 "findings"，值为问题数组。每个对象字段：
- dimension: 枚举 "conflict"（两条要求互相冲突）或 "infeasible"（单条要求无法执行）
- severity: 枚举 "high"（致命，任何执行者都做不到/必然违规）/ "medium"（多数情况会出问题）/ "low"（偶发）
- quote_a: 字符串，指令原文逐字摘抄的相关片段（冲突时为第一条要求）
- quote_b: 字符串，仅 conflict 时填写与 quote_a 冲突的第二条要求原文；infeasible 时填空字符串 ""
- analysis: 字符串，说明为什么冲突或不可行，给出信息量与字数的量级对比等具体理由
- suggestion: 字符串，具体可照抄的指令改写建议
示例（仅示意格式，勿照搬内容）：
{"findings":[{"dimension":"conflict","severity":"high","quote_a":"每次回复不超过10个字","quote_b":"完整介绍三种套餐的价格与权益","analysis":"三种套餐价格权益至少需数十字，10字上限根本装不下，二者无法同时满足。","suggestion":"将字数上限放宽到每轮40字，并把三种套餐拆成三轮分别介绍。"}]}"""


# ----------------------------------------------------------------------------- #
# Prompt：歧义 + 缺失分支
# ----------------------------------------------------------------------------- #
_SYS_AMBIGUOUS = """\
你是资深的对话脚本审核专家，专门给企业外呼任务指令做「可执行性体检」。
你的任务：找出指令中的【歧义表述】和【流程缺失分支】两类缺陷。

方法论：
A) 歧义（ambiguous）：找出会让不同执行者理解不一致、或让自动评测无客观判定标准的表述。
   典型来源：
   - 程度副词无量化（如「频繁地」「尽量」「适当」「简短」——多频繁/多简短没有标准）
   - 指代不清、条件不明（「必要时」「如果合适」——什么时候算必要）
   - 占位符/变量未给具体值，导致执行时无法填空
B) 缺失分支（missing_branch）：逐条检查对话流程里每个会向对方提问/做判断的节点，
   是否对所有可能的回应都规定了处理方式。常见被漏掉的分支：
   - 对方直接拒绝 / 不感兴趣 / 要求别再打
   - 对方挂断、沉默、答非所问
   - 对方提出知识库/FAQ 范围之外的问题
   - 关键判断只写了「是」的走向，没写「否」的走向（反之亦然）

严格要求：
- quote_a 必须是指令原文里【逐字连续摘抄】的片段（缺失分支时摘抄那个不完整的节点原文）。
- quote_b 一律填空字符串 ""（这两类都是单点问题）。
- analysis 说清「会产生什么不一致 / 漏了哪条路径会导致什么后果」。
- suggestion 给具体补法（如「把『频繁』改为『每 2-3 轮至少给商家一次发言机会』」「补一条：若对方明确拒绝，则礼貌致歉并结束通话」）。
- 只报真实缺陷，没有就返回空数组；不要把已经写清楚的分支误报为缺失。"""

_SCHEMA_AMBIGUOUS = """\
输出一个 JSON 对象，含字段 "findings"，值为问题数组。每个对象字段：
- dimension: 枚举 "ambiguous"（歧义）或 "missing_branch"（流程缺失分支）
- severity: 枚举 "high" / "medium" / "low"
- quote_a: 字符串，指令原文逐字摘抄的相关片段
- quote_b: 字符串，固定填 ""
- analysis: 字符串，说明歧义会导致的理解分歧，或缺失分支会漏掉的对话路径及后果
- suggestion: 字符串，具体可照抄的补充/改写建议
示例（仅示意格式，勿照搬内容）：
{"findings":[{"dimension":"ambiguous","severity":"medium","quote_a":"尽量多介绍优惠","quote_b":"","analysis":"『尽量多』没有量化，不同执行者介绍的优惠条数会不一致，评测也无客观标准。","suggestion":"改为『至少介绍2项、至多3项核心优惠』。"}]}"""


# ----------------------------------------------------------------------------- #
# 后处理：单条 finding 归一 + 溯源校验
# ----------------------------------------------------------------------------- #
def _coerce_finding(raw: dict, idx: int, source_text: str) -> Optional[dict]:
    """把模型返回的单条 finding 规整：枚举归一 + quote 溯源校验。

    quote_a 必须能在原文（模糊）找到，否则视为幻觉，丢弃该条（宁缺毋滥，
    保住「每条发现都可溯源」的可信度根基）。quote_b 若给了也校验，
    校验不过则清空但不丢整条（冲突的另一半可能被模型轻微改写）。
    """
    if not isinstance(raw, dict):
        return None
    dimension = str(raw.get("dimension") or "").strip().lower()
    if dimension not in _VALID_DIMENSIONS:
        return None
    severity = str(raw.get("severity") or "medium").strip().lower()
    if severity not in _VALID_SEVERITY:
        severity = "medium"
    quote_a = str(raw.get("quote_a") or "").strip()
    quote_b = str(raw.get("quote_b") or "").strip()
    analysis = str(raw.get("analysis") or "").strip()
    suggestion = str(raw.get("suggestion") or "").strip()

    # 溯源校验：quote_a 是命门，找不到就丢弃
    if not quote_a or not _is_substring_fuzzy(quote_a, source_text):
        return None
    # quote_b 仅冲突维度有意义；校验不过则清空（不影响主结论）
    if quote_b and not _is_substring_fuzzy(quote_b, source_text):
        quote_b = ""
    # 非 conflict 维度强制 quote_b 为空，避免噪声
    if dimension != "conflict":
        quote_b = ""
    if not analysis:
        return None

    return {
        "id": f"{dimension}_{idx}",
        "dimension": dimension,
        "severity": severity,
        "quote_a": quote_a,
        "quote_b": quote_b,
        "analysis": analysis,
        "suggestion": suggestion,
    }


def _run_dimension(
    sys_prompt: str,
    schema_hint: str,
    full_text: str,
    source_body: str,
    model: Optional[str],
) -> list[dict]:
    """跑一次某组维度的体检调用，返回归一后的 findings（失败兜底空列表）。"""
    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": f"请对下面这条外呼任务指令做体检：\n\n{full_text}"},
    ]
    try:
        data = llm.chat_json(messages, schema_hint=schema_hint, model=model)
    except Exception as exc:  # noqa: BLE001 —— 一次维度失败不应让整体体检崩溃
        import sys as _sys

        print(f"[lint] 维度调用失败，跳过该组：{exc}", file=_sys.stderr)
        return []

    if isinstance(data, dict):
        raw_list = data.get("findings") or data.get("issues") or []
    elif isinstance(data, list):
        raw_list = data
    else:
        raw_list = []

    out: list[dict] = []
    for i, raw in enumerate(raw_list, start=1):
        f = _coerce_finding(raw, i, source_body)
        if f is not None:
            out.append(f)
    return out


def _dedupe(findings: list[dict]) -> list[dict]:
    """去重：同维度 + quote_a 归一化后相同视为重复，保留 severity 更高的一条。"""
    rank = {"high": 3, "medium": 2, "low": 1}
    best: dict[str, dict] = {}
    order: list[str] = []
    for f in findings:
        key = f["dimension"] + "|" + re.sub(r"\s+", "", f["quote_a"])[:40]
        if key not in best:
            best[key] = f
            order.append(key)
        elif rank.get(f["severity"], 0) > rank.get(best[key]["severity"], 0):
            best[key] = f
    return [best[k] for k in order]


def _feasibility_score(findings: list[dict]) -> int:
    """由 findings 确定性地算出可遵循度分数（0-100，越高越好）。

    从满分 100 起扣：每条按 severity 权重 × 维度系数扣分。
    conflict/infeasible 的 high 项最狠（28 分），可单条把分数压到很低，
    呼应「一条物理冲突就足以让任何模型全程违规、总分 0」的现实。
    """
    score = 100.0
    for f in findings:
        w = _SEVERITY_WEIGHT.get(f["severity"], 8)
        factor = _DIMENSION_FACTOR.get(f["dimension"], 0.6)
        score -= w * factor
    return max(0, min(100, round(score)))


def _build_summary(task_id: str, findings: list[dict], score: int) -> str:
    """生成一句话人话总结。"""
    if not findings:
        return f"指令「{task_id}」未发现明显可执行性缺陷，可遵循度 {score}/100。"
    counts: dict[str, int] = {}
    for f in findings:
        counts[f["dimension"]] = counts.get(f["dimension"], 0) + 1
    cn = {
        "conflict": "自相矛盾",
        "infeasible": "不可行约束",
        "ambiguous": "歧义",
        "missing_branch": "缺失分支",
    }
    parts = [f"{cn.get(k, k)}{v}条" for k, v in counts.items()]
    has_high = any(f["severity"] == "high" for f in findings)
    head = "存在致命缺陷，" if has_high else ""
    return (
        f"指令「{task_id}」{head}共发现 {len(findings)} 条问题（"
        + "、".join(parts)
        + f"），可遵循度 {score}/100。"
    )


# ----------------------------------------------------------------------------- #
# 对外主函数
# ----------------------------------------------------------------------------- #
def lint_instruction(task: dict, model: Optional[str] = None) -> dict:
    """对一条任务指令做体检，返回结构化报告。

    用 2 次 LLM 调用（冲突+不可行 / 歧义+缺失分支）分别专注，再确定性后处理：
    溯源校验 → 去重 → 算可遵循度分数 → 生成总结。
    """
    full_text, source_body = _task_to_text(task)
    task_id = str(task.get("task_id") or task.get("id") or task.get("name") or "unknown")

    findings: list[dict] = []
    # 第一组：冲突 + 不可行（最核心的差异化发现来自这里）
    findings += _run_dimension(
        _SYS_CONFLICT, _SCHEMA_CONFLICT, full_text, source_body, model
    )
    # 第二组：歧义 + 缺失分支
    findings += _run_dimension(
        _SYS_AMBIGUOUS, _SCHEMA_AMBIGUOUS, full_text, source_body, model
    )

    findings = _dedupe(findings)
    # 重新编号，保证 id 全局唯一稳定（dimension_序号）
    seen: dict[str, int] = {}
    for f in findings:
        seen[f["dimension"]] = seen.get(f["dimension"], 0) + 1
        f["id"] = f"{f['dimension']}_{seen[f['dimension']]}"

    # 排序：severity 高的、conflict/infeasible 维度的排前面（最重要先看）
    sev_rank = {"high": 3, "medium": 2, "low": 1}
    dim_rank = {"conflict": 2, "infeasible": 2, "ambiguous": 1, "missing_branch": 1}
    findings.sort(
        key=lambda f: (sev_rank.get(f["severity"], 0), dim_rank.get(f["dimension"], 0)),
        reverse=True,
    )

    score = _feasibility_score(findings)
    summary = _build_summary(task_id, findings, score)

    return {
        "task_id": task_id,
        "findings": findings,
        "feasibility_score": score,
        "summary": summary,
    }
