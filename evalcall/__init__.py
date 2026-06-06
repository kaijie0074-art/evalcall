"""EvalCall —— 外呼对话模型指令遵循自动评测系统。

核心引擎（Agent A 负责）：
- llm.py       LLM 后端抽象（openai / claude-cli / mock）
- compiler.py  指令 → 检查点清单 编译器
- judge.py     双轨评测引擎（规则轨 + LLM 轨）
- cli.py       命令行入口（run / report）

其余模块由其他 Agent 提供：simulator.py / arena.py / report.py。
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
