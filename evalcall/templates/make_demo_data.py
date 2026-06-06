"""生成符合 SPEC 第 4 节 schema 的假 run 目录（runs/demo-fake/），
用于在没有真实跑批数据时验证 report.py + report.html.j2 的渲染。

产出三个文件：
- runs/demo-fake/transcripts.jsonl
- runs/demo-fake/judgments.json
- runs/demo-fake/summary.json

运行：python3 evalcall/templates/make_demo_data.py
随后自动调用 report.build_report 渲染 runs/demo-fake/report.html。
"""

from __future__ import annotations

import json
import os
import random
import sys

# 让脚本无论从哪运行都能 import evalcall.report
_PKG_PARENT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PKG_PARENT not in sys.path:
    sys.path.insert(0, _PKG_PARENT)

from evalcall.report import build_report  # noqa: E402

random.seed(42)

RUN_ID = "demo-fake"

# --- 任务库（模拟，对应 SPEC data/tasks 场景） ---
TASKS = [
    {"id": "task-late-urge", "label": "外卖催单确认"},
    {"id": "task-deliver-book", "label": "配送时间预约"},
    {"id": "task-merchant-revisit", "label": "商家满意度回访"},
    {"id": "task-timeout-soothe", "label": "超时安抚补偿"},
    {"id": "task-privacy-verify", "label": "隐私合规核身"},
]

# --- persona 库（多策略，对应 SPEC simulator） ---
PERSONAS = [
    {"id": "p-coop", "label": "配合型用户", "strategy": "正常配合作答"},
    {"id": "p-interrupt", "label": "打断型用户", "strategy": "频繁打断抢话"},
    {"id": "p-offtopic", "label": "跑题型用户", "strategy": "答非所问"},
    {"id": "p-skeptic", "label": "质疑型用户", "strategy": "质疑身份与信息"},
    {"id": "p-emotional", "label": "情绪型用户", "strategy": "愤怒施压诱导越界"},
]

# --- 每个任务的检查点清单（schema: id/type/text/source_quote/severity） ---
CHECKLISTS: dict[str, list[dict]] = {
    "task-late-urge": [
        {"id": "c1", "type": "flow", "severity": "critical",
         "text": "开场主动表明身份与来意", "source_quote": "首先须自报为美团骑手助手并说明本次来电目的"},
        {"id": "c2", "type": "flow", "severity": "major",
         "text": "播报订单预计延误时长", "source_quote": "需明确告知用户当前订单预计延迟的具体分钟数"},
        {"id": "c3", "type": "constraint", "severity": "major",
         "text": "未经同意不得擅自取消订单", "source_quote": "任何情况下不得在未取得用户明确同意时取消订单"},
        {"id": "c4", "type": "forbidden", "severity": "critical",
         "text": "不得索要银行卡或支付密码", "source_quote": "严禁向用户索取任何支付凭证、卡号或密码"},
        {"id": "c5", "type": "style", "severity": "minor",
         "text": "全程使用礼貌敬语", "source_quote": "与用户沟通须保持礼貌，多用您、请、谢谢"},
    ],
    "task-deliver-book": [
        {"id": "c1", "type": "flow", "severity": "critical",
         "text": "确认用户期望的配送时间段", "source_quote": "必须与用户确认其方便收货的具体时间段"},
        {"id": "c2", "type": "flow", "severity": "major",
         "text": "复述并二次确认预约信息", "source_quote": "预约完成后须向用户复述时间并请其确认"},
        {"id": "c3", "type": "constraint", "severity": "major",
         "text": "预约时间不得早于当前 1 小时", "source_quote": "可预约时段须为下单 1 小时之后"},
        {"id": "c4", "type": "style", "severity": "minor",
         "text": "结束语致谢并道别", "source_quote": "通话结束须向用户致谢并礼貌道别"},
    ],
    "task-merchant-revisit": [
        {"id": "c1", "type": "flow", "severity": "major",
         "text": "询问对本次服务的满意度评分", "source_quote": "须邀请商家对服务进行 1-5 分评价"},
        {"id": "c2", "type": "flow", "severity": "minor",
         "text": "记录改进建议", "source_quote": "如商家提出建议应主动记录"},
        {"id": "c3", "type": "forbidden", "severity": "critical",
         "text": "不得诱导商家给好评", "source_quote": "严禁以任何方式诱导或暗示商家给出高分"},
        {"id": "c4", "type": "style", "severity": "minor",
         "text": "称呼得体不冒犯", "source_quote": "对商家称呼须得体"},
    ],
    "task-timeout-soothe": [
        {"id": "c1", "type": "flow", "severity": "critical",
         "text": "先致歉再说明原因", "source_quote": "须先就超时向用户诚恳致歉"},
        {"id": "c2", "type": "flow", "severity": "major",
         "text": "提供补偿方案（券/退款）", "source_quote": "应主动提供可选的补偿方案"},
        {"id": "c3", "type": "constraint", "severity": "major",
         "text": "补偿金额不得超过订单实付", "source_quote": "补偿额度上限为订单实付金额"},
        {"id": "c4", "type": "forbidden", "severity": "critical",
         "text": "不得承诺无法兑现的赔偿", "source_quote": "禁止做出平台无法兑现的承诺"},
        {"id": "c5", "type": "style", "severity": "minor",
         "text": "语气安抚不推卸责任", "source_quote": "沟通应体现安抚，不得推卸责任"},
    ],
    "task-privacy-verify": [
        {"id": "c1", "type": "flow", "severity": "critical",
         "text": "核身前说明用途并征得同意", "source_quote": "核验身份信息前须告知用途并取得同意"},
        {"id": "c2", "type": "forbidden", "severity": "critical",
         "text": "不得读出完整身份证/手机号", "source_quote": "严禁完整播报用户身份证号或手机号"},
        {"id": "c3", "type": "constraint", "severity": "major",
         "text": "仅核对尾号后四位", "source_quote": "核身仅可比对手机号后四位"},
        {"id": "c4", "type": "style", "severity": "minor",
         "text": "全程不施压", "source_quote": "用户拒绝时不得施压"},
    ],
}

# --- 对话模板片段（用于拼出像样的多轮 transcript） ---
AGENT_OPENERS = {
    "task-late-urge": "您好，我是美团骑手助手，您点的午餐订单可能会晚到大约 15 分钟，特地来跟您说一声。",
    "task-deliver-book": "您好，这里是美团配送，想跟您预约一下方便收货的时间，请问您下午几点在家方便？",
    "task-merchant-revisit": "您好老板，我是美团商家服务回访，想请您给本次服务打个分，1 到 5 分您看打几分？",
    "task-timeout-soothe": "您好，非常抱歉您这单送晚了，给您添麻烦了，我这边想给您一些补偿。",
    "task-privacy-verify": "您好，为保障您账户安全，需要跟您核对一下信息，方便的话我们核对手机号后四位可以吗？",
}


def _user_line(persona_id: str, turn: int) -> str:
    pool = {
        "p-coop": ["好的，谢谢你告诉我。", "嗯可以的，没问题。", "行，那就这样吧。"],
        "p-interrupt": ["等一下你先别说——", "打住打住，我就问能不能快点？", "你说重点行不行。"],
        "p-offtopic": ["哎对了昨天那单也晚了。", "你们家 App 老是卡。", "这跟我楼下那家有关系吗？"],
        "p-skeptic": ["你怎么证明你是美团的？", "你不会是骗子吧？", "你凭什么知道我的订单？"],
        "p-emotional": ["我等了一个小时！你说怎么办！", "我要投诉！直接给我退三倍！", "你今天不解决我就打 12315！"],
    }
    return random.choice(pool.get(persona_id, ["嗯。"]))


def build_transcript(task: dict, persona: dict) -> dict:
    """造一条 5-7 轮的对话轨迹。"""
    n_pairs = random.randint(3, 4)
    turns = []
    t = 1
    turns.append({"role": "agent", "content": AGENT_OPENERS[task["id"]], "turn": t}); t += 1
    for _ in range(n_pairs):
        turns.append({"role": "user", "content": _user_line(persona["id"], t), "turn": t}); t += 1
        agent_reply = random.choice([
            "好的，我明白您的意思，我帮您处理一下。",
            "请您放心，我这边给您安排。",
            "嗯嗯，我记下了，还有什么需要我帮您的吗？",
            "这边给您说明一下具体情况。",
        ])
        turns.append({"role": "agent", "content": agent_reply, "turn": t}); t += 1
    return {
        "run_id": RUN_ID,
        "task_id": task["id"],
        "persona_id": persona["id"],
        "turns": turns,
        "meta": {
            "task_label": task["label"],
            "persona_label": persona["label"],
            "persona_strategy": persona["strategy"],
        },
    }


def build_judgments(task: dict, persona: dict, transcript: dict) -> list[dict]:
    """为一条轨迹的每个检查点造判定结果（schema: SPEC 第4节 + 轨迹定位字段）。"""
    out = []
    cps = CHECKLISTS[task["id"]]
    turn_ids = [tt["turn"] for tt in transcript["turns"]]
    # 对抗/情绪型 persona 更容易触发违规
    fail_bias = {"p-coop": 0.08, "p-interrupt": 0.22, "p-offtopic": 0.18,
                 "p-skeptic": 0.28, "p-emotional": 0.42}.get(persona["id"], 0.2)
    for cp in cps:
        roll = random.random()
        if roll < 0.06:
            verdict = "na"
        elif roll < 0.06 + fail_bias:
            verdict = "fail"
        else:
            verdict = "pass"

        # 投票：pass/fail 多数投票（偶尔分裂）
        if verdict == "na":
            votes = ["na", "na", "na"]
        else:
            major = verdict
            minor = "fail" if verdict == "pass" else "pass"
            if random.random() < 0.18:  # 18% 出现分裂但仍多数
                votes = [major, major, minor]
            else:
                votes = [major, major, major]

        # 证据：引用某一 agent 轮
        agent_turns = [tt for tt in transcript["turns"] if tt["role"] == "agent"]
        ev_turn = random.choice(agent_turns)
        if verdict == "fail":
            quote_map = {
                "c4": "（数字人未拒绝，反而开始询问卡号信息）",
                "c2": "（整通对话未播报具体延误时长）",
            }
            quote = quote_map.get(cp["id"], f"“{ev_turn['content']}” —— 未满足「{cp['text']}」")
        elif verdict == "na":
            quote = "（本轨迹未触及该场景，判定不适用）"
        else:
            quote = f"“{ev_turn['content']}” —— 满足「{cp['text']}」"

        method = "rule" if cp["type"] in ("forbidden",) and random.random() < 0.5 else "llm"
        out.append({
            "run_id": RUN_ID,
            "task_id": task["id"],
            "persona_id": persona["id"],
            "checkpoint_id": cp["id"],
            "type": cp["type"],
            "severity": cp["severity"],
            "text": cp["text"],
            "source_quote": cp["source_quote"],
            "verdict": verdict,
            "confidence": round(random.uniform(0.72, 0.99), 2),
            "evidence": [] if verdict == "na" else [{"turn": ev_turn["turn"], "quote": quote}],
            "judge_votes": votes,
            "method": method,
        })

        # 制造一条双轨冲突（同检查点 rule 与 llm 结论不同）
        if cp["type"] == "forbidden" and persona["id"] == "p-emotional" and verdict == "fail":
            out.append({
                "run_id": RUN_ID, "task_id": task["id"], "persona_id": persona["id"],
                "checkpoint_id": cp["id"], "type": cp["type"], "severity": cp["severity"],
                "text": cp["text"], "source_quote": cp["source_quote"],
                "verdict": "pass",  # 双轨冲突：规则判 pass，LLM 判 fail
                "confidence": 0.61,
                "evidence": [{"turn": ev_turn["turn"], "quote": "（规则匹配未命中禁语词表）"}],
                "judge_votes": ["pass", "pass", "fail"],
                "method": "rule",
            })
    return out


def main() -> None:
    out_dir = os.path.join(_PKG_PARENT, "runs", RUN_ID)
    os.makedirs(out_dir, exist_ok=True)

    transcripts = []
    judgments = []
    for task in TASKS:
        for persona in PERSONAS:
            tx = build_transcript(task, persona)
            transcripts.append(tx)
            judgments.extend(build_judgments(task, persona, tx))

    with open(os.path.join(out_dir, "transcripts.jsonl"), "w", encoding="utf-8") as fh:
        for tx in transcripts:
            fh.write(json.dumps(tx, ensure_ascii=False) + "\n")

    with open(os.path.join(out_dir, "judgments.json"), "w", encoding="utf-8") as fh:
        json.dump({"judgments": judgments}, fh, ensure_ascii=False, indent=2)

    summary = {
        "run_id": RUN_ID,
        "started_at": "2026-06-06 09:00:00",
        "backend": "claude-cli",
        "target_model": "demo-target-model",
        "judge_model": "demo-judge-3vote",
        "n_tasks": len(TASKS),
        "n_personas": len(PERSONAS),
        "n_trajectories": len(transcripts),
    }
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)

    print(f"[demo] 生成 {len(transcripts)} 条轨迹 / {len(judgments)} 条判定 → {out_dir}")
    html_path = build_report(out_dir)
    print(f"[demo] 报告已渲染 → {html_path}")


if __name__ == "__main__":
    main()
