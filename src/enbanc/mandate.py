"""Mandate: what this run is allowed to touch.

Without this, "did the work stay in scope" is a judgement call that depends on
whether a reviewer happens to notice. With it, out-of-scope changes are
detected the same way every time.

The rule that matters: an empty allow-list means "not restricted" -- it must
never silently mean "nothing allowed".
"""

from __future__ import annotations

import fnmatch
import json
import subprocess
import time
from pathlib import Path

from .state import Run, atomic_write


def _norm(p: str) -> str:
    return str(p or "").replace("\\", "/").strip().lstrip("./")


def matches(path: str, pattern: str) -> bool:
    path, pattern = _norm(path), _norm(pattern)
    if not pattern:
        return False
    if pattern.endswith("/**"):
        prefix = pattern[:-3].rstrip("/")
        return path == prefix or path.startswith(prefix + "/")
    return fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(path, pattern.rstrip("/") + "/**")


def _git_changed(cwd: Path) -> tuple[list[str], str]:
    probe = subprocess.run(["git", "-C", str(cwd), "rev-parse", "--is-inside-work-tree"],
                           capture_output=True, text=True)
    if probe.returncode != 0 or "true" not in probe.stdout.lower():
        return [], "unavailable"
    paths: set[str] = set()
    for args in (["status", "--porcelain", "--untracked-files=all"], ["diff", "--name-only", "HEAD"]):
        r = subprocess.run(["git", "-C", str(cwd)] + args, capture_output=True, text=True)
        for line in (r.stdout or "").splitlines():
            line = line.rstrip()
            if not line:
                continue
            if args[0] == "status":
                line = line[3:] if len(line) > 3 else line
                if " -> " in line:
                    line = line.split(" -> ")[-1]
            paths.add(_norm(line))
    return sorted(paths), "git"


class Mandate:
    def __init__(self, run: Run):
        self.run = run
        self.path = run.dir / "mandate.json"

    def load(self) -> dict | None:
        if not self.path.exists():
            return None
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, data: dict) -> None:
        atomic_write(self.path, json.dumps(data, ensure_ascii=False, indent=1))

    def init(self, allow: list[str] | None = None, deny: list[str] | None = None,
             acceptance: list[str] | None = None, cwd: str | None = None) -> dict:
        cur = self.load() or {}
        data = {
            "version": int(cur.get("version", 0)) + 1,
            "cwd": _norm(cwd or cur.get("cwd") or "."),
            "allow_paths": list(dict.fromkeys([*(cur.get("allow_paths") or []), *(allow or [])])),
            "deny_paths": list(dict.fromkeys([*(cur.get("deny_paths") or []), *(deny or [])])),
            "acceptance": acceptance or cur.get("acceptance") or [],
            "created": cur.get("created") or time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        self.save(data)
        return data

    def check(self, changed: list[str] | None = None) -> dict:
        m = self.load()
        if not m:
            return {"ok": True, "skipped": "no mandate"}

        if changed is None:
            changed, source = _git_changed(Path(m.get("cwd") or "."))
        else:
            source = "manual"

        if source == "unavailable":
            return {"ok": False, "source": source,
                    "error": "not a git tree; pass an explicit change list"}

        allow = [_norm(x) for x in (m.get("allow_paths") or []) if _norm(x)]
        deny = [_norm(x) for x in (m.get("deny_paths") or []) if _norm(x)]

        violations = []
        for p in changed:
            hit = next((d for d in deny if matches(p, d)), None)
            if hit:
                violations.append({"path": p, "reason": f"matches deny rule {hit}"})
            elif allow and not any(matches(p, a) for a in allow):
                violations.append({"path": p, "reason": "outside the allowed scope"})

        return {"ok": not violations, "source": source, "checked": len(changed),
                "allow": allow, "deny": deny, "violations": violations,
                "version": m.get("version")}
