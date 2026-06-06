"""指令体检 CLI —— 评测的不只是模型，还有任务指令本身。

用法:
    python3 lint_instruction.py <task.yaml> [--out out.json]

读取一条外呼任务指令（YAML），调用 evalcall.lint.lint_instruction 做体检，
检测自相矛盾 / 不可行约束 / 歧义 / 缺失分支，打印人类可读报告；
传 --out 时同时把完整结果写入 JSON 文件。

注意：默认走 claude-cli 后端，2 次 LLM 调用，单条体检约 1-6 分钟，请耐心等待。
"""

import argparse
import json
import os
import sys

try:
    import yaml  # type: ignore
except Exception:  # noqa: BLE001
    print(
        "缺少 PyYAML，请先安装：pip3 install --break-system-packages pyyaml",
        file=sys.stderr,
    )
    sys.exit(2)

from evalcall.lint import lint_instruction

# 维度 / 严重度的中文标签 + 显示符号
_DIM_CN = {
    "conflict": "自相矛盾",
    "infeasible": "不可行约束",
    "ambiguous": "歧义",
    "missing_branch": "缺失分支",
}
_SEV_CN = {"high": "高·致命", "medium": "中", "low": "低"}
_SEV_MARK = {"high": "[!!]", "medium": "[! ]", "low": "[· ]"}


def _load_task(path: str) -> dict:
    if not os.path.exists(path):
        print(f"找不到任务文件：{path}", file=sys.stderr)
        sys.exit(2)
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        print(f"任务文件格式不对（应为 YAML 映射）：{path}", file=sys.stderr)
        sys.exit(2)
    return data


def _print_report(result: dict) -> None:
    task_id = result.get("task_id", "unknown")
    score = result.get("feasibility_score", 0)
    findings = result.get("findings", [])

    line = "=" * 64
    print(line)
    print(f"  指令体检报告  Instruction Lint")
    print(f"  任务：{task_id}")
    print(line)
    print(f"  可遵循度（Feasibility Score）：{score}/100")
    print(f"  总结：{result.get('summary', '')}")
    print(f"  发现问题：{len(findings)} 条")
    print(line)

    if not findings:
        print("  未发现明显可执行性缺陷。")
        print(line)
        return

    for i, f in enumerate(findings, start=1):
        dim = _DIM_CN.get(f.get("dimension", ""), f.get("dimension", ""))
        sev = _SEV_CN.get(f.get("severity", ""), f.get("severity", ""))
        mark = _SEV_MARK.get(f.get("severity", ""), "[  ]")
        print(f"\n  {mark} #{i}  【{dim}】严重度：{sev}    id={f.get('id','')}")
        print(f"      原文A：{f.get('quote_a','')}")
        if f.get("quote_b"):
            print(f"      原文B：{f.get('quote_b','')}   ← 与原文A冲突")
        print(f"      分析：{f.get('analysis','')}")
        print(f"      建议：{f.get('suggestion','')}")

    print("\n" + line)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="指令体检：检测外呼任务指令的自相矛盾/不可行/歧义/缺失分支"
    )
    parser.add_argument("task", help="任务指令 YAML 文件路径")
    parser.add_argument("--out", default=None, help="把完整结果写入此 JSON 文件")
    parser.add_argument("--model", default=None, help="覆盖默认模型（可选）")
    args = parser.parse_args()

    task = _load_task(args.task)
    print(f"[lint] 正在体检 {args.task} …（2 次 LLM 调用，可能需要数分钟）", file=sys.stderr)

    result = lint_instruction(task, model=args.model)
    _print_report(result)

    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"\n[lint] 完整结果已写入：{args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
