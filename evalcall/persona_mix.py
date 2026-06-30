"""persona 抽样配比（P4-2）：按可配置权重分配轨迹数，默认贴行业常识（平淡为主）。

为什么需要：真实履约外呼绝大多数是平淡通话，极端/辱骂只占少数。若各 persona 均分轨迹，
等于把罕见场景放大成 1/6，模型会过拟合罕见场景、真实表现被测歪（业务红队提示）。
红线（R-不伪量化）：默认权重是"行业常识"，**不是真实话务统计**——口径随配比一起如实声明。
"""
from __future__ import annotations

import os
from typing import Any, Optional

import yaml

_DEFAULT_MIX = os.path.join("data", "personas", "mix.yaml")


def load_mix(path: Optional[str] = None) -> dict[str, Any]:
    """读取配比文件，返回 {weights: {persona_id: w}, source: 口径声明}。缺失则空（退化为均分）。"""
    p = path or os.getenv("EVALCALL_PERSONA_MIX") or _DEFAULT_MIX
    if not os.path.exists(p):
        return {"weights": {}, "source": "未配置（均分）"}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception:  # noqa: BLE001
        return {"weights": {}, "source": "配比文件损坏（退化为均分）"}
    weights = data.get("weights") if isinstance(data.get("weights"), dict) else {}
    return {"weights": {str(k): float(v) for k, v in weights.items()},
            "source": str(data.get("source") or "未声明口径")}


def allocate(persona_ids: list[str], total: int, weights: dict[str, float]) -> dict[str, int]:
    """把 total 条轨迹按权重分配给各 persona（最大余数法），无权重则尽量均分。

    每个出现的 persona 至少分到 0；权重缺失的 persona 记权重 1（不被静默丢弃）。
    """
    if total <= 0 or not persona_ids:
        return {pid: 0 for pid in persona_ids}
    w = {pid: float(weights.get(pid, 1.0)) for pid in persona_ids}
    wsum = sum(w.values()) or 1.0
    raw = {pid: total * w[pid] / wsum for pid in persona_ids}
    base = {pid: int(raw[pid]) for pid in persona_ids}
    remainder = total - sum(base.values())
    # 余数按小数部分从大到小补，保证总数恰为 total
    frac_order = sorted(persona_ids, key=lambda pid: raw[pid] - base[pid], reverse=True)
    for i in range(remainder):
        base[frac_order[i % len(frac_order)]] += 1
    return base
