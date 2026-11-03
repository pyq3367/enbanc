# 001 — Model

Seven objects. Everything else is a field on one of them.

| Object | Lives in | Owns | Written by |
|---|---|---|---|
| **Run** | `state.json` | requirement hash, cursor, tasks, reviews, escalations, outcome | the orchestrator |
| **Task** | `state.json.tasks[id]` | one unit of production | the dispatcher |
| **Report** | `out.json.report` | a producer's claim about its own work | the producer |
| **Review** | `review-r<N>.json` | verdict + findings for one round | the arbiter |
| **Mandate** | `mandate.json` | what paths this run may touch | the orchestrator |
| **Route** | `route-r<N>.json` | findings split by owner | the router |
| **Outcome** | `state.json.outcome` | how the run ended | the delivery gate |

## Task status

```
          ┌──────────┐
          │ pending  │
          └────┬─────┘
               │ dispatch
          ┌────▼─────┐
          │ running  │
          └────┬─────┘
     ┌─────────┼─────────┬───────────┐
     │         │         │           │
  ready    blocked   no report   process gone
     │         │         │           │
  ┌──▼──┐  ┌───▼────┐ ┌──▼───────┐ ┌─▼──────┐
  │ done│  │degraded│ │unverified│ │ failed │
  └─────┘  └────────┘ └──────────┘ └────────┘
```

`unverified` is the state that does not exist in most frameworks. A task lands
there when text came back but nobody declared it finished. It is not a failure
and it is not a success; it is a claim that has not been adjudicated.

`degraded` means blocked-with-an-answer-available: the producer stopped to ask
one question. The question is in `report.attention.question`.

## Run state

The cursor (`P0`..`P5`) and the derived state are different things.

- **Cursor** — a bookmark the orchestrator moves. `P0` intake, `P1` plan, `P2`
  produce, `P3` review, `P4` escalate, `P5` deliver. Moving it is cheap and
  proves nothing.
- **Derived state** — computed from disk, in [rules.md](reference/rules.md).
  This is the one to show a person.

The gate connects them: the cursor may only reach `P5` when the derived evidence
supports it.

## Outcome

Three values, and the difference matters:

| Outcome | Means | Obligation |
|---|---|---|
| `completed` | passed review | none |
| `stopped_by_user` | a person decided to ship it anyway | report must lead with the open findings |
| `abandoned` | the requirement changed, this run is void | keep the artifacts, drop the conclusions |

Collapsing these three into "done" is how a run that was never reviewed gets
quoted six months later as if it had been.

## Idempotency

`sha1(run_id | task_id | requirement_sha1 | round)`.

The round component is essential and easy to get wrong. Without it, the second
round of repairs hits the same key as the first, is treated as "already
delivered", and is silently skipped — the repair loop appears to run and does
nothing. The round is `max(queue.round, state.rounds + 1)`, so it advances when a
review completes.

## Concurrency and locking

- One `state.json` per run, guarded by a lock file (`O_EXCL`, stale after 25s).
- Every mutation is read-modify-write inside the lock, then written via a temp
  file and `os.replace`. No partial JSON, ever.
- Dispatch runs tasks in a thread pool. There is no distributed mode.
- Two tasks writing the same path is refused **before** anything starts. It is
  not a race to be managed; it is a plan that cannot work.

## What is deliberately absent

| Not modelled | Why |
|---|---|
| a session per task | the provider owns sessions; enbanc owns artifacts |
| message queues between tasks | tasks do not talk; they produce files and reports |
| cost accounting | provider-specific, and wrong numbers are worse than none |
| a scheduler | a run is started by a person, not by an event |
| retry policies on the judge | escalation replaces retry; see [rules.md](reference/rules.md) §5 |
