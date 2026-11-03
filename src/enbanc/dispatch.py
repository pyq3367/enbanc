"""Dispatch: hand tasks to providers, keep the ledger honest.

Three rules that exist because their absence produced real failures:

1. An artifact is written to disk before its status is recorded. Status is
   derived from the artifact, not the other way round.
2. A task that produced text but no ready report is `unverified`, not `done`.
3. Re-dispatching the same (run, task, requirement, round) is a no-op. Re-running
   by hand is how a bug turns into a bill.
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .providers.base import ProviderFactory
from .state import Run, extract_tail_json, normalize_report, report_to_status

KINDS = ("doc", "page", "deck", "code")


@dataclass
class Task:
    id: str
    prompt: str
    kind: str = "doc"
    out: str = "artifact.md"
    verifier: str = ""
    scope: list[str] = field(default_factory=list)
    criteria: list[str] = field(default_factory=list)
    role: str = "producer"
    cwd: str = "."

    def artifact_path(self, run: Run) -> Path:
        return run.dir / "sub" / self.id / self.out


DEFAULT_OUT = {"doc": "artifact.md", "page": "index.html", "deck": "deck.md", "code": "solution.py"}


def _isolate(tasks: list[Task]) -> None:
    """Two workers writing the same file is silent corruption, so refuse it."""
    seen: dict[str, str] = {}
    for t in tasks:
        key = str(Path(t.cwd) / t.out).lower()
        if key in seen:
            raise ValueError(
                f"tasks {seen[key]!r} and {t.id!r} both write {t.out!r} in the same directory; "
                "give each task its own output file or working directory"
            )
        seen[key] = t.id


def dispatch(run: Run, tasks: list[Task], factory: ProviderFactory, max_parallel: int = 2,
             force: bool = False, resume: bool = False) -> list[dict]:
    _isolate(tasks)

    q = run.queue()
    round_no = max(q["round"], int(run.state.get("rounds", 0)) + 1)
    plan: list[Task] = []
    skipped: list[tuple[str, str]] = []

    for t in tasks:
        key = run.idem_key(t.id, round_no)
        prev = q["tasks"].get(t.id) or {}
        if not force:
            if resume:
                if prev.get("status") in ("done", "skipped"):
                    skipped.append((t.id, "already delivered"))
                    continue
            elif prev.get("key") == key and prev.get("status") in ("done", "skipped"):
                skipped.append((t.id, f"idempotent hit (round {round_no})"))
                continue
        plan.append(t)

    if not plan:
        return [{"id": t, "status": "skipped", "note": why} for t, why in skipped]

    results: list[dict] = []
    lock = threading.Lock()

    def record(t: Task, status: str, **extra) -> None:
        def mut(st: dict) -> dict:
            st.setdefault("tasks", {})[t.id] = {
                "id": t.id, "status": status, "kind": t.kind,
                "started": st.get("tasks", {}).get(t.id, {}).get("started") or time.strftime("%Y-%m-%dT%H:%M:%S"),
                "updated": time.strftime("%Y-%m-%dT%H:%M:%S"), **extra,
            }
            return st
        run.update(mut)
        run.queue_update(lambda qq: {
            **qq,
            "round": round_no,
            "tasks": {**qq["tasks"], t.id: {
                "key": run.idem_key(t.id, round_no),
                "status": "done" if status in ("done", "degraded") else status,
                "round": round_no,
                "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }},
        })

    def work(t: Task) -> dict:
        task_dir = run.dir / "sub" / t.id
        task_dir.mkdir(parents=True, exist_ok=True)
        out_name = t.out or DEFAULT_OUT.get(t.kind, "artifact.md")
        out_path = task_dir / out_name
        started = time.time()
        record(t, "running", pid=None)

        provider = factory(t.role)
        reply = provider.complete(t.prompt)
        elapsed = round(time.time() - started, 1)

        if reply.ok and reply.text:
            out_path.write_text(reply.text, encoding="utf-8", newline="")

        report = normalize_report(extract_tail_json(reply.text))
        status = report_to_status(report, reply.text if reply.ok else None, has_error=not reply.ok)
        payload = {
            "result": reply.text,
            "report": report,
            "provider": reply.name if hasattr(reply, "name") else getattr(provider, "name", ""),
            "error": reply.error,
            "elapsed": elapsed,
        }
        (task_dir / "out.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

        record(t, status, elapsed=elapsed, artifact=str(out_path.relative_to(run.dir)),
               report=report, error=reply.error)
        with lock:
            results.append({"id": t.id, "status": status, "elapsed": elapsed,
                            "artifact": str(out_path), "report": report})
        return results[-1]

    with ThreadPoolExecutor(max_workers=max(1, max_parallel)) as pool:
        list(pool.map(work, plan))

    return results
