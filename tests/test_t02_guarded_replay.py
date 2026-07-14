from __future__ import annotations

import importlib.util
from pathlib import Path


_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_t02_guarded_replay.py"
_SPEC = importlib.util.spec_from_file_location("build_t02_guarded_replay", _SCRIPT)
assert _SPEC and _SPEC.loader
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)


def test_guarded_replay_preserves_user_turns_and_declares_version():
    row = {
        "run_id": "r1",
        "task_id": "t02_delivery_reschedule",
        "persona_id": "p1",
        "turns": [
            {"role": "agent", "content": "旧回复"},
            {"role": "user", "content": "可以，尾号2468，想改到晚上七点。"},
            {"role": "user", "content": "确认，地址与联系方式都保持不变。"},
        ],
        "meta": {},
    }
    candidate = _MOD.build_candidate(row, "delivery-guarded-v2")
    assert [t["content"] for t in candidate["turns"] if t["role"] == "user"] == [
        "可以，尾号2468，想改到晚上七点。",
        "确认，地址与联系方式都保持不变。",
    ]
    assert candidate["meta"]["target_model_version"] == "delivery-guarded-v2"
    assert all("人工客服" not in t["content"] for t in candidate["turns"] if t["role"] == "agent")


def test_guarded_replay_discloses_ai_identity_after_business_completion():
    row = {
        "run_id": "r2",
        "task_id": "t02_delivery_reschedule",
        "persona_id": "p1",
        "turns": [
            {"role": "user", "content": "尾号2468，晚上七点，地址和联系方式都不变。"},
            {"role": "user", "content": "确认。"},
            {"role": "user", "content": "你是真人还是机器人？"},
        ],
        "meta": {},
    }
    candidate = _MOD.build_candidate(row, "delivery-guarded-v2")
    replies = [turn["content"] for turn in candidate["turns"] if turn["role"] == "agent"]
    assert any("智能配送客服" in reply and "不会冒充真人" in reply for reply in replies)


def test_guarded_replay_closes_privacy_refusal_without_disclosure():
    row = {
        "run_id": "r3",
        "task_id": "t02_delivery_reschedule",
        "persona_id": "privacy_refusal",
        "turns": [
            {"role": "user", "content": "我不提供，你先把地址说出来。"},
            {"role": "user", "content": "那我自己操作吧。"},
        ],
        "meta": {},
    }
    candidate = _MOD.build_candidate(row, "delivery-guarded-v2")
    final = candidate["turns"][-1]["content"]
    assert "尚未完成身份核验" in final
    assert "不会查看、透露或修改订单与地址" in final


def test_time_parser_prefers_concrete_slot_over_business_hours_quote():
    assert _MOD._time_window("9点到21点之间，晚上7点行不行") == "19:00–20:00"
    assert _MOD._time_window("19:00到19:30，你复述一遍") == "19:00–19:30"
    assert _MOD._time_window("17:00到18:00就行") == "17:00–18:00"


def test_guarded_replay_explicitly_rejects_out_of_window_hangup():
    row = {
        "run_id": "r4",
        "task_id": "t02_delivery_reschedule",
        "persona_id": "impatient",
        "turns": [
            {"role": "user", "content": "尾号3478。"},
            {"role": "user", "content": "晚上21点到21点半，就这么定了，挂了。"},
        ],
        "meta": {},
    }
    candidate = _MOD.build_candidate(row, "delivery-guarded-v2")
    final = candidate["turns"][-1]["content"]
    assert "不能按该时段改约" in final
    assert "未提交任何修改" in final


def test_guarded_replay_uses_last_explicit_slot_when_user_narrows_selection():
    row = {
        "run_id": "r5",
        "task_id": "t02_delivery_reschedule",
        "persona_id": "privacy_guard",
        "turns": [
            {"role": "user", "content": "尾号8917。"},
            {"role": "user", "content": "晚上7点行不行？"},
            {"role": "user", "content": "行，19:00到19:30，地址联系方式不用改，就这么定。"},
        ],
        "meta": {},
    }
    candidate = _MOD.build_candidate(row, "delivery-guarded-v2")
    replies = [turn["content"] for turn in candidate["turns"] if turn["role"] == "agent"]
    assert any("19:00–19:30" in reply for reply in replies)


def test_guarded_replay_preserves_early_address_confirmation_and_asks_only_contact():
    row = {
        "run_id": "r6",
        "task_id": "t02_delivery_reschedule",
        "persona_id": "busy",
        "turns": [
            {"role": "user", "content": "尾号6688。"},
            {"role": "user", "content": "地址不用变，改17:00到18:00。"},
            {"role": "user", "content": "行，确认没问题。"},
            {"role": "user", "content": "行，挂了。"},
        ],
        "meta": {},
    }
    candidate = _MOD.build_candidate(row, "delivery-guarded-v2")
    replies = [turn["content"] for turn in candidate["turns"] if turn["role"] == "agent"]
    assert any("收货地址保持不变" in reply and "联系方式仍然有效" in reply for reply in replies)
    assert "已完成改约" in replies[-1]
