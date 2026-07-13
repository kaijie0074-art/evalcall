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
import uuid
from collections import Counter, defaultdict
from datetime import datetime
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import yaml

from . import attribution, cli, compiler, ingest


ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site-deploy"
WEB_RUNS = ROOT / "runs" / "web_live"
MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_REQUEST_BYTES = MAX_UPLOAD_BYTES * 2 + 200_000

PRESETS: dict[str, dict[str, str]] = {
    "official01": {
        "label": "骑手合同生效通知",
        "tag": "官方脱敏任务",
        "task": "样例选择包/01_骑手合同生效通知_SOP.yaml",
        "transcripts": "样例选择包/01_骑手合同生效通知_对话_12通.jsonl",
        "verified_checklist": "runs/official01_gated_20260702/checklist.json",
        "cache_run": "runs/official01_gated_20260702",
        "cache_report": "report-official-gated.html",
    },
    "t02": {
        "label": "配送时间改约",
        "tag": "健康 SOP 对照",
        "task": "样例选择包/02_配送时间改约_SOP.yaml",
        "transcripts": "样例选择包/02_配送时间改约_对话_10通.jsonl",
        "verified_checklist": "runs/t02_gated_20260702/checklist.json",
        "cache_run": "runs/t02_gated_20260702",
        "cache_report": "report-t02-gated.html",
    },
    "real_recruit": {
        "label": "骑手招聘外呼",
        "tag": "真实生产 SOP",
        "task": "样例选择包/03_骑手招聘外呼_SOP.yaml",
        "transcripts": "样例选择包/03_骑手招聘外呼_对话_10通.jsonl",
        "verified_checklist": "runs/m5_real_recruit_gpt56sol_xhigh_v2_20260712/checklist.json",
        "cache_run": "runs/m5_real_recruit_gpt56sol_xhigh_v2_20260712",
        "cache_report": "report-real-recruit-gpt56sol.html",
    },
    "official02": {
        "label": "低延迟直播升级通知",
        "tag": "官方脱敏任务",
        "task": "样例选择包/04_低延迟直播升级通知_SOP.yaml",
        "transcripts": "样例选择包/04_低延迟直播升级通知_对话_10通.jsonl",
        "verified_checklist": "runs/official02_gated_20260702/checklist.json",
        "cache_run": "runs/official02_gated_20260702",
        "cache_report": "report-official2-gated.html",
    },
}

_jobs: dict[str, dict[str, Any]] = {}
_sessions: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


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
            "normalized_transcripts": _artifact_url(session_id, "normalized-transcripts.jsonl"),
        }
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


def _evaluation_preview(output_dir: Path) -> dict[str, Any]:
    summary = _read_json(output_dir / "summary.json")
    judgments = _read_json(output_dir / "judgments.json")
    errors_path = output_dir / "evaluation_errors.json"
    errors = _read_json(errors_path) if errors_path.is_file() else []
    runtime = summary.get("runtime") or {}
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
        "sample_judgments": _judgment_samples(output_dir),
    }


def _decision_payload(output_dir: Path, report_url: str) -> dict[str, Any]:
    summary = _read_json(output_dir / "summary.json")
    judgments = _read_json(output_dir / "judgments.json")
    checkpoints = _read_json(output_dir / "checklist.json")
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


def _plan_payload(attribution_result: dict[str, Any], *, version: str, session_id: str | None = None) -> dict[str, Any]:
    root = (attribution_result.get("roots") or [{}])[0]
    category = str(root.get("category") or attribution_result.get("primary_category") or "uncertain")
    return_step = {"instruction": 2, "judge": 3, "test_data": 1, "target_model": 3, "uncertain": 5}.get(category, 5)
    result: dict[str, Any] = {
        "version": version,
        "status": "待执行与人工确认",
        "root_category": category,
        "root_label": root.get("label") or attribution_result.get("primary_label"),
        "confidence": root.get("confidence") or attribution_result.get("primary_confidence"),
        "owner": root.get("owner") or "人工复核",
        "evidence": list(root.get("evidence") or []),
        "actions": list(root.get("actions") or []),
        "return_step": return_step,
        "return_reason": {
            1: "测试数据需要补齐或重选，回到材料上传",
            2: "SOP 已变化，需要重新生成评分标准",
            3: "模型或裁判链路已变化，使用同一评分标准重新检查",
            5: "证据不足，先补充人工复核再重新归因",
        }[return_step],
        "regression_acceptance": ["P0 不新增", "目标失败项 fail→pass", "同一检查尺下对比", "低置信结果完成人工复核"],
        "safety_note": "本步骤只生成修复草案和回归请求，不会自动修改生产 SOP 或模型。",
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
        presets[preset_id] = {
            "label": config["label"],
            "tag": config["tag"],
            "cache_run": config["cache_run"],
            "steps": {
                "1": _package_preview(task, loaded.report, loaded.trajectories, scope="历史已验证批次"),
                "2": _checklist_preview(checklist, generation_method="历史运行已审核评分标准", approved=True),
                "3": _evaluation_preview(run_dir),
                "4": _decision_payload(run_dir, config["cache_report"]),
                "5": attribution_result,
                "6": _plan_payload(attribution_result, version=f"cache-{preset_id}-verified"),
            },
        }
    return {
        "schema_version": 2,
        "generated_at": _now(),
        "provenance": "由仓库内真实 task/checklist/transcripts/judgments/summary 生成；不代表浏览器正在调用模型。",
        "presets": presets,
    }


def _create_intake(config: dict[str, Any]) -> dict[str, Any]:
    preset_id = str(config.get("preset") or "real_recruit")
    task_text = str(config.get("task_text") or "")
    transcripts_text = str(config.get("transcripts_text") or "")
    if bool(task_text) != bool(transcripts_text):
        raise ValueError("上传材料时必须同时提供 SOP 和对话记录")
    if len(task_text.encode("utf-8")) > MAX_UPLOAD_BYTES or len(transcripts_text.encode("utf-8")) > MAX_UPLOAD_BYTES:
        raise ValueError("单个上传文件不得超过 2MB")

    session_id = datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]
    session_root = WEB_RUNS / session_id
    input_dir = session_root / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    task_path = input_dir / "task.yaml"

    if task_text:
        task_path.write_text(task_text, encoding="utf-8")
        suffix = Path(str(config.get("transcripts_name") or "uploaded.jsonl")).suffix.lower()
        if suffix not in {".jsonl", ".json", ".csv", ".txt", ".md"}:
            raise ValueError("对话格式仅支持 JSONL / JSON / CSV / TXT / MD")
        raw_transcripts = input_dir / f"uploaded_transcripts{suffix}"
        raw_transcripts.write_text(transcripts_text, encoding="utf-8")
        verified_checklist = None
        source = "用户上传"
        preset_value = None
    else:
        if preset_id not in PRESETS:
            raise ValueError(f"未知样例：{preset_id}")
        preset = PRESETS[preset_id]
        shutil.copyfile(ROOT / preset["task"], task_path)
        raw_transcripts = ROOT / preset["transcripts"]
        verified_checklist = ROOT / preset["verified_checklist"]
        source = "内置演示样例"
        preset_value = preset_id

    task = _load_task(task_path)
    loaded = ingest.load_transcripts(str(raw_transcripts), str(task["task_id"]), redact=True)
    normalized_path = input_dir / "normalized_transcripts.jsonl"
    _write_jsonl(normalized_path, loaded.trajectories)
    _write_json(session_root / "ingestion_report.json", loaded.report)
    package = _package_preview(task, loaded.report, loaded.trajectories, scope="本次本地实时批次", session_id=session_id)
    package.update(source=source, preset=preset_value)
    _write_json(session_root / "task_package.json", package)
    with _lock:
        _sessions[session_id] = {
            "session_id": session_id,
            "root": str(session_root),
            "task_path": str(task_path),
            "transcripts_path": str(normalized_path),
            "verified_checklist": str(verified_checklist) if verified_checklist else None,
            "preset": preset_value,
            "created_at": _now(),
        }
    return package


def _compile_session(session_id: str) -> dict[str, Any]:
    session = _session(session_id)
    task = _load_task(Path(session["task_path"]))
    checklist_path = session.get("verified_checklist")
    try:
        checkpoints = cli._prepare_offline_checkpoints(
            task,
            checklist_path=checklist_path,
            model=os.getenv("EVALCALL_MODEL") or None,
            no_safety=False,
        )
    except SystemExit as exc:
        raise RuntimeError("评分标准生成失败，请检查模型后端或上传的 SOP") from exc
    rows = compiler.checkpoints_to_dicts(checkpoints)
    output_path = Path(session["root"]) / "checklist.json"
    _write_json(output_path, rows)
    method = "读取已审核版本并合并通用安全规则" if checklist_path else "调用当前模型现场编译并校验来源引文"
    result = _checklist_preview(
        rows,
        generation_method=method,
        approved=bool(checklist_path) and not any(row.get("needs_review") for row in rows),
        session_id=session_id,
    )
    _update_session(session_id, checklist_path=str(output_path), compiled_at=_now())
    return result


def _run_evaluation(job_id: str, session_id: str, votes: int) -> None:
    try:
        session = _session(session_id)
        if not session.get("checklist_path"):
            raise ValueError("请先完成第 2 步，生成评分标准")
        output_dir = Path(session["root"]) / "evaluation"
        output_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            sys.executable, "-m", "evalcall", "evaluate",
            "--task", session["task_path"],
            "--transcripts", session["transcripts_path"],
            "--checklist", session["checklist_path"],
            "--out", str(output_dir),
            "--votes", str(votes),
            "--no-resume",
        ]
        _job_update(job_id, status="running", progress=18, stage="正在检查完整批次中的每通电话")
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=1800, env=os.environ.copy())
        log = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()[-16000:]
        if proc.returncode != 0:
            raise RuntimeError(log or f"evalcall 退出码 {proc.returncode}")
        result = _evaluation_preview(output_dir)
        result["report_url"] = f"/session-report/{session_id}"
        _update_session(session_id, output_dir=str(output_dir), evaluated_at=_now())
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
    result = _decision_payload(output_dir, f"/session-report/{session_id}")
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
    result = _plan_payload(attribution_result, version=version, session_id=session_id)
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
            "checklist_sha256": hashlib.sha256(checklist_bytes).hexdigest(),
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
            self._json(
                {
                    "ok": True,
                    "service": "evalcall-six-step-demo",
                    "backend": os.getenv("EVALCALL_BACKEND", "claude-cli"),
                    "model": os.getenv("EVALCALL_MODEL", "default"),
                    "presets": sorted(PRESETS),
                    "real_steps": 6,
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
    WEB_RUNS.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((host, port), DemoHandler)
    print(f"[evalcall] 六步产品工作台：http://{host}:{port}/")
    print("[evalcall] 线上展示读取已验证结果；本地实时模式会依次执行六个真实步骤。Ctrl+C 退出。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
