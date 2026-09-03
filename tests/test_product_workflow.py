from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "site-deploy" / "app.html"
SHELL = ROOT / "site-deploy" / "product-shell.js"
STYLE = ROOT / "site-deploy" / "product-shell.css"


def test_app_exposes_the_complete_user_workflow() -> None:
    html = APP.read_text(encoding="utf-8")

    for view in ("dashboard", "create", "results", "review", "assets", "reports", "engine"):
        assert f'data-product-view="{view}"' in html
        assert f'data-product-nav="{view}"' in html

    for label in (
        "评测任务",
        "新建评测",
        "争议复核",
        "评测资产",
        "报告",
        "运行记录",
        "导出报告",
    ):
        assert label in html


def test_create_flow_freezes_comparison_conditions_before_running() -> None:
    html = APP.read_text(encoding="utf-8")
    javascript = SHELL.read_text(encoding="utf-8")

    assert html.count("data-create-panel=") == 4
    for label in ("基准模型", "候选模型", "测试数据集", "评分标准", "裁判策略"):
        assert label in html
    for condition in ("用户输入", "SOP", "Checklist", "Judge"):
        assert condition in html
    assert "function launchEvaluation()" in javascript
    assert "评测任务已启动" in javascript


def test_review_workbench_supports_evidence_backed_human_actions() -> None:
    html = APP.read_text(encoding="utf-8")
    javascript = SHELL.read_text(encoding="utf-8")

    for capability in ("严重度判断", "可信度校验", "证据排序"):
        assert capability in javascript or capability in html
    for action in ("保存最终结论", "规则待澄清", "转交负责人"):
        assert action in javascript
    assert "handleReviewAction" in javascript
    assert "最终结论已保存" in javascript


def test_product_shell_assets_are_loaded_after_the_selected_theme() -> None:
    html = APP.read_text(encoding="utf-8")

    assert STYLE.exists()
    assert SHELL.exists()
    assert html.index("evalcall-selected-theme.css") < html.index("product-shell.css")
    assert html.index("demo-cache.js") < html.index("product-shell.js")


def test_competition_gateway_publishes_product_shell_assets() -> None:
    gateway = (ROOT / "scripts" / "start_competition_gateway.py").read_text(encoding="utf-8")

    for asset in ("product-shell.css", "product-shell.js"):
        assert f'SITE / "{asset}"' in gateway
        assert f'"{asset}",' in gateway
