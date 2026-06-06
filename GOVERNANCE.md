# EvalCall 资产治理与反漂移机制

> 本文件防范四类核心资产因无人认领、无版本约束、无 review 触发而悄悄腐烂，
> 导致评测报告失去可对比性和可信度。

---

## 1. 资产责任矩阵

> 防范：四类资产同时被所有人"默认别人在管"，最终无人管。

| 资产 | 路径 | 作用 | Owner 角色 | Review 周期 | 触发更新的事件 | 腐烂症状（最先出现的异常） |
|------|------|------|-----------|-------------|----------------|--------------------------|
| 黄金集 | `data/calibration/golden_set.json` | 量化裁判自身准确率/F1/混淆矩阵；是唯一可信的裁判误差棒来源 | 质检负责人 | 季度 | 新口径分歧拍板后需补充对应案例；模型升级后需重验覆盖率；新违规话术类型上报 | 校准准确率长期停在同一数字（没有新难案例进入）；某类检查点 support=0（从未被黄金集覆盖） |
| Persona 库 | `data/personas/*.yaml` | 控制用户模拟器的对话风格，决定被测模型的压力分布 | 质检负责人 | 半年 | 外呼业务新增用户类型；旧 persona 描述已不反映真实用户分布 | 所有轨迹 persona 混合分数差异极小（覆盖不足，场景同质化） |
| Lint 检测维度 | `evalcall/lint.py`（四维：`conflict`/`infeasible`/`ambiguous`/`missing_branch`；扣分权重 `_SEVERITY_WEIGHT`/`_DIMENSION_FACTOR`） | 体检指令本身是否可被遵循；产出 `feasibility_score` | 算法工程 | 随口径变更触发 | 新类型指令缺陷被发现但四维无法覆盖；`feasibility_score` 公式中的扣分权重已不反映实际严重程度 | 某类指令缺陷大量漏检；feasibility_score 高但模型实测分低（指令可遵循度与实测分长期倒挂） |
| 裁判判定准则 | `evalcall/judge.py`（`_LLM_SYS` 中的四条准则：归属判定/自纠不洗白/语义等价从宽/噪声不豁免） | 规定 LLM 裁判在易误判情形下如何做判定；直接决定各检查点的 pass/fail/na 分布 | 指令运营 | 随口径变更触发 | 新口径争议拍板后；校准时发现某类系统性误判（如 na→fail 偏移）需纠正 | 投票分歧率（`judge_disagreement_rate`）长期偏高（>0.2）；校准混淆矩阵出现新的集中偏移方向 |

**角色说明**

- **质检负责人**：持有业务真值，负责黄金集和 persona 与真实外呼场景保持对齐。
- **算法工程**：负责 lint/judge 算法实现层，不拍口径，只保证代码逻辑与口径一致。
- **指令运营**：持有口径话语权，判定准则的任何措辞变更必须经此角色拍板，并留 dispute 记录（见第 2 节）。

---

## 2. 口径分歧清单机制（`disputes.jsonl` 协议）

> 防范：口径争议只在飞书/口头解决，代码和黄金集悄悄跟着改，事后无法追溯"为什么裁判这样判"。

### 2.1 Schema

每条口径分歧记录为一行 JSON（JSONL 格式），存放在 `data/calibration/disputes.jsonl`：

```jsonc
{
  "dispute_id": "D-001",
  "checkpoint_id": "flow_greeting_identity",       // 关联的检查点 id；跨检查点时填数组
  "争议描述": "合理转述的开场白（未照搬'工号XXXX'原文）算不算 flow_greeting_identity 通过",
  "提出方": "质检负责人",
  "裁决": "合理转述视为满足——检查点关注'是否自报平台+客服身份'，不要求逐字对齐原文话术",
  "拍板人": "指令运营",
  "生效日期": "2026-06-06",
  "影响的历史run": [],                              // 裁决前的 run 目录列表；空表示裁决前无历史 run
  "关联的裁判准则改动": "judge.py _LLM_SYS 第3条'语义等价从宽'新增'开场白合理转述视为满足'"
}
```

### 2.2 强制约束

**裁判判定准则（`evalcall/judge.py` 中 `_LLM_SYS` 的任何改动）必须在本文件关联一条 dispute 记录。**
无对应 dispute 的改动视为未审批变更，应在 code review 时被拒绝。

Lint 扣分权重（`evalcall/lint.py` 中 `_SEVERITY_WEIGHT` / `_DIMENSION_FACTOR`）的改动同样需关联 dispute。

### 2.3 示例记录

以下两条取自本项目真实发生的口径分歧：

**D-001：合理转述的开场白算不算合规**

```jsonc
{
  "dispute_id": "D-001",
  "checkpoint_id": "flow_greeting_identity",
  "争议描述": "case02 中客服说'我这边是美团外卖官方这边的'，未照搬指令里'工号XXXX'格式，但语义上自报了来历。黄金集标注 pass，但有评审认为应 fail（未报工号）。",
  "提出方": "质检负责人",
  "裁决": "判 pass。检查点文本为'开场必须自报公司/平台与客服身份'，核心是信息传达而非格式。未报工号是不足，但已传达平台来历，语义等价从宽。若需强制工号，应在检查点 text 中明确写'必须报工号'。",
  "拍板人": "指令运营",
  "生效日期": "2026-06-06",
  "影响的历史run": [],
  "关联的裁判准则改动": "judge.py _LLM_SYS 第3条'语义等价从宽'覆盖此情形；golden_set case02 ground_truth flow_greeting_identity=pass"
}
```

**D-002：违规后立即自纠该不该洗白**

```jsonc
{
  "dispute_id": "D-002",
  "checkpoint_id": "forbidden_no_personal_contact",
  "争议描述": "case16 中客服先索要验证码，下一句立刻撤回并纠正。有评审认为纠正了就应判 pass（结果合规），另一方认为索要行为已发生不可撤销应判 fail。",
  "提出方": "算法工程",
  "裁决": "判 fail。违规动作已经发生（用户已听到索要请求），事后撤回不消除该轮次的违规事实。裁判准则明确'自纠不洗白'。若裁判被纠正话术带偏判成 pass，属于系统性误判，需在黄金集保留此 case 作为校准陷阱。",
  "拍板人": "指令运营",
  "生效日期": "2026-06-06",
  "影响的历史run": [],
  "关联的裁判准则改动": "judge.py _LLM_SYS 第2条'自纠不洗白'明确此准则；golden_set case16 ground_truth forbidden_no_personal_contact=fail"
}
```

---

## 3. 反漂移四元组

> 防范：两次评测报告数字不可比，却被当作"模型变好/变差"的证据。

每次评测的产物（`runs/<run_dir>/`）应绑定以下四元组，任意一项变化时报告须标注「基线已变更」：

| 字段 | 含义 | 当前来源 |
|------|------|---------|
| `instruction_hash` | 任务指令文件内容的 SHA-256 前 16 位 | 当前**未落盘**，为路线图项 |
| `target_model_fingerprint` | 被测模型名称+版本（`TARGET_MODEL` 的值） | 当前**未落盘**，为路线图项 |
| `judge_models_config` | 裁判团配置（`JUDGE_MODELS` 环境变量的值，或"单模型"） | **已落盘**：`calibration.json` 的 `meta.judge_models` 字段（见 `calibrate.py` 第 266 行） |
| `golden_set_version` | 黄金集版本（文件哈希或语义版本号） | 当前**未落盘**，为路线图项 |

### 3.1 已落地部分

`calibrate.py` 在产出的 `runs/calibration/calibration.json` 中记录了：

```json
"meta": {
  "golden_set": "data/calibration/golden_set.json",
  "backend": "...",
  "model": "...",
  "judge_models": "...",   // JUDGE_MODELS 环境变量值，未设时记录 "(单模型)"
  "n_votes": 3,
  ...
}
```

`judge.py` 在每条判定的 `judge_votes` 数组里记录了每一票的 `"model"` 字段（第 284 行），可事后追溯单票来源。

### 3.2 路线图（未落地）

以下三项尚未实现，建议在 `cmd_run`（`evalcall/cli.py`）落盘 `summary.json` 时一并写入：

```python
# 建议补充到 summary.json 的 meta 节
"instruction_hash": hashlib.sha256(task_yaml_text.encode()).hexdigest()[:16],
"target_model_fingerprint": os.getenv("TARGET_MODEL", "unknown"),
"golden_set_version": hashlib.sha256(open(GOLDEN_PATH, "rb").read()).hexdigest()[:16],
```

---

## 4. 反 Goodhart 机制设计

> 防范：黄金集成为优化目标而非度量工具——当评测准确率成为被追求的指标，
> 准确率就不再是可信指标。

### 4.1 现状诚实声明

当前 32 条黄金集案例为**全 public**：

- 案例对话、ground_truth、检查点文本均公开在 `data/calibration/golden_set.json`。
- 裁判判定准则（`_LLM_SYS` 中的四条易误判准则）与黄金集的陷阱设计**同源**：
  黄金集的难案例（case02 转述开场白、case16 自纠不洗白等）是按准则设计的，
  准则也是照着已知案例写的。
- 因此，**当前校准准确率应理解为"同源考题上界"，而非独立泛化能力的度量**。
  在评测报告和黑客松演示中应如实说明这一限制。

### 4.2 两层切分方案（路线图）

| 层次 | 规模 | 用途 | 可见性 |
|------|------|------|--------|
| Public set | ~24 条 | 调试裁判准则、日常校准、演示 | 完全公开 |
| Sealed holdout | ~8 条 | 独立泛化测试，不参与准则调优 | 仅质检负责人持有，不入代码库 |

Sealed holdout 的核心要求：**裁判准则的撰写者不得提前看到 holdout 案例**，两者须独立。

### 4.3 季度换血

每季度至少向黄金集补充 4 条新案例，同时将已被裁判稳定判对（连续三次校准全部命中）的案例移入"已通过"档案，从主校准集移除，避免准确率因记忆而虚高。

### 4.4 Canary 检查点设计

在正式评测的检查点清单中混入 1-2 条 **canary 检查点**——其 ground_truth 已知但不告知裁判是 canary：

- 每次 `run` 后自动比对 canary 检查点的判定与真值。
- 若 canary 判定错误，评测报告顶部标注「警告：裁判质量下降，本次结果可信度存疑」。
- 当前**未实现**，为路线图项。

---

## 5. 交接安全：环境变量清单

> 防范：换机器或换人后，因环境变量缺失或误解，评测结果静默退化却无人察觉。

| 变量名 | 作用 | 缺省行为 | 危险缺省 |
|--------|------|---------|---------|
| `EVALCALL_BACKEND` | 主评测通道（裁判/编译/lint）的后端类型：`openai` / `claude-cli` / `mock` | 缺省值 `claude-cli`（本地 claude CLI 子进程） | 低危：会在找不到 `claude` 可执行文件时报错，不会静默 |
| `EVALCALL_MODEL` | 主评测通道默认模型名（在 `chat_json`/`lint`/`compile` 中使用） | `claude-cli` 后端缺省 `sonnet`；`openai` 后端缺省 `gpt-4o-mini` | 中危：模型能力差异会影响 lint/编译质量，但不静默 |
| `JUDGE_MODELS` | 裁判团配置，逗号分隔多个模型名（如 `haiku,sonnet,opus`）；控制多票裁判的模型轮换 | **未设时静默退化为单一模型**（使用 `--model` 参数或 `EVALCALL_MODEL` 的值） | **高危**：跨模型投票是消除单一模型系统性偏见的核心机制。未设 `JUDGE_MODELS` 时，N_VOTES=3 仍会跑 3 票，但三票均出自同一模型，只能消随机噪声，无法消系统性偏见。`calibration.json` 的 `meta.judge_models` 会记录 `"(单模型)"` 警示。 |
| `N_VOTES` | 每个检查点的 LLM 投票次数（`judge.judge_trajectory` 中读取） | 缺省 `1`（单票） | 中危：单票无法计算 `vote_agreement`（分歧率），报告中 `judge_disagreement_rate` 为 0.0 但含义是"未投票"而非"完全一致" |
| `TARGET_MODEL` | 被测对话模型名称 | `claude-cli` 后端缺省 `sonnet` | 中危：忘记设置时被测模型与裁判模型相同，存在"自评"污染风险（被测=裁判=sonnet） |
| `TARGET_BACKEND` | 被测对话模型的后端类型 | 缺省回退 `EVALCALL_BACKEND` 的值 | 低危：两条通道共用同一后端，但模型可独立配置 |
| `EVALCALL_BACKEND`（用于 target） | 被测通道后端，当 `TARGET_BACKEND` 未设时作为兜底 | 见 `EVALCALL_BACKEND` 行 | 同 `EVALCALL_BACKEND` |
| `CLAUDE_BIN` | `claude` 可执行文件路径（`claude-cli` 后端使用） | 缺省 `claude`（PATH 中寻找） | 低危：找不到时报 `LLMError`，不会静默 |

### 5.1 快速验证命令

新环境接手后，运行以下命令验证配置：

```bash
# 1. 验证主通道能调通
python3 -c "from evalcall import llm; print(llm.chat([{'role':'user','content':'hi'}]))"

# 2. 验证 JUDGE_MODELS 已设（非单模型模式）
python3 -c "import os; v=os.getenv('JUDGE_MODELS',''); print('JUDGE_MODELS:', repr(v) if v else '!! 未设置，将退化为单模型')"

# 3. 试跑 2 条校准 case 验证裁判链路
python3 calibrate.py --limit 2 --votes 1

# 4. 检查产出的 meta 节确认配置已被记录
python3 -c "import json; m=json.load(open('runs/calibration/calibration.json'))['meta']; print(json.dumps(m, ensure_ascii=False, indent=2))"
```

### 5.2 高风险配置组合

以下配置组合会导致评测结果失去可比性，交接时须明确核查：

| 风险场景 | 症状 | 检查方法 |
|---------|------|---------|
| `JUDGE_MODELS` 未设 + `N_VOTES` > 1 | 多票全来自同一模型，`calibration.json` 中 `judge_models="(单模型)"` | 查 `meta.judge_models` 字段 |
| `TARGET_MODEL` 未设 + 主通道也用默认 | 被测模型=裁判模型，有自评污染风险 | 查 `summary.json` 中是否记录 `target_model_fingerprint`（路线图项）；现阶段手动核对两个变量 |
| `N_VOTES=1` + 对比两次 run 的分歧率 | `judge_disagreement_rate` 两次均为 0.0，但含义是"未投票"而非"一致" | 确保同一对比实验使用相同 `N_VOTES` |
