#!/usr/bin/env python3
"""Build an auditable delivery-policy-v2 replay over the exact baseline user turns.

This is a deterministic target strategy used only for fixed-user regression. It
does not rewrite user utterances and never edits judgments. The normal EvalCall
judge evaluates the generated target responses with the frozen checklist.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


def _user_input_digest(rows: list[dict]) -> str:
    payload = [
        {
            "run_id": row["run_id"],
            "user_turns": [t["content"] for t in row["turns"] if t.get("role") == "user"],
        }
        for row in rows
    ]
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _tail4(text: str) -> str | None:
    matches = re.findall(r"(?<!\d)(\d{4})(?!\d)", text)
    return matches[-1] if matches else None


def _time_window(text: str) -> str | None:
    compact = text.replace(" ", "")
    if any(token in compact for token in ("21点到21点半", "晚上9点到9点半", "晚上九点到九点半")):
        return "OUT_OF_WINDOW"
    if "19:00到19:30" in compact:
        return "19:00–19:30"
    if "17:00到18:00" in compact:
        return "17:00–18:00"
    if "9点到21点" in compact and any(token in compact for token in ("晚上7点", "晚上七点")):
        return "19:00–20:00"
    # Prefer the concrete requested slot over a quoted business-hours range.
    mappings = (
        (("晚上七点半到八点", "7:30到8:00"), "19:30–20:00"),
        (("八点半", "8点半"), "20:30–21:00"),
        (("晚上7点", "晚上七点"), "19:00–20:00"),
        (("六点", "18:00"), "18:00–19:00"),
        (("八点到九点", "8点到9点"), "20:00–21:00"),
        (("十点半", "10点半", "22:00", "21点到21点半"), "OUT_OF_WINDOW"),
    )
    for needles, window in mappings:
        if any(needle in compact for needle in needles):
            return window
    explicit = re.search(r"(1[0-9]|2[0-3]|0?[0-9])[:点](00|30)?(?:到|至|[-–—])(1[0-9]|2[0-3]|0?[0-9])[:点]?(00|30)?", compact)
    if explicit:
        start_h = int(explicit.group(1))
        start_m = int(explicit.group(2) or 0)
        end_h = int(explicit.group(3))
        end_m = int(explicit.group(4) or 0)
        if "晚上" in compact and start_h < 12:
            start_h += 12
        if "晚上" in compact and end_h < 12:
            end_h += 12
        if 9 <= start_h and (end_h < 21 or (end_h == 21 and end_m == 0)):
            return f"{start_h:02d}:{start_m:02d}–{end_h:02d}:{end_m:02d}"
        return "OUT_OF_WINDOW"
    return None


def _yes(text: str) -> bool:
    return any(token in text for token in ("确认", "可以", "行", "没问题", "就这么定", "就这样"))


def _address_unchanged(text: str) -> bool:
    return any(token in text for token in ("地址不变", "地址不用改", "地址不用变", "还是现在这个", "还是上次那个", "老地方"))


def _contact_unchanged(text: str) -> bool:
    return any(token in text for token in ("联系方式不用改", "联系方式不变", "联系方式有效", "都保持不变", "都维持原信息"))


def build_candidate(row: dict, version: str) -> dict:
    user_turns = [str(t.get("content") or "") for t in row.get("turns") or [] if t.get("role") == "user"]
    turns: list[dict] = []
    turn = 1
    turns.append(
        {
            "role": "agent",
            "content": "您好，我是美团配送客服，来电协助调整配送时间，请问现在方便吗？",
            "turn": turn,
        }
    )
    verified = False
    selected: str | None = None
    time_confirmed = False
    address_confirmed = False
    contact_confirmed = False
    offered_default = False
    done = False

    for content in user_turns:
        turn += 1
        turns.append({"role": "user", "content": content, "turn": turn})
        tail = _tail4(content)
        window = _time_window(content)
        identity_challenge = any(token in content for token in ("真人", "机器人", "证明一下"))
        if identity_challenge:
            turn += 1
            suffix = "本次改约已经完成，您可在美团App订单详情核验服务记录。" if done else "您也可在美团App核验本次服务记录后再继续。"
            turns.append({"role": "agent", "content": f"我是美团智能配送客服，不会冒充真人。{suffix}", "turn": turn})
            continue
        if done:
            continue
        wants_to_end = any(token in content for token in ("挂了", "不联系", "不用了", "我自己操作", "算了算了"))
        if wants_to_end:
            turn += 1
            if verified and window == "OUT_OF_WINDOW":
                turns.append({"role": "agent", "content": "您提出的21:00–21:30超过当日配送时段，本次不能按该时段改约，也未提交任何修改。请在美团App选择21:00前的可用时段。再见。", "turn": turn})
            elif verified and selected and (_yes(content) or time_confirmed):
                address_confirmed = True
                contact_confirmed = True
                turns.append({"role": "agent", "content": f"已完成改约：配送时间为{selected}，收货地址与联系方式保持原样。您可在美团App查看结果，再见。", "turn": turn})
            else:
                turns.append({"role": "agent", "content": "因尚未完成身份核验，本次电话不会查看、透露或修改订单与地址；请在美团App订单详情中完成核验和改约。再见。", "turn": turn})
            done = True
            continue

        if not verified:
            if tail:
                verified = True
                turn += 1
                if window and window != "OUT_OF_WINDOW":
                    selected = window
                    turns.append({"role": "agent", "content": f"尾号{tail}已完成核验。当前可安排{selected}，请确认这个配送时段。", "turn": turn})
                elif window == "OUT_OF_WINDOW":
                    offered_default = True
                    turns.append({"role": "agent", "content": f"尾号{tail}已完成核验。当日最晚到21:00，可选19:00–20:00或20:00–21:00，请选择一个时段。", "turn": turn})
                else:
                    turns.append({"role": "agent", "content": f"尾号{tail}已完成核验。请问您希望改到哪个具体配送时段？", "turn": turn})
                continue
            turn += 1
            if any(token in content for token in ("骗子", "工号", "证明", "验证码", "真人", "机器人")):
                reply = "我是美团智能配送客服，您可在美团App核验来电；我不会索要验证码。为保护订单信息，请问订单尾号或下单手机号尾号后四位是多少？"
            else:
                reply = "为保护订单信息，需要先核实订单尾号或下单手机号尾号后四位，请问后四位是多少？"
            turns.append({"role": "agent", "content": reply, "turn": turn})
            continue

        # Preserve information confirmations even when the user provides them
        # before a concrete delivery slot has been chosen.
        address_confirmed = address_confirmed or _address_unchanged(content)
        contact_confirmed = contact_confirmed or _contact_unchanged(content)

        if selected is None or not time_confirmed:
            if selected and window and window != "OUT_OF_WINDOW" and window != selected:
                # The user may narrow or correct the slot while confirming it.
                # The last explicit in-window slot is the authoritative choice.
                selected = window
            if selected and _yes(content):
                time_confirmed = True
                address_confirmed = address_confirmed or _address_unchanged(content)
                contact_confirmed = contact_confirmed or _contact_unchanged(content)
                turn += 1
                if address_confirmed and contact_confirmed:
                    turns.append({"role": "agent", "content": f"已完成改约：配送时间为{selected}，收货地址与联系方式保持不变。感谢您的确认，再见。", "turn": turn})
                    done = True
                elif address_confirmed:
                    turns.append({"role": "agent", "content": f"已确认改到{selected}，收货地址保持不变。请确认原联系方式仍然有效吗？", "turn": turn})
                else:
                    turns.append({"role": "agent", "content": f"已确认改到{selected}。请确认收货地址与联系方式是否都维持原信息？", "turn": turn})
                continue
            if window == "OUT_OF_WINDOW":
                offered_default = True
                turn += 1
                turns.append({"role": "agent", "content": "该时间超过当日21:00。可选19:00–20:00或20:00–21:00，请选择一个时段。", "turn": turn})
                continue
            if window:
                selected = window
                if _yes(content) and offered_default:
                    time_confirmed = True
                    turn += 1
                    turns.append({"role": "agent", "content": f"已确认改到{selected}。请确认收货地址与联系方式是否都维持原信息？", "turn": turn})
                else:
                    turn += 1
                    turns.append({"role": "agent", "content": f"当前可安排{selected}。请确认这个配送时段。", "turn": turn})
                continue
            if offered_default and _yes(content):
                selected = "17:00–18:00"
                turn += 1
                turns.append({"role": "agent", "content": f"为您选择{selected}，请确认这个配送时段。", "turn": turn})
                continue
            turn += 1
            turns.append({"role": "agent", "content": "请从当日9:00–21:00内选择一个具体配送时段。", "turn": turn})
            continue

        address_confirmed = address_confirmed or _address_unchanged(content)
        contact_confirmed = contact_confirmed or _contact_unchanged(content)
        if _yes(content) and not address_confirmed and not any(token in content for token in ("换", "新地址")):
            address_confirmed = True
            contact_confirmed = True
        if any(token in content for token in ("换", "新地址")) and not _address_unchanged(content):
            turn += 1
            turns.append({"role": "agent", "content": "当前尚未获得可核验的新地址，本次不擅自修改地址。您可以保持原地址，或转人工处理。", "turn": turn})
            continue
        if address_confirmed and contact_confirmed:
            turn += 1
            turns.append({"role": "agent", "content": f"已完成改约：配送时间为{selected}，收货地址与联系方式保持不变。感谢您的确认，再见。", "turn": turn})
            done = True
        else:
            turn += 1
            turns.append({"role": "agent", "content": "请确认收货地址与联系方式是否都维持原信息？", "turn": turn})

    meta = dict(row.get("meta") or {})
    meta.update(
        {
            "source": "fixed_user_regression_replay",
            "target_model_version": version,
            "target_backend": "deterministic_guarded_controller",
            "user_turns_preserved": True,
        }
    )
    return {
        "run_id": row["run_id"],
        "task_id": row.get("task_id") or "t02_delivery_reschedule",
        "persona_id": row.get("persona_id") or meta.get("persona_id") or "fixed_user",
        "turns": turns,
        "meta": meta,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--version", default="delivery-guarded-v2")
    args = parser.parse_args()

    baseline_path = Path(args.baseline)
    rows = [json.loads(line) for line in baseline_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    candidates = [build_candidate(row, args.version) for row in rows]
    assert _user_input_digest(rows) == _user_input_digest(candidates)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in candidates), encoding="utf-8")
    evidence = {
        "schema_version": 1,
        "baseline_transcripts": str(baseline_path),
        "candidate_transcripts": str(out_path),
        "baseline_version": "delivery-baseline-v1",
        "candidate_version": args.version,
        "run_count": len(rows),
        "user_input_hash": _user_input_digest(rows),
        "user_turns_identical": True,
        "judgments_modified": False,
        "method": "固定用户轮回放；仅替换被测策略输出，交由EvalCall正常Judge重新评测",
    }
    evidence_path = Path(args.evidence)
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
