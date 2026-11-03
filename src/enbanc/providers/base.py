"""Provider interface: how enbanc talks to whatever model or CLI does the work.

enbanc does not ship a model client. It ships a contract. Wrap your CLI, your
HTTP endpoint, or your internal gateway in a Provider and enbanc will use it for
both producing artifacts and judging them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class Reply:
    text: str
    ok: bool = True
    error: str = ""
    meta: dict | None = None


class Provider(Protocol):
    """Minimal surface. One call, one reply, no hidden state."""

    name: str

    def complete(self, prompt: str, system: str = "", timeout: int = 900) -> Reply:
        ...


class ProviderFactory(Protocol):
    """Given a role name, return a provider.

    Roles are deliberately not model names. A deployment maps
    {"producer": ..., "judge": ..., "judge_strong": ...} to real endpoints.
    Keeping the mapping outside the code is what lets the same run be replayed
    against a different model pool.
    """

    def __call__(self, role: str) -> Provider:
        ...
