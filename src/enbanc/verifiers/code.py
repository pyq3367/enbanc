"""Code verifier: existence plus, optionally, a command.

This is intentionally thin. If you have tests, the ground truth is the test
result -- run it and read the exit code. enbanc does not reimplement pytest and
should not pretend a model's opinion of code outranks a failing test.

Rule of thumb: when a deterministic check exists, the judge's verdict on that
question is only advisory. `command` is where you put the deterministic check.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .base import Finding

DEBRIS = re.compile(r"\b(console\.log|debugger|print\(\s*['\"]debug|TODO|FIXME)\b", re.IGNORECASE)


class CodeVerifier:
    name = "code"
    suffixes = (".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".sh")

    def __init__(self, command: list[str] | None = None, cwd: str | None = None, timeout: int = 300):
        self.command = command
        self.cwd = cwd
        self.timeout = timeout

    def check(self, artifact: Path, criteria: list[str] | None = None) -> list[Finding]:
        out: list[Finding] = []
        if not artifact.exists():
            return [Finding("missing", str(artifact), "file does not exist", "high")]

        text = artifact.read_text(encoding="utf-8", errors="replace")
        if not text.strip():
            return [Finding("missing", artifact.name, "file is empty", "high")]

        for m in DEBRIS.finditer(text):
            line = text[: m.start()].count("\n") + 1
            out.append(Finding("format", f"{artifact.name}:{line}",
                               f"debug residue: {m.group(0)}", "low"))

        if self.command:
            try:
                proc = subprocess.run(self.command, cwd=self.cwd or str(artifact.parent),
                                      capture_output=True, text=True, timeout=self.timeout)
            except subprocess.TimeoutExpired:
                return out + [Finding("risk", " ".join(self.command),
                                      f"verification command timed out after {self.timeout}s", "high")]
            if proc.returncode != 0:
                tail = (proc.stdout or proc.stderr or "").strip().splitlines()[-5:]
                out.append(Finding("risk", " ".join(self.command),
                                   f"verification failed (exit {proc.returncode}): " + " | ".join(tail),
                                   "high", f"exit={proc.returncode}"))
        return out
