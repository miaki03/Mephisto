"""零依赖测试入口：`python tests/run.py`。

为什么不是 `pytest`：当前沙箱里 pip 无法安装任何包（临时目录的 rename 被拦）。
测试全部写成 `unittest.TestCase`，所以：
- 现在：`python tests/run.py` 就能跑，零依赖；
- 以后：装了 pytest 之后，`python -m pytest -q` 也能直接收集同一批用例。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    suite = unittest.defaultTestLoader.discover(
        start_dir=str(ROOT / "tests"), pattern="test_*.py", top_level_dir=str(ROOT)
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
