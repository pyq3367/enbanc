"""Smoke tests for the parts that carry the design.

These are not coverage theatre. Each one pins a rule that, when it broke in
practice, produced a run that looked finished and was not.

Runs with pytest, or with `python tests/run.py` when pytest is not installed.
"""

from __future__ import annotations

import io
import json
import os
import re
import time
from contextlib import contextmanager, redirect_stdout

from enbanc import Run, Task, dispatch, review
from enbanc.arbiter import escalate
from enbanc.cli import main
from enbanc.dispatch import plan_groups, write_paths
from enbanc.inspect import inspect_run
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
    run.set_stage("P5", allow_solo=True,
                  reason="single artifact fixture: the judge is a separate call, "
                         "a second producer task would only duplicate it")
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
    # One artifact is the point of this test; the quantity gate is waived on the
    # record instead of being sidestepped. Waiving the count is not the same as
    # waiving the review: the judge above was a separate call.
    run.set_stage("P5", allow_solo=True,
                  reason="single artifact fixture: the judge is a separate call, "
                         "a second producer task would only duplicate it")
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
    run.set_stage("P5", accept_residual=True, allow_solo=True,
                  reason="single artifact fixture: the judge is a separate call, "
                         "a second producer task would only duplicate it")
    st = Run.load("cond2", tmp_path).state
    assert st["outcome"] == "accepted_with_residual", st["outcome"]
    assert len(st["outcome_residual"]) == 1


def test_conditional_never_reads_as_idle(tmp_path):
    run, art = _run_with_artifact(tmp_path, "cond3")
    review(run, [("w", art)], lambda role: _PassWithFinding(), default_verifiers(), [])
    assert Run.load("cond3", tmp_path).derive() == "needs_human"


# -- quantity gate and solo waiver -------------------------------------------

SOLO_WHY = "one deliverable, and the judge is a separate call, so a second task adds nothing"


def test_quantity_gate_refuses_a_run_that_was_never_divided(tmp_path):
    run = Run.init("gate", "produce a report", tmp_path)
    with raises(PermissionError, match="refusing to enter"):
        run.set_stage("P2")
    st = Run.load("gate", tmp_path).state
    assert st["stage"] == "P0", st["stage"]
    assert "solo_waiver" not in st


def test_quantity_gate_counts_tasks_not_stages(tmp_path):
    run = Run.init("pair", "produce a report", tmp_path)
    run.update(lambda st: {**st, "tasks": {
        "a": {"id": "a", "status": "done"}, "b": {"id": "b", "status": "done"}}})
    run.set_stage("P2")
    st = Run.load("pair", tmp_path).state
    assert st["stage"] == "P2"
    assert "solo_waiver" not in st and "force_skip_quantity_gate" not in st


def test_solo_waiver_is_recorded_and_shown(tmp_path):
    run = Run.init("waive", "produce a report", tmp_path)
    run.set_stage("P2", allow_solo=True, reason=SOLO_WHY)
    loaded = Run.load("waive", tmp_path)
    st = loaded.state
    assert st["stage"] == "P2"
    assert st["solo_waiver"]["reason"] == SOLO_WHY
    assert st["solo_waiver"]["stage"] == "P2"
    assert st["solo_waiver"]["at"]
    assert st["solo_waivers"][-1] == st["solo_waiver"]
    # A waiver is a degraded path the delivery note has to mention, so the
    # one-line status cannot stay silent about it.
    assert "solo-waiver" in loaded.render()


def test_allow_solo_without_a_reason_is_refused(tmp_path):
    run = Run.init("waive0", "produce a report", tmp_path)
    with raises(ValueError, match=r"--allow-solo requires --reason"):
        run.set_stage("P2", allow_solo=True)
    with raises(ValueError, match=r"--allow-solo requires --reason"):
        run.set_stage("P2", allow_solo=True, reason="   ")
    st = Run.load("waive0", tmp_path).state
    assert st["stage"] == "P0" and "solo_waiver" not in st


def test_force_past_the_quantity_gate_is_counted(tmp_path):
    run = Run.init("forced", "produce a report", tmp_path)
    run.set_stage("P5", force=True, reason="user stopped the run")
    st = Run.load("forced", tmp_path).state
    assert st["stage"] == "P5"
    assert "solo_waiver" not in st
    rec = st["force_skip_quantity_gate"][-1]
    assert rec["stage"] == "P5" and rec["tasks"] == 0
    assert rec["reason"] == "user stopped the run" and rec["at"]


# -- review usage gate -------------------------------------------------------

class _JudgeWithUsage:
    """A judge that answers in one line, reporting exactly how few tokens it used."""

    name = "judge-with-usage"

    def __init__(self, tokens):
        self.tokens = tokens

    def complete(self, prompt, system="", timeout=900):
        from enbanc.providers.base import Reply
        return Reply(text='{"verdict":"pass","issues":[]}',
                     meta={"usage": {"completion_tokens": self.tokens}})


class _JudgeWithoutUsage:
    """A judge whose provider reports no usage at all."""

    name = "judge-without-usage"

    def complete(self, prompt, system="", timeout=900):
        from enbanc.providers.base import Reply
        return Reply(text='{"verdict":"pass","issues":[]}')


def test_a_pass_bought_with_a_few_tokens_is_insufficient_review(tmp_path):
    run, art = _run_with_artifact(tmp_path, "thin")
    rec = review(run, [("w", art)], lambda role: _JudgeWithUsage(12), default_verifiers(), [])
    assert rec["verdict"] == "insufficient_review", rec["verdict"]
    assert rec["usage"]["completion_tokens"] == 12
    usage_issues = [i for i in rec["issues"] if i.get("source") == "usage"]
    assert len(usage_issues) == 1
    assert usage_issues[0]["type"] == "review" and usage_issues[0]["severity"] == "high"


def test_insufficient_review_cannot_reach_delivery(tmp_path):
    run, art = _run_with_artifact(tmp_path, "thin2")
    review(run, [("w", art)], lambda role: _JudgeWithUsage(12), default_verifiers(), [])
    loaded = Run.load("thin2", tmp_path)
    assert not loaded.is_passed() and not loaded.is_conditional()
    with raises(PermissionError, match="refusing to mark delivered"):
        loaded.set_stage("P5", allow_solo=True, reason=SOLO_WHY)
    assert Run.load("thin2", tmp_path).state["stage"] != "P5"


def test_insufficient_review_derives_as_review_not_idle(tmp_path):
    run, art = _run_with_artifact(tmp_path, "thin3")
    review(run, [("w", art)], lambda role: _JudgeWithUsage(12), default_verifiers(), [])
    assert Run.load("thin3", tmp_path).derive() == "review"


def test_missing_usage_is_recorded_not_punished(tmp_path):
    run, art = _run_with_artifact(tmp_path, "nousage")
    rec = review(run, [("w", art)], lambda role: _JudgeWithoutUsage(), default_verifiers(), [])
    assert rec["verdict"] == "pass", rec["verdict"]
    assert rec["usage"]["completion_tokens"] is None
    assert not any(i.get("source") == "usage" for i in rec["issues"])


def test_required_usage_turns_silence_into_insufficient_review(tmp_path):
    run, art = _run_with_artifact(tmp_path, "requsage")
    rec = review(run, [("w", art)], lambda role: _JudgeWithoutUsage(), default_verifiers(), [],
                 require_usage=True)
    assert rec["verdict"] == "insufficient_review", rec["verdict"]
    assert any(i.get("source") == "usage" for i in rec["issues"])


def test_a_real_review_is_not_flagged_for_being_terse(tmp_path):
    run, art = _run_with_artifact(tmp_path, "thick")
    rec = review(run, [("w", art)], lambda role: _JudgeWithUsage(400), default_verifiers(), [])
    assert rec["verdict"] == "pass", rec["verdict"]
    assert not any(i.get("source") == "usage" for i in rec["issues"])


# -- write sets and scheduling -----------------------------------------------

def test_write_paths_normalise_case_and_separators(tmp_path):
    plain = Task(id="a", prompt="x", out="Report.md", cwd=str(tmp_path))
    dotted = Task(id="b", prompt="x", out="ignored.md",
                  cwd=str(tmp_path / "sub" / ".."), writes=["REPORT.md"])
    assert write_paths(plain) == write_paths(dotted)
    assert "\\" not in write_paths(plain)[0]
    assert write_paths(plain)[0].endswith("/report.md")


def test_write_paths_fall_back_to_the_output_file(tmp_path):
    task = Task(id="a", prompt="x", out="notes.txt", cwd=str(tmp_path))
    assert task.writes == []
    assert write_paths(task) == [os.path.normpath(os.path.join(str(tmp_path), "notes.txt"))
                                 .replace("\\", "/").lower()]
    # A declared set wins: the output file is not silently added to it.
    declared = Task(id="b", prompt="x", out="notes.txt", cwd=str(tmp_path),
                    writes=["draft/notes.txt"])
    paths = write_paths(declared)
    assert len(paths) == 1 and paths[0].endswith("/draft/notes.txt")


def test_plan_groups_merges_a_directory_with_the_file_below_it(tmp_path):
    tasks = [
        Task(id="site", prompt="x", out="index.html", cwd=str(tmp_path), writes=["site"]),
        Task(id="css", prompt="x", out="theme.css", cwd=str(tmp_path),
             writes=["site/theme.css"]),
        Task(id="notes", prompt="x", out="notes.md", cwd=str(tmp_path), writes=["notes.md"]),
    ]
    assert sorted(sorted(group) for group in plan_groups(tasks)) == [["css", "site"], ["notes"]]


def test_plan_groups_puts_every_task_in_exactly_one_group(tmp_path):
    tasks = [Task(id=f"t{i}", prompt="x", out=f"f{i}.md", cwd=str(tmp_path)) for i in range(5)]
    groups = plan_groups(tasks)
    assert sorted(tid for group in groups for tid in group) == [f"t{i}" for i in range(5)]
    assert len(groups) == 5


def test_undeclared_tasks_on_one_output_are_still_refused(tmp_path):
    run = Run.init("clash2", "x", tmp_path)
    tasks = [Task(id="a", prompt="x", out="same.md"), Task(id="b", prompt="x", out="same.md")]
    with raises(ValueError, match="both write"):
        dispatch(run, tasks, lambda role: MockProvider(seed=role))
    # Refused before anything starts: no task is left half-recorded.
    assert Run.load("clash2", tmp_path).state["tasks"] == {}


def test_declared_writes_are_serialised_instead_of_refused(tmp_path):
    run = Run.init("serial", "x", tmp_path)
    tasks = [Task(id="a", prompt="x", out="same.md", writes=["same.md"]),
             Task(id="b", prompt="x", out="same.md", writes=["same.md"])]
    results = dispatch(run, tasks, lambda role: MockProvider(quality=1.0, seed=role),
                       max_parallel=3)
    assert {r["id"]: r["status"] for r in results} == {"a": "done", "b": "done"}
    assert plan_groups(tasks) == [["a", "b"]]


# -- external inspection -----------------------------------------------------

def test_inspect_reports_a_gated_stage_with_no_tasks(tmp_path):
    run = Run.init("zero", "produce a report", tmp_path)
    run.update(lambda st: {**st, "stage": "P2"})
    findings = inspect_run(tmp_path / "zero")
    assert [f["code"] for f in findings] == ["zero_sub"], findings
    assert findings[0]["run"] == "zero" and findings[0]["detail"]


def test_inspect_accepts_a_waived_solo_run(tmp_path):
    run = Run.init("waived", "produce a report", tmp_path)
    run.set_stage("P2", allow_solo=True, reason=SOLO_WHY)
    assert inspect_run(tmp_path / "waived") == []


def test_inspect_flags_a_review_bought_with_a_few_tokens(tmp_path):
    run, art = _run_with_artifact(tmp_path, "thin-inspect")
    review(run, [("w", art)], lambda role: _JudgeWithUsage(9), default_verifiers(), [])
    findings = inspect_run(tmp_path / "thin-inspect")
    assert [f["code"] for f in findings] == ["thin_review"], findings
    assert "9" in findings[0]["detail"]


def test_inspect_flags_a_task_running_past_its_timeout(tmp_path):
    run = Run.init("zombie", "produce a report", tmp_path)
    started = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 4000))
    run.update(lambda st: {**st, "stage": "P3", "tasks": {
        "w": {"id": "w", "status": "running", "started": started, "timeout": 900}}})
    assert [f["code"] for f in inspect_run(tmp_path / "zombie")] == ["zombie"]


def test_inspect_clears_a_healthy_run(tmp_path):
    run, art = _run_with_artifact(tmp_path, "healthy")
    review(run, [("w", art)], lambda role: MockProvider(seed=role), default_verifiers(), [])
    assert inspect_run(tmp_path / "healthy") == []


def test_inspect_ignores_a_shell_and_reports_a_missing_state(tmp_path):
    (tmp_path / "shell").mkdir()
    assert inspect_run(tmp_path / "shell") == []

    orphan = tmp_path / "orphan"
    orphan.mkdir()
    (orphan / "requirement.md").write_text("frozen", encoding="utf-8")
    assert [f["code"] for f in inspect_run(orphan)] == ["no_state"]


def test_inspect_cli_exits_zero_clean_and_ten_on_findings(tmp_path):
    run, art = _run_with_artifact(tmp_path, "cli-ok")
    review(run, [("w", art)], lambda role: MockProvider(seed=role), default_verifiers(), [])
    stream = io.StringIO()
    with redirect_stdout(stream):
        assert main(["--root", str(tmp_path), "inspect"]) == 0

    broken = tmp_path / "cli-broken"
    broken.mkdir()
    (broken / "state.json").write_text("{not json", encoding="utf-8")
    with redirect_stdout(stream):
        assert main(["--root", str(tmp_path), "inspect"]) == 10


# -- provider usage ----------------------------------------------------------

def test_cli_provider_reads_usage_from_its_envelope(tmp_path):
    """An adapter that forwards only text throws away the cost of a verdict.

    The review-usage gate can only work if the adapter stops dropping the usage
    the CLI already printed.
    """
    import sys

    from enbanc.providers.cli import CliProvider

    envelope = json.dumps({"result": '{"verdict":"pass","issues":[]}',
                           "usage": {"completion_tokens": 19}})
    script = tmp_path / "emit.py"
    script.write_text("import sys\nsys.stdout.write(" + repr(envelope) + ")\n", encoding="utf-8")

    reply = CliProvider([sys.executable, str(script)], extract="result").complete("x")
    assert reply.ok, reply.error
    assert (reply.meta or {}).get("usage", {}).get("completion_tokens") == 19
    assert reply.text.strip() == '{"verdict":"pass","issues":[]}'


def test_cli_provider_without_usage_does_not_invent_one(tmp_path):
    import sys

    from enbanc.providers.cli import CliProvider

    script = tmp_path / "plain.py"
    script.write_text("import sys\nsys.stdout.write('a verdict with no envelope')\n", encoding="utf-8")

    reply = CliProvider([sys.executable, str(script)]).complete("x")
    assert reply.ok, reply.error
    assert (reply.meta or {}).get("usage") is None
