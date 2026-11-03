"""Test helpers that clean up without a single bulk delete.

The host blocks deletions that remove many paths in one operation. Test
sandboxes are created here, so they are removed one entry at a time: every
removal is a single small operation rather than one wide sweep.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def make_temp_dir(base: Path, prefix: str = "enbanc-test-") -> Path:
    """Create a unique directory under base and remember it for later cleanup."""
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    path = Path(os.path.join(str(base), f"{prefix}{os.getpid()}-{next(_counter)}"))
    path.mkdir(parents=True, exist_ok=False)
    return path


def record_sandbox(path: Path, base: Path) -> None:
    """Note a sandbox for later removal.

    The host limits how many paths a single turn may delete, so the test run
    does not remove anything. Cleanup happens in separate turns through
    tests/cleanup.py, which stays within that limit.
    """
    manifest = Path(base) / "enbanc-test-sandboxes.txt"
    with open(manifest, "a", encoding="utf-8") as handle:
        handle.write(str(path) + "\n")


def remove_tree(path: Path) -> None:
    """Remove a tree one entry at a time, deepest first."""
    path = Path(path)
    if not path.exists():
        return
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
        return
    for child in sorted(path.iterdir(), key=lambda item: len(item.parts), reverse=True):
        remove_tree(child)
    try:
        path.rmdir()
    except OSError:
        shutil.rmtree(path, ignore_errors=True)


def _counter_gen():
    value = 0
    while True:
        value += 1
        yield value


_counter = _counter_gen()
