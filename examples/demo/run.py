#!/usr/bin/env python3
"""End-to-end demo, offline.

Runs the same job twice:

  A. a producer that finishes        -> review passes -> delivery allowed
  B. a producer that leaves TODOs    -> review fails -> escalate -> chain ends
                                       -> delivery refused, hand to a person

The second one is the interesting half. A framework that can only show the happy
path has not shown you anything.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from enbanc import Run, Task, dispatch, escalate, review, route  # noqa: E402
from enbanc.providers import MockProvider  # noqa: E402
from enbanc.verifiers import default_verifiers  # noqa: E402

ROOT = Path(__file__).resolve().parent / "runs"
CHAIN = [{"name": "judge-a"}, {"name": "judge-b"}]


def banner(text: str) -> None:
    print(f"\n{'=' * 4} {text} {'=' * 4}")


def scenario(run_id: str, quality: float) -> None:
    banner(f"{run_id}: producer quality={quality}")
    req = "Produce a competitive analysis with three sections."

    run = Run.init(run_id, req, ROOT)
    run.update(lambda st: {**st, "criteria": ["three sections", "no placeholders"],
                           "judge_index": 0})
    print(run.render())

    tasks = [Task(id="analyst", prompt=req, kind="doc", out="analysis.md",
                  verifier="document", scope=["analysis.md"])]
    results = dispatch(run, tasks, lambda role: MockProvider(quality=quality, seed=role))
    for r in results:
        print(f"  dispatch: {r['id']} -> {r['status']} ({r['elapsed']}s)")

    run = Run.load(run_id, ROOT)
    print("  collect:", run.collect() or "consistent")

    entries = [(tid, run.dir / t["artifact"])
               for tid, t in run.state["tasks"].items() if t.get("artifact")]

    rec = review(run, entries, lambda role: MockProvider(quality=1.0, seed=role),
                 default_verifiers(), run.state["criteria"])
    print(f"  review r{rec['round']}: {rec['verdict']} ({len(rec['issues'])} findings)")
    for i in rec["issues"][:4]:
        print(f"    [{i['severity']}/{i['type']}] {i['where']}: {i['detail'][:70]}")

    if rec["verdict"] != "pass":
        routed = route(run, rec["round"])
        print(f"  route: {routed['routed']}/{routed['total']} assigned, "
              f"{len(routed['unrouted'])} unrouted")

        esc = escalate(run, CHAIN)
        print(f"  escalate: {esc}")

        rec2 = review(run, entries, lambda role: MockProvider(quality=1.0, seed=role),
                      default_verifiers(), run.state["criteria"])
        print(f"  review r{rec2['round']}: {rec2['verdict']}")

        esc2 = escalate(run, CHAIN)
        print(f"  escalate: {esc2}")

    run = Run.load(run_id, ROOT)
    print("  state:", run.render())

    banner(f"{run_id}: try to deliver")
    try:
        run.set_stage("P5")
        print("  delivered:", run.state.get("outcome"))
    except (PermissionError, ValueError) as e:
        print(f"  refused: {e}")

    print("  final:", Run.load(run_id, ROOT).render())


if __name__ == "__main__":
    scenario("demo-pass", quality=1.0)
    scenario("demo-fail", quality=0.3)
    print("\nDone. Inspect runs/ to see reviews, routes and events.")
