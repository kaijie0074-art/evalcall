# EvalCall 数据目录与脱敏数据接入说明

本目录是 EvalCall 评测系统的「输入数据层」，分三部分：

```
data/
├── tasks/      # 任务指令库：外呼场景的对话模型 system prompt（即被测对象的指令）
├── personas/   # 用户 persona 库：用户模拟器扮演的各类被呼叫用户
└── README.md   # 本文件
```

- `tasks/*.yaml` —— 5 个高仿真外呼任务，每个含 `task_id / name / scenario /
  instruction（被测模型的 system prompt）/ checkpoints（候选检查点，兜底用）`。
- `personas/*.yaml` —— 6 个用户画像，每个含 `persona_id / name / profile /
  strategy_weights（六种对话策略权重）/ quirks`。

正式评测时，指令编译器（`evalcall/compiler.py`）会从 `instruction` 自动编译出结构化
检查点清单；yaml 里内置的 `checkpoints` 仅作为编译失败时的兜底参考。

---

## 一、官方脱敏数据到了，怎么接入？（两条路径）

赛题方提供的脱敏数据通常是两类：(A) 真实外呼**任务指令**文本，(B) 已经发生的真实
**对话记录**。对应两条接入路径，互不冲突，可同时使用。

### 路径 1：把脱敏「任务指令」映射成 task yaml —— 在线评测（跑模拟器）

适用：官方给的是「外呼任务指令 / 话术规范 / SOP」这类文本，希望用我们的用户模拟器
去**主动跑出新对话**并评测被测模型。

映射步骤：

1. 在 `data/tasks/` 下新建一个 yaml，字段按本目录现有任务对齐：
   - `task_id`：唯一 id（建议 `t{编号}_{英文短名}`）。
   - `name` / `scenario`：人类可读的任务名与一句话场景。
   - `instruction`：**把脱敏后的官方任务指令原文粘进来**——这就是被测对话模型的
     system prompt。无需手写检查点，编译器会自动产出。
   - `checkpoints`：可省略；如想兜底，按 SPEC 第 4 节的检查点 schema 补几条。
2. 脱敏自查：确认 `instruction` 中不含真实姓名、手机号、地址、订单号等 PII；如有，
   用占位符（如「尾号 XXXX」「某商家」）替换。
3. 直接运行（无需改代码）：

   ```bash
   # 换个目录就能跑：让 CLI 指向官方数据目录
   EVALCALL_BACKEND=claude-cli \
   python -m evalcall.cli run --task data/tasks/你的新任务.yaml \
       --personas data/personas --n 3
   ```

> 设计上数据与代码解耦：只要 yaml 字段对齐，**把官方数据放进 `data/tasks/`（或
> 任意目录用 `--task` 指定）即可，无需改一行代码**——这就是 SPEC 里的「脱敏数据
> 即插即用」适配层。

### 路径 2：直接评测已有「对话记录」—— 离线模式（跳过模拟器，只跑 Judge）

适用：官方给的是**已经发生的真实对话**（人工坐席或某模型与真实用户的通话转写），
希望直接对这些历史对话做指令遵循评测，**不需要再生成新对话**。

此模式下用户模拟器与 Arena 不参与，流程缩短为：

```
官方对话记录 ──(适配成轨迹 schema)──> 指令编译器编译检查点 ──> Judge 双轨评测 ──> 报告
```

接入步骤：

1. **把官方对话记录转成轨迹 JSONL**（轨迹 schema 见 SPEC 第 4 节）。每行一条对话：

   ```json
   {"run_id": "off_0001", "task_id": "t01_overdue_appease", "persona_id": "real_user",
    "turns": [
      {"role": "agent", "content": "您好，本次通话全程录音……", "turn": 0},
      {"role": "user",  "content": "我这单都超时半小时了！", "turn": 1},
      {"role": "agent", "content": "非常抱歉给您添麻烦……", "turn": 2}
    ],
    "meta": {"task_id": "t01_overdue_appease", "persona_id": "real_user",
             "seed": null, "adversarial_targets": [], "source": "official_offline"}}
   ```

   字段对齐要点：
   - `role` 只能是 `agent`（被评的一方，即数字人/坐席）或 `user`（真实用户）。
   - `turn` 为递增整数；`task_id` 必须对应一个 task yaml（提供编译检查点所需的
     `instruction`）。若官方未给指令，可在 `data/tasks/` 补一个仅含 `instruction`
     的任务 yaml 与之关联。
   - `meta.source` 建议标 `official_offline`，便于报告区分线上模拟与离线真实数据。

2. **运行离线评测**（跳过模拟器，直接对轨迹跑 judge）：

   ```bash
   python -m evalcall evaluate \
       --task data/tasks/t01_overdue_appease.yaml \
       --transcripts runs/官方对话.jsonl \
       --out runs/官方离线评测
   ```

   `evaluate` 会自动生成 `checklist.json / judgments.json /
   judgments_by_run.json / summary.json / report.html`。`report` 只负责根据已有
   judgments 重新渲染 HTML，不会自动对新对话执行裁判。

> 离线模式的价值：用同一套「检查点编译 + 双轨判定 + 可解释报告」直接量化历史真实
> 对话的指令遵循质量，无需任何模型调用来生成对话——**官方数据当天到、当天出报告**。

---

## 二、字段速查

### task yaml
| 字段 | 必填 | 说明 |
|---|---|---|
| `task_id` | 是 | 唯一标识 |
| `name` | 是 | 任务中文名 |
| `scenario` | 是 | 一句话场景 |
| `instruction` | 是 | 被测模型的 system prompt（脱敏指令原文） |
| `checkpoints` | 否 | 兜底检查点，缺省由编译器自动产出 |

### persona yaml
| 字段 | 必填 | 说明 |
|---|---|---|
| `persona_id` | 是 | 唯一标识 |
| `name` | 是 | 画像中文名 |
| `profile` | 是 | 背景与说话风格（注入模拟器 system prompt） |
| `strategy_weights` | 是 | 六种策略权重：cooperate / interrupt / digress / challenge / emotional / silent |
| `quirks` | 否 | 口头禅、小怪癖，增强拟真度 |

### 轨迹 JSONL（离线接入必读）
见 SPEC 第 4 节；最小字段：`run_id / task_id / persona_id / turns[{role,content,turn}] / meta`。
