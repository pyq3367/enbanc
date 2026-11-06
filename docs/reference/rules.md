# Operating rules

The normative part of enbanc. Code implements these; if the two disagree, the
rules win and the code is a bug.

Written by hand. `.sync/publish.py sync` only tells you when the working copy of
the skill has drifted and these rules may need revisiting — it never rewrites
this file.

---

## 1. Derived state

A run's state is computed from evidence, in this order. First match wins.

| State | Condition | Who must act |
|---|---|---|
| `delivered` | review passed **and** cursor is `P5` (or outcome is `completed`) | nobody |
| `ready` | review passed, cursor has not reached `P5` | the orchestrator: deliver it |
| `needs_human` | not passed, and one of: cursor already at `P5`, vote says revise, escalation chain exhausted, run abandoned | a person |
| `blocked` | at least one task `failed` or `timeout` | the orchestrator: retry or take over |
| `stalled` | a task is `running` past its own timeout x1.5 | the orchestrator: `collect` |
| `working` | a task is running and not stalled | nobody |
| `review` | artifacts on disk are newer than the last review | the orchestrator: review |
| `idle` | none of the above | the orchestrator: plan or wait |

Two invariants:

- A cursor at `P5` with a non-passing verdict is **`needs_human`**, never
  `delivered`. This is the failure this project exists to prevent.
- A run that never passed and never got a person's decision stays `needs_human`
  forever. It does not decay into "done".

---

## 2. Delivery gate

`set_stage("P5")` succeeds only if one of these holds:

1. the last review verdict is `pass`
2. a vote resolved to `pass`
3. the caller forces it with an explicit reason

Case 3 records `outcome=stopped_by_user` plus the reason. Cases 1 and 2 record
`outcome=completed`.

A forced delivery is not a process failure — stopping is a legitimate decision.
It is a distinct outcome, and reports must label it. "Unfinished" and "decided
not to finish" produce the same artifact and different obligations.

### The delivery record is revocable

`outcome=completed` is not a permanent stamp. Any later review that is not
`pass` withdraws it: the field is cleared, and `outcome_revoked` records what it
was, when, and which finding set caused the withdrawal. The reverse also holds —
if the cursor is already at `P5` and a re-review passes, `completed` is restored.

Without this, one early pass makes the field claim delivery forever while the
derived state says `needs_human`. Two sources of truth, silently disagreeing, in
the one place a downstream report is most likely to read.

A `pass` is also allowed to carry open findings (low severity, deferred work).
That is fine, but it is not silence: `render` appends `, N open` and the
delivery note must list them.

### A pass is not evidence that anything was produced

A judge sees whatever it is handed. Hand it nothing and it can still answer
`pass` — the verdict is about the payload, not about the disk. Three audited
runs reached `P5` with an empty run directory for exactly this reason.

So two checks sit outside the verdict:

- `review()` refuses to run when no artifact exists or every entry is empty.
  There is nothing to judge, and a verdict on nothing is worse than no verdict.
- `set_stage("P5")` refuses delivery when no non-empty artifact exists. A run
  whose deliverable lives outside the run directory is a legitimate case, so
  `force` with a reason still works — but it has to be a decision, not a default.

---

## 3. Report contract

Every producer ends its reply with one JSON object:

```json
{"state": "ready | blocked | failed | dropped | progress",
 "ready": true,
 "scope_done": ["what you actually touched"],
 "blocked_on": "one sentence",
 "attention": {"question": "one question", "options": ["a", "b"], "blocking": true},
 "evidence": ["file:line or command output line"]}
```

Mapping to task status:

| Report | Status | Meaning |
|---|---|---|
| `ready: true` | `done` | finished, reviewable |
| `dropped` | `done` | nothing needed doing; say why |
| `blocked` | `degraded` | needs one answer from a person |
| `failed` | `failed` | tried, failed, said where |
| `progress` | `unverified` | not finished |
| absent, text present | `unverified` | **looks** finished; nobody declared it |
| absent, no text | `failed` | nothing happened |

`unverified` exists so that "appears done" and "is done" occupy different states.
Treating the first as the second is the most common way a run lies.

---

## 4. Facts outrank opinions

Order of authority, strongest first:

1. a non-zero exit code from a command you ran
2. a verifier finding at `high` severity
3. a verifier finding at `medium` / `low`
4. a judge's verdict
5. a producer's self-report

Anything at levels 1–2 fails the round regardless of what levels 3–5 say. A judge
may add findings; it may never clear one.

---

## 5. Escalation

- Escalation is not retry. Re-submitting to the same judge after a `revise` is a
  wasted round.
- Each judge in the chain gets one shot, in order.
- A judge may be skipped for any reason (cost, availability, a known blind spot).
- When the chain is exhausted the run becomes `needs_human`. The bar is never
  lowered to reach a conclusion.

Why not "retry until pass": a judge that keeps producing fresh findings has
reached its ceiling, not found new truth. Handing the same artifact to the same
judge again measures the judge's variance, not the artifact's quality.

---

## 6. Scope

A mandate bounds what a run may touch.

- Empty `allow_paths` means **unrestricted**, never "nothing allowed".
- A path matching `deny_paths` is a violation regardless of the allow list.
- Out-of-scope changes are detected mechanically (git, or an explicit change
  list). If neither is available the check reports `unavailable` — it does not
  guess.
- Widening the scope is a new mandate version, recorded with a reason.

---

## 7. Failure handling

| Situation | Rule |
|---|---|
| producer returns text, no report | `unverified`; inspect the artifact before treating it as done |
| producer says `blocked` | surface **one** question with options; do not bury it in a report |
| verification command times out | `high` severity, never a pass |
| judge returns unparseable output | retry once, then record `unavailable`; never assume `pass` |
| two tasks write the same path | refuse the dispatch before starting anything |
| process gone, artifact absent | `failed` |
| process gone, artifact present | re-derive from the artifact, then `done` or `unverified` |

---

## 8. What these rules cost

They add a review round to work that might have been fine on the first pass.
That is the trade: slower and honest, instead of fast and occasionally lying
about being finished.

When it is not worth it: single-turn tasks, work you will read yourself
immediately, anything cheap to redo. The gate earns its cost when a wrong "done"
is expensive — a shipped deck, a published page, a decision someone acts on.
