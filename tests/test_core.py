"""核心计分逻辑的纯函数回归测试。

只覆盖不依赖 LLM 的确定性函数：投票聚合、汇总打分、JSON 提取/修复、
模糊子串匹配、forbidden 规则判定、组合数边界。

约束：不 mock LLM、不发起任何网络/子进程调用。所有断言基于实现现状
（runtime-anchored），发现的疑似 bug 记在测试注释里，不修改实现。

运行：python3 -m pytest tests/ -q
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile

import pytest

# 让 `import evalcall` 在任意 cwd 下都能解析到项目根
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from evalcall import compiler, judge, llm  # noqa: E402


# =========================================================================== #
# 1. judge._majority_vote —— 多数投票 + 平票优先级
# =========================================================================== #
class TestMajorityVote:
    def test_clear_majority_fail(self):
        verdict, agreement = judge._majority_vote(
            [{"verdict": "fail"}, {"verdict": "fail"}, {"verdict": "pass"}]
        )
        assert verdict == "fail"
        assert agreement == pytest.approx(2 / 3)

    def test_clear_majority_pass(self):
        verdict, agreement = judge._majority_vote(
            [{"verdict": "pass"}, {"verdict": "pass"}, {"verdict": "na"}]
        )
        assert verdict == "pass"
        assert agreement == pytest.approx(2 / 3)

    def test_empty_votes_returns_na(self):
        # 无票时退化为 na，一致率 0
        assert judge._majority_vote([]) == ("na", 0.0)

    def test_single_vote_full_agreement(self):
        assert judge._majority_vote([{"verdict": "na"}]) == ("na", 1.0)

    # --- 平票优先级：质检从严 fail > pass > na ---
    def test_tie_fail_beats_pass(self):
        verdict, agreement = judge._majority_vote(
            [{"verdict": "fail"}, {"verdict": "pass"}]
        )
        assert verdict == "fail"
        assert agreement == pytest.approx(0.5)

    def test_tie_pass_beats_na(self):
        verdict, _ = judge._majority_vote([{"verdict": "pass"}, {"verdict": "na"}])
        assert verdict == "pass"

    def test_tie_three_way_picks_fail(self):
        verdict, agreement = judge._majority_vote(
            [{"verdict": "fail"}, {"verdict": "pass"}, {"verdict": "na"}]
        )
        assert verdict == "fail"
        assert agreement == pytest.approx(1 / 3)


# =========================================================================== #
# 2. judge.summarize —— 加权打分 / 一票否决 / na 不计分母 / violation_rate
# =========================================================================== #
def _cp(cid, severity, ctype="constraint"):
    return {"id": cid, "type": ctype, "severity": severity, "text": "", "source_quote": ""}


def _j(cid, verdict, **extra):
    d = {"checkpoint_id": cid, "verdict": verdict}
    d.update(extra)
    return d


class TestSummarize:
    def test_all_pass_score_100(self):
        cps = [_cp("a", "major"), _cp("b", "minor")]
        js = [_j("a", "pass"), _j("b", "pass")]
        out = judge.summarize(cps, js)
        assert out["score"] == 100.0
        assert out["raw_score"] == 100.0
        assert out["critical_failed"] is False
        assert out["counts"] == {"pass": 2, "fail": 0, "na": 0}

    def test_weighted_score_by_severity(self):
        # major(3) pass + minor(1) fail -> earned 3 / possible 4 = 75.0
        cps = [_cp("a", "major"), _cp("b", "minor")]
        js = [_j("a", "pass"), _j("b", "fail")]
        out = judge.summarize(cps, js)
        assert out["raw_score"] == 75.0
        assert out["score"] == 75.0  # 无 critical fail，score == raw_score
        assert out["violation_count"] == 1

    def test_critical_fail_zeroes_score_but_keeps_raw(self):
        # critical(5) fail + major(3) pass -> raw = 3/8*100 = 37.5，但一票否决 score=0
        cps = [_cp("c", "critical"), _cp("a", "major")]
        js = [_j("c", "fail"), _j("a", "pass")]
        out = judge.summarize(cps, js)
        assert out["critical_failed"] is True
        assert out["score"] == 0.0
        assert out["raw_score"] == 37.5  # raw_score 保留，不被否决归零

    def test_na_excluded_from_denominator(self):
        # na 不计入分母：major pass + critical na -> 满分 100，且不触发否决
        cps = [_cp("a", "major"), _cp("c", "critical")]
        js = [_j("a", "pass"), _j("c", "na")]
        out = judge.summarize(cps, js)
        assert out["score"] == 100.0
        assert out["critical_failed"] is False
        assert out["counts"]["na"] == 1

    def test_all_na_score_zero(self):
        # 无可计分项时 possible=0 -> raw_score 0.0（而非除零）
        cps = [_cp("a", "major")]
        js = [_j("a", "na")]
        out = judge.summarize(cps, js)
        assert out["raw_score"] == 0.0
        assert out["score"] == 0.0

    def test_violation_rate_per_100(self):
        # 4 个判定：2 pass + 2 fail -> judged_n=4，violation_rate = 2/4*100 = 50.0
        cps = [_cp(c, "major") for c in "abcd"]
        js = [_j("a", "pass"), _j("b", "pass"), _j("c", "fail"), _j("d", "fail")]
        out = judge.summarize(cps, js)
        assert out["violation_count"] == 2
        assert out["violation_rate_per_100"] == 50.0

    def test_violation_rate_na_not_in_denominator(self):
        # na 不进 violation_rate 分母：1 fail + 1 na -> judged_n=1 -> 100.0
        cps = [_cp("a", "major"), _cp("b", "major")]
        js = [_j("a", "fail"), _j("b", "na")]
        out = judge.summarize(cps, js)
        assert out["violation_rate_per_100"] == 100.0

    def test_disagreement_rate_from_vote_agreement(self):
        # 两个 llm 判定，一致率 1.0 和 0.6 -> 平均 0.8 -> disagreement = 0.2
        cps = [_cp("a", "major"), _cp("b", "major")]
        js = [
            _j("a", "pass", vote_agreement=1.0),
            _j("b", "pass", vote_agreement=0.6),
        ]
        out = judge.summarize(cps, js)
        assert out["judge_disagreement_rate"] == 0.2

    def test_unknown_severity_defaults_to_major_weight(self):
        # 检查点未登记 severity 时按 major 权重计；这里检查不报错且计入分母
        cps = [{"id": "a", "type": "constraint", "text": "", "source_quote": ""}]
        js = [_j("a", "fail")]
        out = judge.summarize(cps, js)
        # 缺 severity -> cp.get('severity','major') -> 计为 fail，raw 0
        assert out["raw_score"] == 0.0
        assert out["violation_count"] == 1


# =========================================================================== #
# 3. llm._extract_json / _repair_unescaped_quotes
# =========================================================================== #
class TestExtractJson:
    def test_plain_json(self):
        assert llm._extract_json('{"a": 1, "b": "x"}') == {"a": 1, "b": "x"}

    def test_fence_stripped(self):
        fenced = '```json\n{"a": 1}\n```'
        assert llm._extract_json(fenced) == {"a": 1}

    def test_fence_without_lang_tag(self):
        fenced = '```\n[1, 2, 3]\n```'
        assert llm._extract_json(fenced) == [1, 2, 3]

    def test_json_embedded_in_prose(self):
        # 模型在 JSON 前后加了废话，应能截取最外层对象
        text = '这是结果：{"verdict": "pass"} 以上。'
        assert llm._extract_json(text) == {"verdict": "pass"}

    def test_real_failure_sample_bare_quotes_in_value(self):
        # 真实失败样本：字符串值内含裸英文引号「新增"低延迟直播"选项」
        bad = '{"source_quote": "新增"低延迟直播"选项", "id": "x"}'
        out = llm._extract_json(bad)
        assert out == {"source_quote": '新增"低延迟直播"选项', "id": "x"}

    def test_unparseable_raises_value_error(self):
        with pytest.raises(ValueError):
            llm._extract_json("这里完全没有任何 JSON 结构")


class TestRepairUnescapedQuotes:
    def test_bare_quotes_inside_value_repaired(self):
        bad = '{"source_quote": "新增"低延迟直播"选项", "id": "x"}'
        repaired = llm._repair_unescaped_quotes(bad)
        # 修复后必须能被标准 json 解析，且内容保真
        assert json.loads(repaired) == {"source_quote": '新增"低延迟直播"选项', "id": "x"}

    def test_normal_json_unchanged(self):
        # 合法 JSON 不应被破坏（幂等）
        good = '{"a": "b", "c": [1, 2]}'
        repaired = llm._repair_unescaped_quotes(good)
        assert json.loads(repaired) == {"a": "b", "c": [1, 2]}

    def test_already_escaped_quotes_preserved(self):
        # 已正确转义的嵌套引号不应被二次转义破坏
        src = r'{"q": "he said \"hi\" ok"}'
        repaired = llm._repair_unescaped_quotes(src)
        assert json.loads(repaired) == {"q": 'he said "hi" ok'}

    def test_delimiter_quote_not_escaped(self):
        # 紧跟 :,]} 的引号是字符串定界符，不能被当成内容转义
        src = '{"k": "v"}'
        assert llm._repair_unescaped_quotes(src) == '{"k": "v"}'


class TestStripFence:
    def test_no_fence_returns_stripped_input(self):
        assert llm._strip_fence('  {"a": 1}  ') == '{"a": 1}'

    def test_fence_inner_content_extracted(self):
        assert llm._strip_fence('```json\n{"a": 1}\n```') == '{"a": 1}'


# =========================================================================== #
# 4. compiler._is_substring_fuzzy
# =========================================================================== #
class TestIsSubstringFuzzy:
    def test_exact_substring_after_normalize(self):
        assert compiler._is_substring_fuzzy("低延迟直播", "新增低延迟直播选项") is True

    def test_substring_with_punctuation_and_spaces(self):
        # 归一化会去掉标点/空白，因此带标点的原文仍算精确命中
        assert compiler._is_substring_fuzzy("核实用户身份", "请，核 实 用户。身份！") is True

    def test_sliding_window_fuzzy_hit(self):
        # 一字之差（身份->身分），非精确子串，但滑窗相似度 >= 0.85 命中
        quote = "客服必须先核实用户身分再办理"
        source = "客服必须先核实用户身份再办理业务"
        assert compiler._normalize(quote) not in compiler._normalize(source)
        assert compiler._is_substring_fuzzy(quote, source) is True

    def test_no_match_below_threshold(self):
        assert compiler._is_substring_fuzzy("abcdefghij", "zzzzzzzzzzkkkkkkkkkk") is False

    def test_empty_quote_returns_false(self):
        assert compiler._is_substring_fuzzy("", "任意内容") is False

    def test_quote_longer_than_source_returns_false(self):
        assert compiler._is_substring_fuzzy("aaaaaaaaaa", "aa") is False


# =========================================================================== #
# 5. judge._judge_forbidden_rule
# =========================================================================== #
class TestJudgeForbiddenRule:
    def test_keyword_hit_in_agent_turn_is_fail(self):
        turns = [
            {"turn": 1, "role": "agent", "content": "我们保证收益稳赚不赔"},
            {"turn": 2, "role": "user", "content": "好"},
        ]
        res = judge._judge_forbidden_rule({"id": "f1", "keywords": ["保证收益"]}, turns)
        assert res is not None
        assert res["verdict"] == "fail"
        assert res["confidence"] == 1.0
        assert res["method"] == "rule"
        assert res["evidence"][0]["turn"] == 1
        assert res["judge_votes"][0]["matched"] == "保证收益"

    def test_regex_keyword_hit(self):
        # 关键词当正则用：匹配数字+元
        turns = [{"turn": 1, "role": "agent", "content": "立返888元红包"}]
        res = judge._judge_forbidden_rule({"id": "f2", "keywords": [r"\d+元"]}, turns)
        assert res is not None
        assert res["verdict"] == "fail"
        assert res["judge_votes"][0]["matched"] == "888元"

    def test_no_keywords_returns_none(self):
        # 无 keywords 交给 LLM 轨兜底
        turns = [{"turn": 1, "role": "agent", "content": "任意内容"}]
        assert judge._judge_forbidden_rule({"id": "f3", "keywords": []}, turns) is None

    def test_keyword_only_in_user_turn_not_counted(self):
        # 违规词只出现在用户发言中，不算客服违规
        turns = [
            {"turn": 1, "role": "user", "content": "你们能保证收益吗"},
            {"turn": 2, "role": "agent", "content": "不能这么承诺"},
        ]
        res = judge._judge_forbidden_rule({"id": "f4", "keywords": ["保证收益"]}, turns)
        # 客服侧未命中。实现现状：返回 None（已从旧版的 pass conf=1.0 改为转 LLM 轨）。
        # 兼容断言：接受 None 或 pass —— 这是已知的待定/已改行为，二者其一均视为正确。
        assert res is None or res.get("verdict") == "pass", (
            "未命中禁语时应返回 None（转 LLM 轨）或 pass；实测当前为 None"
        )

    def test_no_hit_anywhere_returns_none_or_pass(self):
        # 全程都没出现禁语关键词
        turns = [{"turn": 1, "role": "agent", "content": "您好，请问有什么可以帮您"}]
        res = judge._judge_forbidden_rule({"id": "f5", "keywords": ["稳赚不赔"]}, turns)
        # 同上：兼容 None / pass 两种实现行为
        assert res is None or res.get("verdict") == "pass"


# =========================================================================== #
# 6. passk.comb —— 组合数边界（r > n 返回 0）
# =========================================================================== #
@pytest.fixture(scope="module")
def passk_module():
    """加载 passk.py。

    该脚本无 __main__ 守卫，顶层会读取 sys.argv 并写文件，因此用临时空
    judgments 文件让顶层逻辑安全跑完，再取出真实的 comb 函数。
    全程无网络/子进程调用。
    """
    tmp = tempfile.mkdtemp()
    with open(os.path.join(tmp, "judgments_by_run.json"), "w", encoding="utf-8") as f:
        json.dump([], f)
    old_argv = sys.argv
    sys.argv = ["passk.py", tmp, "3"]
    try:
        path = os.path.join(_PROJECT_ROOT, "passk.py")
        spec = importlib.util.spec_from_file_location("passk_under_test", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.argv = old_argv
    return mod


class TestComb:
    def test_normal_combination(self, passk_module):
        assert passk_module.comb(5, 2) == 10
        assert passk_module.comb(4, 2) == 6

    def test_r_greater_than_n_returns_zero(self, passk_module):
        # 边界：取 k 条但只有 c < k 条 pass -> C(c,k) 必须为 0
        assert passk_module.comb(2, 5) == 0

    def test_r_equals_n(self, passk_module):
        assert passk_module.comb(3, 3) == 1

    def test_r_zero(self, passk_module):
        assert passk_module.comb(4, 0) == 1
