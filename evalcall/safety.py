"""全局安全/合规红线检查点加载。

安全红线来自平台合规 policy（`data/policy/safety_redlines.yaml`），不是单条任务指令，
但仍是有据可溯的（policy_source 字段），不是无源悬空检查点（守 R-溯源）。
这些 P0 检查点附加到每次评测的清单，任一 fail 触发"打回"门禁。

设计要点：
- forbidden 类带 keywords 的红线复用 judge 现有 forbidden 规则轨——**单一规则源**，不另立一套。
- safety=True 标志驱动 P0 业务等级与门禁。
"""
from __future__ import annotations

import os
from typing import Any

import yaml

from .compiler import Checkpoint

_DEFAULT_POLICY = os.path.join("data", "policy", "safety_redlines.yaml")


def load_safety_checkpoints(policy_path: str | None = None) -> list[Checkpoint]:
    """读取安全红线 policy，返回 Checkpoint 列表（safety=True）。

    文件缺失/损坏不致命：返回空列表并由调用方决定是否告警。
    """
    path = policy_path or os.getenv("EVALCALL_SAFETY_POLICY") or _DEFAULT_POLICY
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception:  # noqa: BLE001 —— policy 坏了不该让整个评测崩
        return []
    rows = data.get("redlines") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    out: list[Checkpoint] = []
    for r in rows:
        if not isinstance(r, dict) or not r.get("id"):
            continue
        policy_src = str(r.get("policy_source") or "").strip()
        out.append(
            Checkpoint(
                id=str(r["id"]),
                type=str(r.get("type") or "constraint"),
                text=str(r.get("text") or ""),
                # source_quote 也写 policy 出处，保证任何消费 source_quote 的下游都能看到来源（不留空＝不悬空）
                source_quote=policy_src,
                severity=str(r.get("severity") or "critical"),
                safety=True,
                policy_source=policy_src,
                keywords=[str(k).strip() for k in (r.get("keywords") or []) if str(k).strip()],
            )
        )
    return out


def business_level(severity: str, safety: bool = False) -> str:
    """把 severity + safety 映射成业务化等级 P0/P1/P2（让门禁/复核真消费）。

    P0 = 安全红线 或 critical（一票否决/打回）
    P1 = major（建议复核）
    P2 = minor（记录）
    """
    if safety or severity == "critical":
        return "P0"
    if severity == "major":
        return "P1"
    return "P2"
