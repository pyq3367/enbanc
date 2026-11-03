"""离线便携性检查：不执行账户 CLI，不读取认证配置。"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from importlib.resources import files
from pathlib import Path

from .arbiter import review
from .dispatch import Task, dispatch
from .providers import MockProvider
from .state import Run
from .verifiers import default_verifiers


_PACKAGE_FILES = ("__init__.py", "cli.py", "providers/base.py", "providers/mock.py",
                  "verifiers/document.py")


def discover_cli() -> dict[str, bool]:
    """只返回命令是否存在，丢弃 which 返回的绝对路径。"""
    return {name: shutil.which(name) is not None for name in ("codebuddy", "cbc")}


def _source_root() -> Path:
    package = Path(__file__).resolve().parent
    return package.parent.parent if package.parent.name == "src" else package


def temporary_base() -> Path:
    """显式临时目录优先；无效设置不悄悄回退到其他目录。"""
    base = (os.environ.get("ENBANC_TEST_TMP") or os.environ.get("TMPDIR")
            or os.environ.get("TEMP") or tempfile.gettempdir())
    path = Path(base)
    if not path.is_absolute():
        raise ValueError("临时目录必须是项目外已存在的绝对目录")
    path = path.resolve()
    project = _source_root()
    if not path.is_dir() or path == project or project in path.parents:
        raise ValueError("临时目录必须是项目外已存在的绝对目录")
    return path


def mock_smoke() -> bool:
    """在独立临时目录验证生产、审阅与交付门禁。

    The sandbox is left behind rather than deleted here: deletion is a
    separate, explicitly batched operation (tests/cleanup.py), and a check
    command should not remove files as a side effect.
    """
    base = temporary_base()
    root = Path(os.path.join(str(base), f"enbanc-doctor-{os.getpid()}"))
    root.mkdir(parents=True, exist_ok=True)
    requirement = "生成包含三个章节的离线检查报告。"
    run = Run.init("smoke", requirement, root)
    factory = lambda role: MockProvider(seed=role)
    results = dispatch(run, [Task(id="writer", prompt=requirement, out="artifact.md")],
                       factory, max_parallel=1)
    if not all(result["status"] == "done" for result in results):
        return False
    run = Run.load("smoke", root)
    artifact = run.dir / run.state["tasks"]["writer"]["artifact"]
    record = review(run, [("writer", artifact)], factory, default_verifiers(), [])
    if record["verdict"] != "pass":
        return False
    run.set_stage("P5")
    return Run.load("smoke", root).state["outcome"] == "completed"


def run_offline() -> int:
    python_ok = sys.version_info >= (3, 10)
    print(f"Python {sys.version.split()[0]} >=3.10: {'ok' if python_ok else 'unsupported'}")
    print("package path: relative")
    try:
        package = files("enbanc")
        resources_ok = all(package.joinpath(name).is_file() for name in _PACKAGE_FILES)
        skill_found = package.joinpath("SKILL.md").is_file()
        if not skill_found:
            skill_found = (_source_root() / "SKILL.md").is_file()
    except (OSError, ModuleNotFoundError):
        resources_ok, skill_found = False, False
    print(f"package resources: {'found' if resources_ok else 'missing'}")
    print(f"skill entry: {'found' if skill_found else 'not-found (optional)'}")
    for name, found in discover_cli().items():
        print(f"CLI {name}: {'found' if found else 'not-found (optional)'}")
    print("office: optional/not-checked")
    print("desktop login: unverified（需要用户人工确认）")
    print("model permissions: unverified（需要用户人工确认）")
    print("provider workbuddy: unsupported（未实现账户适配，不调用模型）")
    try:
        smoke_ok = mock_smoke()
    except Exception:
        # 异常可能包含路径或环境内容，诊断只报告组件状态。
        smoke_ok = False
    print(f"provider mock: {'available' if smoke_ok else 'failed'}")
    ok = python_ok and resources_ok and smoke_ok
    print(f"offline: {'available' if ok else 'failed'}")
    return 0 if ok else 1
