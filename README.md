# enbanc

A multi-model collaboration and review skill for documents, slide decks, websites, and other work.

[English](README.md) | 简体中文

When I use AI to make things, I often run into this problem: the model says it has finished, but I open the file and still find omissions. Asking it to check again sometimes fixes them, and sometimes it just says there are no problems. On a longer task, especially after changing models or sessions, it also becomes easy to lose track of what has and has not been done.

enbanc splits a task among different producers, arranges a separate review, takes the findings back for changes, and keeps a record of each run. The name comes from the legal term *en banc*, which means a hearing with all the judges taking part. Here it means several people reviewing work together. It does not involve legal services.

## What it can do

This workflow suits deliverables that need repeated checking. Examples include long reports divided into chapters, research that combines several sources, presentations with more than a dozen slides, websites with multiple pages or interactions, and code changes with clear acceptance requirements. Keeping task and review records is more useful when the work spans multiple files, needs several producers, or may move between models and sessions.

A simple question, a short piece of copy, or a task that only needs a first draft usually does not justify the full workflow. Without a clear deliverable and acceptance requirements, more review rounds may not help either. Presentations, websites, and formatted documents still need to be opened or rendered and checked. It cannot replace that step.

What it actually does:

- Splits the requirements into tasks and records each task's producer, output location, and execution state.
- Configures `producer` and `judge` separately. Production and review can use different models, or independent contexts with the same model. These two cases are clearly distinguished.
- Requires producers to submit structured reports covering the completed scope, evidence, and anything a person needs to decide. A single "finished" does not count as normal completion.
- Runs verifiers suitable for the task first, then records the programmatic results together with the model review.
- Uses three review verdicts: `pass` / `conditional_pass` / `revise`. `pass` means it passed. `conditional_pass` means it passed with conditions that still need confirmation. `revise` sends the findings back for changes, after which the output is collected and reviewed again.
- Does not allow a run to close as normally completed when review has not passed. Stopping early by user decision, exhausting the review chain, and ending the workflow without passing all leave different records.
- Saves the requirements, task states, outputs, reviews, and stage changes. If new problems appear later, a recorded completion can be withdrawn.

It has two entry points. One is a host skill that can be imported into WorkBuddy and uses the host's existing account models and tools. The other is the Python reference implementation in this repository, which can run offline examples or connect to your own model commands or adapters.

## About the author and the implementation

I'm not a professional programmer. enbanc is a general-purpose skill I gradually built around my own needs while using AI. Most of the code was also generated with model assistance.

During development, I try the requirements with different models and ask other models to check the results. Some models handle the implementation, while others check it or suggest another approach. I keep adjusting things based on how they work in use. The main implementation of this skill came from that repeated cross-checking and revision.

The project started with my own reviews of several cases where a model said the work was finished, but something was missing when I opened the file. The fixes ended up in the script's decision conditions, rather than staying as a line in a document saying "check before delivery." A run cannot close as normally completed without passing review, and tasks without an explicit completion report are marked separately.

One thing needs to be clear: a failed review does not necessarily mean the work has a problem. Sometimes the acceptance criteria need changing, or the review itself made the wrong judgment.

Offline examples, automated tests, and pre-release checks can now verify the basic workflow. The examples cover both a normal pass and a result with placeholders that cannot be delivered normally. The tests also check the staged-content and history release gates. They use a mock provider and verify state transitions, file records, and release checks. They do not demonstrate the output quality of real models.

I still call it a reference implementation. Its verifiers are fairly basic, model adapters need to be configured by the person connecting them, and it has no complete Office file generation and editing toolchain. You still need to verify how it behaves with real accounts, host tools, and different tasks. I suggest starting with one small task whose scope is clear.

## How a run works

Before starting, make clear what needs to be done, what must be delivered, and what must not be changed. Tasks can go to several producers, with a reviewer assigned separately. Run programmatic checks first where possible, then give the rest to model review. When problems are found, take them back for changes, then collect and check the output again.

The run keeps the requirements, task states, outputs, and review records. Producers need to provide a structured report saying what they completed, where the evidence is, and whether anything needs a human decision. For example:

```json
{"state": "ready", "ready": true,
 "scope_done": ["report.md"],
 "attention": {"question": "", "options": [], "blocking": false},
 "evidence": ["report.md:12"]}
```

A task that returns only text without a valid report is marked `unverified`. It is not treated as normally completed just because it says "finished." The report is still a claim made by a model and does not replace checking the file contents.

Run status and task reports are two separate layers of information. Stages `P0` through `P5` record the position in the workflow. When showing progress, the script also derives the current status from the review records, task states, and output files:

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

Entering `P5` normally requires a passed review. If you decide to stop there, use `--force --reason "..."` to explain why. The record keeps `outcome=stopped_by_user` and does not count it as completion after passing. A recorded completion can also be withdrawn. If a later review returns `revise`, the completion marker is cleared and the reason is kept. The field and the derived status will not give two different answers about whether the work was delivered.

If repeated reviews are not converging, you can change reviewers. The current reference implementation uses `enbanc escalate` to record progress through the review chain and leaves the decision to a person when the chain runs out. It does not automatically guarantee that the next model is stronger, or switch model requests based only on a list. The integration layer must configure which model is actually called.

A run also records four things about its own honesty. Entering the production, review, escalation or delivery stage with fewer than two tasks is refused unless you waive it explicitly with `--allow-solo --reason`, and the waiver stays on the record. A review whose reported usage falls below the threshold is marked `insufficient_review` rather than `pass` — a one-line approval reviewed nothing — while a provider that reports no usage is only flagged, not failed. Tasks that declare the paths they write (`writes`) run in parallel when those sets do not overlap and one after another when they do; two undeclared tasks aiming at the same file are refused before anything starts. And `inspect` sweeps every run without changing a thing, reporting a run that never dispatched a task, a review too thin to count, or a task recorded as running long after its process is gone, and exits 10 when it finds one.

Findings from programmatic checks and model review are recorded together. A verifier finding marked `high` prevents that round from passing, and the model cannot directly delete those records. Verifiers can also make mistakes, especially with approximate rules such as character counts. Confirm that a verifier suits your task before using it.

## Using it in WorkBuddy

After downloading, you can import `SKILL.md` and `references/safety-rules.md` from the repository into WorkBuddy using the skill package structure, or ask an authorized host to install this public skill. Once enabled, enter `/enbanc` or "合议：", then describe your workspace, delivery format, and acceptance requirements. You can also run `python .sync/publish.py export-skill` first to generate an importable ZIP. This packages the files locally and does not upload anything.

This mode uses the host's existing account models and tools. It does not require my gateway, API key, model matrix, or any local directories. `default`, `lite`, and `reasoning` are resolved according to the host's actual configuration and do not guarantee three different models. If your account has only one available model, you can do independent-context review, but it will be clearly labeled and not described as cross-model verification. Whether you can access a specific model depends on what your account can actually call.

I suggest giving each task a short set of general rules: limit it to the approved directory, keep credentials out of materials, pass only what is needed, keep review read-only, check facts against their sources, set timeout and budget limits, and do not mark failed work complete. The included rules are in `references/safety-rules.md`. The skill does not actively copy private memory or computer settings. When Office, screenshot, or other tools are missing, the skill states the limitation. It will not fail to start just because a file from the author's machine is missing.

See [docs/004-environment.md](docs/004-environment.md) for installation methods and the prerequisites for each path. This host skill and the Python reference implementation below are two entry points. You do not need to install Python before using it in WorkBuddy.

## Try it first

You need Python 3.10 or later. From the repository directory, run the offline example without configuring a model or an API key:

```bash
python examples/demo/run.py
python tests/run.py
```

The example uses a mock provider to simulate model replies and writes files and review records locally. It shows one result that passes the checks and another that leaves placeholders and cannot be delivered normally. This demonstrates the workflow, not the quality of a real model. The example does not require Git. The full tests do require Git to check the staged-content and history release gates, and they clean up the temporary files they create.

To try the commands individually, install the reference implementation first:

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

Files are saved under `runs/demo/`. The last step can close the run normally only if the review returns `pass`. If it returns `revise`, you need to address the findings first.

## Connecting your own models

It currently provides mock and CLI adapters and is not tied to one model service. The CLI adapter passes prompts as command arguments and reads standard output. If your service uses HTTP, you can also write an adapter using the provider interface.

Production and review are separated by the role names `producer` and `judge`. You can choose different models for them when connecting your own service, but you need to configure this yourself. The project does not select them automatically. When using a command-line tool, pass the task and review requirements through that tool's actual arguments. Changing only the executable name is not enough.

The interface is in [providers/base.py](src/enbanc/providers/base.py), and the CLI adapter is in [providers/cli.py](src/enbanc/providers/cli.py). The public reference implementation currently focuses on text output and does not include a complete Office file generation or editing toolchain. Making actual PowerPoint or Word files requires connecting the appropriate tools.

## What it can check today

The repository includes several basic verifiers to try and extend:

| Verifier | Current checks |
|---|---|
| `document` | Text structure, placeholders, empty links, and minimum length |
| `page` | HTML title, `lang`, viewport, `img alt`, and some in-page anchors |
| `deck` | Slide count, character count, and placeholders in Markdown decks; existence and file size only for PPTX |
| `code` | File existence, some debugging residue, and an optional test command |

These checks are all fairly basic. Too many characters do not prove that a page will overflow, and meeting a length requirement does not make the content useful. Passing an HTML check says even less about whether a website is well designed. Presentations, websites, and formatted documents still need to be opened or rendered and checked. The current implementation also does not automatically verify every citation or fact.

See [docs/002-verifiers.md](docs/002-verifiers.md) for how to add a verifier. Start with checks you actually need and keep location information so you can return to the original file and check the findings. When review comments involve trade-offs, a person still needs to make the final judgment.

## Keeping it updated

The public repository and the local skill I use myself are maintained separately. The local skill may contain specific models, gateways, and paths, so the public version needs separate preparation. I cannot simply copy the entire skill directory over.

The current update steps are:

```bash
python .sync/publish.py sync
python .sync/publish.py check
python .sync/publish.py stage
```

`sync` detects whether the local skill has changed. It does not automatically merge those changes into the public implementation. The code and documentation that need updating still have to be prepared manually. `check` runs the privacy scan, tests, and structural and identifier checks on the Chinese and English READMEs. It cannot judge whether a translation reads naturally or guarantee that it finds every sensitive detail. `stage` runs the checks again and reports whether there are unhandled local changes. It does not stage them automatically. The checks also cover staged content and reachable local branch and tag history, so a sensitive value that has already been deleted cannot still be uploaded. Unknown files and binaries are rejected by default.

Only after confirming the content, committing locally, and configuring a remote should you use `python .sync/publish.py push --yes`. That command really uploads; the three preceding commands do not. See [docs/003-operations.md](docs/003-operations.md) for the detailed procedure.

The public version does not include my computer configuration, gateway, credentials, or private run records. It also has no feature that calls back to an author-operated service. Account-based cloud models still process the material you provide, so a sanitized repository does not mean the whole process is offline. See [docs/005-privacy.md](docs/005-privacy.md) for the scope of the privacy checks and the boundaries they do not cover.

Offline tests and text scans can only verify the parts they cover. You still need to try it yourself in a real host. The public implementation and my local skill are two separate codebases, and I still need to check their differences manually when updating. That step is not yet fully automated.

## Working on it together

This is a personal project I have gradually organized through use. I'm not a professional developer, so maintenance and replies may not always be timely. Issues, documentation improvements, tests, and better adapters are welcome. You are also welcome to fork it or create your own repository to improve and share it. Just retain the required notices under the MIT license. You do not need to ask me for separate permission.

If you make your own version, I would also like to know where you use it and what you have improved. When convenient, please send me the repository link so we can learn from each other. Letting me know is an invitation, not an extra condition on using or distributing the project.

Contact: [pyq3367@zoho.com](mailto:pyq3367@zoho.com). This is a project contact address I have agreed to publish. GitHub has no general private-messaging feature, so project questions can be discussed through an Issue or email. For security or privacy concerns, please email first and do not post raw logs, account details, or credentials publicly. Publishing the address may attract spam, and replies may be delayed.

## License

MIT. See [LICENSE](LICENSE).
