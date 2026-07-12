"""真实/脱敏对话的输入适配、校验、规范化与本地脱敏。

主格式是 EvalCall 轨迹 JSONL；同时支持 JSON、CSV 和简单的说话人标记文本。
所有适配结果先进入同一个内部 schema，再交给 judge，防止坏数据静默退化。
"""

from __future__ import annotations

import csv
import json
import os
import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any


class IngestError(ValueError):
    """输入不能安全规范化时抛出；issues 可直接展示给用户。"""

    def __init__(self, issues: list[str]):
        self.issues = issues
        super().__init__("\n".join(issues))


@dataclass
class IngestResult:
    trajectories: list[dict[str, Any]]
    report: dict[str, Any]


_ROLE_MAP = {
    "agent": "agent",
    "assistant": "agent",
    "bot": "agent",
    "ai": "agent",
    "model": "agent",
    "客服": "agent",
    "坐席": "agent",
    "外呼": "agent",
    "招聘专员": "agent",
    "user": "user",
    "human": "user",
    "customer": "user",
    "caller": "user",
    "用户": "user",
    "客户": "user",
    "骑手": "user",
    "候选人": "user",
}

_TEXT_LINE = re.compile(
    r"^\s*(?:\[(?P<bracket>[^\]]+)\]|(?P<plain>[^:：]{1,20}))\s*[:：]\s*(?P<content>.+?)\s*$"
)

_PII_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("PHONE", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("ID_CARD", re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)")),
    ("EMAIL", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("BANK_CARD", re.compile(r"(?<!\d)(?:\d[ -]?){16,19}(?!\d)")),
    ("ORDER_ID", re.compile(r"(?i)(?:(?:订单|单号|order)[号#:\s：-]*)([A-Za-z0-9-]{8,})")),
    ("ADDRESS", re.compile(r"(?:(?:地址|家住|住址)[\s:：]*)([^\n，。；;]{4,40})")),
]


class _Redactor:
    def __init__(self) -> None:
        self._tokens: dict[tuple[str, str], str] = {}
        self._counts: dict[str, int] = {}

    def _token(self, kind: str, value: str) -> str:
        key = (kind, value)
        if key not in self._tokens:
            n = self._counts.get(kind, 0) + 1
            self._counts[kind] = n
            self._tokens[key] = f"<{kind}_{n}>"
        return self._tokens[key]

    def redact(self, text: str) -> str:
        out = text
        for kind, pattern in _PII_PATTERNS:
            if kind in {"ORDER_ID", "ADDRESS"}:
                def repl(match: re.Match[str], _kind: str = kind) -> str:
                    whole = match.group(0)
                    value = match.group(1)
                    return whole.replace(value, self._token(_kind, value))
            else:
                def repl(match: re.Match[str], _kind: str = kind) -> str:
                    return self._token(_kind, match.group(0))
            out = pattern.sub(repl, out)
        return out

    @property
    def counts(self) -> dict[str, int]:
        return dict(sorted(self._counts.items()))


def _read_jsonl(path: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    issues: list[str] = []
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                issues.append(f"{path}:{lineno} JSON 解析失败：{exc.msg}")
                continue
            if not isinstance(item, dict):
                issues.append(f"{path}:{lineno} 每行必须是 JSON 对象")
                continue
            rows.append(item)
    if issues:
        raise IngestError(issues)
    return rows


def _read_json(path: str) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict):
        data = data.get("transcripts") or data.get("trajectories") or data.get("runs")
    if not isinstance(data, list) or not all(isinstance(x, dict) for x in data):
        raise IngestError([f"{path} 顶层必须是轨迹数组，或包含 transcripts/trajectories/runs 数组"])
    return list(data)


def _read_csv(path: str) -> list[dict[str, Any]]:
    groups: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        required = {"role", "content"}
        if not reader.fieldnames or not required <= set(reader.fieldnames):
            raise IngestError([f"{path} CSV 至少需要 role,content 两列"])
        for lineno, row in enumerate(reader, 2):
            rid = (row.get("run_id") or "offline_0001").strip()
            tx = groups.setdefault(
                rid,
                {
                    "run_id": rid,
                    "task_id": (row.get("task_id") or "").strip(),
                    "persona_id": (row.get("persona_id") or "real_user").strip(),
                    "turns": [],
                    "meta": {"source": "offline_csv"},
                },
            )
            raw_turn = (row.get("turn") or "").strip()
            turn: Any = int(raw_turn) if raw_turn.isdigit() else None
            tx["turns"].append(
                {"role": (row.get("role") or "").strip(), "content": row.get("content") or "", "turn": turn,
                 "_source_line": lineno}
            )
    return list(groups.values())


def _read_text(path: str) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    issues: list[str] = []
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            match = _TEXT_LINE.match(line)
            if not match:
                issues.append(f"{path}:{lineno} 无法识别说话人，请使用 '客服: 内容' 或 '[user]: content'")
                continue
            turns.append(
                {
                    "role": (match.group("bracket") or match.group("plain") or "").strip(),
                    "content": match.group("content").strip(),
                    "turn": len(turns) + 1,
                    "_source_line": lineno,
                }
            )
    if issues:
        raise IngestError(issues)
    return [{"run_id": "offline_0001", "persona_id": "real_user", "turns": turns,
             "meta": {"source": "offline_text"}}]


def _read_source(path: str) -> tuple[list[dict[str, Any]], str]:
    suffix = os.path.splitext(path)[1].lower()
    if suffix == ".jsonl":
        return _read_jsonl(path), "jsonl"
    if suffix == ".json":
        return _read_json(path), "json"
    if suffix == ".csv":
        return _read_csv(path), "csv"
    if suffix in {".txt", ".md"}:
        return _read_text(path), "text"
    raise IngestError([f"不支持的对话格式：{suffix or '(无扩展名)'}；支持 .jsonl/.json/.csv/.txt/.md"])


def _canonical_role(raw: Any) -> str | None:
    role = str(raw or "").strip().lower()
    return _ROLE_MAP.get(role)


def load_transcripts(path: str, task_id: str, *, redact: bool = True) -> IngestResult:
    """读取并规范化外部对话。

    静默修复只限于可确定的缺省值（task/persona/turn），每次修复都记入 warnings。
    任务不匹配、重复 run_id、非法 role、空对话等返回错误，不进入 judge。
    """
    if not os.path.isfile(path):
        raise IngestError([f"找不到对话文件：{path}"])
    raw_rows, source_format = _read_source(path)
    if not raw_rows:
        raise IngestError([f"对话文件为空：{path}"])

    redactor = _Redactor()
    errors: list[str] = []
    warnings: list[str] = []
    trajectories: list[dict[str, Any]] = []
    seen_run_ids: set[str] = set()

    for index, raw in enumerate(raw_rows, 1):
        rid = str(raw.get("run_id") or "").strip()
        if not rid:
            rid = f"offline_{index:04d}"
            warnings.append(f"第 {index} 条轨迹缺 run_id，已生成 {rid}")
        if rid in seen_run_ids:
            errors.append(f"重复 run_id：{rid}")
            continue
        seen_run_ids.add(rid)

        raw_task_id = str(raw.get("task_id") or (raw.get("meta") or {}).get("task_id") or "").strip()
        if raw_task_id and raw_task_id != task_id:
            errors.append(f"{rid} task_id={raw_task_id} 与 --task 的 {task_id} 不一致")
            continue
        if not raw_task_id:
            warnings.append(f"{rid} 缺 task_id，已填入 {task_id}")

        persona_id = str(raw.get("persona_id") or (raw.get("meta") or {}).get("persona_id") or "real_user").strip()
        turns_raw = raw.get("turns")
        if not isinstance(turns_raw, list) or not turns_raw:
            errors.append(f"{rid} turns 必须是非空数组")
            continue

        normalized_turns: list[dict[str, Any]] = []
        original_turns: list[int | None] = []
        for turn_index, turn_raw in enumerate(turns_raw, 1):
            if not isinstance(turn_raw, dict):
                errors.append(f"{rid} 第 {turn_index} 轮必须是对象")
                continue
            role = _canonical_role(turn_raw.get("role"))
            if role is None:
                errors.append(f"{rid} 第 {turn_index} 轮 role={turn_raw.get('role')!r} 不可识别")
                continue
            content = str(turn_raw.get("content") or "").strip()
            if not content:
                errors.append(f"{rid} 第 {turn_index} 轮 content 为空")
                continue
            turn_value = turn_raw.get("turn")
            original_turns.append(turn_value if isinstance(turn_value, int) else None)
            if redact:
                content = redactor.redact(content)
            normalized_turns.append({"role": role, "content": content, "turn": turn_index})

        if len(normalized_turns) != len(turns_raw):
            continue
        expected = list(range(1, len(normalized_turns) + 1))
        if original_turns != expected:
            warnings.append(f"{rid} turn 缺失/非连续，已按原文顺序重编为 1..{len(normalized_turns)}")

        meta = dict(raw.get("meta") or {})
        meta.update(
            {
                "task_id": task_id,
                "persona_id": persona_id,
                "source": meta.get("source") or f"offline_{source_format}",
                "source_file": os.path.basename(path),
                "pii_redacted": redact,
            }
        )
        trajectories.append(
            {"run_id": rid, "task_id": task_id, "persona_id": persona_id, "turns": normalized_turns, "meta": meta}
        )

    if errors:
        raise IngestError(errors)
    return IngestResult(
        trajectories=trajectories,
        report={
            "source_file": os.path.abspath(path),
            "source_format": source_format,
            "task_id": task_id,
            "input_trajectories": len(raw_rows),
            "accepted_trajectories": len(trajectories),
            "warnings": warnings,
            "pii_redacted": redact,
            "redaction_counts": redactor.counts,
        },
    )
