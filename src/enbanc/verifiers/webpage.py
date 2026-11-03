"""Web page verifier.

Structural only. It cannot tell you whether the page looks good -- that needs a
renderer and eyes. What it can do is catch the failures that are certain:
no title, images without alt, dead anchors, missing viewport, runaway inline
styles.

Pair it with a render step (screenshot) when the artifact is visual; a text
review of HTML is blind by construction.
"""

from __future__ import annotations

import re
from pathlib import Path

from .base import Finding

TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
HTML_TAG = re.compile(r"<html\b([^>]*)>", re.I)
IMG = re.compile(r"<img\b[^>]*>", re.I)
ANCHOR = re.compile(r"<a\b[^>]*href=\"([^\"]*)\"[^>]*>", re.I)
INLINE_STYLE = re.compile(r"\bstyle=\"", re.I)


class WebpageVerifier:
    name = "webpage"
    suffixes = (".html", ".htm")

    def __init__(self, max_inline_styles: int = 40):
        self.max_inline_styles = max_inline_styles

    def check(self, artifact: Path, criteria: list[str] | None = None) -> list[Finding]:
        out: list[Finding] = []
        if not artifact.exists():
            return [Finding("missing", str(artifact), "page does not exist", "high")]

        text = artifact.read_text(encoding="utf-8", errors="replace")
        if not text.strip():
            return [Finding("missing", artifact.name, "page is empty", "high")]

        m = TITLE.search(text)
        if not m or not m.group(1).strip():
            out.append(Finding("format", artifact.name, "missing or empty <title>", "high"))

        m = HTML_TAG.search(text)
        if not m or "lang=" not in m.group(1).lower():
            out.append(Finding("format", artifact.name, "<html> has no lang attribute", "medium"))

        if 'name="viewport"' not in text.lower():
            out.append(Finding("format", artifact.name, "no viewport meta; will not scale on mobile", "medium"))

        for img in IMG.finditer(text):
            tag = img.group(0)
            if "alt=" not in tag.lower():
                line = text[: img.start()].count("\n") + 1
                out.append(Finding("format", f"{artifact.name}:{line}", "<img> without alt", "medium"))

        for a in ANCHOR.finditer(text):
            href = a.group(1).strip()
            if href in ("", "#"):
                line = text[: a.start()].count("\n") + 1
                out.append(Finding("format", f"{artifact.name}:{line}", f"dead anchor href={href!r}", "medium"))

        inline = len(INLINE_STYLE.findall(text))
        if inline > self.max_inline_styles:
            out.append(Finding("format", artifact.name,
                               f"{inline} inline style attributes (> {self.max_inline_styles}); "
                               "likely unmaintainable", "low"))

        return out
