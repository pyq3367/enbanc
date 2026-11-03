"""Arbiter: run the deterministic checks, then ask a judge, then merge.

Merging rule, in one line: **a fact outranks an opinion**. Anything a verifier
flagged at high severity fails the review no matter what the judge says. The
judge can add findings; it cannot clear them.

Escalation is the part most orchestrators skip. When a judge keeps finding new
problems it is not being thorough, it has hit its ceiling. Re-submitting to the
same judge wastes a round; the next judge should be a different one.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .providers.base import ProviderFactory
from .state import Run, extract_tail_json
from .verifiers.base import Finding

JUDGE_SYSTEM = """You are an independent reviewer. You did not produce this work.

Rules:
1. Output JSON only. No prose, no code fences.
2. You only report problems. You never rewrite the artifact.
3. Check against the acceptance criteria given to you, not against your taste.
4. If nothing is wrong, say pass. Do not invent findings to look useful.
5. `where` must be locatable: file:line, section title, or slide number.

Schema:
{"verdict":"pass|revise","issues":[{"type":"missing|fact|logic|scope|format|risk",
 "where":"...","detail":"...","severity":"high|medium|low","evidence":"..."}]}"""

MAX_PAYLOAD = 24_000


def _read(p: Path, limit: int = 8_000) -> str:
    try:
        t = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return t[:limit] + ("\n... [truncated]" if len(t) > limit else "")


def build_payload(run: Run, entries: list[tuple[str, Path]], findings: list[Finding],
                  criteria: list[str]) -> str:
    parts = ["## Frozen requirement\n", _read(run.requirement_path, 4_000)]

    if criteria:
        parts.append("\n## Acceptance criteria\n" + "\n".join(f"- {c}" for c in criteria))

    if findings:
        parts.append("\n## Mechanically detected (already facts, do not re-judge)\n" +
                     "\n".join(f"- [{f.severity}] {f.where}: {f.detail}" for f in findings))

    for task_id, path in entries:
        parts.append(f"\n## Artifact: {task_id} ({path.name})\n{_read(path)}")

    body = "\n".join(parts)
    return body[:MAX_PAYLOAD] + ("\n... [payload truncated]" if len(body) > MAX_PAYLOAD else "")


def _parse_verdict(text: str) -> dict | None:
    obj = extract_tail_json(text)
    if not isinstance(obj, dict):
        return None
    verdict = str(obj.get("verdict", "")).lower().strip()
    if verdict not in ("pass", "revise"):
        verdict = "revise" if obj.get("issues") else "pass"
    issues = [i for i in (obj.get("issues") or []) if isinstance(i, dict)]
    return {"verdict": verdict, "issues": issues}


def review(run: Run, entries: list[tuple[str, Path]], factory: ProviderFactory,
           verifiers: dict, criteria: list[str] | None = None,
           round_no: int | None = None) -> dict:
    round_no = round_no or (int(run.state.get("rounds", 0)) + 1)
    criteria = criteria or []
    findings: list[Finding] = []

    for task_id, path in entries:
        task = (run.state.get("tasks") or {}).get(task_id) or {}
        name = task.get("verifier") or task.get("kind") or "document"
        verifier = verifiers.get(name) or verifiers.get("document")
        if verifier:
            findings.extend(verifier.check(path, criteria))

    judge = factory("judge")
    payload = build_payload(run, entries, findings, criteria)
    reply = judge.complete(payload, system=JUDGE_SYSTEM)
    verdict = _parse_verdict(reply.text) or {
        "verdict": "unavailable",
        "issues": [],
        "error": reply.error or "judge returned no parseable verdict",
    }

    # facts outrank opinions
    hard = [f for f in findings if f.severity == "high"]
    merged = [f.as_issue() for f in findings]
    seen = {(i.get("where"), i.get("detail")) for i in merged}
    for i in verdict.get("issues") or []:
        key = (i.get("where"), i.get("detail"))
        if key not in seen:
            merged.append({
                "type": str(i.get("type", "logic")),
                "where": str(i.get("where", "unspecified")),
                "detail": str(i.get("detail", "")),
                "severity": str(i.get("severity", "medium")),
                "evidence": str(i.get("evidence", "")),
                "source": "judge",
            })
            seen.add(key)

    final = verdict["verdict"]
    if hard and final == "pass":
        final = "revise"

    record = {
        "round": round_no,
        "judge": getattr(judge, "name", "judge"),
        "verdict": final,
        "issues": merged,
        "counts": {
            "mechanical": len(findings),
            "high": sum(1 for i in merged if i["severity"] == "high"),
        },
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    (run.dir / f"review-r{round_no}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")

    def mut(st: dict) -> dict:
        st.setdefault("reviews", []).append(record)
        st["rounds"] = max(int(st.get("rounds", 0)), round_no)
        return st

    run.update(mut)
    run.event("review", {"round": round_no, "verdict": final, "issues": len(merged)})
    return record


def escalate(run: Run, chain: list[dict]) -> dict:
    """Move to the next judge in the chain. Escalation is not a retry.

    The chain is a deployment decision, not a code constant: put the judges you
    trust in order, and enbanc walks it once each.
    """
    if not chain:
        raise ValueError("escalation chain is empty")

    idx = int(run.state.get("judge_index", 0))
    if idx + 1 >= len(chain):
        run.update(lambda st: {**st, "escalation_exhausted": True})
        run.event("escalate", {"result": "exhausted", "index": idx})
        return {"escalated": False, "reason": "chain exhausted", "index": idx,
                "verdict": "hand over to a person"}

    nxt = idx + 1
    entry = {"from": chain[idx].get("name"), "to": chain[nxt].get("name"),
             "round": run.state.get("rounds", 0), "at": time.strftime("%Y-%m-%dT%H:%M:%S")}

    def mut(st: dict) -> dict:
        st["judge_index"] = nxt
        st.setdefault("escalations", []).append(entry)
        return st

    run.update(mut)
    run.event("escalate", entry)
    return {"escalated": True, **entry}
