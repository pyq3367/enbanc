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

### Three verdicts, not two

`pass` means nothing is open. `revise` means a high-severity finding, or the
judge refused. Between them sits `conditional_pass`: the judge thinks the work
can ship but has left findings open. It is not a pass, and it does not become
one by being written next to the word `pass`.

The normalisation is mechanical, so the caller cannot skip it:

- any `high` finding (verifier or judge) forces `revise`
- `pass` with a non-empty finding list becomes `conditional_pass`

`set_stage("P5")` refuses a `conditional_pass` run unless the caller passes
`--accept-residual`. Accepting writes `outcome=accepted_with_residual` and keeps
the finding list in `outcome_residual`, so "shipped with known defects" stays
countable instead of dissolving into a plain pass.

Why the middle state exists at all: two audited runs reached `P5` with one or
two findings open and a report that said "passed". The information was in
`review-r<N>.json` the whole time; the gate simply read the verdict and stopped.

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
- Delivery also refuses when a task is still `failed`, `timeout`, `unverified`,
  or carries an unanswered blocking question. A passing verdict on the artifacts
  says nothing about the task that never delivered.

### The quantity gate sits before delivery

Delivery additionally requires that work was actually split: entering `P5`
— and every stage from `P2` on — with fewer than two dispatched tasks is
refused (§9). The refusal names the waiver on its way out:

```
stage refused: quantity gate — 1 task dispatched (< 2)
solo runs need --allow-solo --reason "<why one worker suffices>"
```

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
| review usage below threshold with a passing verdict | verdict becomes `insufficient_review`, treated as not passed (§10) |
| fewer than two tasks dispatched | refuse the stage transition; a waiver needs an explicit reason (§9) |
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

---

## 9. The quantity gate and the solo waiver

Split work, or say why you did not. A run entering `P2`, `P3`, `P4` or `P5`
with fewer than two dispatched tasks is refused by default: work that was
never divided cannot have been read by anyone else, so "reviewed" would be
a claim without a witness.

The refusal is waivable, never silent:

| Exit | What it costs | What it records |
|---|---|---|
| `--allow-solo --reason "<...>"` | the reason must be stated | `state.solo_waiver`; the delivery summary surfaces it in the degradation section |
| `--force` | crosses the gate without a waiver | `force_skip_quantity_gate`, countable after the fact |

Forcing is a recorded decision, not a hidden pass. Anything that crossed a
gate can later be counted from disk.

---

## 10. Review usage

A verdict has a cost, and the cost is evidence. The code reads the
reviewer's reported `completion_tokens`: below a threshold (default 120)
and with a verdict of `pass` or `conditional_pass`, the verdict is
rewritten to `insufficient_review` and handled as a failure — it cannot
reach `P5`.

| Reviewer reported | Verdict | Outcome |
|---|---|---|
| usage ≥ threshold | `pass` / `conditional_pass` | stands; normal rules apply |
| usage < threshold | `pass` / `conditional_pass` | rewritten to `insufficient_review`, treated as failed |
| no usage reported | any | marked unverified usage, not an offence |
| no usage reported, `--require-usage` | any | treated as failed |

Whether usage is reported at all is the provider's decision, so absence is
not guilt: without evidence the run marks the review unverified rather than
asserting laziness. Only an explicit `--require-usage` from the caller
turns a missing report into a failure.

---

## 11. Write-set parallelism

Parallelism is derived from declarations, not judged by hand. A task may
declare the paths it writes (`writes`); the scheduler groups tasks by
conflict:

| Relationship between write sets | Scheduling |
|---|---|
| disjoint | different groups, run in parallel |
| overlapping (same file, or one path inside the other's directory) | same group, serial |
| not declared, same output file | refused before dispatch |

The third row keeps the second honest: serial execution prevents races
between declared writers, but two undeclared tasks aiming at one file are
a collision the scheduler refuses outright rather than serialise into an
overwrite. What can run in parallel follows from what was declared.

---

## 12. The external inspector

The main session is not a reliable monitor: it is compacted, interrupted,
and displaced by the next task. The inspector is a separate read-only pass
— it runs once, reads the state once, prints, and exits. It modifies
nothing.

It alarms with exit code `10` when any of these hold. The first five are
findings about a run; the sixth is the inspector's own fallback, so a broken
state file is reported instead of silently disappearing from the sweep:

| Code | Condition |
|---|---|
| `no_state` | a requirement file exists but no state file |
| `stalled` | the cursor has sat at `P0`/`P1` past the stall threshold |
| `zero_sub` | the cursor is past `P2` yet no task was ever dispatched, unless a waiver is recorded |
| `thin_review` | any review round reported usage below the threshold |
| `zombie` | a task is marked `running` past 1.5× its own timeout |
| `bad_state` | the state file cannot be read; one broken run does not affect the others |

Exit codes: `0` — nothing found; `10` — at least one alarm; `2` — the
inspector itself could not run. It runs at three fixed points: after
decomposition, before delivery, and once a day.
