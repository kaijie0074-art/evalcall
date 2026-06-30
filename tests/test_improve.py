"""改进项(P0-P4)的回归测试。

与 test_core.py 不同：本文件允许打桩 LLM 调用缝（judge.llm.chat_json 等），
用于验证不依赖真实模型的"接线/逻辑"是否正确（如默认票数、门禁分支、分层统计）。
不发起任何真实网络/子进程调用。

运行：python3 -m pytest tests/test_improve.py -q
"""
from __future__ import annotations

import os
import sys

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from evalcall import cli, compiler, judge, llm  # noqa: E402


def _stub_chat_json(verdict="pass", confidence=0.9):
    """返回一个假的 chat_json：对收到的每个检查点回一条结果。"""
    def _fake(messages, schema_hint=None, model=None):
        # 从 user 消息里粗略捞出 id=xxx 形式的检查点 id
        user = messages[-1]["content"]
        ids = []
        for tok in user.split():
            if tok.startswith("id="):
                ids.append(tok[3:].split("|")[0].strip())
        return {
            "results": [
                {"checkpoint_id": cid, "verdict": verdict, "confidence": confidence,
                 "evidence": [{"turn": 1, "quote": "示例证据"}]}
                for cid in ids
            ]
        }
    return _fake


def _mk_traj():
    return {
        "run_id": "t__p__1", "task_id": "t", "persona_id": "p",
        "turns": [
            {"role": "agent", "content": "您好，这里是外卖客服。", "turn": 1},
            {"role": "user", "content": "好的。", "turn": 2},
        ],
    }


# =========================================================================== #
# P0-1：标准 run 默认 3 票（旗舰多数投票不再退化成单票）
# =========================================================================== #
class TestDefaultVotes:
    def test_parser_default_votes_is_3(self):
        parser = cli.build_parser()
        args = parser.parse_args(["run", "--task", "x.yaml"])
        assert args.votes == 3

    def test_judge_trajectory_3_votes(self, monkeypatch):
        monkeypatch.setattr(judge.llm, "chat_json", _stub_chat_json(verdict="pass"))
        cps = [
            compiler.Checkpoint(id="c1", type="flow", text="要播报订单号",
                                source_quote="须播报订单号", severity="major"),
            compiler.Checkpoint(id="c2", type="style", text="语气友好",
                                source_quote="保持友好", severity="minor"),
        ]
        judgments = judge.judge_trajectory(cps, _mk_traj(), n_votes=3)
        assert len(judgments) == 2
        for j in judgments:
            # 非 forbidden 检查点，judge_votes 应恰为 3 票
            assert len(j["judge_votes"]) == 3, j
