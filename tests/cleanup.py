#!/usr/bin/env python3
"""Remove test sandboxes in small batches.

The host limits how many paths one turn may delete. The test run therefore
leaves its sandboxes behind and records them; this script removes them in
batches that stay under that limit. Run it until it reports nothing left.

    python tests/cleanup.py [--limit 40]

It only touches directories this test suite created, named with the
enbanc-test- or enbanc-portable- prefix, under the chosen base directory.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from support import remove_tree  # noqa: E402

PREFIXES = ("enbanc-test-", "enbanc-portable-", "enbanc-doctor-")


def base_dir() -> Path:
    candidate = (os.environ.get("ENBANC_TEST_TMP") or os.environ.get("TMPDIR")
                 or os.environ.get("TEMP"))
    if candidate:
        return Path(candidate).resolve()
    import tempfile
    return Path(tempfile.gettempdir()).resolve()


def main() -> int:
    parser = argparse.ArgumentParser(description="remove enbanc test sandboxes in small batches")
    parser.add_argument("--limit", type=int, default=40,
                        help="maximum number of paths to remove in this run")
    parser.add_argument("--base", default=None)
    args = parser.parse_args()

    base = Path(args.base).resolve() if args.base else base_dir()
    if not base.is_dir():
        print("base directory does not exist")
        return 1

    removed = 0
    for entry in sorted(base.iterdir()):
        if removed >= args.limit:
            break
        if not entry.name.startswith(PREFIXES):
            continue
        remove_tree(entry)
        removed += 1

    remaining = [entry.name for entry in base.iterdir() if entry.name.startswith(PREFIXES)]
    print(f"removed {removed} sandbox(es); {len(remaining)} remaining")
    if remaining:
        print("run this again to finish, or raise --limit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
