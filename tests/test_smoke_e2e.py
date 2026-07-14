"""2026-07-02 体检修复的回归测试 + 端到端冒烟。

体检抓到的四类真缺陷，每类钉一颗防复发的钉子：
- A1 grow 崩溃：单测 stub 的 chat_json 签名比真实版宽松，掩盖了漏传 schema_hint。
  本文件的 grow 测试只打桩 llm.get_backend（传输层），让**真实的 chat_json 签名**参与执行。
- A2 run_id 双轨断裂：走真实 cmd_run 接线（只桩 编译/对话/判定 三个 LLM 缝），
  断言 transcripts 与 judgments 的 run_id 一致、报告锚点两端能对上。
- A3 门禁 fail-open：全 na 必须判"无法判定"，聚合层同理。
- A4 --checklist 白名单剥 safety：走 --checklist 通道断言 safety/policy_source 存活。

运行：python3 -m pytest tests/test_smoke_e2e.py -q
"""
from __future__ import annotations

import json
import os
import re
import sys

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from evalcall import arena, cli, compiler, grow, judge, lint, llm, report  # noqa: E402


def test_arena_emits_progress_only_after_real_model_events(tmp_path, monkeypatch):
    progress_path = tmp_path / "live-progress.json"
    monkeypatch.setenv("EVALCALL_PROGRESS_FILE", str(progress_path))
    monkeypatch.setattr(arena, "target_chat", lambda messages: "外呼模型真实回复")

    class _Simulator:
        strategy_log = []

        def __init__(self, **kwargs):
            pass

        def next_reply(self, turns):
            return "用户模拟器真实回应", False

    monkeypatch.setattr(arena, "UserSimulator", _Simulator)
    trajectory = arena.run_dialogue(
        {"task_id": "t", "instruction": "完成外呼任务"},
        {"persona_id": "p"},
        max_turns=1,
    )
    payload = json.loads(progress_path.read_text(encoding="utf-8"))
    assert payload["stage"] == "target_reply"
    assert payload["completed_steps"] == payload["total_steps"] == 3
    assert trajectory["turns"][-1]["content"] == "外呼模型真实回复"


# =========================================================================== #
# A1：grow 走真实 chat_json 签名（只桩传输层，签名不匹配会当场 TypeError）
# =========================================================================== #
class _FakeBackend:
    """假传输层：get_backend().chat() 返回固定 JSON 字符串。"""

    def __init__(self, payload):
        self._payload = payload

    def chat(self, messages, model=None):
        return json.dumps(self._payload, ensure_ascii=False)


class TestGrowRealSignature:
    def test_mine_candidates_through_real_chat_json(self, monkeypatch):
        task = {"id": "t", "instruction": "开场必须播报录音提示。严禁承诺具体赔偿金额。"}
        payload = {
            "candidates": [
                {"type": "flow", "text": "开场播报录音提示",
                 "source_quote": "开场必须播报录音提示", "severity": "major"},
                {"type": "forbidden", "text": "凭空编造的检查点",
                 "source_quote": "这句话不在指令原文里", "severity": "minor"},
            ]
        }
        monkeypatch.setattr(llm, "get_backend", lambda: _FakeBackend(payload))
        result = grow.mine_candidates(task, existing_checkpoints=[])
        # 曾经的 bug：chat_json 漏传 schema_hint，恒走 except 分支返回 error
        assert "error" not in result, f"mine_candidates 报错：{result.get('error')}"
        assert len(result["accepted"]) == 1
        assert result["accepted"][0]["needs_confirm"] is True
        # 溯源硬闸仍然生效：无源候选被拦
        assert any("溯源" in r.get("_reason", "") for r in result["rejected"])


# =========================================================================== #
# A3：门禁 fail-closed——判定通道故障（全/多数 na）不得输出"可上线"
# =========================================================================== #
def _cp(cid, severity="major", **kw):
    d = {"id": cid, "type": kw.pop("type", "constraint"), "text": f"检查点{cid}",
         "source_quote": "…", "severity": severity}
    d.update(kw)
    return d


class TestGateFailClosed:
    def test_instruction_lint_backend_failure_is_not_reported_as_healthy(self, monkeypatch):
        def fail_chat_json(*args, **kwargs):
            raise llm.LLMError("codex-cli 401 Unauthorized")

        monkeypatch.setattr(llm, "chat_json", fail_chat_json)
        task = {"id": "lint_fail_closed", "instruction": "必须问候并说明来意。"}
        with pytest.raises(llm.LLMError, match="401 Unauthorized"):
            lint.lint_instruction(task)

    def test_all_na_gate_undecidable(self):
        cps = [_cp("c1"), _cp("c2", severity="critical")]
        judgments = [
            {"checkpoint_id": "c1", "verdict": "na", "confidence": 0.0},
            {"checkpoint_id": "c2", "verdict": "na", "confidence": 0.0},
        ]
        s = judge.summarize(cps, judgments)
        assert s["gate"] == "无法判定"

    def test_majority_na_gate_undecidable(self):
        cps = [_cp(f"c{i}") for i in range(4)]
        judgments = [
            {"checkpoint_id": "c0", "verdict": "pass", "confidence": 0.9},
            {"checkpoint_id": "c1", "verdict": "na", "confidence": 0.0},
            {"checkpoint_id": "c2", "verdict": "na", "confidence": 0.0},
            {"checkpoint_id": "c3", "verdict": "na", "confidence": 0.0},
        ]
        s = judge.summarize(cps, judgments)
        assert s["gate"] == "无法判定"

    def test_normal_pass_still_deployable(self):
        cps = [_cp("c1"), _cp("c2")]
        judgments = [
            {"checkpoint_id": "c1", "verdict": "pass", "confidence": 0.9},
            {"checkpoint_id": "c2", "verdict": "pass", "confidence": 0.9},
        ]
        s = judge.summarize(cps, judgments)
        assert s["gate"] == "可上线"

    def test_aggregate_undecided_not_deployable(self):
        base = {"score": 0.0, "raw_score": 0.0, "critical_failed": False,
                "gate_reasons": [], "violation_rate_per_100": 0.0,
                "judge_disagreement_rate": 0.0, "fulfillment": {"pass": 0, "fail": 0, "na": 0},
                "persona_id": "p"}
        per_run = [
            {**base, "run_id": "r1", "gate": "可上线"},
            {**base, "run_id": "r2", "gate": "无法判定"},
        ]
        overall = cli._aggregate_summary(per_run, "t", [])
        assert overall["gate"] == "无法判定"

    def test_aggregate_blocked_wins(self):
        base = {"score": 0.0, "raw_score": 0.0, "critical_failed": False,
                "gate_reasons": [], "violation_rate_per_100": 0.0,
                "judge_disagreement_rate": 0.0, "fulfillment": {"pass": 0, "fail": 0, "na": 0},
                "persona_id": "p"}
        per_run = [
            {**base, "run_id": "r1", "gate": "无法判定"},
            {**base, "run_id": "r2", "gate": "打回"},
        ]
        overall = cli._aggregate_summary(per_run, "t", [])
        assert overall["gate"] == "打回"


# =========================================================================== #
# A2 + A4 + A5：端到端冒烟（真实 cmd_run / build_report 接线，只桩 LLM 缝）
# =========================================================================== #
_UUID_LIKE = "deadbeef0001"


def _stub_compile_task(task, model=None):
    return [
        compiler.Checkpoint(id="flow_1", type="flow", text="开场播报录音提示",
                            source_quote="开场必须播报录音提示", severity="major"),
        compiler.Checkpoint(id="forbidden_1", type="forbidden", text="不得承诺具体赔偿金额",
                            source_quote="严禁承诺具体赔偿金额", severity="critical"),
    ]


def _stub_run_dialogue(task, persona, checkpoints=None, max_turns=12,
                       seed=None, adversarial=True, priority_targets=None):
    # 模拟 arena 真实行为：自带 uuid 型 run_id（曾因 setdefault 不覆盖导致双轨断裂）
    return {
        "run_id": _UUID_LIKE,
        "task_id": task.get("task_id", "arena_side"),
        "persona_id": persona.get("id", "arena_side"),
        "turns": [
            {"role": "agent", "content": "您好，这里是外卖客服，通话将录音。", "turn": 1},
            {"role": "user", "content": "你赔我多少钱？", "turn": 2},
            {"role": "agent", "content": "赔您 500 元。", "turn": 3},
        ],
        "meta": {"persona_label": persona.get("id", "p"), "task_label": "冒烟任务"},
    }


def _stub_judge_trajectory(checkpoints, trajectory, model=None, n_votes=1):
    out = []
    for cp in checkpoints:
        cpd = cp if isinstance(cp, dict) else cp.to_dict()
        if cpd["id"] == "forbidden_1":
            out.append({"checkpoint_id": cpd["id"], "verdict": "fail", "confidence": 0.95,
                        "evidence": [{"turn": 3, "quote": "赔您 500 元。"}]})
        else:
            out.append({"checkpoint_id": cpd["id"], "verdict": "pass", "confidence": 0.9,
                        "evidence": [{"turn": 1, "quote": "通话将录音"}]})
    return out


@pytest.fixture()
def smoke_run(tmp_path, monkeypatch):
    """在临时目录里走一次完整 cmd_run → build_report，返回 run 目录。"""
    monkeypatch.setattr(compiler, "compile_task", _stub_compile_task)
    monkeypatch.setattr(arena, "run_dialogue", _stub_run_dialogue)
    monkeypatch.setattr(judge, "judge_trajectory", _stub_judge_trajectory)

    task_yaml = tmp_path / "task_smoke.yaml"
    task_yaml.write_text(
        "id: smoke\ntask_id: smoke\ninstruction: 开场必须播报录音提示。严禁承诺具体赔偿金额。\n"
        "goal: 安抚用户并解释赔付流程\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "run_out"
    cli.main([
        "run", "--task", str(task_yaml),
        "--personas", "p01_cooperative_worker,p02_impatient",
        "--n", "2", "--no-mix", "--no-safety", "--out", str(out_dir),
    ])
    return out_dir


class TestEndToEndSmoke:
    def test_run_id_consistent_across_artifacts(self, smoke_run):
        with open(smoke_run / "transcripts.jsonl", encoding="utf-8") as f:
            tx_ids = [json.loads(line)["run_id"] for line in f if line.strip()]
        judgments = json.loads((smoke_run / "judgments.json").read_text(encoding="utf-8"))
        j_ids = {j["run_id"] for j in judgments}
        assert tx_ids, "冒烟 run 没产出任何轨迹"
        # 轨迹侧不得残留 arena 内部 uuid，必须是 CLI 的复合 ID
        assert _UUID_LIKE not in tx_ids
        assert all(re.match(r"smoke__p\d+_\w+__\d+$", rid) for rid in tx_ids)
        # 两份产物 ID 完全一致
        assert set(tx_ids) == j_ids

    def test_report_anchor_pairs_match(self, smoke_run):
        html_path = report.build_report(str(smoke_run))
        html = open(html_path, encoding="utf-8").read()
        hrefs = set(re.findall(r'href="#call-([^"]+)"', html))
        ids = set(re.findall(r'id="call-([^"]+)"', html))
        assert hrefs, "报告里没有通话级跳转链接"
        # 每个跳转链接都必须有对应的回放锚点（曾因两套 run_id 永不匹配）
        assert hrefs <= ids, f"断链锚点：{hrefs - ids}"

    def test_case_study_uses_correct_trajectory(self, smoke_run):
        """n=2 时案例回放必须能区分同 (task,persona) 的多条轨迹（曾互相覆盖）。"""
        transcripts = [json.loads(l) for l in open(smoke_run / "transcripts.jsonl", encoding="utf-8") if l.strip()]
        judgments = json.loads((smoke_run / "judgments.json").read_text(encoding="utf-8"))
        model = report._aggregate(transcripts, judgments, None)
        for case in model["case_studies"]:
            assert case["run_id"] in {t["run_id"] for t in transcripts}

    def test_checklist_channel_keeps_safety(self, tmp_path, monkeypatch):
        """--checklist 通道不得剥掉 safety/policy_source（安全红线降级缺陷）。"""
        monkeypatch.setattr(arena, "run_dialogue", _stub_run_dialogue)
        monkeypatch.setattr(judge, "judge_trajectory", _stub_judge_trajectory)

        checklist = [
            {"id": "flow_1", "type": "flow", "text": "开场播报录音提示",
             "source_quote": "开场必须播报录音提示", "severity": "major"},
            {"id": "forbidden_1", "type": "forbidden", "text": "不得辱骂用户",
             "source_quote": "policy", "severity": "major",
             "safety": True, "policy_source": "safety_redlines.yaml#abuse"},
        ]
        cl_path = tmp_path / "checklist.json"
        cl_path.write_text(json.dumps(checklist, ensure_ascii=False), encoding="utf-8")
        task_yaml = tmp_path / "task_smoke.yaml"
        task_yaml.write_text(
            "id: smoke\ntask_id: smoke\ninstruction: 开场必须播报录音提示。\n", encoding="utf-8")
        out_dir = tmp_path / "run_cl"
        cli.main([
            "run", "--task", str(task_yaml), "--checklist", str(cl_path),
            "--personas", "p01_cooperative_worker", "--n", "1",
            "--no-mix", "--no-safety", "--out", str(out_dir),
        ])
        saved = json.loads((out_dir / "checklist.json").read_text(encoding="utf-8"))
        by_id = {c["id"]: c for c in saved}
        assert by_id["forbidden_1"].get("safety") is True
        assert by_id["forbidden_1"].get("policy_source", "").startswith("safety_redlines")
        # 扁平 judgments 也要携带 safety（报告通话级 P0 标记消费它）
        judgments = json.loads((out_dir / "judgments.json").read_text(encoding="utf-8"))
        j = next(x for x in judgments if x["checkpoint_id"] == "forbidden_1")
        assert j.get("safety") is True


class TestPersonaResolution:
    def test_all_excludes_mix_config(self):
        """--personas all 不得把配比配置文件 mix.yaml 当 persona 加载（2026-07-02 真实重跑踩坑）。"""
        personas = cli._resolve_personas("all")
        ids = {p.get("id") for p in personas}
        assert "mix" not in ids
        assert len(personas) == 6
        assert all("weights" not in p for p in personas)
