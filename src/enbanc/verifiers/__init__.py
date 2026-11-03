"""Default verifier registry.

Names are stable API: a task says `"verifier": "deck"` and this mapping decides
what that means. Add your own alongside, not instead -- a run that cannot
resolve its verifier should fail loudly rather than fall back to "looks fine".
"""

from __future__ import annotations

from .base import Finding, Result, Verifier
from .code import CodeVerifier
from .document import DocumentVerifier
from .slides import SlidesVerifier
from .webpage import WebpageVerifier


def default_verifiers(command: list[str] | None = None) -> dict[str, Verifier]:
    return {
        "document": DocumentVerifier(),
        "page": WebpageVerifier(),
        "deck": SlidesVerifier(),
        "code": CodeVerifier(command=command),
    }


__all__ = ["Finding", "Result", "Verifier", "CodeVerifier", "DocumentVerifier",
           "SlidesVerifier", "WebpageVerifier", "default_verifiers"]
