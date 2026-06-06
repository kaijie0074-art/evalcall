# SPEC — 美团黑客松赛道二：复杂指令下的多轮对话评测系统

> 截止：2026-06-07 投稿。评审维度：创新性、完整性、应用效果、商业价值。

## 0. 赛题

履约数字人外呼场景：系统自动给用户打电话，对话模型按预设指令完成任务。指令含复杂流程和多重约束，人工评估贵且难量化。
**交付目标**：
1. 用户模拟器——充分有效测试对话模型在特定任务指令下的效果
2. 自动产出评测报告——过程可解释、结果可量化

## 1. 产品名

**EvalCall —— 外呼对话模型指令遵循自动评测系统**

## 2. 核心设计（创新点）

```
任务指令(自然语言) ──┐
                     ▼
              ① 指令编译器 Instruction Compiler
                 把指令编译成结构化「检查点清单」Checklist
                 (流程节点 / 硬约束 / 禁止项 / 话术要求)
                 每条检查点可溯源到指令原文 → 可解释性的根基
                     │
                     ▼
              ② 用户模拟器 User Simulator（交付目标1）
                 LLM 扮演被呼叫用户，多 persona × 多策略：
                 配合型 / 打断型 / 跑题型 / 质疑型 / 情绪型 / 沉默型
                 + 对抗模式：针对 checklist 中的约束“定向诱导”被测模型违规
                 → 覆盖率驱动：还没被测到的检查点，模拟器优先制造场景
                     │
                     ▼
              ③ 对话竞技场 Arena Runner
                 被测对话模型 × 用户模拟器 多轮对话，批量并发跑 N 条轨迹
                 输出 transcript JSONL
                     │
                     ▼
              ④ 双轨评测引擎 Judge（可解释+可靠）
                 a. 规则判定：确定性检查（关键信息是否播报、禁语是否出现…）
                 b. LLM-as-Judge：逐检查点判定 pass/fail/NA，
                    必须引用对话第 N 轮原文作为证据，多数投票(3票)定结论
                 + 可靠性指标：judge 自一致率、规则/LLM 双轨冲突率
                     │
                     ▼
              ⑤ 评测报告 Report（交付目标2）
                 总分 + 四维雷达(流程完整度/约束遵循率/异常处理/话术合规)
                 逐检查点明细（结论+证据引用+置信度）
                 失败案例剖析 + persona 维度切片
                 输出：HTML 可视化报告 + JSON 机器可读结果
```

差异化卖点：
- **指令→检查点编译**：评的不是笼统印象，是逐条可溯源的检查点（可解释）
- **对抗式模拟器 + 覆盖率驱动**：不是随机聊天，是主动找漏洞（充分有效）
- **双轨判定 + 多数投票 + 自一致率**：评测结果自带可靠性度量（可靠）
- **脱敏数据即插即用**：数据格式适配层，官方数据一到换个目录就能跑

## 3. 技术栈与目录

Python 3.11+，零重依赖（requests + jinja2，可选 rich）。LLM 后端可插拔：
- `openai` 后端：标准 OpenAI 兼容 API（base_url/key/model 全部 env 配置）——交付主线
- `claude-cli` 后端：本地 `claude -p` 子进程——本机开发/演示用（无需 key）
- `mock` 后端：录制回放，CI/无网演示兜底

```
美团黑客松/
├── SPEC.md
├── evalcall/
│   ├── __init__.py
│   ├── llm.py           # LLM 后端抽象：openai / claude-cli / mock（A）
│   ├── compiler.py      # ① 指令→检查点编译器（A）
│   ├── judge.py         # ④ 双轨评测引擎（A）
│   ├── simulator.py     # ② 用户模拟器（B）
│   ├── arena.py         # ③ 对话执行器+被测模型适配（B）
│   ├── report.py        # ⑤ 报告数据聚合（C）
│   ├── templates/report.html.j2   # HTML 报告模板（C）
│   └── cli.py           # 命令行入口：evalcall run / report（A）
├── data/
│   ├── tasks/*.yaml     # 模拟任务指令库（B）：外卖催单确认、配送时间预约、
│   │                    #   商家回访、超时安抚、隐私合规场景，每个含复杂流程+约束
│   ├── personas/*.yaml  # 用户 persona 库（B）
│   └── README.md        # 脱敏数据接入说明：官方数据到了怎么映射（B）
├── runs/                # 输出：transcripts + judgments + report.html
├── README.md            # 项目说明+架构图+快速开始（D）
└── docs/提交材料草稿（D）
```

括号字母 = 负责的子 Agent，互不碰对方文件。

## 4. 关键约定（所有 Agent 必须遵守）

- 检查点 schema：`{id, type: flow|constraint|forbidden|style, text, source_quote, severity: critical|major|minor}`
- 轨迹 schema：JSONL，每行 `{run_id, task_id, persona_id, turns: [{role: agent|user, content, turn}] , meta}`
- 判定 schema：`{checkpoint_id, verdict: pass|fail|na, confidence, evidence: [{turn, quote}], judge_votes, method: rule|llm}`
- 评分：critical fail 一票否决该项满分；总分 = Σ severity 加权；同时报「约束违反数/百次对话」
- LLM 调用统一走 `evalcall/llm.py` 的 `chat(messages, model=None) -> str`，JSON 输出用 `chat_json()`（带重试+schema校验）
- 环境变量：`EVALCALL_BACKEND=claude-cli|openai|mock`、`OPENAI_BASE_URL`、`OPENAI_API_KEY`、`EVALCALL_MODEL`、被测模型独立配置 `TARGET_*` 同名变量
- Python 全部带类型标注；中文注释；不引入 pandas/numpy 等重依赖

## 5. 里程碑（今天内）

1. 并行开发四块（A/B/C/D）
2. 主 Agent 集成，`claude-cli` 后端端到端跑通 1 任务 × 3 persona × 3 轨迹
3. 生成示例报告 report.html，截图验收
4. 批量跑全部任务出正式 demo 报告
5. 提交材料（介绍/README/演示截图），等用户给官网登录密码后投稿
