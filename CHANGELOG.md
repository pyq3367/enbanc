# Changelog

## 0.1.3 — 2026-11-08

A pass with open findings is no longer a pass. Three outcomes, three exits.

### Added

- **`conditional_pass` is its own verdict.** A judge that returns `pass` while
  still listing findings used to be recorded as a pass; the delivery gate only
  read the verdict, so runs shipped with known defects and the report said
  "passed". Now `pass` + findings normalises to `conditional_pass`, and
  `stage --set P5` refuses unless the caller passes `--accept-residual`.
  Accepted runs record `outcome=accepted_with_residual` plus the residual
  findings, so "shipped with known defects" is countable after the fact.
- **Derived state treats it as `needs_human`**, not `idle` — fixing or
  accepting is a person's decision, not the orchestrator's.

### Why

Two real runs (`p2-closeout`, `rack-pdu`) passed with one or two open findings
and were reported as delivered. The information was in `review-rN.json` the
whole time; nothing read it at the gate.

### Documentation

- The READMEs were rewritten around what the tool does, where it fits, and how
  it works, instead of why it was started. The "Why I made it" section is gone;
  "What it can do" replaces it, and the author page now describes how the
  implementation was actually assembled.
- `docs/reference/rules.md`, `docs/001-model.md` and `docs/003-operations.md`
  now describe the three verdicts and the `accepted_with_residual` outcome.
  `docs/000-why.md` keeps the original audit but points at the current rules.
- Both READMEs are 178 lines and share the same section order, tables and links.

### Tests

3 new smoke tests pin the outcome: `pass` + finding becomes `conditional_pass`,
delivery is refused without explicit acceptance, and the accepted run records
its residual. 62/62 pass with `python tests/run.py`.

Format: keep it short. What changed, and why it matters to someone using this.

## 0.1.1 — 2026-11-06

Two fixes for state that described itself incorrectly. Both came out of
auditing real runs, not out of reading the code.

### Fixed

- **The delivery record is revocable.** A run that passed early and then came
  back `revise` kept `outcome=completed` forever — the field said delivered, the
  derived state said `needs_human`, and anything reading the field believed the
  run was finished. A non-passing review now clears the record and writes
  `outcome_revoked` with the reason; a re-review that passes while the cursor is
  already at `P5` restores it.
- **A pass with open findings is labelled.** `render` appends `, N open` when
  the final verdict is `pass` but findings remain, and `stage --set P5` prints
  them. Low-severity deferred work is allowed; being quiet about it is not.
  `docs/reference/rules.md` §2 documents both rules.
- **Nothing to review is refused.** A review with an empty or missing artifact
  set used to return `pass` — the judge was answering an empty payload. Three
  audited runs reached delivery with an empty directory that way. `review()` now
  raises before calling a judge, and `set_stage("P5")` refuses delivery when no
  non-empty artifact exists (force still works, with a reason).

## 0.1.0 — 2026-11-05

First public release.

### Workflow

- **Derived state.** Eight states computed from evidence on disk. The stage
  cursor is separate and cannot by itself mark a run delivered.
- **Delivery gate.** `P5` requires a passing verdict, or an explicit forced
  stop with a reason.
- **Report contract.** `ready` is the only path to `done`; text without a report
  is `unverified`.
- **Verifiers.** `document`, `page`, `deck`, `code`. Mechanical findings outrank
  a judge's opinion.
- **Escalation chain.** Each judge gets one shot; exhaustion produces
  `needs_human` rather than a lowered bar.
- **Idempotent dispatch.** Re-dispatching the same round is a no-op.
- **Mandate.** Scope is checked mechanically, with `unavailable` reported
  instead of guessed when there is no ground truth.
- **Routing.** Findings go back to the task that owns them; unmatched findings
  are surfaced, not guessed.

### Packaging

- Self-contained WorkBuddy skill using host models and relative resources; no
  author gateway, model matrix or credentials are required.
- Optional offline environment checks, and a minimal skill ZIP export containing
  the public instructions, safety rules and MIT license.
- Positive public-file list, index and history checks, and mandatory checks
  before push; sync state and logs stay local.
- CLI environment inheritance limited to system essentials plus explicit
  settings.
- General safety rules, privacy limits, and contribution and contact
  information.

### Repository

- Issue and pull-request templates, and a private security reporting path.
- CI on Python 3.10, 3.12 and 3.13: run the tests, install the package, smoke
  the console script.
- The publish gate now admits `.github/` Markdown and workflow files. They go
  through the same privacy scan as every other file; the allow-list was widened,
  not bypassed.

### Known gaps

- no cost tracking; that belongs in the provider adapter
- no render step for visual artifacts; structural checks only
- one process, no distributed workers
- account-based cloud calls and desktop skill import are untested here; offline
  tests are not evidence of model entitlement
