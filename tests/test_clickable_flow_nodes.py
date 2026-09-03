from pathlib import Path


APP = Path(__file__).resolve().parents[1] / "site-deploy" / "app.html"


def test_all_flow_nodes_use_the_shared_clickable_inspector() -> None:
    html = APP.read_text(encoding="utf-8")

    assert "const NODE_GUIDE=" in html
    for step in range(1, 7):
        assert f"      {step}:{{" in html

    assert 'type="button" class="flow-node' in html
    assert 'data-flow-node="' in html
    assert 'aria-haspopup="dialog"' in html
    assert "function openNodeDetail(key)" in html
    assert "bindFlowNodes()" in html


def test_every_flow_node_has_a_concrete_implementation_blueprint() -> None:
    html = APP.read_text(encoding="utf-8")
    start = html.index("const NODE_IMPLEMENTATION=")
    end = html.index("const state=", start)
    implementation_block = html[start:end]

    assert implementation_block.count("P('") == 47
    for label in (
        "CODE ENTRY · 真实代码落点",
        "READS · 输入字段",
        "WRITES · 输出字段 / 产物",
        "FAILURE GUARD · 失败兜底",
        "AUTOMATED ACCEPTANCE · 自动验收",
    ):
        assert label in html

    for code_entry in (
        "_create_intake()",
        "compile_task() / compile_task_fast()",
        "UserSimulator.next_reply()",
        "judge_trajectory()",
        "summarize() gate rules",
        "analyze()",
        "replay_trajectory() / compare_manifests()",
    ):
        assert code_entry in implementation_block


def test_primary_root_inspector_exposes_all_attribution_branches() -> None:
    html = APP.read_text(encoding="utf-8")

    assert "四类根因分支与底层原因" in html
    for label in ("外呼模型", "SOP / 任务指令", "裁判", "测试数据"):
        assert label in html
    assert "rootBranchesHtml" in html
    assert "judge_disagreement_rate" in html
    assert "persona_failure_concentration" in html


def test_artifacts_open_inside_the_demo_before_offering_download() -> None:
    html = APP.read_text(encoding="utf-8")

    assert "Artifact viewer · 站内产物查看器" in html
    assert "function openArtifact(url,label)" in html
    assert 'data-artifact-url="' in html
    assert "站内查看 ↗" in html
    assert "备用：下载原文件" in html
    assert "document.querySelectorAll('[data-artifact-url]')" in html
