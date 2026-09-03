from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "evalcall" / "templates" / "report.html.j2"
DEPLOYED_REPORTS = (
    "report-official-gated.html",
    "report-official2-gated.html",
    "report-real-recruit-gpt56sol.html",
    "report-t02-gated.html",
    "report-t02-v2.html",
)
SECTIONS = (
    "report-overview",
    "report-gate",
    "report-attribution",
    "report-audit",
    "report-actions",
    "report-evidence",
)


def assert_report_navigation(html: str) -> None:
    assert 'class="report-nav"' in html
    assert 'aria-label="评测报告章节导航"' in html
    assert "阅读关系" in html
    assert "看结果" in html
    assert "做决策" in html
    assert "定责任" in html
    assert "验可信" in html
    assert "给行动" in html
    assert "可复核" in html

    for section in SECTIONS:
        assert f'data-report-tab="{section}"' in html
        assert f'data-report-logic="{section}"' in html
        assert f'id="{section}"' in html


def test_report_template_defines_clickable_reading_path() -> None:
    html = TEMPLATE.read_text(encoding="utf-8")

    assert_report_navigation(html)
    assert "position:sticky" in html
    assert "scroll-behavior:smooth" in html
    assert "aria-current" in html
    assert "window.addEventListener('scroll'" in html
    assert "window.requestAnimationFrame(sync)" in html


def test_all_deployed_reports_include_the_same_navigation() -> None:
    for report_name in DEPLOYED_REPORTS:
        report = ROOT / "site-deploy" / report_name
        assert report.exists(), report_name
        assert_report_navigation(report.read_text(encoding="utf-8"))
