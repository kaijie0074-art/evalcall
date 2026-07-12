#!/usr/bin/env python3
"""把答辩主张映射到产物、JSON 值与 SHA-256，失败则非零退出。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parent.parent


def _pointer(data: Any, pointer: str) -> Any:
    current = data
    for part in pointer.split(".") if pointer else []:
        if isinstance(current, list):
            current = current[int(part)]
        elif isinstance(current, dict):
            current = current[part]
        else:
            raise KeyError(pointer)
    return current


def build(registry_path: Path) -> dict[str, Any]:
    registry = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
    rows = []
    for claim in registry.get("claims") or []:
        evidence_rows = []
        for item in claim.get("evidence") or []:
            relative = str(item["path"])
            path = ROOT / relative
            row: dict[str, Any] = {"path": relative, "exists": path.is_file()}
            if path.is_file():
                row["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
                if item.get("pointer"):
                    row["pointer"] = str(item["pointer"])
                    try:
                        value = _pointer(json.loads(path.read_text(encoding="utf-8")), row["pointer"])
                        row["value"] = value
                        if "expected" in item:
                            row["expected"] = item["expected"]
                            row["matches"] = value == item["expected"]
                    except Exception as exc:  # noqa: BLE001
                        row["error"] = f"{type(exc).__name__}: {exc}"
                        row["matches"] = False
            evidence_rows.append(row)
        passed = bool(evidence_rows) and all(
            item.get("exists") and item.get("matches", True) for item in evidence_rows
        )
        rows.append({"id": claim["id"], "claim": claim["claim"], "passed": passed, "evidence": evidence_rows})
    return {
        "schema_version": 1,
        "registry": str(registry_path.relative_to(ROOT)),
        "passed": all(row["passed"] for row in rows),
        "claims": rows,
    }


def markdown(result: dict[str, Any]) -> str:
    lines = [
        "# EvalCall 答辩证据索引",
        "",
        "> 自动生成。每条主张都绑定原始产物、JSON 指针、实测值与 SHA-256。",
        "",
        f"总门禁：**{'PASS' if result['passed'] else 'FAIL'}**",
        "",
    ]
    for claim in result["claims"]:
        lines += [f"## {'✅' if claim['passed'] else '❌'} {claim['claim']}", ""]
        for item in claim["evidence"]:
            detail = f"`{item['path']}`"
            if item.get("pointer"):
                detail += f" · `{item['pointer']}` = `{json.dumps(item.get('value'), ensure_ascii=False)}`"
            if item.get("sha256"):
                detail += f" · SHA-256 `{item['sha256']}`"
            lines.append(f"- {detail}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", default="data/evidence_registry.yaml")
    parser.add_argument("--out-json", default="runs/evidence_index_20260712.json")
    parser.add_argument("--out-md", default="docs/EvalCall证据索引-20260712.md")
    args = parser.parse_args()
    result = build(ROOT / args.registry)
    out_json = ROOT / args.out_json
    out_md = ROOT / args.out_md
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out_md.write_text(markdown(result), encoding="utf-8")
    print(f"evidence index: {'PASS' if result['passed'] else 'FAIL'} ({len(result['claims'])} claims)")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
