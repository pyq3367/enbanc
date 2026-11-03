"""Route findings back to whoever owns them.

A review produces findings. A repair round needs them split by owner and turned
into an incremental instruction. Doing that by hand is where findings silently
get dropped -- drop one and the next round pays for it again.

Ownership is matched on evidence in the finding text (paths, task ids), never on
semantic similarity. When nothing matches, the finding is `unrouted` and goes to
a person. Guessing here is worse than admitting ignorance: a misrouted fix
touches the wrong artifact and usually introduces a new defect.
"""

from __future__ import annotations

import json
from pathlib import Path

from .state import Run


def _patterns(task: dict) -> list[str]:
    pats = [str(p).replace("\\", "/").lstrip("./") for p in (task.get("scope") or [])]
    if task.get("out"):
        pats.append(str(task["out"]).lstrip("./"))
    pats.append(str(task.get("id", "")))
    return [p for p in pats if p]


def _score(issue: dict, task: dict) -> tuple[int, str]:
    text = f"{issue.get('where','')} {issue.get('detail','')}".replace("\\", "/")
    best, why = 0, ""
    for p in _patterns(task):
        base = p.rstrip("/*")
        if base and base in text:
            score = 3 if "/" in base else 2
            if score > best:
                best, why = score, f"mentions {base}"
    return best, why


def route(run: Run, round_no: int | None = None, tasks: list[dict] | None = None) -> dict:
    reviews = run.state.get("reviews") or []
    if not reviews:
        raise ValueError("no reviews to route")

    round_no = round_no or max(int(r.get("round", 0)) for r in reviews)
    review = next((r for r in reversed(reviews) if int(r.get("round", 0)) == round_no), None)
    if review is None:
        raise ValueError(f"no review for round {round_no}")

    if tasks is None:
        tasks = [{"id": k, **v} for k, v in (run.state.get("tasks") or {}).items()]

    routes, unrouted = [], []
    for issue in review.get("issues") or []:
        if len(tasks) == 1:
            best, why, owner = 1, "only one task", tasks[0]["id"]
        else:
            ranked = sorted(((*_score(issue, t), t["id"]) for t in tasks), key=lambda x: -x[0])
            best, why, owner = ranked[0] if ranked and ranked[0][0] > 0 else (0, "", None)
        if owner is None:
            unrouted.append({"issue": issue, "reason": "no task mentions this location"})
        else:
            routes.append({"issue": issue, "owner": owner, "score": best, "reason": why})

    by_owner: dict[str, list[dict]] = {}
    for r in routes:
        by_owner.setdefault(r["owner"], []).append(r["issue"])

    prompts = {
        owner: _incremental(owner, issues)
        for owner, issues in by_owner.items()
    }

    out = {"round": round_no, "routed": len(routes), "total": len(review.get("issues") or []),
           "by_owner": {k: len(v) for k, v in by_owner.items()},
           "routes": routes, "unrouted": unrouted, "prompts": prompts}
    (run.dir / f"route-r{round_no}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def _incremental(owner: str, issues: list[dict]) -> str:
    lines = [f"## Repair round for {owner}",
             "",
             f"{len(issues)} finding(s) on your previous artifact. Fix these only:", ""]
    for i, it in enumerate(issues, 1):
        lines.append(f"{i}. [{it.get('severity')}/{it.get('type')}] {it.get('where')}")
        lines.append(f"   {it.get('detail')}")
    lines += ["",
              "Constraints:",
              "- Do not redo work that was not flagged.",
              "- Re-check each item; changing the wording is not fixing it.",
              "- End with the report block; scope_done lists what you actually touched."]
    return "\n".join(lines)
