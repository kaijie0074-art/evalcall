# EvalCall 改进落地 · 进度交接

> 更新:2026-07-02 ｜ 分支:`evalcall-improve-20260630` 已合并入 main（commit `0f25cd7`）｜ 测试:`python3 -m pytest tests/ -q`（69 绿，2026-07-02 新增 10 个冒烟/回归测试）
> 完整建模与每节点判据:`docs/EvalCall改进-建模-20260630.md`；2026-07-02 体检修复建模:`docs/EvalCall体检修复-建模-20260702.md`

## 做到哪（已完成并提交，已验证）
8 个高价值节点全绿入库，5 个 commit：
- **P0**（be9754c）N_VOTES默认3票（修旗舰多数投票退化bug）+ README漂移 + `__main__`入口
- **P1**（7dacd76）安全/合规红线轨（policy单一规则源）+ 业务分级P0/P1/P2 + 上线红线门禁 + calibrate P0查全率分层 + 低置信交人复核
- **C18+决策头条**（778dd29）履约达成检查点（task.goal→outcome，真实外呼第一KPI）+ 报告决策头条（门禁徽章/履约率/复核数/P0红线，合成渲染验证）
- **P3-3+P4-1**（d9df463）跨版本回归diff（规则确定/LLM需复测）+ 真实性·拟人度检查点（导师建议，守溯源）
- **P3-2**（f1f6c58）模型弱点定位（数据驱动聚合，不做LLM自动改写prompt）

覆盖:导师全部主诉求（P0/P1分级✓ 安全底线轨✓ 识别AI✓ 优化建议→弱点定位✓）+ 业务红队最大盲点（C18履约✓）+ researcher真bug（N_VOTES✓ README✓）。

## ✅ 全部完成（2026-06-30 全 16 节点落地）
续做批次（commit 1c81e36 / cf2682d）：P4-3 补无冲突任务t02 + P2-2 通话级清单 + P2-1 证据跳转锚点 + P3-1 活清单增量(溯源硬闸,grow子命令) + P4-2 persona配比。P2-4 lint卡已存在无需新增。
- 59 单测全绿；综合 demo 报告 playwright 截图视觉验货通过。
- 新 CLI:`evalcall diff`（回归对比）、`evalcall grow`（活清单增量，候选过溯源硬闸进待确认区）。

## 下一步（交由用户决定）
- 分支 `evalcall-improve-20260630` 已于 commit `0f25cd7` 合并入 main（不再是待决事项）。
- 周四与导师过项目（C12识别AI的合规nuance见建模P4-1备注）。

## 阻塞点
无。

## 关键决策
- 用户拍板①:P4-1 保留导师原意「测识别AI」（业务红队的「合规反例」nuance留作周四与导师议，已写进建模P4-1诚实备注）。
- 用户拍板②:全量分阶段 P0→P4 落地。
- 红线（不可违反）:检查点必须可溯源（指令原文或policy来源），数据驱动只增补不替换；安全轨与forbidden共享单一规则源。
- 开发全程在 `evalcall-improve-20260630` 分支进行，已于 commit `0f25cd7` 合并入 main；2026-07-02 起主线开发在 main 上进行。

## 2026-07-02 体检修复记录

全面体检（`docs/EvalCall全面体检-问题与待完善清单-20260702.md`）交叉审查发现并修复 4 类真缺陷（对应建模 `docs/EvalCall体检修复-建模-20260702.md` A 节点）：

- **grow 崩溃**：`evalcall/grow.py` 调 `llm.chat_json` 漏传必填参数 `schema_hint`，`python -m evalcall grow` 实跑必炸；单测未抓到是因 stub 签名比真实签名宽松。已补真实签名参与执行的回归测试。
- **run_id 双轨断裂**：`arena.py` 生成的 uuid 型 run_id 与 `cli.py` 另造的复合 ID 两套并存，`transcripts` 与 `judgments` 对不上号，报告的 `#call-{run_id}` 证据跳转锚点在真实 run 上永远失效。已统一为落盘前强制写回同一 run_id。
- **门禁 fail-open**：判定通道故障（LLM 批异常）时整批退化 `na`，`na` 不进分母 → 无 fail → 反而判出「可上线」，确凿的规则轨证据被静默丢弃。已新增「无法判定」门禁态：na 占比超阈值时不再误判可上线。
- **`--checklist` 白名单剥字段**：同尺复用通道重建 Checkpoint 时白名单漏了 `safety`/`policy_source`，安全红线在 A/B 同尺对比路径上会被降级丢失。已补齐白名单。
- 顺手修复：通话级问题清单中无回放案例的通话行渲染断链锚点。
- 新增 `tests/test_smoke_e2e.py`（mock LLM 全链路 run→judge→report 端到端冒烟，断言产物 schema 与锚点一致），69 测全绿。
- 完整问题清单与逐条实证见 `docs/EvalCall全面体检-问题与待完善清单-20260702.md`。
