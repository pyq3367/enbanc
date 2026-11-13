"""Wrap any command-line agent as a Provider.

The point of a CLI adapter is that enbanc never needs to know which vendor you
use. Anything that accepts a prompt on argv or stdin and prints text on stdout
can be a producer or a judge.

    provider = CliProvider(["claude", "-p", "{prompt}"])
    provider = CliProvider(["codex", "exec", "{prompt}"])
    provider = CliProvider(["my-agent", "--json", "{prompt}"], extract="result")
"""

from __future__ import annotations

import json
import os
import subprocess

# Only operating-system basics are inherited. Provider-specific credentials or
# proxy settings must be passed explicitly by the user via env.
BASE_ENV = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP",
            "TMPDIR", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
            "LANG", "LC_ALL", "LC_CTYPE", "TERM", "PYTHONUTF8"}


def child_environment(overrides: dict | None = None) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key.upper() in BASE_ENV}
    env.update({str(key): str(value) for key, value in (overrides or {}).items()})
    return env

from .base import Reply


class CliProvider:
    def __init__(self, template: list[str], name: str = "cli", cwd: str | None = None,
                 extract: str | None = None, env: dict | None = None):
        self.template = template
        self.name = name
        self.cwd = cwd
        self.extract = extract
        self.env = env or {}

    def complete(self, prompt: str, system: str = "", timeout: int = 900) -> Reply:
        argv = [part.replace("{prompt}", prompt).replace("{system}", system) for part in self.template]
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, cwd=self.cwd,
                                  timeout=timeout, env=child_environment(self.env),
                                  encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            return Reply(text="", ok=False, error=f"timeout after {timeout}s")
        except FileNotFoundError:
            return Reply(text="", ok=False, error=f"command not found: {argv[0]}")

        out = proc.stdout or ""
        meta: dict = {"exit": proc.returncode}
        if self.extract:
            envelope = self._envelope(out)
            if envelope:
                value = envelope.get(self.extract)
                if isinstance(value, str):
                    out = value
                # Usage, when the CLI prints it, is what lets a thin review be
                # told apart from a real one. Read it rather than drop it.
                usage = envelope.get("usage")
                tokens = usage.get("completion_tokens") if isinstance(usage, dict) else None
                if isinstance(tokens, int) and not isinstance(tokens, bool):
                    meta["usage"] = {"completion_tokens": tokens}
        if proc.returncode != 0 and not out.strip():
            return Reply(text="", ok=False, error=(proc.stderr or f"exit {proc.returncode}")[:400])
        return Reply(text=out, ok=proc.returncode == 0, meta=meta)

    def _envelope(self, text: str) -> dict | None:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            obj = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
        return obj if isinstance(obj, dict) else None

    def _extract(self, text: str) -> str | None:
        """Pull a field out of a JSON envelope, if the CLI prints one."""
        obj = self._envelope(text)
        if not obj:
            return None
        val = obj.get(self.extract)
        return val if isinstance(val, str) else None
