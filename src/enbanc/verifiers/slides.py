"""Deck verifier for slide sources.

Two input styles are supported because both are common:
- a Markdown deck split by `---` (Marp, Slidev, reveal.js sources)
- a .pptx, checked only for existence and size (no python-pptx dependency)

Overflow is the failure that matters most in decks and the one a text reviewer
never sees: a slide can be structurally valid and still unreadable because
someone pasted 400 characters into it.
"""

from __future__ import annotations

import re
from pathlib import Path

from .base import Finding

PLACEHOLDER = re.compile(r"\b(TODO|FIXME|TBD|XXX)\b", re.IGNORECASE)


class SlidesVerifier:
    name = "slides"
    suffixes = (".md", ".markdown", ".pptx")

    def __init__(self, max_chars_per_slide: int = 400, min_slides: int = 3):
        self.max_chars_per_slide = max_chars_per_slide
        self.min_slides = min_slides

    def check(self, artifact: Path, criteria: list[str] | None = None) -> list[Finding]:
        if not artifact.exists():
            return [Finding("missing", str(artifact), "deck does not exist", "high")]

        if artifact.suffix.lower() == ".pptx":
            size = artifact.stat().st_size
            if size < 10_000:
                return [Finding("format", artifact.name, f"pptx is only {size} bytes; likely empty", "high")]
            return []

        text = artifact.read_text(encoding="utf-8", errors="replace")
        if not text.strip():
            return [Finding("missing", artifact.name, "deck source is empty", "high")]

        slides = re.split(r"^\s*---\s*$", text, flags=re.MULTILINE)
        slides = [s for s in slides if s.strip()]
        out: list[Finding] = []

        if len(slides) < self.min_slides:
            out.append(Finding("missing", artifact.name,
                               f"{len(slides)} slides, expected at least {self.min_slides}", "high"))

        for i, slide in enumerate(slides, 1):
            body = slide.strip()
            if not re.search(r"^#{1,6}\s+\S", body, re.MULTILINE):
                out.append(Finding("format", f"slide {i}", "no heading; slide has no claim", "medium"))
            chars = len(re.sub(r"\s+", "", body))
            if chars > self.max_chars_per_slide:
                out.append(Finding("format", f"slide {i}",
                                   f"{chars} non-space chars (> {self.max_chars_per_slide}); "
                                   "will overflow on a projector", "high"))
            for m in PLACEHOLDER.finditer(body):
                out.append(Finding("missing", f"slide {i}", f"placeholder: {m.group(0)}", "high"))

        return out
