"""M4：本地产品工作台服务与安全边界。"""

from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from evalcall import demo_server


@pytest.fixture()
def demo_http(tmp_path, monkeypatch):
    monkeypatch.setattr(demo_server, "WEB_RUNS", tmp_path / "web_live")
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
    assert "生成修复方案" in html
    assert health["real_steps"] == 6
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

    with urlopen(f"{demo_http}{checklist['artifacts']['checklist']}", timeout=3) as response:  # noqa: S310
        rows = json.loads(response.read())
    assert len(rows) == checklist["checkpoints"]


def test_decision_attribution_and_plan_persist_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(demo_server, "WEB_RUNS", tmp_path / "web_live")
    demo_server._sessions.clear()
    package = demo_server._create_intake({"preset": "real_recruit"})
    session_id = package["session_id"]
    demo_server._compile_session(session_id)
    source = demo_server.ROOT / demo_server.PRESETS["real_recruit"]["cache_run"]
    output = Path(demo_server._sessions[session_id]["root"]) / "evaluation"
    output.mkdir()
    for filename in ("summary.json", "judgments.json", "checklist.json", "report.html"):
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
