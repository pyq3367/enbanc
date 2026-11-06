# enbanc

A multi-model workflow for creating and reviewing documents, slide decks, websites, and other work.

English | [简体中文](README.zh-CN.md)

I often run into the same problem when working with AI: a model says it has finished, but I open the file and find something missing. Asking it to check again sometimes helps, and sometimes just produces another assurance that everything is fine. On a longer job, especially one that moves between models or sessions, keeping track of what was actually done gets harder.

enbanc grew out of those jobs. It splits up the work, gives it to different models, arranges a separate review, and carries the findings back into the next round of changes. It also keeps a record of each run. I use this approach for proposals, research reports, presentations, websites, and other things worth checking carefully. For a quick question or a first draft, the extra steps usually aren't necessary.

The name comes from *en banc*, a legal term for a hearing before the full court. Here it refers to reviewing work together; this project does not provide legal services.

## Why I made it

Looking back through my own records, I found six runs whose stages had all reached `P5`. In five of them, the last review still said `revise`. The records showed the work as delivered without a matching review result.

The script allowed the stage to be changed directly, without checking whether the review had passed. That made me reconsider how much a written instruction such as “check before delivering” could accomplish on its own. I started putting some of those requirements into the scripts: a run cannot finish normally without passing review, and a task without an explicit completion report is marked separately.

Those six runs explain where the project came from; they are not an evaluation of its effectiveness. A failed review does not necessarily mean the work is wrong. Sometimes the acceptance criteria need changing, or the review itself needs questioning.

## About the author and the implementation

I'm not a professional programmer. enbanc is a general-purpose skill I have gradually developed around what I need while using AI.

During development, I try a request with different models and ask other models to check the results. Some handle the implementation; others check it or suggest a different approach. I then adjust things based on what happens when I use them. Most of the implementation has come from that back-and-forth, with the code largely generated with model assistance.

I'm sharing a working method that I use and continue to revise. The code in this repository is a reference implementation prepared for public use, not something that should be considered thoroughly validated just because it includes a review process. If you try it, I suggest starting with a small job whose scope is clear. Feedback about problems or better approaches is welcome.

## How a run works

Before starting, describe what you want made, what should be delivered, and what must not be changed. The work can go to several producers, with a reviewer assigned separately. Checks that can run in code come first; model review handles the rest. Findings go back into the work, and the revised result is checked again.

The run keeps its requirement, task states, outputs, and reviews. Producers are expected to return a structured report saying what they completed, where the evidence is, and whether a person needs to decide anything. For example:

```json
{"state": "ready", "ready": true,
 "scope_done": ["report.md"],
 "attention": {"question": "", "options": [], "blocking": false},
 "evidence": ["report.md:12"]}
```

A task that returns text without a valid report is marked `unverified`, rather than accepted on the strength of “finished.” The report is still a claim made by a model, so it does not replace checking the file.

Run status and task reports are separate. Stages `P0` through `P5` record a position in the workflow. When displaying progress, the script also derives a status from the review records, task states, and output files:

| State | What it means |
|---|---|
| `working` | A task is running |
| `stalled` | A task is still recorded as running but has exceeded the timeout threshold and needs checking |
| `blocked` | A task failed or timed out |
| `review` | New task output is waiting for review |
| `ready` | Review passed, but the run has not been closed |
| `delivered` | Review passed and the run entered the delivery stage |
| `needs_human` | Someone needs to intervene, for example after the review chain runs out or the workflow ends without passing |
| `idle` | None of the states above currently applies |

Entering `P5` normally requires a passed review. If you decide to stop instead, `--force --reason "..."` records why. The result is kept as `outcome=stopped_by_user`, not counted as a completed, passed run. A recorded delivery can also be withdrawn: if a later review comes back `revise`, the completed marker is cleared and the reason is kept, so the field and the derived state never disagree about whether the work shipped.

If repeated reviews aren't helping the work converge, you can change reviewers. The reference implementation uses `enbanc escalate` to record progress through a review chain and asks for human intervention when the chain runs out. It does not guarantee that the next model is stronger or switch model requests just from the list; the integration layer has to configure which model is actually called.

Findings from programmatic checks and model review are stored together. A verifier finding marked `high` prevents the round from passing, and the model cannot remove that finding directly. Verifiers can also be wrong, particularly when they use a heuristic such as a character limit. Check that a rule makes sense for your work before relying on it.

## Using it in WorkBuddy

After downloading, import `SKILL.md` and `references/safety-rules.md` using the skill package structure, or ask an authorized host to install this public skill. Start it with `/enbanc` and describe your workspace, deliverable, and acceptance criteria. You can also run `python .sync/publish.py export-skill` to create an importable ZIP locally; this does not upload anything.

This mode uses the host's existing account models and tools. It does not need my gateway, API key, model matrix, or local directories. `default`, `lite`, and `reasoning` follow the host's actual configuration and do not guarantee three different models. With only one available model, separate-context review is possible, but must be labeled rather than described as cross-model verification. Access to a specific model still depends on your account's actual permissions.

Give each task a short set of rules: stay within the approved directory, keep credentials out of materials, pass only what is needed, keep review read-only, check claims against sources, set time and cost limits, and do not mark failed work complete. The included rules are in `references/safety-rules.md`; the skill does not actively copy private memory or computer settings. Missing Office, rendering, or other tools are reported as limitations, rather than making the workflow depend on a missing file from my machine.

See [docs/004-environment.md](docs/004-environment.md) for installation and prerequisites. The host skill and the Python reference implementation below are separate entry points; Python is not required to use the WorkBuddy skill.

## Try it first

You need Python 3.10 or later. From the repository directory, run the offline example without configuring a model or an API key:

```bash
python examples/demo/run.py
python tests/run.py
```

The example uses a mock provider to simulate model replies and writes files and review records locally. It shows one result that passes the checks and another that leaves placeholders and cannot be delivered normally. This demonstrates the workflow, not the quality of a real model. The example does not require Git; the full tests do, to check the index and history gates, and they clean up their own temporary files.

To try the commands individually, install the reference implementation:

```bash
pip install -e .
enbanc doctor --offline
```

Save the following as `tasks.json` in the repository directory:

```json
[{"id": "analyst", "prompt": "Write a three-section analysis",
  "kind": "doc", "out": "analysis.md", "verifier": "document"}]
```

Then run these commands in order:

```bash
enbanc --root runs init --run-id demo --requirement "Write a three-section analysis"
enbanc --root runs dispatch --run-id demo --tasks tasks.json --provider mock
enbanc --root runs collect --run-id demo
enbanc --root runs review --run-id demo --provider mock
enbanc --root runs render --run-id demo
enbanc --root runs stage --run-id demo --set P5
```

Files are saved under `runs/demo/`. The last step can close the run normally only after the review returns `pass`. If it returns `revise`, address the findings first.

## Connecting your own models

The repository currently provides mock and CLI adapters, without being tied to one model service. The CLI adapter passes prompts as command arguments and reads standard output. For an HTTP service, you can write an adapter using the provider interface.

Production and review are separated by the role names `producer` and `judge`. You can configure different models for them, but the project does not select them automatically. When using a command-line agent, pass the task and review instructions through that tool's actual arguments; changing the executable name alone is not enough.

The interface is in [providers/base.py](src/enbanc/providers/base.py), and the CLI adapter is in [providers/cli.py](src/enbanc/providers/cli.py). The public reference implementation currently handles text output. It does not include a complete Office document creation or editing toolchain; producing actual PowerPoint or Word files requires additional tools.

## What it can check today

The repository includes a few basic verifiers to try and extend:

| Verifier | Current checks |
|---|---|
| `document` | Text structure, placeholders, empty links, and minimum length |
| `page` | HTML title, `lang`, viewport, `img alt`, and some in-page anchors |
| `deck` | Slide count, character count, and placeholders in Markdown decks; existence and file size only for PPTX |
| `code` | File existence, some debugging residue, and an optional test command |

These are basic checks. Too many characters don't prove that a slide overflows, and meeting a length requirement doesn't make the content useful. Passing an HTML check says nothing about whether a website is well designed. Presentations, websites, and formatted documents still need to be opened or rendered and inspected. The current implementation also does not automatically verify every citation or factual claim.

See [docs/002-verifiers.md](docs/002-verifiers.md) for adding a verifier. Start with checks you actually need and include locations so findings can be checked against the original file. Reviews involving trade-offs still need human judgment.

## Keeping it updated

The public repository and the local skill used for everyday work are maintained separately. A local skill may contain specific models, gateways, and paths, so the public version needs its own preparation rather than a copy of the entire skill directory.

The current update steps are:

```bash
python .sync/publish.py sync
python .sync/publish.py check
python .sync/publish.py stage
```

`sync` detects changes to the local skill; it does not merge them into the public implementation. Code and documentation updates still need to be prepared manually. `check` runs the privacy scan, tests, and structural and identifier checks on the English and Chinese READMEs. It cannot judge whether a translation reads naturally or guarantee that it finds every sensitive detail. `stage` runs the checks again and reports whether local changes need attention; it does not stage them. Checks also cover staged content and reachable local branch and tag history, so deleting a sensitive value from the working copy is not enough. Unknown files and binaries are rejected by default.

Only after reviewing the content, committing locally, and configuring a remote should you use `python .sync/publish.py push --yes`. That command really uploads; the three preceding commands do not. See [docs/003-operations.md](docs/003-operations.md) for the procedure.

The public version does not include my computer configuration, gateway, credentials, or private run records, and has no callback to an author-operated service. Account-based cloud models still process the material you provide; a sanitized repository does not mean fully offline use. See [docs/005-privacy.md](docs/005-privacy.md) for the checks and their limits. Full regression testing of the additions was interrupted by a local test-cleanup protection and is still pending; passing the text scan does not make this version ready to publish.

## Working on it together

This is a personal project developed through use. I'm not a professional developer, so updates and replies may take time. Contributions to the code, documentation, tests, and integrations are welcome. You're also welcome to fork it or maintain and share your own repository under the MIT license, retaining the required notices. You do not need separate permission from me.

If you build your own version, I'd like to hear where you use it and what you've changed. Send me a repository link when convenient so we can learn from each other's work. Letting me know is an invitation, not an extra condition on using or distributing the project.

Contact: [pyq3367@zoho.com](mailto:pyq3367@zoho.com). This is a project contact address I have agreed to publish. GitHub has no general private-messaging feature; use an Issue or email for project discussions. For a security or privacy concern, please email first and do not post raw logs, account details, or credentials publicly. Publishing the address may attract spam, and replies may be delayed.

## License

MIT. See [LICENSE](LICENSE).
