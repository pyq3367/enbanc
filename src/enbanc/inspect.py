"""External inspection: read run directories and report what looks wrong.

The main session is not a reliable monitor. It gets compacted, interrupted, or
pulled onto the next task, and the run nobody is watching is the run that ships
with a defect. So the check lives outside the execution path: it runs once,
reads, prints, and exits.

It never writes. A monitor that mutates state is another thing to verify.

Exit codes:

| Code | Meaning |
|---|---|
| 0    | nothing found |
| 10   | at least one finding |
| 2    | bad usage (unreadable root, unusable threshold) |

Findings, one per line: `<run>  <code>  <detail>`. Every code below exists
because a real run looked finished and was not.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .state import SOLO_GATED

# A run parked at intake or planning is abandoned work, not work in progress.
# Later stages can legitimately sit still while a person reads the output.
STALL_STAGES = ("P0", "P1")
DEFAULT_STALL_MIN = 120.0
DEFAULT_THIN_TOKENS = 120


def _last_activity(state: dict, run_dir: Path) -> float | None:
    """Epoch seconds of the newest evidence of life.

    The event log is authoritative when present: it records every stage move and
    collection, not just the last full state write.
    """
    log = run_dir / "events.jsonl"
    stamp = None
    if log.is_file():
        try:
            lines = [ln for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]
            if lines:
                stamp = json.loads(lines[-1]).get("ts")
        except (OSError, ValueError):
            stamp = None
    stamp = stamp or state.get("created")
    if not stamp:
        return None
    try:
        return time.mktime(time.strptime(str(stamp), "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return None


def _zombie(task: dict) -> bool:
    if task.get("status") != "running":
        return False
    try:
        started = time.mktime(time.strptime(task["started"], "%Y-%m-%dT%H:%M:%S"))
    except (KeyError, ValueError):
        return False
    return (time.time() - started) > float(task.get("timeout") or 900) * 1.5


def inspect_run(run_dir: Path, stall_min: float = DEFAULT_STALL_MIN,
                thin_tokens: int = DEFAULT_THIN_TOKENS) -> list[dict]:
    """Findings for one run directory. Read-only, never raises for bad data."""
    findings: list[dict] = []
    state_path = run_dir / "state.json"
    requirement = run_dir / "requirement.md"

    if not state_path.exists():
        # A directory with neither file is not a run, just a leftover folder.
        if requirement.exists():
            findings.append({"code": "no_state", "run": run_dir.name,
                             "detail": "requirement frozen but no state.json"})
        return findings

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise ValueError
    except (OSError, ValueError):
        return [{"code": "bad_state", "run": run_dir.name,
                 "detail": "state.json unreadable or not an object"}]

    stage = str(state.get("stage", ""))
    tasks = state.get("tasks") or {}
    if not isinstance(tasks, dict):
        tasks = {}

    # Zero sub-agents after the work was supposed to be divided. A waiver is
    # the one legitimate reason, and it has to be on the record.
    if stage in SOLO_GATED and not tasks and not state.get("solo_waiver"):
        findings.append({"code": "zero_sub", "run": run_dir.name,
                         "detail": f"cursor at {stage} with no task ever dispatched"})

    # A pass bought with a handful of tokens reviewed nothing.
    for review in state.get("reviews") or []:
        if not isinstance(review, dict):
            continue
        usage = review.get("usage") or {}
        tokens = usage.get("completion_tokens") if isinstance(usage, dict) else None
        if isinstance(tokens, int) and not isinstance(tokens, bool) and tokens < thin_tokens:
            findings.append({
                "code": "thin_review", "run": run_dir.name,
                "detail": f"round {review.get('round', '?')} verdict={review.get('verdict', '?')} "
                          f"in {tokens} completion tokens (< {thin_tokens})",
            })

    # Running past its own timeout x1.5: the process is gone, the status is not.
    for tid, task in tasks.items():
        if isinstance(task, dict) and _zombie(task):
            findings.append({"code": "zombie", "run": run_dir.name,
                             "detail": f"task {tid} running past timeout x1.5"})

    # Parked at intake or planning for longer than the stall threshold.
    if stage in STALL_STAGES:
        last = _last_activity(state, run_dir)
        if last is not None:
            idle = (time.time() - last) / 60.0
            if idle > stall_min:
                findings.append({"code": "stalled", "run": run_dir.name,
                                 "detail": f"cursor at {stage}, idle {idle:.0f} min (> {stall_min:g})"})

    return findings


def inspect_root(root: Path, stall_min: float = DEFAULT_STALL_MIN,
                 thin_tokens: int = DEFAULT_THIN_TOKENS) -> list[dict]:
    """Findings for every run under `root`. A broken run does not stop the sweep."""
    root = Path(root)
    if not root.is_dir():
        raise ValueError(f"run root is not a directory: {root}")
    findings: list[dict] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        try:
            findings.extend(inspect_run(child, stall_min, thin_tokens))
        except Exception:  # noqa: BLE001 - one bad run must not hide the others
            findings.append({"code": "bad_state", "run": child.name,
                             "detail": "inspection raised while reading this run"})
    return findings
