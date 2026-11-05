# Contributing

## Before you start

Open an issue for anything larger than a typo. A few constraints are deliberate
and easy to break by accident:

- a run must not be markable as delivered without a passing review
- a task without a structured report is `unverified`, never `done`
- mechanical verifier findings outrank a judge's opinion
- derived state comes from evidence on disk, not from what a caller claims

## Local checks

No third-party dependencies are needed to run the tests:

```bash
python tests/run.py
```

Optional, for the `enbanc` console script:

```bash
python -m pip install -e .
enbanc --help
```

## Pull requests

Keep one change to one concern. Describe the failure mode being fixed, not only
the code that changed. Add or adjust a test when behaviour changes, and update
`CHANGELOG.md` when a user-visible behaviour changes.

## What will not be merged

- vendor-specific model identifiers or endpoints in the core
- telemetry, network calls at import time, or credential storage
- anything that lets `stage --set P5` bypass the review gate
- generated artifacts committed to the repository

## License

Contributions are accepted under the MIT license in `LICENSE`.
