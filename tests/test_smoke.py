"""Smoke tests for the parts that carry the design.

These are not coverage theatre. Each one pins a rule that, when it broke in
practice, produced a run that looked finished and was not.

Runs with pytest, or with `python tests/run.py` when pytest is not installed.
"""

from __future__ import annotations

import json
import re
from contextlib import contextmanager

from enbanc import Run, Task, dispatch, review
from enbanc.arbiter import escalate
from enbanc.mandate import Mandate
from enbanc.providers import MockProvider
from enbanc.state import extract_tail_json, normalize_report, report_to_status
from enbanc.verifiers import default_verifiers
from enbanc.verifiers.document import DocumentVerifier


@contextmanager
def raises(exc, match=None):
    """Minimal stand-in for pytest.raises so the suite has no hard dependency."""
    try:
        yield
    except exc as e:
        if match and not re.search(match, str(e)):
            raise AssertionError(f"{exc.__name__} raised, but message {str(e)!r} does not match {match!r}")
    else:
        raise AssertionError(f"expected {exc.__name__}, nothing was raised")


# -- report contract ---------------------------------------------------------

def test_tail_json_survives_prose_with_braces():
    text = 'Here is the analysis {not json} and then:\n{"state":"ready","ready":true}'
    assert extract_tail_json(text) == {"state": "ready", "ready": True}


def test_tail_json_rejects_broken_json():
    assert extract_tail_json('{"state":"ready", "ready":') is None
    assert extract_tail_json("no json here") is None


def test_text_without_report_is_unverified_not_done():
    assert report_to_status(None, "I finished the task.") == "unverified"
    assert report_to_status(None, None) == "failed"


def test_ready_is_the_only_path_to_done():
    ready = normalize_report({"state": "ready", "ready": True})
    blocked = normalize_report({"state": "blocked", "attention": {"question": "which one?"}})
    assert report_to_status(ready, "x") == "done"
    assert report_to_status(blocked, "x") == "degraded"


def test_dropped_counts_as_done():
    rep = normalize_report({"state": "dropped"})
    assert report_to_status(rep, "nothing to do") == "done"


# -- state -------------------------------------------------------------------

def test_requirement_is_frozen(tmp_path):
    Run.init("r", "original", tmp_path)
    with raises(ValueError, match="requirement changed"):
        Run.init("r", "different", tmp_path)


def test_delivery_is_refused_without_a_pass(tmp_path):
    run = Run.init("r", "do the thing", tmp_path)
    with raises(PermissionError, match="refusing to mark delivered"):
        run.set_stage("P5")


def test_force_requires_a_reason(tmp_path):
    run = Run.init("r", "do the thing", tmp_path)
    with raises(ValueError, match="requires --reason"):
        run.set_stage("P5", force=True)


def test_forced_delivery_is_recorded_as_stopped(tmp_path):
    run = Run.init("r", "do the thing", tmp_path)
    run.set_stage("P5", force=True, reason="user stopped the run")
    assert run.state["outcome"] == "stopped_by_user"


def test_derived_state_never_calls_a_failed_run_delivered(tmp_path):
    run = Run.init("r", "do the thing", tmp_path)
    run.update(lambda st: {**st, "stage": "P5"})  # cursor moved without evidence
    assert run.derive() == "needs_human"


def test_review_refuses_when_there_is_nothing_to_review(tmp_path):
    """A judge handed an empty payload approves nothing.

    Real case: three runs reached delivery with an empty directory. The review
    had been given no artifact at all, and the pass was recorded anyway.
    """
    run = Run.init("empty", "produce a report", tmp_path)
    with raises(ValueError, match="nothing to review"):
        review(run, [], lambda role: MockProvider(seed=role), default_verifiers(), [])

    missing = tmp_path / "never-written.md"
    with raises(ValueError, match="nothing to review"):
        review(run, [("w", missing)], lambda role: MockProvider(seed=role), default_verifiers(), [])


def test_delivery_is_refused_when_no_artifact_exists(tmp_path):
    run = Run.init("nofile", "produce a report", tmp_path)
    run.update(lambda st: {**st, "reviews": [{"round": 1, "verdict": "pass", "issues": []}]})
    with raises(PermissionError, match="no artifact exists"):
        run.set_stage("P5")
    run.set_stage("P5", force=True, reason="report lives outside the run directory")
    final = Run.load("nofile", tmp_path).state
    assert final["stage"] == "P5" and final["outcome"] == "completed"


def test_a_failing_review_revokes_an_earlier_delivery(tmp_path):
    """outcome=completed must not survive a later revise.

    Real case: a run passed at round 3, entered P5, then rounds 4 and 5 came
    back revise. state.json still said completed, so every consumer of the
    field reported a delivered run that the derived state called needs_human.
    """
    run = Run.init("revoke", "produce a report", tmp_path)
    dispatch(run, [Task(id="w", prompt="produce a report", kind="doc", out="a.md")],
             lambda role: MockProvider(quality=1.0, seed=role))
    run = Run.load("revoke", tmp_path)
    art = run.dir / run.state["tasks"]["w"]["artifact"]

    class AlwaysPass:
        name = "always-pass"

        def complete(self, prompt, system="", timeout=900):
            from enbanc.providers.base import Reply
            return Reply(text='{"verdict":"pass","issues":[]}')

    review(run, [("w", art)], lambda role: AlwaysPass(), default_verifiers(), [])
    run = Run.load("revoke", tmp_path)
    run.set_stage("P5")
    assert run.state["outcome"] == "completed"

    class AlwaysRevise:
        name = "always-revise"

        def complete(self, prompt, system="", timeout=900):
            from enbanc.providers.base import Reply
            return Reply(text='{"verdict":"revise","issues":[{"type":"logic","where":"a.md",'
                              '"detail":"section 2 does not match the data","severity":"low",'
                              '"evidence":"table 1"}]}')

    rec = review(Run.load("revoke", tmp_path), [("w", art)],
                 lambda role: AlwaysRevise(), default_verifiers(), [])
    assert rec["verdict"] == "revise"

    st = Run.load("revoke", tmp_path).state
    assert st["outcome"] == ""
    assert st["outcome_revoked"]["from"] == "completed"


def test_render_flags_a_pass_that_still_has_findings(tmp_path):
    run = Run.init("open", "x", tmp_path)
    run.update(lambda st: {**st, "reviews": [{
        "round": 1, "verdict": "pass",
        "issues": [{"type": "format", "where": "a.md", "detail": "d",
                    "severity": "low", "evidence": "e"}]}]})
    # "pass (1 findings)" reads like zero problems; the run must say otherwise
    assert "1 open" in Run.load("open", tmp_path).render()


# -- verifiers ---------------------------------------------------------------

def test_placeholder_is_a_high_finding(tmp_path):
    p = tmp_path / "a.md"
    p.write_text("# Title\n\n## One\n\nTODO: write this\n\n## Two\n\n" + "x" * 200, encoding="utf-8")
    findings = DocumentVerifier().check(p)
    assert any(f.severity == "high" and "TODO" in f.detail for f in findings)


def test_missing_file_is_high(tmp_path):
    findings = DocumentVerifier().check(tmp_path / "nope.md")
    assert findings[0].severity == "high"


# -- pipeline ----------------------------------------------------------------

def test_good_artifact_passes_and_delivers(tmp_path):
    run = Run.init("good", "produce a report", tmp_path)
    run.update(lambda st: {**st, "criteria": ["three sections"], "judge_index": 0})

    dispatch(run, [Task(id="w", prompt="produce a report", kind="doc", out="a.md")],
             lambda role: MockProvider(quality=1.0, seed=role))
    run = Run.load("good", tmp_path)
    art = run.dir / run.state["tasks"]["w"]["artifact"]

    rec = review(run, [("w", art)], lambda role: MockProvider(seed=role),
                 default_verifiers(), run.state["criteria"])
    assert rec["verdict"] == "pass"

    run = Run.load("good", tmp_path)
    run.set_stage("P5")
    assert Run.load("good", tmp_path).state["outcome"] == "completed"


def test_bad_artifact_fails_even_if_the_judge_is_polite(tmp_path):
    run = Run.init("bad", "produce a report", tmp_path)
    dispatch(run, [Task(id="w", prompt="produce a report", kind="doc", out="a.md")],
             lambda role: MockProvider(quality=0.3, seed=role))
    run = Run.load("bad", tmp_path)
    art = run.dir / run.state["tasks"]["w"]["artifact"]

    # a judge that always approves cannot clear a mechanical high-severity fact
    class YesMan:
        name = "yes-man"

        def complete(self, prompt, system="", timeout=900):
            from enbanc.providers.base import Reply
            return Reply(text='{"verdict":"pass","issues":[]}')

    rec = review(run, [("w", art)], lambda role: YesMan(), default_verifiers(), [])
    assert rec["verdict"] == "revise"
    assert any(i["source"] == "verifier" for i in rec["issues"])


def test_escalation_walks_the_chain_then_stops(tmp_path):
    run = Run.init("esc", "x", tmp_path)
    chain = [{"name": "a"}, {"name": "b"}]

    first = escalate(run, chain)
    assert first["escalated"] and first["to"] == "b"

    second = escalate(Run.load("esc", tmp_path), chain)
    assert not second["escalated"] and "exhausted" in second["reason"]


def test_redispatch_is_idempotent(tmp_path):
    run = Run.init("idem", "x", tmp_path)
    task = Task(id="w", prompt="x", kind="doc", out="a.md")

    first = dispatch(run, [task], lambda role: MockProvider(seed=role))
    assert first[0]["status"] == "done"

    again = dispatch(Run.load("idem", tmp_path), [task], lambda role: MockProvider(seed=role))
    assert again[0]["status"] == "skipped"


def test_two_tasks_writing_one_file_is_refused(tmp_path):
    run = Run.init("clash", "x", tmp_path)
    tasks = [Task(id="a", prompt="x", out="same.md"), Task(id="b", prompt="x", out="same.md")]
    with raises(ValueError, match="both write"):
        dispatch(run, tasks, lambda role: MockProvider())


# -- mandate -----------------------------------------------------------------

def test_empty_allow_list_means_unrestricted(tmp_path):
    run = Run.init("m", "x", tmp_path)
    Mandate(run).init(allow=[], deny=[], cwd=str(tmp_path))
    res = Mandate(Run.load("m", tmp_path)).check(["anything.py"])
    assert res["ok"]


def test_deny_list_blocks(tmp_path):
    run = Run.init("m2", "x", tmp_path)
    Mandate(run).init(allow=["src/**"], deny=["*.lock"], cwd=str(tmp_path))
    res = Mandate(Run.load("m2", tmp_path)).check(["src/a.py", "config.lock", "other.txt"])
    paths = {v["path"] for v in res["violations"]}
    assert paths == {"config.lock", "other.txt"}


# -- three outcomes ----------------------------------------------------------

class _PassWithFinding:
    """Judge says pass but leaves one finding — the historical false-pass shape."""

    name = "pass-with-finding"

    def complete(self, prompt, system="", timeout=900):
        from enbanc.providers.base import Reply
        return Reply(text='{"verdict":"pass","issues":[{"type":"format","where":"a.md:3",'
                          '"detail":"unit mixes GB and GiB","severity":"low",'
                          '"evidence":"a.md:3 GB/GiB"}]}')


def _run_with_artifact(tmp_path, name="three"):
    run = Run.init(name, "produce a report", tmp_path)
    dispatch(run, [Task(id="w", prompt="produce a report", kind="doc", out="a.md")],
             lambda role: MockProvider(quality=1.0, seed=role))
    run = Run.load(name, tmp_path)
    art = run.dir / run.state["tasks"]["w"]["artifact"]
    return run, art


def test_pass_with_findings_becomes_conditional(tmp_path):
    run, art = _run_with_artifact(tmp_path, "cond")
    rec = review(run, [("w", art)], lambda role: _PassWithFinding(), default_verifiers(), [])
    assert rec["verdict"] == "conditional_pass", rec["verdict"]


def test_conditional_delivery_needs_explicit_acceptance(tmp_path):
    run, art = _run_with_artifact(tmp_path, "cond2")
    review(run, [("w", art)], lambda role: _PassWithFinding(), default_verifiers(), [])
    run = Run.load("cond2", tmp_path)
    with raises(PermissionError, match="conditional_pass"):
        run.set_stage("P5")
    run = Run.load("cond2", tmp_path)
    run.set_stage("P5", accept_residual=True)
    st = Run.load("cond2", tmp_path).state
    assert st["outcome"] == "accepted_with_residual", st["outcome"]
    assert len(st["outcome_residual"]) == 1


def test_conditional_never_reads_as_idle(tmp_path):
    run, art = _run_with_artifact(tmp_path, "cond3")
    review(run, [("w", art)], lambda role: _PassWithFinding(), default_verifiers(), [])
    assert Run.load("cond3", tmp_path).derive() == "needs_human"
