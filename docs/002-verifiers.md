# 002 — Verifiers

A verifier is where enbanc's generality actually lives. Swapping providers changes
who writes and who reads. Swapping verifiers changes what "done" means.

## The contract

```python
from enbanc.verifiers.base import Finding

class MyVerifier:
    name = "my-kind"          # referenced by Task(verifier="my-kind")
    suffixes = (".xyz",)

    def check(self, artifact: Path, criteria: list[str] | None = None) -> list[Finding]:
        ...
```

`Finding(type, where, detail, severity, evidence)`.

- `type`: `missing` | `fact` | `logic` | `scope` | `format` | `risk`
- `where`: must be locatable — `file:line`, `slide 4`, `section §Pricing`
- `severity`: `high` blocks the round on its own; `medium`/`low` are recorded

Return `[]` when the artifact is fine. Do not invent findings to look thorough;
the judge already covers taste.

## Rules that keep it honest

**1. Check what a machine can check.**
If you can write it as an assertion over bytes, it is a verifier. If it needs an
opinion, it is the judge's job. Mixing them is how you get a verifier that is
just a second model with extra steps — and no better than the first.

**2. Never modify the artifact.**
A verifier reports. The moment it repairs, it becomes a producer, and then it
needs its own verifier. This is the recursion that makes review pipelines
unreliable.

**3. Be reproducible.**
Same input, same findings. No network calls, no clock, no randomness. A finding
you cannot reproduce is an opinion wearing a lab coat.

**4. Fail loud on missing input.**
If the file does not exist, return a `high` finding. Do not return `[]` — silence
means "checked and fine", and conflating the two is exactly the bug this project
exists to prevent.

**5. Prefer a command over a heuristic.**
If a tool already answers the question (`pytest`, `tsc`, `linkchecker`), run it
and read the exit code. Heuristics that approximate a real tool are strictly
worse.

## What ships

| Verifier | Good for | Catches | Misses |
|---|---|---|---|
| `document` | reports, proposals, specs | placeholders, missing headings, dead links, empty output | whether the argument holds |
| `page` | static HTML | missing title/lang/viewport, `img` without `alt`, dead anchors, runaway inline styles | layout, contrast, anything visual |
| `deck` | Markdown decks; limited `.pptx` check | Markdown slide count, character threshold and placeholders; PPTX existence and size only | actual layout, overflow and persuasiveness |
| `code` | source files | debug residue, optional test command failing | design problems |

Every one of them misses the thing you care most about. That is not a defect to
patch — it is the boundary of mechanical checking. The judge covers the rest, and
a person covers what the judge misses.

## The visual case

A text review of a rendered artifact is blind by construction. It can confirm an
element exists in the source; it cannot see that the element is drawn as a
scribble.

If your artifact is visual, the loop needs a third track:

```
source check (verifier)  +  render (screenshot)  +  judge reads the render
```

A host with image tools can render to PNG and arrange a separate visual review.
The current Python provider/payload is text-only and does not implement image
attachments. Do not claim a visual check from a text-only verdict. Screenshots
kept with local runs must be inspected for sensitive content before sharing.

## Example: citations

```python
import re
from enbanc.verifiers.base import Finding

URL = re.compile(r"https?://\S+")

class CitationVerifier:
    name = "citation"
    suffixes = (".md",)

    def __init__(self, min_sources: int = 2):
        self.min_sources = min_sources

    def check(self, artifact, criteria=None):
        if not artifact.exists():
            return [Finding("missing", str(artifact), "artifact does not exist", "high")]
        text = artifact.read_text(encoding="utf-8", errors="replace")
        urls = URL.findall(text)
        if len(urls) < self.min_sources:
            return [Finding("missing", artifact.name,
                            f"{len(urls)} sources cited, expected {self.min_sources}",
                            "high")]
        if "example.com" in text:
            return [Finding("fact", artifact.name, "placeholder domain used as a source", "high")]
        return []
```

Register it and a task can ask for it by name:

```python
verifiers = {**default_verifiers(), "citation": CitationVerifier(min_sources=3)}
Task(id="research", prompt="...", kind="doc", out="report.md", verifier="citation")
```

## Anti-patterns

| Anti-pattern | Why it fails |
|---|---|
| "Ask a model if this looks good" as a verifier | you have built one judge and called it two |
| A verifier that rewrites the artifact | it is now a producer with no independent check |
| A verifier that only logs warnings | findings that never block are decoration |
| Thresholds tuned until the demo passes | the gate now measures your tuning, not the work |
| One verifier for all artifact types | it will be so loose it catches nothing, or so tight it blocks everything |
