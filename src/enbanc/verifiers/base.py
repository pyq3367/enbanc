"""Verifier interface: the real extension point.

A verifier answers one question mechanically: "does this artifact meet the
criteria I can check without a model?" Anything it flags is a fact, not an
opinion, so the judge cannot overrule it.

Why this is the extension point and not the provider: swapping models changes
who writes and who reads. Swapping verifiers changes what "done" means. The
second is what makes enbanc usable for documents, pages and decks instead of
only for code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

SEVERITIES = ("high", "medium", "low")


@dataclass
class Finding:
    type: str          # missing | fact | logic | scope | format | risk
    where: str
    detail: str
    severity: str = "medium"
    evidence: str = ""
    source: str = "verifier"

    def as_issue(self) -> dict:
        return {
            "type": self.type,
            "where": self.where,
            "detail": self.detail,
            "severity": self.severity if self.severity in SEVERITIES else "medium",
            "evidence": self.evidence,
            "source": self.source,
        }


class Verifier(Protocol):
    name: str
    suffixes: tuple[str, ...]

    def check(self, artifact: Path, criteria: list[str] | None = None) -> list[Finding]:
        ...


@dataclass
class Result:
    verifier: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return any(f.severity == "high" for f in self.findings)
