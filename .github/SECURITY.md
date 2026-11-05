# Security policy

## Reporting a vulnerability

Do not open a public issue. Use GitHub's private reporting:

<https://github.com/pyq3367/enbanc/security/advisories/new>

Include the affected version, a minimal reproduction, and what an attacker
gains. If the report involves a run directory, strip model names, keys and
local paths first.

## Scope

enbanc runs commands and writes files on the machine that invokes it. In scope:

- path escape from a run directory or from `--root`
- command injection through task fields, requirement text or provider settings
- credentials or environment values copied into run artifacts
- a change that lets `stage --set P5` mark work delivered without a passing review

Out of scope: the behaviour of the models you connect, and the security of
third-party CLIs you point `--provider-cli` at.

## What to expect

Acknowledgment within 7 days. This is a small project maintained in spare time;
there is no bounty, and no guaranteed fix window.
