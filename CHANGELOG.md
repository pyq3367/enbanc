# Changelog

Format: keep it short. What changed, and why it matters to someone using this.

## Unreleased — local preparation

- Add a self-contained WorkBuddy skill using host models and relative resources;
  no author gateway, model matrix or credentials are required.
- Add optional offline environment checks and a minimal skill ZIP export that
  includes the public instructions, safety rules and MIT license.
- Apply a positive public-file list, index/history checks and mandatory checks
  before push; keep sync state and logs local.
- Limit CLI environment inheritance to system essentials plus explicit settings.
- Add general safety rules, privacy limits and contribution/contact information.
- Account-based cloud calls and desktop skill import remain untested here;
  offline tests are not evidence of model entitlement.

## 0.1.0 — 2026-11-03

First public shape.

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

Known gaps:

- no cost tracking; that belongs in the provider adapter
- no render step for visual artifacts; structural checks only
- one process, no distributed workers
