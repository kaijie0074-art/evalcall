"""M4：本地产品工作台服务与安全边界。"""

from __future__ import annotations

import json
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from evalcall import demo_server


@pytest.fixture()
def demo_http(tmp_path, monkeypatch):
    monkeypatch.setattr(demo_server, "WEB_RUNS", tmp_path / "web_live")
    monkeypatch.setattr(
        demo_server,
        "_backend_status",
        {
            "checked": True,
            "checking": False,
            "available": True,
            "checked_at": "2026-07-13T00:00:00",
            "backend": "mock",
            "model": "mock-model",
            "error": None,
        },
    )
    demo_server._jobs.clear()
    demo_server._sessions.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), demo_server.DemoHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def _get_json(url: str) -> dict:
    with urlopen(url, timeout=3) as response:  # noqa: S310 - localhost fixture only
        return json.loads(response.read())


def _post_json(url: str, payload: dict) -> dict:
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=10) as response:  # noqa: S310 - localhost fixture only
        return json.loads(response.read())


def test_all_presets_reference_real_files():
    for preset in demo_server.PRESETS.values():
        for key in ("task", "transcripts", "verified_checklist"):
            relative = preset[key]
            assert (demo_server.ROOT / relative).is_file(), relative
        assert (demo_server.ROOT / preset["cache_run"]).is_dir()
        assert (demo_server.ROOT / preset["live_verified_run"]).is_dir()
        assert (demo_server.SITE / preset["cache_report"]).is_file()


def test_live_presets_keep_complete_batches():
    expected = {"official01": 12, "official02": 10, "t02": 10, "real_recruit": 10}
    for preset_id, count in expected.items():
        path = demo_server.ROOT / demo_server.PRESETS[preset_id]["transcripts"]
        assert len([line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]) == count


def test_judgment_samples_prioritize_fail_and_strip_to_preview(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    (output / "judgments.json").write_text(
        json.dumps(
            [
                {"checkpoint_id": "pass_1", "severity": "major", "verdict": "pass", "confidence": 0.9, "evidence": []},
                {
                    "checkpoint_id": "fail_1",
                    "severity": "critical",
                    "verdict": "fail",
                    "confidence": 0.99,
                    "evidence": [{"turn": 2, "quote": "逐字证据"}],
                    "votes": [{"raw": "不得进入预览"}],
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    samples = demo_server._judgment_samples(output, limit=1)
    assert samples == [
        {
            "run_id": "unknown",
            "checkpoint_id": "fail_1",
            "checkpoint_text": "fail_1",
            "severity": "critical",
            "verdict": "fail",
            "confidence": 0.99,
            "evidence": "逐字证据",
        }
    ]


def test_health_root_and_missing_job(demo_http):
    health = _get_json(f"{demo_http}/api/health")
    assert health["ok"] is True
    assert set(health["presets"]) == set(demo_server.PRESETS)
    with urlopen(f"{demo_http}/", timeout=3) as response:  # noqa: S310 - localhost fixture only
        html = response.read().decode("utf-8")
    assert "外呼模型评测工作台" in html
    assert "输入 → 执行 → 输出" in html
    assert "生成优化与回归计划" in html
    assert "模拟测试模式" in html
    assert "已有日志质检模式" in html
    assert health["real_steps"] == 6
    assert health["backend_available"] is True
    assert health["backend_checked"] is True
    assert health["backend_checking"] is False
    with pytest.raises(HTTPError) as error:
        urlopen(f"{demo_http}/api/jobs/not-found", timeout=3)  # noqa: S310
    assert error.value.code == 404


def test_post_rejects_unknown_route_and_invalid_payload(demo_http):
    bad_route = Request(f"{demo_http}/api/nope", data=b"{}", method="POST")
    with pytest.raises(HTTPError) as error:
        urlopen(bad_route, timeout=3)  # noqa: S310
    assert error.value.code == 404

    invalid = Request(
        f"{demo_http}/api/evaluate",
        data=b"[]",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(HTTPError) as error:
        urlopen(invalid, timeout=3)  # noqa: S310
    assert error.value.code == 400


def test_intake_and_compile_are_real_api_steps(demo_http):
    intake = _post_json(f"{demo_http}/api/intake", {"preset": "t02"})
    assert intake["ok"] is True
    package = intake["result"]
    assert package["conversations"] == 10
    assert package["turns"] >= 10
    assert package["pii_redacted"] is True
    assert package["test_mode"] == "simulation"
    assert package["target_model_version"] == "delivery-baseline-v1"
    assert package["persona_count"] > 0
    assert package["hashes"]["sop_sha256"]

    compiled = _post_json(
        f"{demo_http}/api/compile",
        {"session_id": package["session_id"]},
    )
    assert compiled["ok"] is True
    checklist = compiled["result"]
    assert checklist["checkpoints"] > 0
    assert checklist["l0_common_rules"] > 0
    assert checklist["l1_sop_rules"] > 0
    assert "已审核版本" in checklist["generation_method"]
    assert checklist["checklist_sha256"]

    with urlopen(f"{demo_http}{checklist['artifacts']['checklist']}", timeout=3) as response:  # noqa: S310
        rows = json.loads(response.read())
    assert len(rows) == checklist["checkpoints"]


def test_uploaded_builtin_sample_is_recognized_and_reuses_verified_checklist(tmp_path, monkeypatch):
    monkeypatch.setattr(demo_server, "WEB_RUNS", tmp_path / "web_live")
    demo_server._sessions.clear()
    preset = demo_server.PRESETS["official01"]
    package = demo_server._create_intake(
        {
            "preset": "t02",
            "task_text": (demo_server.ROOT / preset["task"]).read_text(encoding="utf-8"),
            "transcripts_text": (demo_server.ROOT / preset["transcripts"]).read_text(encoding="utf-8"),
            "transcripts_name": "01_骑手合同生效通知_对话_12通.jsonl",
        }
    )
    assert package["preset"] == "official01"
    assert package["recognized_sample"] is True
    assert package["source"].startswith("样例文件")
    session = demo_server._sessions[package["session_id"]]
    assert session["verified_checklist"].endswith("official01_gated_20260702/checklist.json")
    compiled = demo_server._compile_session(package["session_id"])
    assert "已审核版本" in compiled["generation_method"]


def test_changed_uploaded_sample_is_not_treated_as_verified(tmp_path, monkeypatch):
    monkeypatch.setattr(demo_server, "WEB_RUNS", tmp_path / "web_live")
    demo_server._sessions.clear()
    preset = demo_server.PRESETS["t02"]
    transcripts = (demo_server.ROOT / preset["transcripts"]).read_text(encoding="utf-8")
    assert "地址不用改" in transcripts
    package = demo_server._create_intake(
        {
            "task_text": (demo_server.ROOT / preset["task"]).read_text(encoding="utf-8"),
            "transcripts_text": transcripts.replace("地址不用改", "地址需要改", 1),
            "transcripts_name": "changed.jsonl",
        }
    )
    assert package["recognized_sample"] is False
    assert package["evaluation_strategy"] == "调用当前模型现场评测"
    assert demo_server._sessions[package["session_id"]]["verified_run"] is None


def test_custom_simulation_accepts_sop_without_transcripts(tmp_path, monkeypatch):
    monkeypatch.setattr(demo_server, "WEB_RUNS", tmp_path / "web_live")
    demo_server._sessions.clear()
    preset = demo_server.PRESETS["t02"]
    package = demo_server._create_intake(
        {
            "test_mode": "simulation",
            "task_text": (demo_server.ROOT / preset["task"]).read_text(encoding="utf-8") + "\n# custom\n",
            "target_model": "gpt-5.6-sol",
            "test_count": 6,
        }
    )
    assert package["test_mode"] == "simulation"
    assert package["conversations"] == 6
    assert package["simulator_generated_calls"] == 6
    assert "normalized_transcripts" not in package["artifacts"]
    session = demo_server._sessions[package["session_id"]]
    assert session["transcripts_path"] is None
    assert session["simulator_n"] * len(session["personas"].split(",")) == 6


def test_logs_mode_requires_both_sop_and_transcripts(tmp_path, monkeypatch):
    monkeypatch.setattr(demo_server, "WEB_RUNS", tmp_path / "web_live")
    demo_server._sessions.clear()
    preset = demo_server.PRESETS["t02"]
    with pytest.raises(ValueError, match="同时提供"):
        demo_server._create_intake(
            {
                "test_mode": "logs",
                "task_text": (demo_server.ROOT / preset["task"]).read_text(encoding="utf-8"),
            }
        )


def test_exact_sample_evaluation_replays_verified_batch_without_model(tmp_path, monkeypatch):
    monkeypatch.setattr(demo_server, "WEB_RUNS", tmp_path / "web_live")
    demo_server._sessions.clear()
    demo_server._jobs.clear()
    package = demo_server._create_intake({"preset": "official01"})
    session_id = package["session_id"]
    demo_server._compile_session(session_id)
    job_id = "test-replay"
    demo_server._jobs[job_id] = {"job_id": job_id, "session_id": session_id, "status": "queued"}

    def fail_if_called(*args, **kwargs):
        raise AssertionError("exact sample replay must not call a model subprocess")

    monkeypatch.setattr(demo_server.subprocess, "run", fail_if_called)
    demo_server._run_evaluation(job_id, session_id, votes=1)
    job = demo_server._jobs[job_id]
    assert job["status"] == "completed"
    assert job["result"]["execution_mode"] == "verified_exact_replay"
    assert job["result"]["total_runs"] == 12
    assert (Path(demo_server._sessions[session_id]["output_dir"]) / "report.html").is_file()


@pytest.mark.parametrize(
    ("preset_id", "expected_runs"),
    [("official01", 12), ("t02", 10), ("real_recruit", 10), ("official02", 10)],
)
def test_every_selectable_sample_completes_all_six_steps_without_live_model(
    preset_id, expected_runs, tmp_path, monkeypatch
):
    monkeypatch.setattr(demo_server, "WEB_RUNS", tmp_path / "web_live")
    demo_server._sessions.clear()
    demo_server._jobs.clear()
    package = demo_server._create_intake({"preset": preset_id})
    session_id = package["session_id"]
    checklist = demo_server._compile_session(session_id)
    assert checklist["checkpoints"] > 0
    checklist_path = Path(demo_server._sessions[session_id]["checklist_path"])
    checklist_before = checklist_path.read_bytes()
    job_id = f"test-{preset_id}"
    demo_server._jobs[job_id] = {"job_id": job_id, "session_id": session_id, "status": "queued"}

    def fail_if_called(*args, **kwargs):
        raise AssertionError("bundled samples must use their exact-input verified run")

    monkeypatch.setattr(demo_server.subprocess, "run", fail_if_called)
    demo_server._run_evaluation(job_id, session_id, votes=1)
    assert demo_server._jobs[job_id]["status"] == "completed"
    assert demo_server._jobs[job_id]["result"]["total_runs"] == expected_runs
    assert demo_server._decision_session(session_id)["total_runs"] == expected_runs
    root = demo_server._attribution_session(session_id)
    assert root["roots"]
    plan = demo_server._plan_session(session_id)
    assert plan["actions"]
    assert checklist_path.read_bytes() == checklist_before
    if preset_id == "t02":
        assert root["primary_category"] == "target_model"
        assert plan["return_step"] == 3
        assert plan["root_category"] == "target_model"
        assert plan["optimization_target"] == "外呼模型与对话策略"
        assert plan["sop_changed"] is False
        assert plan["checklist_changed"] is False
        assert plan["sop_sha256_before"] == plan["sop_sha256_for_regression"]
        assert plan["checklist_sha256_before"] == plan["checklist_sha256_for_regression"]
    if preset_id == "official02":
        assert root["primary_category"] == "instruction"
        assert plan["return_step"] == 2


def test_uploaded_sample_completes_six_http_steps_and_report(demo_http):
    preset = demo_server.PRESETS["official01"]
    intake = _post_json(
        f"{demo_http}/api/intake",
        {
            "task_text": (demo_server.ROOT / preset["task"]).read_text(encoding="utf-8"),
            "transcripts_text": (demo_server.ROOT / preset["transcripts"]).read_text(encoding="utf-8"),
            "transcripts_name": "01_骑手合同生效通知_对话_12通.jsonl",
        },
    )["result"]
    assert intake["preset"] == "official01"
    session_id = intake["session_id"]
    assert _post_json(f"{demo_http}/api/compile", {"session_id": session_id})["ok"] is True
    started = _post_json(f"{demo_http}/api/evaluate", {"session_id": session_id, "votes": 1})
    job_id = started["job_id"]
    for _ in range(100):
        job = _get_json(f"{demo_http}/api/jobs/{job_id}")["job"]
        if job["status"] in {"completed", "failed"}:
            break
        time.sleep(0.02)
    assert job["status"] == "completed"
    assert job["result"]["execution_mode"] == "verified_exact_replay"
    assert _post_json(f"{demo_http}/api/decision", {"session_id": session_id})["ok"] is True
    assert _post_json(f"{demo_http}/api/attribution", {"session_id": session_id})["ok"] is True
    assert _post_json(f"{demo_http}/api/plan", {"session_id": session_id})["ok"] is True
    with urlopen(f"{demo_http}/session-report/{session_id}", timeout=3) as response:  # noqa: S310
        assert response.status == 200
        assert "EvalCall" in response.read().decode("utf-8")


def test_decision_attribution_and_plan_persist_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(demo_server, "WEB_RUNS", tmp_path / "web_live")
    demo_server._sessions.clear()
    package = demo_server._create_intake({"preset": "real_recruit"})
    session_id = package["session_id"]
    demo_server._compile_session(session_id)
    source = demo_server.ROOT / demo_server.PRESETS["real_recruit"]["cache_run"]
    output = Path(demo_server._sessions[session_id]["root"]) / "evaluation"
    output.mkdir()
    for filename in ("summary.json", "judgments.json", "checklist.json", "transcripts.jsonl", "report.html"):
        (output / filename).write_bytes((source / filename).read_bytes())
    demo_server._update_session(session_id, output_dir=str(output), evaluated_at=demo_server._now())

    decision = demo_server._decision_session(session_id)
    assert decision["top_problems"]
    assert decision["report_url"].endswith(session_id)
    root = demo_server._attribution_session(session_id)
    assert root["primary_label"] == "SOP/任务指令"
    plan = demo_server._plan_session(session_id)
    assert plan["return_step"] == 2
    session_root = Path(demo_server._sessions[session_id]["root"])
    for filename in ("decision.json", "attribution.json", "repair_plan.json", "regression_request.json", "revision_draft.md"):
        assert (session_root / filename).is_file()


def test_report_route_cannot_escape_run_root(demo_http):
    outside = Path(demo_server.WEB_RUNS).parent / "secret" / "output"
    outside.mkdir(parents=True)
    (outside / "report.html").write_text("secret", encoding="utf-8")
    with pytest.raises(HTTPError) as error:
        urlopen(f"{demo_http}/job-report/../secret", timeout=3)  # noqa: S310
    assert error.value.code == 404
