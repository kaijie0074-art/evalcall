# Superseded acceptance run

本目录是首次 `gpt-5.6-sol/xhigh` 三票正式评测的验收痕迹，仅用于复现 v1→v2 稳定性对比。

首次运行暴露两项元数据问题：票面默认模型写成 `null`，离线已有对话的 `target_model_fingerprint` 写成 `unknown`。判定调用本身成功，但本目录不作为答辩主口径。

正式主口径请使用：`runs/m5_real_recruit_gpt56sol_xhigh_v2_20260712/`。v2 已修复票面模型与离线来源，并由 `diff_vs_v1.json` 记录同尺波动。
