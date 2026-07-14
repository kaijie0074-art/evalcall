"""从真实历史运行产物重建六步工作台的静态展示数据。"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evalcall.demo_server import PRESETS, ROOT, build_static_cache


def main() -> None:
    destination = ROOT / "site-deploy" / "demo-cache.json"
    payload = build_static_cache()
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    destination.write_text(serialized, encoding="utf-8")
    script_destination = ROOT / "site-deploy" / "demo-cache.js"
    script_destination.write_text(f"window.EVALCALL_DEMO_CACHE = {serialized};\n", encoding="utf-8")
    for config in PRESETS.values():
        source = ROOT / config["cache_run"] / "report.html"
        target = ROOT / "site-deploy" / config["cache_report"]
        if source.is_file():
            shutil.copyfile(source, target)
        if config.get("regression_run") and config.get("regression_report"):
            regression_source = ROOT / config["regression_run"] / "report.html"
            regression_target = ROOT / "site-deploy" / config["regression_report"]
            if regression_source.is_file():
                shutil.copyfile(regression_source, regression_target)
    print(f"[evalcall] 静态演示数据已生成：{destination}")
    print(f"[evalcall] 样例 {len(payload['presets'])} 组，步骤 {sum(len(x['steps']) for x in payload['presets'].values())} 个")


if __name__ == "__main__":
    main()
