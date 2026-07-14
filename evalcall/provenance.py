"""Run 可复现元数据、模型计价与跨 run 可比性门禁。"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


_ROOT = Path(__file__).resolve().parent.parent
_GOLDEN = _ROOT / "data" / "calibration" / "golden_set.json"
_SAFETY = _ROOT / "data" / "policy" / "safety_redlines.yaml"
_PRICES = _ROOT / "data" / "policy" / "model_prices.yaml"

COMPARABILITY_FIELDS = (
    "instruction_hash",
    "checklist_hash",
    "safety_policy_hash",
    "golden_set_version",
    "judge_policy_hash",
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | os.PathLike[str]) -> str | None:
    p = Path(path)
    if not p.is_file():
        return None
    return sha256_bytes(p.read_bytes())


def sha256_json(data: Any) -> str:
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_bytes(raw)


def _git(args: list[str]) -> str:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=_ROOT, capture_output=True, text=True, timeout=10, check=False
        )
        return proc.stdout.strip() if proc.returncode == 0 else "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def build_manifest(
    *,
    task: dict[str, Any],
    task_path: str,
    checklist: list[dict[str, Any]],
    source_mode: str,
    n_votes: int,
    model: str | None,
    seed: int | None = None,
) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    judge_models = os.getenv("JUDGE_MODELS", "").strip()
    judge_model = model or os.getenv("EVALCALL_MODEL") or (
        "sonnet" if os.getenv("EVALCALL_BACKEND", "claude-cli") == "claude-cli" else "gpt-4o-mini"
    )
    target_backend = os.getenv("TARGET_BACKEND") or os.getenv("EVALCALL_BACKEND", "claude-cli")
    if source_mode.startswith("offline"):
        # 已有日志默认不声称知道被测模型；但对“固定用户轮、预生成模型输出”的
        # 回归夹具，调用方可以显式声明模型/策略版本。只有显式设置才写入，避免
        # 把普通人工/生产日志误标成某个模型结果。
        declared_offline_model = os.getenv("EVALCALL_OFFLINE_TARGET_MODEL", "").strip()
        if declared_offline_model:
            target_backend = os.getenv("EVALCALL_OFFLINE_TARGET_BACKEND", "replay").strip() or "replay"
            target_model = declared_offline_model
        else:
            target_backend = "existing-transcript"
            target_model = "not_applicable"
    else:
        target_model = os.getenv("TARGET_MODEL") or (
            "sonnet" if target_backend == "claude-cli" else "unknown"
        )
    task_id = str(task.get("id") or task.get("task_id") or "unknown_task")
    return {
        "schema_version": 1,
        "run_id": f"{task_id}__{source_mode}__{started.strftime('%Y%m%dT%H%M%SZ')}",
        "task_id": task_id,
        "source_mode": source_mode,
        "started_at": started.isoformat(),
        "task_file": os.path.abspath(task_path),
        "task_file_hash": sha256_file(task_path),
        "instruction_hash": sha256_bytes(str(task.get("instruction") or "").encode("utf-8")),
        "checklist_hash": sha256_json(checklist),
        "safety_policy_hash": sha256_file(os.getenv("EVALCALL_SAFETY_POLICY") or _SAFETY),
        "golden_set_version": sha256_file(_GOLDEN),
        "judge_policy_hash": sha256_file(_ROOT / "evalcall" / "judge.py"),
        "identity_policy": str(task.get("identity_policy") or "default"),
        "target_model_fingerprint": {"backend": target_backend, "model": target_model},
        "judge_models_config": {
            "backend": os.getenv("EVALCALL_BACKEND", "claude-cli"),
            "models": [x.strip() for x in judge_models.split(",") if x.strip()] or [judge_model],
            "n_votes": int(n_votes),
        },
        "seed": seed,
        "code_commit": _git(["rev-parse", "HEAD"]),
        "code_branch": _git(["branch", "--show-current"]),
        "code_dirty": bool(_git(["status", "--porcelain"]) not in {"", "unknown"}),
    }


def load_prices(path: str | None = None) -> dict[str, Any]:
    price_path = Path(path) if path else _PRICES
    if not price_path.is_file():
        return {"currency": "CNY", "models": {}, "source_file": str(price_path)}
    data = yaml.safe_load(price_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("currency", "CNY")
    data.setdefault("models", {})
    data["source_file"] = str(price_path)
    data["source_file_hash"] = sha256_file(price_path)
    return data


def price_telemetry(telemetry: dict[str, Any], prices: dict[str, Any] | None = None) -> dict[str, Any]:
    prices = prices or load_prices()
    model_prices = prices.get("models") if isinstance(prices.get("models"), dict) else {}
    total_cost = 0.0
    priced_calls = 0
    unknown_models: set[str] = set()
    token_sources: set[str] = set()
    by_model: dict[str, dict[str, Any]] = {}
    for event in telemetry.get("events") or []:
        model = str(event.get("model") or "unknown")
        token_sources.add(str(event.get("token_source") or "unknown"))
        row = by_model.setdefault(
            model,
            {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost": None},
        )
        row["calls"] += 1
        row["input_tokens"] += int(event.get("input_tokens") or 0)
        row["output_tokens"] += int(event.get("output_tokens") or 0)
        cfg = model_prices.get(model)
        if not isinstance(cfg, dict):
            unknown_models.add(model)
            continue
        input_rate = float(cfg.get("input_per_million") or 0.0)
        output_rate = float(cfg.get("output_per_million") or 0.0)
        cost = row["input_tokens"] / 1_000_000 * input_rate + row["output_tokens"] / 1_000_000 * output_rate
        row["cost"] = round(cost, 6)

    for model, row in by_model.items():
        if row["cost"] is not None:
            total_cost += float(row["cost"])
            priced_calls += int(row["calls"])
    totals = telemetry.get("totals") or {}
    total_calls = int(totals.get("calls") or 0)
    return {
        "status": "priced" if total_calls == priced_calls else ("unpriced" if priced_calls == 0 else "partially_priced"),
        "currency": str(prices.get("currency") or "CNY"),
        "total_cost": round(total_cost, 6) if priced_calls else None,
        "priced_calls": priced_calls,
        "total_calls": total_calls,
        "unknown_models": sorted(unknown_models),
        "token_measurement": "actual" if token_sources == {"actual"} else "estimated_or_mixed",
        "price_source_file": prices.get("source_file"),
        "price_source_hash": prices.get("source_file_hash"),
        "by_model": by_model,
    }


def finalize_manifest(
    manifest: dict[str, Any],
    telemetry: dict[str, Any],
    *,
    n_trajectories: int,
    price_path: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    completed = datetime.now(timezone.utc)
    finalized = dict(manifest)
    finalized["completed_at"] = completed.isoformat()
    try:
        started = datetime.fromisoformat(str(manifest["started_at"]))
        wall_seconds = round((completed - started).total_seconds(), 3)
    except Exception:  # noqa: BLE001
        wall_seconds = None
    cost = price_telemetry(telemetry, load_prices(price_path))
    totals = dict(telemetry.get("totals") or {})
    runtime = {
        **totals,
        "wall_seconds": wall_seconds,
        "n_trajectories": n_trajectories,
        "calls_per_trajectory": round(int(totals.get("calls") or 0) / n_trajectories, 2) if n_trajectories else None,
        "tokens_per_trajectory": round(int(totals.get("total_tokens") or 0) / n_trajectories, 1) if n_trajectories else None,
        "cost": cost,
    }
    finalized["runtime"] = runtime
    return finalized, runtime


def compare_manifests(base: dict[str, Any] | None, new: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(base, dict) or not isinstance(new, dict):
        return {
            "status": "unknown",
            "comparable": False,
            "reasons": ["基线或新版缺 manifest.json，无法证明两次使用同一把尺"],
        }
    mismatches = []
    for field in COMPARABILITY_FIELDS:
        if not base.get(field) or not new.get(field):
            mismatches.append(f"{field} 缺失")
        elif base.get(field) != new.get(field):
            mismatches.append(f"{field} 不一致")
    return {
        "status": "comparable" if not mismatches else "incomparable",
        "comparable": not mismatches,
        "reasons": mismatches,
        "checked_fields": list(COMPARABILITY_FIELDS),
    }
