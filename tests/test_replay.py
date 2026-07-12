"""M5：固定用户轮的跨模型反事实重放。"""

from evalcall import replay


def test_replay_keeps_user_script_and_regenerates_agent(monkeypatch):
    replies = iter(["新开场", "新回复一", "新回复二"])
    monkeypatch.setattr(replay, "target_chat", lambda messages: next(replies))
    source = {
        "run_id": "old",
        "task_id": "t",
        "persona_id": "p",
        "turns": [
            {"role": "agent", "content": "旧开场", "turn": 0},
            {"role": "user", "content": "固定一", "turn": 1},
            {"role": "agent", "content": "旧回复", "turn": 1},
            {"role": "user", "content": "固定二", "turn": 2},
        ],
    }
    out = replay.replay_trajectory({"task_id": "t", "instruction": "守规则"}, source)
    assert [x["content"] for x in out["turns"] if x["role"] == "user"] == ["固定一", "固定二"]
    assert [x["content"] for x in out["turns"] if x["role"] == "agent"] == ["新开场", "新回复一", "新回复二"]
    assert out["meta"]["counterfactual_replay"] is True
    assert "不完全衔接" in out["meta"]["limitation"]
