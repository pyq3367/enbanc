"""Run state: the single source of truth for one orchestration run.

Design rule: the stage a run is in is *derived* from evidence on disk, never
asserted by the agent doing the work. A caller can move the cursor (P0..P5),
but only evidence can move the run to "delivered".
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

DEFAULT_ROOT = Path(os.environ.get("ENBANC_ROOT", "runs"))
STAGES = ("P0", "P1", "P2", "P3", "P4", "P5")
STAGE_LABEL = {
    "P0": "intake",
    "P1": "plan",
    "P2": "produce",
    "P3": "review",
    "P4": "escalate",
    "P5": "deliver",
}
OUTCOMES = ("completed", "stopped_by_user", "abandoned")

# Derived states, in match order. "person" states mean: no progress without a human.
DERIVED = ("delivered", "ready", "needs_human", "blocked", "stalled", "working", "review", "idle")

TERMINAL = ("done", "degraded", "failed", "timeout", "unverified")


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class FileLock:
    """Cross-process lock backed by the operating system.

    The lock file is created once and kept. It is never deleted: removing it
    on release would make the lock racy (a process could hold the name while
    another removes it) and would turn every state write into a delete
    operation. The OS releases the lock when the process exits, including
    when it is killed, so a crashed run cannot leave a stale lock behind.
    """

    def __init__(self, path: Path, timeout: float = 15.0, stale: float = 25.0):
        self.path = Path(path)
        self.timeout = timeout
        self.stale = stale
        self.fd: int | None = None

    def _acquire(self, fd: int) -> bool:
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError:
                return False
            return True
        import fcntl
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    def _release(self, fd: int) -> None:
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        start = time.time()
        while True:
            fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o600)
            if os.fstat(fd).st_size == 0:
                # Windows locks a byte range, so the file needs at least one byte.
                try:
                    os.write(fd, b"0")
                except OSError:
                    pass
            if self._acquire(fd):
                self.fd = fd
                return self
            os.close(fd)
            if time.time() - start > self.timeout:
                raise TimeoutError(f"lock timeout: {self.path}")
            time.sleep(0.01)

    def __exit__(self, *exc):
        if self.fd is not None:
            self._release(self.fd)
            os.close(self.fd)
            self.fd = None
        return False


# ---------------------------------------------------------------- report parsing


def extract_tail_json(text: str | None) -> dict | None:
    """Pull the last balanced JSON object out of a model reply.

    The contract is: the report block sits at the end. Scanning from the right
    is more robust than from the left, because prose is full of braces.
    Returns None on any doubt -- callers must treat that as "unverified".
    """
    if not text:
        return None
    t = str(text).rstrip()
    end = t.rfind("}")
    if end < 0:
        return None
    depth = 0
    for i in range(end, -1, -1):
        ch = t[i]
        if ch == "}":
            depth += 1
        elif ch == "{":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(t[i : end + 1])
                except json.JSONDecodeError:
                    return None
                return obj if isinstance(obj, dict) else None
    return None


REPORT_STATES = ("ready", "blocked", "failed", "dropped", "progress")


def normalize_report(raw: Any) -> dict | None:
    if not isinstance(raw, dict):
        return None
    state = str(raw.get("state", "")).strip().lower()
    if state not in REPORT_STATES:
        return None
    att = raw.get("attention") or {}
    if not isinstance(att, dict):
        att = {}
    return {
        "state": state,
        "ready": bool(raw.get("ready")) and state == "ready",
        "scope_done": [str(x) for x in (raw.get("scope_done") or []) if str(x).strip()],
        "blocked_on": str(raw.get("blocked_on") or "").strip(),
        "attention": {
            "question": str(att.get("question") or "").strip(),
            "options": [str(x) for x in (att.get("options") or [])],
            "blocking": bool(att.get("blocking")),
        },
        "evidence": [str(x) for x in (raw.get("evidence") or []) if str(x).strip()],
    }


def report_to_status(report: dict | None, result_text: str | None, has_error: bool = False) -> str:
    """Map a report (or its absence) to a task status.

    The only path to "done" is an explicit ready claim. Text without a report
    is "unverified" -- it looks like work but nobody declared it finished.
    """
    if report:
        st = report["state"]
        if st == "ready" and report["ready"]:
            return "done"
        if st == "dropped":
            return "done"
        if st == "blocked":
            return "degraded"
        if st == "failed":
            return "failed"
        return "unverified"
    if result_text and not has_error:
        return "unverified"
    return "failed"


# ---------------------------------------------------------------- run


@dataclass
class Run:
    run_id: str
    root: Path = DEFAULT_ROOT
    state: dict = field(default_factory=dict)

    # -- paths ---------------------------------------------------------------
    @property
    def dir(self) -> Path:
        return Path(self.root) / self.run_id

    @property
    def state_path(self) -> Path:
        return self.dir / "state.json"

    @property
    def queue_path(self) -> Path:
        return self.dir / "queue.json"

    @property
    def requirement_path(self) -> Path:
        return self.dir / "requirement.md"

    # -- lifecycle -----------------------------------------------------------
    @classmethod
    def init(cls, run_id: str, requirement: str, root: Path | str = DEFAULT_ROOT,
             main_model: str = "") -> "Run":
        run = cls(run_id, Path(root))
        if not requirement.strip():
            raise ValueError("requirement must not be empty: it is frozen at intake")
        sha = hashlib.sha1(requirement.encode("utf-8")).hexdigest()

        if run.state_path.exists():
            old = json.loads(run.state_path.read_text(encoding="utf-8"))
            if old["requirement_sha1"] == sha:
                run.state = old
                return run
            raise ValueError(
                "requirement changed since this run was frozen "
                f"({old['requirement_sha1'][:12]} -> {sha[:12]}); start a new run_id"
            )

        run.dir.mkdir(parents=True, exist_ok=True)
        atomic_write(run.requirement_path, requirement)
        run.state = {
            "run_id": run_id,
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "stage": "P0",
            "outcome": "",
            "requirement_sha1": sha,
            "main_model": main_model,
            "rounds": 0,
            "tasks": {},
            "reviews": [],
            "escalations": [],
        }
        run.save()
        run.event("init", {"requirement_sha1": sha[:12]})
        return run

    @classmethod
    def load(cls, run_id: str, root: Path | str = DEFAULT_ROOT) -> "Run":
        run = cls(run_id, Path(root))
        if not run.state_path.exists():
            raise FileNotFoundError(f"run not found: {run.state_path}")
        run.state = json.loads(run.state_path.read_text(encoding="utf-8"))
        return run

    def save(self) -> None:
        atomic_write(self.state_path, json.dumps(self.state, ensure_ascii=False, indent=1))

    def update(self, mutator: Callable[[dict], dict]) -> dict:
        with FileLock(self.dir / ".state.lock"):
            current = json.loads(self.state_path.read_text(encoding="utf-8"))
            self.state = mutator(current) or current
            self.save()
        return self.state

    def event(self, kind: str, data: dict) -> None:
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "type": kind, "data": data}
        with open(self.dir / "events.jsonl", "a", encoding="utf-8", newline="") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # -- queue ---------------------------------------------------------------
    def queue(self) -> dict:
        if not self.queue_path.exists():
            return {"round": 1, "tasks": {}}
        try:
            q = json.loads(self.queue_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"round": 1, "tasks": {}}
        q.setdefault("tasks", {})
        q.setdefault("round", 1)
        return q

    def queue_update(self, mutator: Callable[[dict], dict]) -> dict:
        with FileLock(self.dir / ".state.lock"):
            q = mutator(self.queue())
            atomic_write(self.queue_path, json.dumps(q, ensure_ascii=False, indent=1))
        return q

    def idem_key(self, task_id: str, round_no: int | None = None) -> str:
        rnd = round_no if round_no is not None else max(self.queue()["round"], self.state.get("rounds", 0) + 1)
        raw = f"{self.run_id}|{task_id}|{self.state.get('requirement_sha1','')}|{rnd}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    # -- reviews -------------------------------------------------------------
    def last_review(self) -> dict | None:
        rs = self.state.get("reviews") or []
        return rs[-1] if rs else None

    def is_passed(self) -> bool:
        last = self.last_review()
        if last and last.get("verdict") == "pass":
            return True
        return (self.state.get("vote") or {}).get("final") == "pass"

    def is_conditional(self) -> bool:
        last = self.last_review() or {}
        return last.get("verdict") == "conditional_pass"

    # -- derived stage -------------------------------------------------------
    def derive(self) -> str:
        st = self.state
        tasks = st.get("tasks") or {}
        outcome = st.get("outcome") or ""
        passed = self.is_passed()
        vote = st.get("vote") or {}

        if outcome == "abandoned":
            return "needs_human"
        if passed:
            if outcome == "completed" or st.get("stage") == "P5":
                return "delivered"
            return "ready"
        if st.get("stage") == "P5" or vote.get("final") == "revise" or st.get("escalation_exhausted"):
            return "needs_human"
        if any(t.get("status") in ("failed", "timeout") for t in tasks.values()):
            return "blocked"
        if any(self._stale(t) for t in tasks.values()):
            return "stalled"
        if any(t.get("status") == "running" and not self._stale(t) for t in tasks.values()):
            return "working"
        if self._unreviewed():
            return "review"
        # conditional_pass: shippable but with findings. Neither pass nor revise —
        # a person has to decide, so it must not read as "idle".
        if self.is_conditional():
            return "needs_human"
        return "idle"

    @staticmethod
    def _stale(task: dict, grace: float = 1.5) -> bool:
        if task.get("status") != "running":
            return False
        try:
            started = time.mktime(time.strptime(task["started"], "%Y-%m-%dT%H:%M:%S"))
        except (KeyError, ValueError):
            return False
        return (time.time() - started) > float(task.get("timeout") or 900) * grace

    def _unreviewed(self) -> bool:
        subs = list((self.dir / "sub").glob("*/out.json"))
        if not subs:
            return False

        def newest(paths: Iterable[Path]) -> float:
            ts = 0.0
            for p in paths:
                try:
                    ts = max(ts, p.stat().st_mtime)
                except OSError:
                    pass
            return ts

        reviewed = newest((self.dir).glob("review-r*.json"))
        delivered = newest(subs)
        return delivered > reviewed

    def artifacts_present(self) -> bool:
        """Is there at least one non-empty artifact on disk for this run?

        A passed review is not evidence that anything was produced — the
        judge can be handed an empty payload. Delivery checks the disk.
        """
        for task in (self.state.get("tasks") or {}).values():
            rel = task.get("artifact")
            if not rel:
                continue
            p = self.dir / rel
            try:
                if p.is_file() and p.stat().st_size > 0:
                    return True
            except OSError:
                continue
        return False

    # -- stage gate ----------------------------------------------------------
    def set_stage(self, target: str, force: bool = False, reason: str = "",
                  accept_residual: bool = False) -> None:
        if target not in STAGES:
            raise ValueError(f"stage must be one of {STAGES}")
        if target == "P5":
            if self.is_conditional() and not accept_residual and not force:
                last = self.last_review() or {}
                raise PermissionError(
                    "refusing to mark delivered: last verdict=conditional_pass with %d open "
                    "finding(s). Fix them, or accept them explicitly (--accept-residual)."
                    % len(last.get("issues") or [])
                )
            if not self.is_passed() and not self.is_conditional() and not force:
                last = self.last_review() or {}
                raise PermissionError(
                    "refusing to mark delivered: last verdict=%s (%d open findings). "
                    "Pass review, escalate, or force with a reason."
                    % (last.get("verdict", "none"), len(last.get("issues") or []))
                )
            if not self.is_passed() and not self.is_conditional() and not reason.strip():
                raise ValueError("forcing delivery requires --reason: 'unfinished' and 'decided not to' are different things")
            if not self.artifacts_present() and not force:
                raise PermissionError(
                    "refusing to mark delivered: no artifact exists on disk. "
                    "Produce the deliverable, or force with a reason if there is none."
                )

        def mut(st: dict) -> dict:
            st["stage"] = target
            if target == "P5":
                if self.is_passed():
                    st["outcome"] = "completed"
                elif self.is_conditional() and accept_residual:
                    # Accepted with findings: not completed (it did not pass),
                    # not stopped_by_user (nobody halted it). Keep it separable
                    # so the rate of "shipped with known defects" is countable.
                    st["outcome"] = "accepted_with_residual"
                    st["outcome_residual"] = (self.last_review() or {}).get("issues") or []
                    st["outcome_reason"] = "user accepted %d open finding(s)" % len(st["outcome_residual"])
                    st["outcome_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                else:
                    st["outcome"] = "stopped_by_user"
                    st["outcome_reason"] = reason.strip()
                    st["outcome_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            return st

        self.update(mut)
        self.event("stage", {"to": target, "outcome": self.state.get("outcome", "")})

    # -- collect -------------------------------------------------------------
    def collect(self) -> list[str]:
        """Reconcile task status with what is actually on disk.

        This is the fix for the classic failure: the worker process died, out.raw
        is 0 bytes, but state still says "running" forever.
        """
        changes: list[str] = []
        tasks = dict(self.state.get("tasks") or {})

        for tid, task in list(tasks.items()):
            out = self.dir / "sub" / tid / "out.json"
            result, report, info = None, None, {}
            if out.exists():
                try:
                    info = json.loads(out.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    info = {}
                result = info.get("result")
                report = normalize_report(info.get("report")) or normalize_report(extract_tail_json(result))

            if report:
                task["report"] = report

            status = task.get("status")
            if status in TERMINAL:
                if status == "done" and report and not report["ready"] and report["state"] != "dropped":
                    task["status"] = "unverified"
                    changes.append(f"{tid}: done -> unverified (declared {report['state']})")
                continue
            if status != "running":
                continue

            alive = bool(task.get("pid")) and _pid_alive(task["pid"])
            if alive and not self._stale(task):
                continue

            if report and report["ready"]:
                task["status"] = "done"
                changes.append(f"{tid}: running -> done (ready report)")
            elif result and not info.get("is_error"):
                task["status"] = "unverified"
                changes.append(f"{tid}: running -> unverified (text without ready)")
            else:
                task["status"] = "failed"
                task["error"] = "process gone, no artifact" if not alive else "exceeded timeout x1.5"
                changes.append(f"{tid}: running -> failed (no usable artifact)")

        if changes:
            self.update(lambda st: {**st, "tasks": tasks})
            for c in changes:
                self.event("collect", {"change": c})
        return changes

    # -- rendering -----------------------------------------------------------
    def render(self) -> str:
        st = self.state
        tasks = st.get("tasks") or {}
        counts = {k: sum(1 for t in tasks.values() if t.get("status") == k) for k in TERMINAL}
        parts = [f"{counts['done'] + counts['degraded']} done"]
        if counts["unverified"]:
            parts.append(f"{counts['unverified']} unverified")
        parts.append(f"{counts['failed'] + counts['timeout']} failed")
        parts.append(f"{sum(1 for t in tasks.values() if t.get('status') == 'running')} running")
        last = self.last_review()
        tail = ""
        if last:
            n = len(last.get("issues") or [])
            tail = f" | review r{last.get('round','?')} {last.get('verdict','?')} ({n} findings)"
            if last.get("verdict") == "pass" and n:
                # "pass (3 findings)" reads like zero problems. Say it plainly:
                # a pass can still leave items the delivery note must cover.
                tail += f", {n} open"
        esc = len(st.get("escalations") or [])
        return "[%s] %s | cursor %s | %s%s%s" % (
            self.run_id,
            self.derive(),
            st.get("stage", "?"),
            "/".join(parts),
            f" | escalations {esc}" if esc else "",
            tail,
        )


def _pid_alive(pid: int) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(k32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            k32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
