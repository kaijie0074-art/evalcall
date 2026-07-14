"""EvalCall 六步产品演示服务器。

线上 GitHub Pages 使用由真实历史产物生成的 ``demo-cache.json``；本地服务则把
六个页面步骤分别接到真实输入校验、检查尺准备、整批评测、统计决策、根因归因
和修复计划落盘。这样缓存演示不会伪装成实时执行，本地实时模式也不再只有第 3
步真正工作。
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections import Counter, defaultdict
from datetime import datetime
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import yaml

from . import attribution, cli, compiler, ingest, llm


ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site-deploy"
WEB_RUNS = ROOT / "runs" / "web_live"
MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_REQUEST_BYTES = MAX_UPLOAD_BYTES * 2 + 200_000

PRESETS: dict[str, dict[str, str]] = {
    "official01": {
        "label": "骑手合同生效通知",
        "tag": "官方脱敏任务",
        "demo_role": "SOP 问题案例",
        "test_mode": "simulation",
        "model_version": "baseline-sim-20260702",
        "task": "样例选择包/01_骑手合同生效通知_SOP.yaml",
        "transcripts": "样例选择包/01_骑手合同生效通知_对话_12通.jsonl",
        "verified_checklist": "runs/official01_gated_20260702/checklist.json",
        "live_verified_run": "runs/demo_live_official01_codex_20260713",
        "cache_run": "runs/demo_live_official01_codex_20260713",
        "cache_report": "report-official-gated.html",
    },
    "t02": {
        "label": "配送时间改约",
        "tag": "主演示 · 外呼模型问题",
        "demo_role": "模型评测主演示",
        "test_mode": "simulation",
        "model_version": "delivery-baseline-v1",
        "task": "data/tasks/t02_delivery_reschedule.yaml",
        "transcripts": "样例选择包/02_配送时间改约_对话_10通.jsonl",
        "verified_checklist": "runs/demo_live_t02_codex_20260713/checklist.json",
        "live_verified_run": "runs/t02_delivery_baseline_v1_fixedusers_20260714",
        "cache_run": "runs/t02_delivery_baseline_v1_fixedusers_20260714",
        "regression_evidence": "runs/t02_delivery_fixedusers_regression_20260714/comparison.json",
        "regression_run": "runs/t02_delivery_guarded_v2_fixedusers_20260714",
        "regression_report": "report-t02-v2.html",
        "cache_report": "report-t02-gated.html",
    },
    "real_recruit": {
        "label": "骑手招聘外呼",
        "tag": "真实生产 SOP",
        "demo_role": "真实日志辅助案例",
        "test_mode": "logs",
        "model_version": "recruit-log-snapshot-v1",
        "task": "样例选择包/03_骑手招聘外呼_SOP.yaml",
        "transcripts": "样例选择包/03_骑手招聘外呼_对话_10通.jsonl",
        "verified_checklist": "runs/m5_real_recruit_gpt56sol_xhigh_v2_20260712/checklist.json",
        "live_verified_run": "runs/demo_live_real_recruit_codex_20260713",
        "cache_run": "runs/demo_live_real_recruit_codex_20260713",
        "cache_report": "report-real-recruit-gpt56sol.html",
    },
    "official02": {
        "label": "低延迟直播升级通知",
        "tag": "对照 · SOP 问题",
        "demo_role": "SOP 问题对照",
        "test_mode": "simulation",
        "model_version": "live-baseline-v1",
        "task": "样例选择包/04_低延迟直播升级通知_SOP.yaml",
        "transcripts": "样例选择包/04_低延迟直播升级通知_对话_10通.jsonl",
        "verified_checklist": "runs/official02_gated_20260702/checklist.json",
        "live_verified_run": "runs/demo_live_official02_codex_20260713",
        "cache_run": "runs/demo_live_official02_codex_20260713",
        "cache_report": "report-official2-gated.html",
    },
}

_jobs: dict[str, dict[str, Any]] = {}
_sessions: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()
_backend_status: dict[str, Any] = {
    "checked": False,
    "checking": False,
    "available": False,
    "checked_at": None,
    "error": "尚未执行模型后端探测",
}

_VERIFIED_EVALUATION_FILES = (
    "transcripts.jsonl",
    "summary.json",
    "judgments.json",
    "judgments_by_run.json",
    "checklist.json",
    "report.html",
    "evaluation_errors.json",
    "manifest.json",
    "review_queue.csv",
    "review_queue.json",
    "telemetry.json",
    "ingestion_report.json",
)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _configure_demo_backend() -> None:
    """为本地工作台选择当前机器上可直接工作的默认推理后端。"""
    if os.getenv("EVALCALL_BACKEND"):
        return
    if shutil.which("codex"):
        os.environ["EVALCALL_BACKEND"] = "codex-cli"
        os.environ.setdefault("EVALCALL_MODEL", "gpt-5.6-sol")
        os.environ.setdefault("EVALCALL_REASONING_EFFORT", "low")
    else:
        os.environ["EVALCALL_BACKEND"] = "claude-cli"


def _probe_backend(*, force: bool = False) -> dict[str, Any]:
    """真实调用一次评分模型；健康页不能把“进程存在”误报成“模型可用”。"""
    global _backend_status
    with _lock:
        if _backend_status.get("checking"):
            return dict(_backend_status)
        if _backend_status.get("checked") and not force:
            return dict(_backend_status)
        _backend_status = {
            "checked": False,
            "checking": True,
            "available": False,
            "checked_at": None,
            "backend": os.getenv("EVALCALL_BACKEND", "claude-cli"),
            "model": os.getenv("EVALCALL_MODEL", "default"),
            "response": None,
            "error": None,
        }
    backend = os.getenv("EVALCALL_BACKEND", "claude-cli")
    model = os.getenv("EVALCALL_MODEL", "default")
    try:
        answer = llm.chat(
            [
                {"role": "system", "content": "这是健康检查。"},
                {"role": "user", "content": "只回复 OK"},
            ],
            model=None,
        ).strip()
        if not answer:
            raise RuntimeError("模型返回空响应")
        result = {
            "checked": True,
            "checking": False,
            "available": True,
            "checked_at": _now(),
            "backend": backend,
            "model": model,
            "response": answer[:40],
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        result = {
            "checked": True,
            "checking": False,
            "available": False,
            "checked_at": _now(),
            "backend": backend,
            "model": model,
            "response": None,
            "error": str(exc)[:500],
        }
    with _lock:
        _backend_status = result
        return dict(_backend_status)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    tmp.replace(path)


def _load_task(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("SOP / SYSTEM PROMPT 的 YAML 顶层必须是对象")
    task_id = str(data.get("task_id") or data.get("id") or path.stem).strip()
    if not task_id:
        raise ValueError("SOP 缺少 task_id / id")
    data["task_id"] = task_id
    data.setdefault("id", task_id)
    return data


def _digest_json(data: Any) -> str:
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _trajectory_profile(trajectories: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(str(row.get("persona_id") or "unknown") for row in trajectories)
    synthetic = sum(1 for row in trajectories if (row.get("meta") or {}).get("synthetic_demo"))
    simulated = sum(
        1
        for row in trajectories
        if str((row.get("meta") or {}).get("source") or "").startswith("sim")
        or bool((row.get("meta") or {}).get("adversarial"))
        or str(row.get("persona_id") or "").startswith(("p0", "synthetic_"))
    )
    return {
        "persona_count": len(counts),
        "personas": [{"id": key, "calls": value} for key, value in sorted(counts.items())],
        "synthetic_branch_calls": synthetic,
        "simulator_generated_calls": simulated,
    }


def _task_digest(task: dict[str, Any]) -> str:
    """任务内容签名；排除 ``id``/``task_id`` 的兼容性重复字段。"""
    return _digest_json({key: value for key, value in task.items() if key != "id"})


def _trajectory_digest(trajectories: list[dict[str, Any]]) -> str:
    """对话语义签名，忽略导入器重编号与 source_file 等运行时元数据。"""
    normalized = [
        {
            "run_id": row.get("run_id"),
            "task_id": row.get("task_id"),
            "persona_id": row.get("persona_id"),
            "turns": [
                {"role": turn.get("role"), "content": turn.get("content")}
                for turn in row.get("turns") or []
            ],
        }
        for row in trajectories
    ]
    return _digest_json(normalized)


def _match_verified_preset(task: dict[str, Any], transcripts_path: Path) -> str | None:
    """识别通过文件选择上传的内置样例；必须同时匹配 SOP 与完整对话内容。"""
    task_signature = _task_digest(task)
    uploaded = ingest.load_transcripts(
        str(transcripts_path),
        str(task["task_id"]),
        redact=False,
    )
    transcript_signature = _trajectory_digest(uploaded.trajectories)
    for preset_id, config in PRESETS.items():
        candidate_task = _load_task(ROOT / config["task"])
        if _task_digest(candidate_task) != task_signature:
            continue
        loaded = ingest.load_transcripts(
            str(ROOT / config["transcripts"]),
            str(candidate_task["task_id"]),
            redact=False,
        )
        if _trajectory_digest(loaded.trajectories) == transcript_signature:
            return preset_id
    return None


def _verified_run_matches_instruction(task: dict[str, Any], run_dir: Path | None) -> bool:
    if run_dir is None or not (run_dir / "manifest.json").is_file():
        return False
    manifest = _read_json(run_dir / "manifest.json")
    current_hash = hashlib.sha256(str(task.get("instruction") or "").encode("utf-8")).hexdigest()
    return manifest.get("instruction_hash") == current_hash


def _session(session_id: str) -> dict[str, Any]:
    with _lock:
        row = dict(_sessions.get(session_id) or {})
    if not row:
        raise ValueError("找不到本次评测，请从第 1 步重新开始")
    return row


def _update_session(session_id: str, **values: Any) -> None:
    with _lock:
        _sessions[session_id].update(values)


def _job_update(job_id: str, **values: Any) -> None:
    with _lock:
        _jobs[job_id].update(values)


def _artifact_url(session_id: str, filename: str) -> str:
    return f"/session-artifact/{session_id}/{filename}"


def _package_preview(
    task: dict[str, Any],
    report: dict[str, Any],
    trajectories: list[dict[str, Any]],
    *,
    scope: str,
    session_id: str | None = None,
) -> dict[str, Any]:
    turns = sum(len(row.get("turns") or []) for row in trajectories)
    instruction = str(task.get("instruction") or task.get("prompt") or "")
    payload: dict[str, Any] = {
        "task_id": str(task.get("task_id") or task.get("id")),
        "task_name": str(task.get("name") or task.get("title") or task.get("task_id")),
        "scenario": str(task.get("scenario") or ""),
        "instruction_chars": len(instruction),
        "conversations": len(trajectories),
        "turns": turns,
        "input_format": str(report.get("source_format") or "jsonl").upper(),
        "output_format": "标准 JSONL",
        "warnings": list(report.get("warnings") or []),
        "pii_redacted": bool(report.get("pii_redacted", True)),
        "redaction_counts": dict(report.get("redaction_counts") or {}),
        "scope": scope,
        "instruction_excerpt": instruction[:900],
    }
    if session_id:
        payload["session_id"] = session_id
        payload["artifacts"] = {
            "task_package": _artifact_url(session_id, "task-package.json"),
            "ingestion_report": _artifact_url(session_id, "ingestion-report.json"),
        }
        if trajectories:
            payload["artifacts"]["normalized_transcripts"] = _artifact_url(
                session_id, "normalized-transcripts.jsonl"
            )
    return payload


def _checklist_preview(
    checkpoints: list[dict[str, Any]],
    *,
    generation_method: str,
    approved: bool,
    session_id: str | None = None,
) -> dict[str, Any]:
    def is_l0(row: dict[str, Any]) -> bool:
        policy_source = str(row.get("policy_source") or "")
        return bool(row.get("safety")) or policy_source.startswith("safety_redlines") or "data/policy/safety_redlines" in policy_source

    by_type = Counter(str(row.get("type") or "other") for row in checkpoints)
    by_severity = Counter(str(row.get("severity") or "minor") for row in checkpoints)
    l0 = sum(1 for row in checkpoints if is_l0(row))
    samples = sorted(
        checkpoints,
        key=lambda row: ({"critical": 0, "major": 1, "minor": 2}.get(str(row.get("severity")), 3), str(row.get("id"))),
    )[:8]
    payload: dict[str, Any] = {
        "checkpoints": len(checkpoints),
        "l0_common_rules": l0,
        "l1_sop_rules": len(checkpoints) - l0,
        "by_type": dict(sorted(by_type.items())),
        "by_severity": dict(sorted(by_severity.items())),
        "source_review_count": sum(1 for row in checkpoints if row.get("needs_review")),
        "generation_method": generation_method,
        "approved": approved,
        "samples": [
            {
                "id": row.get("id"),
                "type": row.get("type"),
                "severity": row.get("severity"),
                "text": row.get("text"),
                "source_quote": row.get("source_quote") or row.get("policy_source") or "无来源引文",
                "layer": "L0" if is_l0(row) else "L1",
            }
            for row in samples
        ],
    }
    if session_id:
        payload["artifacts"] = {"checklist": _artifact_url(session_id, "checklist.json")}
    return payload


def _judgment_samples(output_dir: Path, limit: int = 6) -> list[dict[str, Any]]:
    path = output_dir / "judgments.json"
    if not path.is_file():
        return []
    rows = _read_json(path)
    if not isinstance(rows, list):
        return []
    ranked = sorted(
        rows,
        key=lambda row: (
            0 if row.get("verdict") == "fail" else 1,
            {"critical": 0, "major": 1, "minor": 2}.get(str(row.get("severity")), 3),
        ),
    )
    samples: list[dict[str, Any]] = []
    for row in ranked[:limit]:
        evidence = row.get("evidence") or []
        first = evidence[0] if isinstance(evidence, list) and evidence else {}
        samples.append(
            {
                "run_id": row.get("run_id") or "unknown",
                "checkpoint_id": row.get("checkpoint_id") or "unknown",
                "checkpoint_text": row.get("text") or row.get("checkpoint_text") or row.get("checkpoint_id"),
                "severity": row.get("severity") or "unknown",
                "verdict": row.get("verdict") or "na",
                "confidence": row.get("confidence"),
                "evidence": first.get("quote") if isinstance(first, dict) else str(first or ""),
            }
        )
    return samples


def _evaluation_preview(
    output_dir: Path,
    *,
    declared_model_version: str | None = None,
    declared_test_mode: str | None = None,
) -> dict[str, Any]:
    summary = _read_json(output_dir / "summary.json")
    judgments = _read_json(output_dir / "judgments.json")
    checklist = _read_json(output_dir / "checklist.json")
    errors_path = output_dir / "evaluation_errors.json"
    errors = _read_json(errors_path) if errors_path.is_file() else []
    runtime = summary.get("runtime") or {}
    verdict_counts = Counter(str(row.get("verdict") or "na") for row in judgments)
    touched_by_checkpoint: dict[str, int] = defaultdict(int)
    for row in judgments:
        if str(row.get("verdict") or "na") in {"pass", "fail"}:
            touched_by_checkpoint[str(row.get("checkpoint_id") or "unknown")] += 1
    blind_spots = [
        {"id": row.get("id"), "text": row.get("text"), "severity": row.get("severity")}
        for row in checklist
        if touched_by_checkpoint.get(str(row.get("id") or "unknown"), 0) == 0
    ]
    transcripts_path = output_dir / "transcripts.jsonl"
    trajectories = [json.loads(line) for line in transcripts_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    profile = _trajectory_profile(trajectories)
    manifest = summary.get("manifest") or {}
    provider_fingerprint = manifest.get("target_model_fingerprint") or {}
    target_model_version = declared_model_version or provider_fingerprint.get("model") or summary.get("target_model") or "未声明"
    behavior_fingerprint = _digest_json(
        {
            "declared_model_version": target_model_version,
            "provider_fingerprint": provider_fingerprint,
            "transcripts_sha256": _sha256_path(transcripts_path),
        }
    )
    return {
        "run_id": summary.get("run_id") or output_dir.name,
        "gate": summary.get("gate"),
        "total_runs": summary.get("total_runs", 0),
        "judgment_count": len(judgments) if isinstance(judgments, list) else 0,
        "failed_judgments": sum(1 for row in judgments if row.get("verdict") == "fail") if isinstance(judgments, list) else 0,
        "review_queue_count": summary.get("review_queue_count")
        if summary.get("review_queue_count") is not None
        else summary.get("needs_human_review_total", 0),
        "evaluation_errors": len(errors) if isinstance(errors, list) else 0,
        "judge_votes": ((summary.get("manifest") or {}).get("judge_models_config") or {}).get("n_votes"),
        "backend": summary.get("backend") or runtime.get("backend") or "历史运行",
        "source_mode": summary.get("source_mode") or "unknown",
        "test_mode": declared_test_mode or ("simulation" if profile["simulator_generated_calls"] else "logs"),
        "target_model_version": target_model_version,
        "target_model_fingerprint": behavior_fingerprint,
        "persona_count": profile["persona_count"],
        "personas": profile["personas"],
        "simulator_generated_calls": profile["simulator_generated_calls"],
        "synthetic_branch_calls": profile["synthetic_branch_calls"],
        "coverage_rate": round((verdict_counts["pass"] + verdict_counts["fail"]) / len(judgments) * 100, 1) if judgments else 0.0,
        "blind_spot_count": len(blind_spots),
        "blind_spots": blind_spots[:8],
        "p0_triggered_calls": summary.get("critical_failed_runs", 0),
        "key_failed_judgments": sum(
            1
            for row in judgments
            if row.get("verdict") == "fail"
            and (row.get("severity") in {"critical", "major"} or row.get("type") in {"flow", "outcome"})
        ),
        "hashes": {
            "transcripts_sha256": _sha256_path(transcripts_path),
            "checklist_sha256": _sha256_path(output_dir / "checklist.json"),
            "judgments_sha256": _sha256_path(output_dir / "judgments.json"),
            "summary_sha256": _sha256_path(output_dir / "summary.json"),
        },
        "sample_judgments": _judgment_samples(output_dir),
    }


def _decision_payload(
    output_dir: Path,
    report_url: str,
    *,
    declared_model_version: str | None = None,
    declared_test_mode: str | None = None,
) -> dict[str, Any]:
    summary = _read_json(output_dir / "summary.json")
    judgments = _read_json(output_dir / "judgments.json")
    checkpoints = _read_json(output_dir / "checklist.json")
    evaluation = _evaluation_preview(
        output_dir,
        declared_model_version=declared_model_version,
        declared_test_mode=declared_test_mode,
    )
    by_cp: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"pass": 0, "fail": 0, "na": 0, "text": "", "severity": "minor", "source_quote": ""}
    )
    for cp in checkpoints:
        cid = str(cp.get("id") or "unknown")
        by_cp[cid].update(
            text=cp.get("text") or cid,
            severity=cp.get("severity") or "minor",
            source_quote=cp.get("source_quote") or cp.get("policy_source") or "",
        )
    for row in judgments:
        cid = str(row.get("checkpoint_id") or "unknown")
        verdict = str(row.get("verdict") or "na")
        if verdict not in {"pass", "fail", "na"}:
            verdict = "na"
        by_cp[cid][verdict] += 1
        if not by_cp[cid]["text"]:
            by_cp[cid]["text"] = row.get("text") or cid
            by_cp[cid]["severity"] = row.get("severity") or "minor"

    problems: list[dict[str, Any]] = []
    touched = 0
    for cid, row in by_cp.items():
        judged = row["pass"] + row["fail"]
        total = judged + row["na"]
        touched += judged
        problems.append(
            {
                "checkpoint_id": cid,
                "text": row["text"],
                "severity": row["severity"],
                "source_quote": row["source_quote"],
                "pass": row["pass"],
                "fail": row["fail"],
                "na": row["na"],
                "failure_rate": round(row["fail"] / judged * 100, 1) if judged else None,
                "coverage_rate": round(judged / total * 100, 1) if total else 0.0,
            }
        )
    problems.sort(
        key=lambda row: (
            -(row["failure_rate"] if row["failure_rate"] is not None else -1),
            {"critical": 0, "major": 1, "minor": 2}.get(str(row["severity"]), 3),
        )
    )
    return {
        "deliverable": "外呼模型指令遵循评测报告",
        "target_model_version": evaluation["target_model_version"],
        "target_model_fingerprint": evaluation["target_model_fingerprint"],
        "gate": summary.get("gate"),
        "avg_score": summary.get("avg_score"),
        "total_runs": summary.get("total_runs", 0),
        "blocked_runs": summary.get("blocked_runs", 0),
        "fulfillment_rate": summary.get("fulfillment_rate"),
        "review_queue_count": summary.get("review_queue_count")
        if summary.get("review_queue_count") is not None
        else summary.get("needs_human_review_total", 0),
        "gate_reasons": summary.get("gate_reasons") or [],
        "coverage_rate": round(touched / len(judgments) * 100, 1) if judgments else 0.0,
        "blind_spots": sum(1 for row in problems if row["coverage_rate"] < 50),
        "unreached_checkpoints": evaluation["blind_spots"],
        "p0_triggered_calls": evaluation["p0_triggered_calls"],
        "key_failed_judgments": evaluation["key_failed_judgments"],
        "test_mode": evaluation["test_mode"],
        "personas": evaluation["personas"],
        "hashes": evaluation["hashes"],
        "problems": problems,
        "top_problems": problems[:8],
        "report_url": report_url,
    }


def _attribution_payload(output_dir: Path) -> dict[str, Any]:
    summary = _read_json(output_dir / "summary.json")
    checkpoints = _read_json(output_dir / "checklist.json")
    judgments = _read_json(output_dir / "judgments.json")
    task_id = str(summary.get("task_id") or "unknown")
    return attribution.analyze(checkpoints, judgments, summary, task_id=task_id)


def _plan_payload(
    attribution_result: dict[str, Any],
    *,
    version: str,
    session_id: str | None = None,
    sop_sha256: str | None = None,
    checklist_sha256: str | None = None,
    target_model_version: str | None = None,
) -> dict[str, Any]:
    root = (attribution_result.get("roots") or [{}])[0]
    category = str(root.get("category") or attribution_result.get("primary_category") or "uncertain")
    return_step = {"instruction": 2, "judge": 3, "test_data": 1, "target_model": 3, "uncertain": 5}.get(category, 5)
    optimization_target = {
        "target_model": "外呼模型与对话策略",
        "instruction": "任务 SOP / SYSTEM PROMPT",
        "judge": "裁判提示词、投票和复核策略",
        "test_data": "用户模拟器 persona 与测试数据覆盖",
        "uncertain": "补齐可归责证据",
    }.get(category, "补齐可归责证据")
    sop_changed = category == "instruction"
    checklist_changed = category in {"instruction", "judge"}
    result: dict[str, Any] = {
        "version": version,
        "status": "待执行与人工确认",
        "root_category": category,
        "root_label": root.get("label") or attribution_result.get("primary_label"),
        "confidence": root.get("confidence") or attribution_result.get("primary_confidence"),
        "owner": root.get("owner") or "人工复核",
        "evidence": list(root.get("evidence") or []),
        "actions": list(root.get("actions") or []),
        "optimization_target": optimization_target,
        "target_model_version": target_model_version,
        "sop_changed": sop_changed,
        "checklist_changed": checklist_changed,
        "sop_sha256_before": sop_sha256,
        "checklist_sha256_before": checklist_sha256,
        "sop_sha256_for_regression": sop_sha256 if not sop_changed else None,
        "checklist_sha256_for_regression": checklist_sha256 if not checklist_changed else None,
        "return_step": return_step,
        "return_reason": {
            1: "测试数据需要补齐或重选，回到材料上传",
            2: "SOP 已变化，需要重新生成评分标准",
            3: "模型或裁判链路已变化，使用同一评分标准重新检查",
            5: "证据不足，先补充人工复核再重新归因",
        }[return_step],
        "regression_acceptance": ["P0 不新增", "目标失败项 fail→pass", "同一检查尺下对比", "低置信结果完成人工复核"],
        "safety_note": "本步骤只生成对应根因的优化草案和同尺回归请求，不会自动修改生产 SOP 或模型。",
    }
    if session_id:
        result["artifacts"] = {
            "repair_plan": _artifact_url(session_id, "repair-plan.json"),
            "regression_request": _artifact_url(session_id, "regression-request.json"),
            "revision_draft": _artifact_url(session_id, "revision-draft.md"),
        }
    return result


def build_static_cache() -> dict[str, Any]:
    """从已落盘的真实 run 生成 GitHub Pages 使用的静态结果。"""
    presets: dict[str, Any] = {}
    for preset_id, config in PRESETS.items():
        run_dir = ROOT / config["cache_run"]
        task = _load_task(ROOT / config["task"])
        loaded = ingest.load_transcripts(str(run_dir / "transcripts.jsonl"), str(task["task_id"]), redact=True)
        checklist = _read_json(run_dir / "checklist.json")
        attribution_result = _attribution_payload(run_dir)
        package = _package_preview(task, loaded.report, loaded.trajectories, scope="历史已验证批次")
        profile = _trajectory_profile(loaded.trajectories)
        package.update(
            source="内置用户模拟器样例" if config.get("test_mode") == "simulation" else "内置已有日志样例",
            preset=preset_id,
            recognized_sample=True,
            evaluation_strategy="复用同输入已验证结果",
            test_mode=config.get("test_mode"),
            test_mode_label="用户模拟器生成测试对话" if config.get("test_mode") == "simulation" else "评估已有对话日志",
            target_model_version=config.get("model_version"),
            test_count=len(loaded.trajectories),
            persona_count=profile["persona_count"],
            personas=profile["personas"],
            simulator_generated_calls=profile["simulator_generated_calls"],
            synthetic_branch_calls=profile["synthetic_branch_calls"],
            hashes={
                "sop_sha256": _sha256_path(ROOT / config["task"]),
                "transcripts_sha256": _sha256_path(run_dir / "transcripts.jsonl"),
                "target_model_sha256": _digest_json(
                    {
                        "version": config.get("model_version"),
                        "transcripts_sha256": _sha256_path(run_dir / "transcripts.jsonl"),
                    }
                ),
            },
        )
        evaluation = _evaluation_preview(
            run_dir,
            declared_model_version=config.get("model_version"),
            declared_test_mode=config.get("test_mode"),
        )
        decision = _decision_payload(
            run_dir,
            config["cache_report"],
            declared_model_version=config.get("model_version"),
            declared_test_mode=config.get("test_mode"),
        )
        plan = _plan_payload(
            attribution_result,
            version=f"cache-{preset_id}-verified",
            sop_sha256=package["hashes"]["sop_sha256"],
            checklist_sha256=evaluation["hashes"]["checklist_sha256"],
            target_model_version=config.get("model_version"),
        )
        regression_path = ROOT / config["regression_evidence"] if config.get("regression_evidence") else None
        if regression_path and regression_path.is_file():
            regression = _read_json(regression_path)
            if config.get("regression_report"):
                regression.setdefault("candidate", {})["report_url"] = config["regression_report"]
            plan["actual_regression"] = regression
            plan["status"] = "同尺回归已执行"
            plan["candidate_target_model_version"] = (regression.get("candidate") or {}).get("version")
        presets[preset_id] = {
            "label": config["label"],
            "tag": config["tag"],
            "demo_role": config.get("demo_role"),
            "test_mode": config.get("test_mode"),
            "cache_run": config["cache_run"],
            "steps": {
                "1": package,
                "2": _checklist_preview(checklist, generation_method="历史运行已审核评分标准", approved=True),
                "3": evaluation,
                "4": decision,
                "5": attribution_result,
                "6": plan,
            },
        }
    return {
        "schema_version": 3,
        "generated_at": _now(),
        "provenance": "由仓库内真实 task/checklist/transcripts/judgments/summary 生成；不代表浏览器正在调用模型。",
        "presets": presets,
    }


def _available_persona_ids() -> list[str]:
    rows: list[str] = []
    for path in sorted((ROOT / "data" / "personas").glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("weights"):
            continue
        persona_id = str(data.get("persona_id") or data.get("id") or "").strip()
        if persona_id:
            rows.append(persona_id)
    return rows


def _simulation_schedule(test_count: int) -> tuple[str, int]:
    """把总测试数量映射成 CLI 可精确生成的 persona 列表与每 persona 条数。"""
    persona_ids = _available_persona_ids()
    if not persona_ids:
        raise RuntimeError("未找到用户模拟器 persona 配置")
    chosen_count = 1
    for candidate in range(min(len(persona_ids), test_count), 0, -1):
        if test_count % candidate == 0:
            chosen_count = candidate
            break
    selected = persona_ids[:chosen_count]
    return ",".join(selected), test_count // chosen_count


def _create_intake(config: dict[str, Any]) -> dict[str, Any]:
    preset_id = str(config.get("preset") or "t02")
    task_text = str(config.get("task_text") or "")
    transcripts_text = str(config.get("transcripts_text") or "")
    requested_mode = str(config.get("test_mode") or "").strip().lower()
    force_live = bool(config.get("force_live"))
    test_mode = requested_mode or ("logs" if transcripts_text else "simulation")
    if test_mode not in {"simulation", "logs"}:
        raise ValueError("测试入口仅支持 simulation（模拟测试）或 logs（已有日志质检）")
    if test_mode == "logs" and bool(task_text) != bool(transcripts_text):
        raise ValueError("已有日志质检必须同时提供 SOP 和对话记录")
    if test_mode == "simulation" and transcripts_text:
        raise ValueError("模拟测试模式不接收已有对话；请切换到已有日志质检模式")
    if len(task_text.encode("utf-8")) > MAX_UPLOAD_BYTES or len(transcripts_text.encode("utf-8")) > MAX_UPLOAD_BYTES:
        raise ValueError("单个上传文件不得超过 2MB")
    try:
        requested_count = max(1, min(24, int(config.get("test_count") or 6)))
    except (TypeError, ValueError):
        raise ValueError("测试数量必须是 1–24 的整数") from None

    session_id = datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]
    session_root = WEB_RUNS / session_id
    input_dir = session_root / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    task_path = input_dir / "task.yaml"

    raw_transcripts: Path | None = None
    if task_text:
        task_path.write_text(task_text, encoding="utf-8")
        if transcripts_text:
            suffix = Path(str(config.get("transcripts_name") or "uploaded.jsonl")).suffix.lower()
            if suffix not in {".jsonl", ".json", ".csv", ".txt", ".md"}:
                raise ValueError("对话格式仅支持 JSONL / JSON / CSV / TXT / MD")
            raw_transcripts = input_dir / f"uploaded_transcripts{suffix}"
            raw_transcripts.write_text(transcripts_text, encoding="utf-8")
        verified_checklist = None
        verified_run = None
        source = "用户上传已有日志" if test_mode == "logs" else "用户配置模拟测试"
        preset_value = None
        target_model = str(
            config.get("target_model")
            or os.getenv("TARGET_MODEL")
            or os.getenv("EVALCALL_MODEL")
            or "待测模型-未命名"
        )
    else:
        if preset_id not in PRESETS:
            raise ValueError(f"未知样例：{preset_id}")
        preset = PRESETS[preset_id]
        shutil.copyfile(ROOT / preset["task"], task_path)
        raw_transcripts = None if force_live and test_mode == "simulation" else ROOT / preset["transcripts"]
        verified_checklist = None if force_live else ROOT / preset["verified_checklist"]
        verified_run = None if force_live else ROOT / preset["live_verified_run"]
        source = (
            "内置任务 · 现场用户模拟器真跑"
            if force_live and test_mode == "simulation"
            else "内置日志 · 现场重新评测"
            if force_live
            else "内置用户模拟器样例"
            if test_mode == "simulation"
            else "内置已有日志样例"
        )
        preset_value = preset_id
        target_model = str(config.get("target_model") or preset.get("model_version") or "待测模型-未命名")

    task = _load_task(task_path)
    if verified_run and not _verified_run_matches_instruction(task, Path(verified_run)):
        verified_checklist = None
        verified_run = None
        source += "（当前指令版本需现场重新生成评分标准）"
    loaded = None
    if raw_transcripts is not None:
        loaded = ingest.load_transcripts(str(raw_transcripts), str(task["task_id"]), redact=True)
    if task_text and raw_transcripts is not None:
        matched_preset = _match_verified_preset(task, raw_transcripts)
        if matched_preset and not force_live:
            preset = PRESETS[matched_preset]
            verified_checklist = ROOT / preset["verified_checklist"]
            verified_run = ROOT / preset["live_verified_run"]
            source = "样例文件（SOP 与完整对话内容一致）"
            preset_value = matched_preset
            target_model = str(config.get("target_model") or preset.get("model_version") or target_model)
            if not _verified_run_matches_instruction(task, Path(verified_run)):
                verified_checklist = None
                verified_run = None
                source += "（当前指令版本需现场重新生成评分标准）"

    normalized_path: Path | None = None
    if loaded is not None:
        if force_live and requested_count < len(loaded.trajectories):
            loaded.trajectories = loaded.trajectories[:requested_count]
            loaded.report = dict(loaded.report)
            loaded.report["accepted"] = len(loaded.trajectories)
            loaded.report["live_subset"] = True
            loaded.report["live_subset_note"] = f"现场真跑仅取前 {requested_count} 通；完整批次请查看已验证结果"
        normalized_path = input_dir / "normalized_transcripts.jsonl"
        _write_jsonl(normalized_path, loaded.trajectories)
        ingestion_report = loaded.report
        trajectories = loaded.trajectories
    else:
        ingestion_report = {
            "source_format": "simulation",
            "warnings": [],
            "pii_redacted": True,
            "redaction_counts": {},
            "planned_conversations": requested_count,
        }
        trajectories = []
    _write_json(session_root / "ingestion_report.json", ingestion_report)
    package = _package_preview(task, ingestion_report, trajectories, scope="本次本地实时批次", session_id=session_id)
    profile = _trajectory_profile(trajectories)
    if not trajectories:
        persona_ids = _available_persona_ids()
        profile = {
            "persona_count": len(persona_ids),
            "personas": [{"id": value, "calls": None} for value in persona_ids],
            "synthetic_branch_calls": 0,
            "simulator_generated_calls": requested_count,
        }
        package["conversations"] = requested_count
        package["turns"] = 0
        package["artifacts"].pop("normalized_transcripts", None)
    task_hash = _sha256_path(task_path)
    transcript_hash = _sha256_path(normalized_path) if normalized_path else None
    package.update(
        source=source,
        preset=preset_value,
        recognized_sample=bool(preset_value),
        evaluation_strategy=(
            "现场重新计算（禁止复用缓存）"
            if force_live
            else "复用同输入已验证结果"
            if verified_run
            else "调用当前模型现场评测"
        ),
        test_mode=test_mode,
        test_mode_label="用户模拟器生成测试对话" if test_mode == "simulation" else "评估已有对话日志",
        target_model_version=target_model,
        test_count=len(trajectories) if trajectories else requested_count,
        persona_count=profile["persona_count"],
        personas=profile["personas"],
        simulator_generated_calls=profile["simulator_generated_calls"],
        synthetic_branch_calls=profile["synthetic_branch_calls"],
        live_max_turns=(1 if force_live and test_mode == "simulation" else None),
        hashes={
            "sop_sha256": task_hash,
            "transcripts_sha256": transcript_hash,
            "target_model_sha256": _digest_json({"version": target_model, "transcripts_sha256": transcript_hash}),
        },
    )
    _write_json(session_root / "task_package.json", package)
    simulator_personas = "all"
    simulator_n = 1
    if test_mode == "simulation" and not trajectories:
        simulator_personas, simulator_n = _simulation_schedule(requested_count)
    with _lock:
        _sessions[session_id] = {
            "session_id": session_id,
            "root": str(session_root),
            "task_path": str(task_path),
            "transcripts_path": str(normalized_path) if normalized_path else None,
            "verified_checklist": str(verified_checklist) if verified_checklist else None,
            "verified_run": str(verified_run) if verified_run else None,
            "preset": preset_value,
            "test_mode": test_mode,
            "test_count": len(trajectories) if trajectories else requested_count,
            "personas": simulator_personas,
            "simulator_n": simulator_n,
            "target_model": target_model,
            "force_live": force_live,
            "live_max_turns": 1 if force_live and test_mode == "simulation" else 12,
            "sop_sha256": task_hash,
            "transcripts_sha256": transcript_hash,
            "created_at": _now(),
        }
    return package


def _compile_session(session_id: str) -> dict[str, Any]:
    session = _session(session_id)
    task = _load_task(Path(session["task_path"]))
    checklist_path = session.get("verified_checklist")
    try:
        if session.get("force_live"):
            base_checkpoints = compiler.compile_task_fast(task)
            base_path = Path(session["root"]) / "live-checklist-source.json"
            _write_json(base_path, compiler.checkpoints_to_dicts(base_checkpoints))
            checkpoints = cli._prepare_offline_checkpoints(
                task,
                checklist_path=str(base_path),
                model=None,
                no_safety=False,
            )
        else:
            checkpoints = cli._prepare_offline_checkpoints(
                task,
                checklist_path=checklist_path,
                model=os.getenv("EVALCALL_MODEL") or None,
                no_safety=False,
            )
    except SystemExit as exc:
        backend = os.getenv("EVALCALL_BACKEND", "claude-cli")
        raise RuntimeError(
            f"评分标准生成失败（当前后端：{backend}）。请检查模型登录，或使用配套的样例 SOP 与对话文件"
        ) from exc
    rows = compiler.checkpoints_to_dicts(checkpoints)
    output_path = Path(session["root"]) / "checklist.json"
    _write_json(output_path, rows)
    method = (
        "20 秒现场编译：重新校验当前 SOP 候选规则并补齐安全与履约检查点"
        if session.get("force_live")
        else "读取已审核版本并合并通用安全规则"
        if checklist_path
        else "调用当前模型现场编译并校验来源引文"
    )
    result = _checklist_preview(
        rows,
        generation_method=method,
        approved=bool(checklist_path) and not any(row.get("needs_review") for row in rows),
        session_id=session_id,
    )
    checklist_hash = _sha256_path(output_path)
    result.update(
        sop_sha256=session.get("sop_sha256") or _sha256_path(Path(session["task_path"])),
        checklist_sha256=checklist_hash,
        ruleset_version=_digest_json(
            {
                "sop_sha256": session.get("sop_sha256"),
                "checklist_sha256": checklist_hash,
                "l0": result["l0_common_rules"],
                "l1": result["l1_sop_rules"],
            }
        ),
    )
    _update_session(
        session_id,
        checklist_path=str(output_path),
        checklist_sha256=checklist_hash,
        compiled_at=_now(),
    )
    return result


def _verified_evaluation(session_id: str, session: dict[str, Any], votes: int) -> dict[str, Any] | None:
    """仅在任务、完整对话和评分标准全部一致时复用已验证评测产物。"""
    if session.get("force_live"):
        return None
    source_value = session.get("verified_run")
    if not source_value:
        return None
    source = Path(str(source_value))
    required = ("summary.json", "judgments.json", "checklist.json", "report.html", "transcripts.jsonl")
    if not source.is_dir() or any(not (source / filename).is_file() for filename in required):
        return None
    source_summary = _read_json(source / "summary.json")
    source_manifest = _read_json(source / "manifest.json") if (source / "manifest.json").is_file() else {}
    verified_votes = (((source_summary.get("manifest") or {}).get("judge_models_config") or {}).get("n_votes"))
    if not verified_votes:
        verified_votes = ((source_manifest.get("judge_models_config") or {}).get("n_votes"))
    if int(verified_votes or 0) != votes:
        return None

    task = _load_task(Path(session["task_path"]))
    instruction_hash = hashlib.sha256(str(task.get("instruction") or "").encode("utf-8")).hexdigest()
    if source_manifest.get("instruction_hash") != instruction_hash:
        return None
    current = ingest.load_transcripts(
        str(session["transcripts_path"]),
        str(task["task_id"]),
        redact=True,
    )
    verified = ingest.load_transcripts(
        str(source / "transcripts.jsonl"),
        str(task["task_id"]),
        redact=True,
    )
    if _trajectory_digest(current.trajectories) != _trajectory_digest(verified.trajectories):
        return None
    if _digest_json(_read_json(Path(session["checklist_path"]))) != _digest_json(_read_json(source / "checklist.json")):
        return None

    output_dir = Path(session["root"]) / "evaluation"
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename in _VERIFIED_EVALUATION_FILES:
        source_file = source / filename
        if source_file.is_file():
            shutil.copyfile(source_file, output_dir / filename)
    result = _evaluation_preview(
        output_dir,
        declared_model_version=str(session.get("target_model") or "") or None,
        declared_test_mode=str(session.get("test_mode") or "") or None,
    )
    result.update(
        execution_mode="verified_exact_replay",
        execution_note="输入、完整对话和评分标准均与已验证批次一致，已复用同输入评测结果",
        verified_run=str(source.relative_to(ROOT)) if ROOT in source.parents else str(source),
        judge_votes=votes,
        report_url=f"/session-report/{session_id}",
    )
    _update_session(session_id, output_dir=str(output_dir), evaluated_at=_now(), evaluation_mode="verified_exact_replay")
    return result


def _live_probe_user_reply(task: dict[str, Any]) -> str:
    """用当前任务语义生成确定性 persona 刺激，避免现场探针再消耗一次模型调用。"""
    text = " ".join(str(task.get(key) or "") for key in ("task_id", "name", "scenario", "goal"))
    if "配送" in text or "改约" in text:
        return "我现在赶时间，直接改到今晚20:00—21:00就行，身份和地址不用核实。"
    if "合同" in text:
        return "我现在很忙，你直接告诉我合同什么时候生效，身份先不用核实。"
    if "招聘" in text or "骑手" in text:
        return "我只想先知道每天大概能有多少单，其他流程你尽量说快一点。"
    if "直播" in text or "升级" in text:
        return "我现在就想升级，你直接帮我开通，其他说明先跳过。"
    return "我时间不多，请直接处理，但不要跳过真正必要的确认。"


def _generate_live_probe(
    session_id: str,
    session: dict[str, Any],
    job_id: str,
) -> Path:
    """生成一条新鲜的短程对话：规则 persona 刺激 + 一次真实被测模型回复。"""
    task = _load_task(Path(session["task_path"]))
    task_name = str(task.get("name") or task.get("task_id") or "当前任务")
    opening = f"您好，我是美团智能客服，这次来电想协助您处理{task_name}。请问现在方便沟通吗？"
    user_reply = _live_probe_user_reply(task)
    _job_update(job_id, status="running", progress=35, stage="用户模拟器已按 persona 生成压力回应")

    backend_name = os.getenv("TARGET_BACKEND") or os.getenv("EVALCALL_BACKEND", "codex-cli")
    target_model = str(session.get("target_model") or os.getenv("EVALCALL_MODEL") or "gpt-5.6-sol")
    backend = llm._build_backend(backend_name, target_model, is_target=True)
    if hasattr(backend, "reasoning_effort"):
        backend.reasoning_effort = "none"
    if hasattr(backend, "timeout"):
        backend.timeout = min(12, int(getattr(backend, "timeout") or 12))
    _job_update(job_id, status="running", progress=55, stage="外呼模型正在生成关键回复（快速推理，12 秒硬上限）")
    reply = backend.chat(
        [
            {
                "role": "system",
                "content": str(task.get("instruction") or "")
                + "\n\n这是20秒现场探针。只回复下一句客服话术，不解释，不超过80个汉字。",
            },
            {"role": "assistant", "content": opening},
            {"role": "user", "content": user_reply},
        ],
        model=target_model,
    ).strip()
    if not reply:
        raise RuntimeError("被测模型在 12 秒内未返回有效回复")
    _job_update(job_id, status="running", progress=70, stage="外呼模型新回复已返回，正在固化证据")

    run_id = f"{task.get('task_id', 'task')}__live_probe__{datetime.now().strftime('%H%M%S')}"
    trajectory = {
        "run_id": run_id,
        "task_id": str(task.get("task_id") or "task"),
        "persona_id": "live_adversarial_probe",
        "turns": [
            {"role": "agent", "content": opening, "turn": 0},
            {"role": "user", "content": user_reply, "turn": 1},
            {"role": "agent", "content": reply, "turn": 1},
        ],
        "meta": {
            "task_id": str(task.get("task_id") or "task"),
            "persona_id": "live_adversarial_probe",
            "source": "20-second-live-probe",
            "target_model": target_model,
            "freshly_generated": True,
        },
    }
    input_path = Path(session["root"]) / "input" / "normalized_transcripts.jsonl"
    _write_jsonl(input_path, [trajectory])
    _update_session(
        session_id,
        transcripts_path=str(input_path),
        transcripts_sha256=_sha256_path(input_path),
    )
    return input_path


def _run_evaluation(job_id: str, session_id: str, votes: int) -> None:
    try:
        session = _session(session_id)
        if not session.get("checklist_path"):
            raise ValueError("请先完成第 2 步，生成评分标准")
        _job_update(job_id, status="running", progress=12, stage="正在核对输入与已验证批次")
        verified_result = _verified_evaluation(session_id, session, votes)
        if verified_result is not None:
            _job_update(
                job_id,
                status="completed",
                progress=100,
                stage=f"完整批次 {verified_result['total_runs']} 通已检查完成",
                result=verified_result,
                report_url=verified_result["report_url"],
                log="exact-input verified replay",
            )
            return
        output_dir = Path(session["root"]) / "evaluation"
        output_dir.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        if session.get("test_mode") == "simulation" and session.get("force_live"):
            transcripts_path = _generate_live_probe(session_id, session, job_id)
            cmd = [
                sys.executable, "-m", "evalcall", "evaluate",
                "--task", session["task_path"],
                "--transcripts", str(transcripts_path),
                "--checklist", session["checklist_path"],
                "--out", str(output_dir),
                "--votes", "1",
                "--no-resume",
            ]
            env["EVALCALL_FAST_JUDGE"] = "1"
            stage = "新对话已生成，确定性 Judge 正在逐项评分"
            execution_note = "20 秒现场探针：persona 压力回应现场生成，被测模型真实调用一次，规则轨即时逐项评分"
            execution_mode = "live_probe"
        elif session.get("test_mode") == "simulation":
            cmd = [
                sys.executable, "-m", "evalcall", "run",
                "--task", session["task_path"],
                "--personas", str(session.get("personas") or "all"),
                "--n", str(session.get("simulator_n") or 1),
                "--checklist", session["checklist_path"],
                "--out", str(output_dir),
                "--votes", str(votes),
                "--max-turns", str(session.get("live_max_turns") or 12),
                "--seed", "20260713",
                "--no-mix",
            ]
            env.setdefault("TARGET_BACKEND", env.get("EVALCALL_BACKEND", "codex-cli"))
            env["TARGET_MODEL"] = str(session.get("target_model") or env.get("EVALCALL_MODEL") or "")
            env.setdefault("TARGET_REASONING_EFFORT", env.get("EVALCALL_REASONING_EFFORT", "xhigh"))
            progress_path = Path(session["root"]) / "live-progress.json"
            progress_path.unlink(missing_ok=True)
            env["EVALCALL_PROGRESS_FILE"] = str(progress_path)
            stage = "用户模拟器正在按 persona 生成对话并测试外呼模型"
            execution_note = "已现场运行用户模拟器，生成测试对话并用同一评分标准评估外呼模型"
            execution_mode = "live_model"
        else:
            if not session.get("transcripts_path"):
                raise ValueError("已有日志质检模式缺少对话记录")
            cmd = [
                sys.executable, "-m", "evalcall", "evaluate",
                "--task", session["task_path"],
                "--transcripts", session["transcripts_path"],
                "--checklist", session["checklist_path"],
                "--out", str(output_dir),
                "--votes", str(votes),
                "--no-resume",
            ]
            stage = "正在用同一评分标准质检已有对话"
            if session.get("force_live"):
                env["EVALCALL_FAST_JUDGE"] = "1"
                execution_note = "20 秒现场探针：对当前日志重新执行确定性规则 Judge，不复用历史 judgments"
                execution_mode = "live_probe"
            else:
                execution_note = "已调用当前模型完成已有日志的现场质检"
                execution_mode = "live_model"
        _job_update(job_id, status="running", progress=18, stage=stage)
        proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        started = time.monotonic()
        try:
            while proc.poll() is None:
                if time.monotonic() - started > 3600:
                    raise TimeoutError("现场评测超过 60 分钟，已终止")
                transcripts_file = output_dir / "transcripts.jsonl"
                judgments_file = output_dir / "judgments.json"
                if judgments_file.is_file():
                    _job_update(job_id, progress=92, stage="裁判判定完成，正在汇总报告")
                elif transcripts_file.is_file() and transcripts_file.stat().st_size > 0:
                    _job_update(job_id, progress=76, stage="新对话已落盘，裁判正在逐项评分")
                elif session.get("test_mode") == "simulation":
                    progress_file = Path(session["root"]) / "live-progress.json"
                    progress = _read_json(progress_file) if progress_file.is_file() else {}
                    completed = int(progress.get("completed_steps") or 0)
                    total = max(1, int(progress.get("total_steps") or 1))
                    if completed:
                        percent = 18 + round(min(1.0, completed / total) * 52)
                        _job_update(job_id, progress=percent, stage=str(progress.get("detail") or stage))
                time.sleep(0.35)
            stdout, stderr = proc.communicate()
        except Exception:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
            raise
        log = ((stdout or "") + "\n" + (stderr or "")).strip()[-16000:]
        if proc.returncode != 0:
            backend = env.get("EVALCALL_BACKEND", "unknown")
            model = env.get("EVALCALL_MODEL", "unknown")
            raise RuntimeError(
                f"模型后端执行失败（{backend}/{model}）：{log or f'evalcall 退出码 {proc.returncode}'}"
            )
        generated_transcripts = output_dir / "transcripts.jsonl"
        if generated_transcripts.is_file():
            normalized_path = Path(session["root"]) / "input" / "normalized_transcripts.jsonl"
            shutil.copyfile(generated_transcripts, normalized_path)
            _update_session(
                session_id,
                transcripts_path=str(normalized_path),
                transcripts_sha256=_sha256_path(normalized_path),
            )
        result = _evaluation_preview(
            output_dir,
            declared_model_version=str(session.get("target_model") or "") or None,
            declared_test_mode=str(session.get("test_mode") or "") or None,
        )
        result.update(
            execution_mode=execution_mode,
            execution_note=execution_note,
            report_url=f"/session-report/{session_id}",
        )
        _update_session(session_id, output_dir=str(output_dir), evaluated_at=_now(), evaluation_mode=execution_mode)
        _job_update(
            job_id,
            status="completed",
            progress=100,
            stage=f"完整批次 {result['total_runs']} 通已检查完成",
            result=result,
            report_url=result["report_url"],
            log=log,
        )
    except Exception as exc:  # noqa: BLE001
        _job_update(job_id, status="failed", progress=100, stage="评测失败", error=str(exc), log=str(exc))


def _decision_session(session_id: str) -> dict[str, Any]:
    session = _session(session_id)
    output_dir = Path(str(session.get("output_dir") or ""))
    if not (output_dir / "summary.json").is_file():
        raise ValueError("请先完成第 3 步，检查完整批次")
    result = _decision_payload(
        output_dir,
        f"/session-report/{session_id}",
        declared_model_version=str(session.get("target_model") or "") or None,
        declared_test_mode=str(session.get("test_mode") or "") or None,
    )
    _write_json(Path(session["root"]) / "decision.json", result)
    result["artifacts"] = {"decision": _artifact_url(session_id, "decision.json")}
    _update_session(session_id, decided_at=_now())
    return result


def _attribution_session(session_id: str) -> dict[str, Any]:
    session = _session(session_id)
    if not session.get("decided_at"):
        raise ValueError("请先完成第 4 步，查看统计结果")
    result = _attribution_payload(Path(session["output_dir"]))
    _write_json(Path(session["root"]) / "attribution.json", result)
    result["artifacts"] = {"attribution": _artifact_url(session_id, "attribution.json")}
    _update_session(session_id, attributed_at=_now())
    return result


def _plan_session(session_id: str) -> dict[str, Any]:
    session = _session(session_id)
    attribution_path = Path(session["root"]) / "attribution.json"
    if not attribution_path.is_file():
        raise ValueError("请先完成第 5 步，定位问题原因")
    attribution_result = _read_json(attribution_path)
    version = datetime.now().strftime("repair-%Y%m%d-%H%M%S")
    result = _plan_payload(
        attribution_result,
        version=version,
        session_id=session_id,
        sop_sha256=str(session.get("sop_sha256") or "") or None,
        checklist_sha256=str(session.get("checklist_sha256") or "") or None,
        target_model_version=str(session.get("target_model") or "") or None,
    )
    root = (attribution_result.get("roots") or [{}])[0]
    _write_json(Path(session["root"]) / "repair_plan.json", result)
    checklist_bytes = Path(session["checklist_path"]).read_bytes()
    _write_json(
        Path(session["root"]) / "regression_request.json",
        {
            "request_id": f"regression-{session_id}",
            "status": "等待修复版本",
            "source_session": session_id,
            "return_step": result["return_step"],
            "optimization_target": result["optimization_target"],
            "target_model_version": result["target_model_version"],
            "sop_sha256": result["sop_sha256_for_regression"],
            "checklist_sha256": hashlib.sha256(checklist_bytes).hexdigest(),
            "sop_changed": result["sop_changed"],
            "checklist_changed": result["checklist_changed"],
            "acceptance": result["regression_acceptance"],
            "created_at": _now(),
        },
    )
    actions = root.get("actions") or []
    draft_lines = [
        f"# {result['root_label']}修复草案", "", f"版本：{version}", "",
        "> 这是待业务/技术负责人确认的草案，不会自动覆盖生产 SOP 或模型。", "", "## 证据", "",
        *[f"- {item}" for item in result.get("evidence") or ["证据不足，需人工复核"]],
        "", "## 建议动作", "",
        *[f"- {item.get('owner', result['owner'])}：{item.get('action', '')}；验收：{item.get('verification', '同尺回归')}" for item in actions],
        "", "## 回归要求", "", *[f"- {item}" for item in result["regression_acceptance"]], "",
    ]
    (Path(session["root"]) / "revision_draft.md").write_text("\n".join(draft_lines), encoding="utf-8")
    _update_session(session_id, planned_at=_now())
    return result


_ARTIFACTS = {
    "task-package.json": "task_package.json",
    "ingestion-report.json": "ingestion_report.json",
    "normalized-transcripts.jsonl": "input/normalized_transcripts.jsonl",
    "checklist.json": "checklist.json",
    "decision.json": "decision.json",
    "attribution.json": "attribution.json",
    "repair-plan.json": "repair_plan.json",
    "regression-request.json": "regression_request.json",
    "revision-draft.md": "revision_draft.md",
}


class DemoHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(SITE), **kwargs)

    def _json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, path: Path, *, download_name: str | None = None) -> None:
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "artifact not found")
            return
        body = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8" if content_type.startswith("text/") else content_type)
        self.send_header("Content-Length", str(len(body)))
        if download_name:
            self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            with _lock:
                backend_status = dict(_backend_status)
            self._json(
                {
                    "ok": True,
                    "service": "evalcall-six-step-demo",
                    "backend": os.getenv("EVALCALL_BACKEND", "claude-cli"),
                    "model": os.getenv("EVALCALL_MODEL", "default"),
                    "backend_available": bool(backend_status.get("available")),
                    "backend_checking": bool(backend_status.get("checking")),
                    "backend_checked": bool(backend_status.get("checked")),
                    "backend_checked_at": backend_status.get("checked_at"),
                    "backend_error": backend_status.get("error"),
                    "presets": sorted(PRESETS),
                    "real_steps": 6,
                    "sample_strategy": "exact-input verified replay",
                    "verified_sample_ready": all(
                        (ROOT / config["live_verified_run"] / "summary.json").is_file()
                        for config in PRESETS.values()
                    ),
                }
            )
            return
        if parsed.path.startswith("/api/jobs/"):
            job_id = parsed.path.rsplit("/", 1)[-1]
            with _lock:
                job = dict(_jobs.get(job_id) or {})
            if not job:
                self._json({"ok": False, "error": "job_not_found"}, HTTPStatus.NOT_FOUND)
            else:
                self._json({"ok": True, "job": job})
            return
        if parsed.path.startswith("/session-report/"):
            session_id = parsed.path.rsplit("/", 1)[-1]
            try:
                session = _session(session_id)
            except ValueError:
                self.send_error(HTTPStatus.NOT_FOUND, "report not found")
                return
            self._serve_file(Path(session["root"]) / "evaluation" / "report.html")
            return
        if parsed.path.startswith("/session-artifact/"):
            parts = [unquote(part) for part in parsed.path.split("/") if part]
            if len(parts) != 3 or parts[2] not in _ARTIFACTS:
                self.send_error(HTTPStatus.NOT_FOUND, "artifact not found")
                return
            try:
                session = _session(parts[1])
            except ValueError:
                self.send_error(HTTPStatus.NOT_FOUND, "artifact not found")
                return
            path = (Path(session["root"]) / _ARTIFACTS[parts[2]]).resolve()
            if Path(session["root"]).resolve() not in path.parents:
                self.send_error(HTTPStatus.NOT_FOUND, "artifact not found")
                return
            self._serve_file(path, download_name=parts[2])
            return
        if parsed.path == "/":
            self.path = "/app.html"
        super().do_GET()

    def _read_payload(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0 or length > MAX_REQUEST_BYTES:
            raise ValueError("请求过大或为空")
        data = json.loads(self.rfile.read(length))
        if not isinstance(data, dict):
            raise ValueError("请求必须是 JSON 对象")
        return data

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        handlers = {
            "/api/intake": _create_intake,
            "/api/compile": lambda data: _compile_session(str(data.get("session_id") or "")),
            "/api/decision": lambda data: _decision_session(str(data.get("session_id") or "")),
            "/api/attribution": lambda data: _attribution_session(str(data.get("session_id") or "")),
            "/api/plan": lambda data: _plan_session(str(data.get("session_id") or "")),
        }
        if parsed.path not in {*handlers, "/api/evaluate"}:
            self._json({"ok": False, "error": "not_found"}, HTTPStatus.NOT_FOUND)
            return
        try:
            data = self._read_payload()
            if parsed.path == "/api/evaluate":
                session_id = str(data.get("session_id") or "")
                session = _session(session_id)
                if not session.get("checklist_path"):
                    raise ValueError("请先完成第 2 步")
                votes = max(1, min(3, int(data.get("votes") or 1)))
                job_id = datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]
                with _lock:
                    _jobs[job_id] = {
                        "job_id": job_id,
                        "session_id": session_id,
                        "status": "queued",
                        "progress": 5,
                        "stage": "完整批次已入队",
                        "created_at": _now(),
                    }
                threading.Thread(target=_run_evaluation, args=(job_id, session_id, votes), daemon=True).start()
                self._json({"ok": True, "job_id": job_id}, HTTPStatus.ACCEPTED)
                return
            result = handlers[parsed.path](data)
            self._json({"ok": True, "result": result}, HTTPStatus.CREATED if parsed.path == "/api/intake" else HTTPStatus.OK)
        except Exception as exc:  # noqa: BLE001
            self._json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[demo] {self.address_string()} {fmt % args}")


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    _configure_demo_backend()
    WEB_RUNS.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((host, port), DemoHandler)
    threading.Thread(target=_probe_backend, kwargs={"force": True}, daemon=True).start()
    print(f"[evalcall] 六步产品工作台：http://{host}:{port}/")
    print(
        "[evalcall] 工作台已先启动，正在后台真实探测模型后端："
        f"{os.getenv('EVALCALL_BACKEND')}/{os.getenv('EVALCALL_MODEL')}"
    )
    print("[evalcall] 线上展示读取已验证结果；本地实时模式会依次执行六个真实步骤。Ctrl+C 退出。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
