# Changelog

Format: keep it short. What changed, and why it matters to someone using this.

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
