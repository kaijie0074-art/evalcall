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


def test_all_presets_reference_real_files():
    for preset in demo_server.PRESETS.values():
        for relative in preset.values():
            assert (demo_server.ROOT / relative).is_file(), relative


def test_first_transcript_extracts_exactly_one_jsonl_row(tmp_path):
    source = tmp_path / "source.jsonl"
    target = tmp_path / "target.jsonl"
    source.write_text('\n{"run_id":"one"}\n{"run_id":"two"}\n', encoding="utf-8")
    demo_server._first_transcript(source, target)
    assert target.read_text(encoding="utf-8") == '{"run_id":"one"}\n'


def test_health_root_and_missing_job(demo_http):
    health = _get_json(f"{demo_http}/api/health")
    assert health["ok"] is True
    assert set(health["presets"]) == set(demo_server.PRESETS)
    with urlopen(f"{demo_http}/", timeout=3) as response:  # noqa: S310 - localhost fixture only
        html = response.read().decode("utf-8")
    assert "外呼质检工作台" in html
    assert "SOP to go / no-go decision" in html
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


def test_report_route_cannot_escape_run_root(demo_http):
    outside = Path(demo_server.WEB_RUNS).parent / "secret" / "output"
    outside.mkdir(parents=True)
    (outside / "report.html").write_text("secret", encoding="utf-8")
    with pytest.raises(HTTPError) as error:
        urlopen(f"{demo_http}/job-report/../secret", timeout=3)  # noqa: S310
    assert error.value.code == 404
