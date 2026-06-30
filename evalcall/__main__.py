"""使 `python -m evalcall ...` 等价于 `python -m evalcall.cli ...`。

README 与帮助文档以 `python -m evalcall run/report` 为准，本文件提供该入口。
"""
from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    main()
