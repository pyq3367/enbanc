"""Document verifier: for reports, proposals, specs, memos.

Checks structure and hygiene, never taste. Anything subjective belongs to the
judge, which reads; anything countable belongs here.
"""

from __future__ import annotations

import re
from pathlib import Path

from .base import Finding

PLACEHOLDER = re.compile(r"\b(TODO|FIXME|TBD|XXX|Lorem ipsum)\b", re.IGNORECASE)
HEADING = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


class DocumentVerifier:
    name = "document"
    suffixes = (".md", ".markdown", ".txt", ".rst")

    def __init__(self, min_chars: int = 200, min_sections: int = 2):
        self.min_chars = min_chars
        self.min_sections = min_sections

    def check(self, artifact: Path, criteria: list[str] | None = None) -> list[Finding]:
        out: list[Finding] = []
        if not artifact.exists():
            return [Finding("missing", str(artifact), "artifact does not exist", "high")]

        text = artifact.read_text(encoding="utf-8", errors="replace")
        if not text.strip():
            return [Finding("missing", artifact.name, "artifact is empty", "high")]

        if len(text) < self.min_chars:
            out.append(Finding("format", artifact.name,
                               f"only {len(text)} chars, below {self.min_chars}",
                               "medium", f"{artifact.name}:1"))

        heads = HEADING.findall(text)
        h1 = [h for lvl, h in heads if len(lvl) == 1]
        if not h1:
            out.append(Finding("format", artifact.name, "no level-1 heading", "medium"))
        if len(heads) < self.min_sections:
            out.append(Finding("format", artifact.name,
                               f"{len(heads)} headings, expected at least {self.min_sections}",
                               "low"))

        for m in PLACEHOLDER.finditer(text):
            line = text[: m.start()].count("\n") + 1
            out.append(Finding("missing", f"{artifact.name}:{line}",
                               f"placeholder left in place: {m.group(0)}",
                               "high", f"{artifact.name}:{line}"))

        # Markdown links with empty targets are almost always unfinished.
        for m in re.finditer(r"\[[^\]]*\]\(\s*\)", text):
            line = text[: m.start()].count("\n") + 1
            out.append(Finding("format", f"{artifact.name}:{line}", "empty link target", "medium"))

        return out
