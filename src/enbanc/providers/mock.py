"""A provider that never calls a model.

Two uses:
- tests and the demo run offline, with no keys and no cost
- a template for what a real adapter must return

It is not a fake that "passes". It writes plausible artifacts *and* honest
reports, including blocked ones, so the gates can be exercised for real.
"""

from __future__ import annotations

import hashlib
import re

from .base import Reply


class MockProvider:
    name = "mock"

    def __init__(self, quality: float = 1.0, seed: str = ""):
        self.quality = quality
        self.seed = seed

    def complete(self, prompt: str, system: str = "", timeout: int = 900) -> Reply:
        role = self._role(system or prompt)
        if role == "judge":
            # A real judge spends a few hundred tokens on a verdict. The stub
            # reports a nominal figure so the thin-review gate is not tripped by
            # the mock itself; a test that wants a thin review brings its own
            # judge and its own usage.
            return Reply(text=self._verdict(prompt),
                         meta={"usage": {"completion_tokens": 240}})
        text = self._artifact(prompt)
        return Reply(text=text, meta={"usage": {"completion_tokens": max(1, len(text) // 4)}})

    @staticmethod
    def _role(text: str) -> str:
        low = text.lower()
        if "judge" in low or "review" in low or "找到" in low:
            return "judge"
        return "producer"

    def _artifact(self, prompt: str) -> str:
        """Deterministic output derived from the prompt, so reruns are comparable."""
        h = hashlib.sha1((self.seed + prompt).encode("utf-8")).hexdigest()[:6]
        title = re.sub(r"\s+", " ", prompt.strip())[:60] or "untitled"

        if self.quality < 0.5:
            # Deliberately incomplete: missing sections, placeholder text.
            return (
                f"# {title}\n\n"
                "Draft. TODO: finish the analysis.\n\n"
                '{"state":"progress","ready":false,"scope_done":[],'
                '"blocked_on":"not finished","attention":{},"evidence":[]}'
            )

        body = "\n".join(
            f"## Section {i}\n\nContent for section {i} (ref {h}-{i}).\n"
            for i in range(1, 4)
        )
        report = (
            '{"state":"ready","ready":true,'
            f'"scope_done":["artifact.md"],'
            '"blocked_on":"","attention":{},"evidence":["artifact.md:1"]}'
        )
        return f"# {title}\n\n{body}\n{report}"

    def _verdict(self, prompt: str) -> str:
        """A judge that reads only the artifact, never the criteria.

        Scanning the whole payload is a real bug worth avoiding: an acceptance
        criterion like "no placeholders" would make every artifact look like it
        contains one. A judge must be given the artifact, and judge that.
        """
        marker = "## Artifact:"
        idx = prompt.find(marker)
        body = prompt[idx:] if idx >= 0 else prompt
        incomplete = ("TODO" in body) or ("Draft." in body) or ("Lorem ipsum" in body)
        if incomplete:
            return (
                '{"verdict":"revise","issues":['
                '{"type":"missing","where":"artifact body","detail":"Draft or placeholder left '
                'in place; the analysis is not finished.","severity":"high",'
                '"evidence":"artifact:3"}]}'
            )
        return '{"verdict":"pass","issues":[]}'
