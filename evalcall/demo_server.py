"""EvalCall 本地产品演示服务器（零额外依赖）。

静态缓存模式可直接打开 site-deploy/app.html；本服务器额外提供：
- 四步式界面的 health 检查；
- 对内置样例或用户上传内容启动真实 `evalcall evaluate`；
- 轮询任务状态并在完成后安全地打开该次 report.html。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import uuid
from datetime import datetime
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site-deploy"
WEB_RUNS = ROOT / "runs" / "web_live"
MAX_UPLOAD_BYTES = 2 * 1024 * 1024

PRESETS: dict[str, dict[str, str]] = {
    "official01": {
        "task": "data/tasks_official/official_01_feimaotui.yaml",
        "transcripts": "runs/official01_gated_20260702/transcripts.jsonl",
        "checklist": "runs/official01_gated_20260702/checklist.json",
    },
    "official02": {
        "task": "data/tasks_official/official_02_lowlatency.yaml",
        "transcripts": "runs/official02_gated_20260702/transcripts.jsonl",
        "checklist": "runs/official02_gated_20260702/checklist.json",
    },
    "t02": {
        "task": "data/tasks/t02_delivery_reschedule.yaml",
        "transcripts": "runs/t02_gated_20260702/transcripts.jsonl",
        "checklist": "runs/t02_gated_20260702/checklist.json",
    },
    "real_recruit": {
        "task": "data/tasks_real/real_recruit_rider.yaml",
        "transcripts": "runs/real_recruit_20260702/transcripts.jsonl",
        "checklist": "runs/real_recruit_20260702/checklist.json",
    },
}

_jobs: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def _job_update(job_id: str, **values: Any) -> None:
    with _lock:
        _jobs[job_id].update(values)


def _first_transcript(source: Path, destination: Path) -> None:
    for line in source.read_text(encoding="utf-8").splitlines():
        if line.strip():
            destination.write_text(line.strip() + "\n", encoding="utf-8")
            return
    raise ValueError(f"对话文件为空：{source}")


def _run_job(job_id: str, config: dict[str, Any]) -> None:
    job_root = WEB_RUNS / job_id
    input_dir = job_root / "input"
    output_dir = job_root / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        preset = str(config.get("preset") or "official01")
        uploaded_task = str(config.get("task_text") or "")
        uploaded_transcripts = str(config.get("transcripts_text") or "")
        if len(uploaded_task.encode("utf-8")) > MAX_UPLOAD_BYTES or len(uploaded_transcripts.encode("utf-8")) > MAX_UPLOAD_BYTES:
            raise ValueError("单个上传文件不得超过 2MB")

        if uploaded_task and uploaded_transcripts:
            task_path = input_dir / "uploaded_task.yaml"
            transcripts_path = input_dir / "uploaded_transcripts.jsonl"
            task_path.write_text(uploaded_task, encoding="utf-8")
            transcripts_path.write_text(uploaded_transcripts, encoding="utf-8")
            checklist_path = None
            source_label = "uploaded"
        else:
            if preset not in PRESETS:
                raise ValueError(f"未知样例：{preset}")
            row = PRESETS[preset]
            task_path = ROOT / row["task"]
            transcripts_path = input_dir / "single_transcript.jsonl"
            _first_transcript(ROOT / row["transcripts"], transcripts_path)
            checklist_path = ROOT / row["checklist"]
            source_label = preset

        votes = max(1, min(3, int(config.get("votes") or 1)))
        cmd = [
            sys.executable, "-m", "evalcall", "evaluate",
            "--task", str(task_path),
            "--transcripts", str(transcripts_path),
            "--out", str(output_dir),
            "--votes", str(votes),
            "--no-resume",
        ]
        if checklist_path:
            cmd += ["--checklist", str(checklist_path)]
        _job_update(job_id, status="running", progress=20, stage="正在校验输入并准备检查尺")
        proc = subprocess.run(
            cmd,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=1800,
            env=os.environ.copy(),
        )
        log = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()[-12000:]
        if proc.returncode != 0:
            raise RuntimeError(log or f"evalcall 退出码 {proc.returncode}")
        summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
        _job_update(
            job_id,
            status="completed",
            progress=100,
            stage="评测与报告已完成",
            source=source_label,
            report_url=f"/job-report/{job_id}",
            summary={
                key: summary.get(key)
                for key in ("gate", "total_runs", "avg_score", "blocked_runs", "fulfillment_rate", "review_queue_count", "attribution", "runtime")
            },
            log=log,
        )
    except Exception as exc:  # noqa: BLE001
        _job_update(job_id, status="failed", progress=100, stage="评测失败", error=str(exc), log=str(exc))


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

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self._json(
                {
                    "ok": True,
                    "service": "evalcall-demo",
                    "backend": os.getenv("EVALCALL_BACKEND", "claude-cli"),
                    "model": os.getenv("EVALCALL_MODEL", "default"),
                    "presets": sorted(PRESETS),
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
        if parsed.path.startswith("/job-report/"):
            job_id = parsed.path.rsplit("/", 1)[-1]
            report = (WEB_RUNS / job_id / "output" / "report.html").resolve()
            if WEB_RUNS.resolve() not in report.parents or not report.is_file():
                self.send_error(HTTPStatus.NOT_FOUND, "report not found")
                return
            body = report.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/":
            self.path = "/app.html"
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/api/evaluate":
            self._json({"ok": False, "error": "not_found"}, HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
            if length <= 0 or length > MAX_UPLOAD_BYTES * 2 + 100_000:
                raise ValueError("请求过大或为空")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("请求必须是 JSON 对象")
        except Exception as exc:  # noqa: BLE001
            self._json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        job_id = datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]
        with _lock:
            _jobs[job_id] = {
                "job_id": job_id,
                "status": "queued",
                "progress": 5,
                "stage": "已入队",
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
        threading.Thread(target=_run_job, args=(job_id, data), daemon=True).start()
        self._json({"ok": True, "job_id": job_id}, HTTPStatus.ACCEPTED)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[demo] {self.address_string()} {fmt % args}")


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    WEB_RUNS.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((host, port), DemoHandler)
    print(f"[evalcall] 产品演示页：http://{host}:{port}/")
    print("[evalcall] 静态缓存可直接演示；实时模式使用当前 EVALCALL_BACKEND/EVALCALL_MODEL。Ctrl+C 退出。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
