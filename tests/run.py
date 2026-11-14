#!/usr/bin/env python3
"""Run the smoke tests with no third-party dependencies.

    python tests/run.py

Every `test_*` function is called. A parameter named `tmp_path` gets a fresh
temporary directory, matching the pytest fixture of the same name, so the same
file works under pytest when it is available.
"""

from __future__ import annotations

import inspect
import os
import sys
import tempfile
import traceback
from pathlib import Path

sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE))

import test_smoke  # noqa: E402
import test_privacy  # noqa: E402
import test_distribution  # noqa: E402
import test_scoring  # noqa: E402
import test_portable  # noqa: E402
import unittest
from support import make_temp_dir, record_sandbox


def main() -> int:
    tests = [(n, f) for module in (test_smoke, test_privacy, test_distribution, test_scoring)
             for n, f in vars(module).items() if n.startswith("test_") and callable(f)]
    base = os.environ.get("ENBANC_TEST_TMP") or os.environ.get("TMPDIR") or os.environ.get("TEMP") or tempfile.gettempdir()
    base_path = Path(base).resolve()
    project = HERE.parent.resolve()
    if not base_path.is_dir() or base_path == project or project in base_path.parents:
        print("测试临时目录必须位于项目之外的系统或环境指定目录")
        return 1
    tests.sort()
    passed, failed = 0, []

    for name, fn in tests:
        params = inspect.signature(fn).parameters
        kwargs = {}
        tmp = None
        try:
            if "tmp_path" in params:
                tmp = make_temp_dir(base_path)
                kwargs["tmp_path"] = tmp
            fn(**kwargs)
            passed += 1
            print(f"  ok   {name}")
        except Exception as exc:
            failed.append(name)
            frames = traceback.extract_tb(exc.__traceback__)[-2:]
            location = ", ".join(f"{Path(frame.filename).name}:{frame.lineno}" for frame in frames)
            print(f"  FAIL {name} ({location})")
            # 只显示定位，不打印异常值、源代码或临时绝对路径。
        finally:
            if tmp:
                record_sandbox(tmp, base_path)

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(test_portable.PortableTests)
    result = unittest.TestResult()
    suite.run(result)
    portable_failed = len(result.failures) + len(result.errors)
    portable_passed = result.testsRun - portable_failed
    print(f"  portable: {portable_passed}/{result.testsRun} passed")
    passed += portable_passed
    total = len(tests) + result.testsRun
    print(f"\n{passed}/{total} passed")
    if failed or portable_failed:
        print("failed: " + ", ".join(failed + (["portable tests"] if portable_failed else [])))
    return 0 if not failed and not portable_failed else 1


if __name__ == "__main__":
    sys.exit(main())
