"""Dispatch: hand tasks to providers, keep the ledger honest.

Four rules that exist because their absence produced real failures:

1. An artifact is written to disk before its status is recorded. Status is
   derived from the artifact, not the other way round.
2. A task that produced text but no ready report is `unverified`, not `done`.
3. Re-dispatching the same (run, task, requirement, round) is a no-op. Re-running
   by hand is how a bug turns into a bill.
4. Tasks that write disjoint paths may run at the same time; tasks whose write
   sets overlap run one after another. Parallelism is derived from what each
   task declares it touches, never from how many tasks were queued.
"""

from __future__ import annotations

import json
import os
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
    # Declared write set, relative to `cwd`. Empty means "just the output file".
    # Naming every path a task writes is what lets independent work run in
    # parallel instead of being serialised out of caution.
    writes: list[str] = field(default_factory=list)

    def artifact_path(self, run: Run) -> Path:
        return run.dir / "sub" / self.id / self.out


DEFAULT_OUT = {"doc": "artifact.md", "page": "index.html", "deck": "deck.md", "code": "solution.py"}


def _normalise(cwd: str, item: str) -> str:
    """One comparable form for a path: absolute-ish, forward slashes, lower case.

    Case is folded because a Windows and a POSIX view of the same tree must not
    disagree about whether two tasks collide on `Report.md`.
    """
    joined = item if os.path.isabs(item) else os.path.join(cwd or ".", item)
    return os.path.normpath(joined).replace("\\", "/").lower()


def write_paths(task: Task) -> list[str]:
    """The paths a task claims. A declared set wins; otherwise it is its output."""
    declared = task.writes or [task.out or DEFAULT_OUT.get(task.kind, "artifact.md")]
    return [_normalise(task.cwd, item) for item in declared]


def overlaps(a: str, b: str) -> bool:
    """Paths collide when equal, or when one is a directory containing the other."""
    if a == b:
        return True
    return b.startswith(a.rstrip("/") + "/") or a.startswith(b.rstrip("/") + "/")


def plan_groups(tasks: list[Task]) -> list[list[str]]:
    """Split tasks into groups that must not run concurrently.

    Each group is a connected component of the "our write sets overlap" graph.
    Within a group the order is the order given; between groups everything is
    free to run at once. This is what turns "five tasks" into "three workers and
    a queue" without a person guessing which pairs are safe.
    """
    ids = [t.id for t in tasks]
    paths = {t.id: write_paths(t) for t in tasks}
    parent = {i: i for i in ids}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if any(overlaps(x, y) for x in paths[a] for y in paths[b]):
                union(a, b)

    grouped: dict[str, list[str]] = {}
    for tid in ids:
        grouped.setdefault(find(tid), []).append(tid)
    return list(grouped.values())


def _isolate(tasks: list[Task]) -> None:
    """Two workers writing the same file is silent corruption, so refuse it.

    A declared write set is the exception: those tasks are scheduled against
    each other by `plan_groups` and run one after another. Undeclared work has
    no such guarantee, so it is rejected before anything starts.
    """
    for i, a in enumerate(tasks):
        if a.writes:
            continue
        for b in tasks[i + 1:]:
            if b.writes:
                continue
            key = str(Path(a.cwd) / (a.out or DEFAULT_OUT.get(a.kind, "artifact.md"))).lower()
            key_b = str(Path(b.cwd) / (b.out or DEFAULT_OUT.get(b.kind, "artifact.md"))).lower()
            if key == key_b:
                raise ValueError(
                    f"tasks {a.id!r} and {b.id!r} both write {a.out!r} in the same directory; "
                    "give each task its own output file or working directory, or declare "
                    "'writes' if you really mean them to share a path in turn"
                )


def dispatch(run: Run, tasks: list[Task], factory: ProviderFactory, max_parallel: int = 3,
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
        item = {"id": t.id, "status": status, "elapsed": elapsed,
                "artifact": str(out_path), "report": report}
        with lock:
            results.append(item)
        return item

    # Serial inside a group, parallel between groups. The partition comes from
    # the declared write sets, so nobody has to eyeball which tasks are safe to
    # run together.
    by_id = {t.id: t for t in plan}

    def run_group(group: list[str]) -> list[dict]:
        return [work(by_id[tid]) for tid in group]

    with ThreadPoolExecutor(max_workers=max(1, max_parallel)) as pool:
        list(pool.map(run_group, plan_groups(plan)))

    return results
