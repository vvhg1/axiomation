"""The methods session as a queue (`methods_queue`): each pair of a block
and a script it uses served with the script, its run read through `run`,
answered by a typed `judge` whose line the session's gate checks, and
`review --session methods` asking it, on the synthetic paper with a
script (`test_review_methods`)."""

from __future__ import annotations

import pytest

from axiomation import edits, queries, queue, review, versions
from axiomation import methods_queue as mq
from axiomation.judgments import Author, answer_finding
from axiomation.questions import Template, load_template
from axiomation.questions import review_file as rf
from axiomation.queue import Accepted, Refused, Working
from axiomation.records import (
    AGENT,
    Aspect,
    FindingStatus,
    RecordStore,
    ValuePair,
    Verdict,
)
from axiomation.resolver import Resolver
from axiomation.runner import Runner
from axiomation.runner.hosts import Rates
from axiomation.runner.queued import run_queue
from runner.fakes import FakeQueueHost, working_through
from test_review_methods import COMPUTES, KEY, VERIFIED, paper_with_a_script

__all__ = ["paper_with_a_script"]  # the fixture, used by name

TEMPLATE = load_template("review-methods")
#: A judge's typed answer that writes `VERIFIED`.
JUDGED = {
    "task": "s1",
    "verdict": "verified",
    "run": True,
    "values": [{"text": "The output is bounded", "output": "bounded by 1"}],
    "code": [COMPUTES],
    "evidence": "the script prints the bound",
}


@pytest.fixture
def methods(paper_with_a_script):
    store, paper = paper_with_a_script
    state = versions.latest(store)
    assert state is not None
    with edits.opened(store, state) as kept:
        found = review.brief(
            kept,
            rf.Session.METHODS,
            TEMPLATE,
            None,
            None,
            review.Selection.ALL,
            paper,
        )
        yield mq.Methods(
            kept,
            queries.View(store, state),
            found,
            TEMPLATE,
            edits.agent("model-a", None),
        )


def run(workflow: mq.Methods, working: Working, **args) -> str:
    (tool,) = (r for r in workflow.reads if r.tool.name == "run")
    return str(tool.answer(working, args))


# --- the judge line ---------------------------------------------------------------


def test_a_typed_judge_writes_the_line_the_gate_reads() -> None:
    assert mq.judge_lines("s1", KEY, JUDGED) == [VERIFIED]
    informal = {"verdict": "not-shown", "evidence": "it prints a constant"}
    assert mq.judge_lines("s1", KEY, informal) == [
        "judge s1 not-shown informal — it prints a constant"
    ]
    ran = {**JUDGED, "arguments": ["--n", "3 4"], "values": [], "code": []}
    assert mq.judge_lines("s1", KEY, ran) == [
        f"judge s1 verified computational run {KEY} --n '3 4' — the script prints "
        "the bound"
    ]
    finding = {
        "anchors": [f"file:{KEY}", "thm:bound"],
        "severity": 3,
        "quote": "print(",
        "problem": "printed, not computed — a constant",
        "fix": "compute it",
    }
    (_, line) = mq.judge_lines("s1", KEY, {**JUDGED, "finds": [finding]})
    assert line == (
        f'find computed file:{KEY} thm:bound 3 "print(" — printed, not computed; a '
        "constant — compute it"
    )


@pytest.mark.parametrize(
    ("args", "wanted"),
    [
        ({"verdict": "verified"}, "verdict and its evidence"),
        ({**JUDGED, "run": False, "arguments": ["1"]}, "arguments name a run"),
        (
            {**JUDGED, "values": [{"text": 'the "bound"', "output": "1"}]},
            "holds a double quote",
        ),
        ({**JUDGED, "values": [{"text": "", "output": "1"}]}, "is empty"),
        ({**JUDGED, "code": ["  "]}, "a code line is empty"),
        ({**JUDGED, "arguments": ["a — b"]}, "holds a dash"),
        ({**JUDGED, "arguments": ["a\nb"]}, "line break"),
        # evidence that would read back as the line's code or values
        (
            {**JUDGED, "code": [], "evidence": 'code "x = 1" — then it prints'},
            "would not read back",
        ),
        ({**JUDGED, "code": [], "evidence": "code prints a constant"}, "read back"),
        (
            {**JUDGED, "values": [], "code": [], "evidence": '"a" ↔ "b" it is'},
            "read back",
        ),
    ],
)
def test_a_judge_code_cannot_write_is_refused(args, wanted) -> None:
    with pytest.raises(mq.MethodsError, match=wanted):
        mq.judge_lines("s1", KEY, args)


# --- served and run ---------------------------------------------------------------


def test_a_pair_is_served_with_its_script_and_its_block(methods) -> None:
    working = Working(methods)
    (task,) = methods.draw(working)
    (step,) = methods.steps
    served = step.render(task, working)
    assert served.startswith("judge this block and its script\n⟦s1 computed⟧ ")
    assert f"its script, {KEY} (`code/{KEY}`):" in served
    assert COMPUTES in served and "its block:" in served
    assert f"code/{KEY}" in methods.serving.files
    assert rf.PAPER_FILE in methods.serving.files


def test_run_returns_what_the_script_printed_and_refuses_outside_the_paper(
    methods,
) -> None:
    working = Working(methods)
    said = run(methods, working, task="s1", arguments=["7"])
    assert f"`{KEY} 7`: ran in" in said
    assert "The output is bounded by 1.\n7" in said
    failed = run(methods, working, task="s1", arguments=["fail"])
    assert ": failed in" in failed and "its stderr ends:" in failed
    with pytest.raises(queue.ReadError, match="outside the paper"):
        run(methods, working, task="s1", arguments=["../x"])
    with pytest.raises(queue.ReadError, match="name a task on a script"):
        run(methods, working, task="s9")


def test_a_run_past_its_time_is_said_and_no_judge_rests_on_it(
    methods, monkeypatch
) -> None:
    """A run past `RUN_SECONDS` is not cached, and a judge naming it is
    refused; one already cached is read from the cache."""
    working = Working(methods)
    (task,) = methods.draw(working)
    (step,) = methods.steps
    monkeypatch.setattr(mq, "RUN_SECONDS", 0.5)
    said = run(methods, working, task="s1")
    assert "ran past 0.5 s" in said and "nothing is cached" in said
    refused = step.answer(task, "judge", JUDGED, working, True)
    assert isinstance(refused, Refused) and "ran past" in refused.problems[0]
    monkeypatch.setattr(mq, "RUN_SECONDS", 480)
    assert "ran in" in run(methods, working, task="s1")
    monkeypatch.setattr(mq, "RUN_SECONDS", 0.5)
    assert "ran in" in run(methods, working, task="s1"), "read from the cache"


def test_an_answer_is_checked_by_the_session_s_gate(methods) -> None:
    """A value the run did not print is refused with the output; a pair
    settled by a settling tool is refused; the typed judge, accepted,
    files its run, values and code."""
    working = Working(methods)
    (task,) = methods.draw(working)
    (step,) = methods.steps
    wrong = {**JUDGED, "values": [{"text": "The output is bounded", "output": "by 9"}]}
    refused = step.answer(task, "judge", wrong, working, True)
    assert isinstance(refused, Refused)
    (problem,) = refused.problems
    assert "not in what" in problem and "bounded by 1" in problem
    settled = step.answer(task, "stands", {"task": "s1", "why": "x"}, working, True)
    assert isinstance(settled, Refused) and "answered by `judge`" in settled.problems[0]
    accepted = step.answer(task, "judge", JUDGED, working, True)
    assert isinstance(accepted, Accepted), accepted
    (j,) = accepted.judgments.judgments
    assert j.aspect is Aspect.COMPUTED and j.verdict is Verdict.VERIFIED
    assert j.run is not None and j.run.key == KEY and j.run.ran
    assert j.values == (ValuePair("The output is bounded", "bounded by 1"),)
    assert j.code == (COMPUTES,)


def test_the_brief_is_composed_from_the_template_s_sections() -> None:
    brief = mq.reader_brief(TEMPLATE).text
    assert "## Your tasks and reads" in brief and "`run`:" in brief
    assert "## Your files" not in brief and "review.md`, your sheet" not in brief
    assert brief.index("## What to check") < brief.index("## What your answers write")
    with pytest.raises(mq.MethodsError, match="## What to write"):
        mq.reader_brief(Template("mine", "## What to check\n"))


def test_a_queue_step_s_answer_is_its_session_s_review() -> None:
    assert review.of_session(mq.READER_KIND, rf.Session.METHODS)
    assert not review.of_session(mq.READER_KIND, rf.Session.CITATIONCHECK)


# --- a run --------------------------------------------------------------------------


ON_SCRIPT = {
    "anchors": [f"file:{KEY}", "thm:bound"],
    "severity": 1,
    "quote": COMPUTES,
    "problem": "the bound is printed, not computed",
    "fix": "compute it",
}


def _judged(served: str) -> tuple[str, dict]:
    return "judge", {**JUDGED, "task": served.split()[1], "finds": [ON_SCRIPT]}


def test_a_run_files_its_pair_in_its_round(paper_with_a_script) -> None:
    """A pair judged and a finding on its script filed in the run's round;
    the finding proposed fixed is the next round's task, served with its
    script and its block, its script run by its id, and settled."""
    store, paper = paper_with_a_script
    state = versions.latest(store)
    assert state is not None
    options = {
        "profile": "standard",
        "profiles": None,
        "paper": str(paper),
        "select": review.Selection.OPEN.value,
        "round": "r1",
        "model": "fake/queue",
    }
    run_ = queue.Run(store, state, mq.FACTORY, options)
    host = FakeQueueHost(working_through(_judged))
    runner = Runner(host, Resolver(store), 10.0, Rates(0.0, 1.0), review_round="r1")
    report = run_queue(runner, run_, [host], (1.0,), wait_seconds=5.0)
    assert (report.settled, report.open) == (1, 0), report
    filed = RecordStore(store.directory).authoritative()
    (answer,) = [a for a in filed.values() if a.kind == mq.READER_KIND]
    assert answer.review_round == "r1"
    (j,) = store.judgment_records().judgments
    assert j.verdict is Verdict.VERIFIED and j.run is not None
    (finding,) = store.judgment_records().findings
    answer_finding(
        store,
        finding.id,
        FindingStatus.FIXED,
        note="it computes the bound now",
        author=Author(AGENT, "model-b", "s9"),
    )
    settling = queue.Run(store, state, mq.FACTORY, {**options, "round": "r2"})
    with queue.opened(settling) as workflow:
        assert isinstance(workflow, mq.Methods)
        working = Working(workflow)
        (task,) = workflow.draw(working)
        (step,) = workflow.steps
        served = step.render(task, working)
        assert served.startswith("settle this finding\n⟦f1 ")
        assert f"its script, {KEY}" in served and "its block:" in served
        assert "The output is bounded by 1." in run(workflow, working, task="f1")
        why = {"task": "f1", "why": "the script still prints a constant"}
        stood = step.answer(task, "stands", why, working, True)
        assert isinstance(stood, Accepted), stood
        (refiled,) = stood.judgments.findings
        assert refiled.reaffirms == finding.id


def test_review_asks_the_methods_session_as_a_queue(
    paper_with_a_script, tmp_path, monkeypatch, capsys
) -> None:
    """`review --session methods`: a dry run prices the queue and writes
    its brief and tasks; `--scope` is refused; asked, on a fake host, its
    pair is filed, and asked again, the round replays and asks nothing."""
    from axiomation.cli import main
    from axiomation.commands import seating

    store, paper = paper_with_a_script
    args = ["review", str(paper), "--store", str(store.directory)]
    args += ["--session", "methods"]
    capsys.readouterr()
    assert main([*args, "--dry-run", "--prompts", str(tmp_path / "dry")]) == 0
    out = capsys.readouterr().out
    assert "methods at state" in out and ": 1 pair of a block and a script" in out
    assert "review-methods-reader on claude-opus-5-5" in out
    assert sorted(p.name for p in (tmp_path / "dry" / "methods").iterdir()) == [
        "review-methods-reader.md",
        "tasks.md",
    ]
    assert main([*args, "--scope", "1", "--budget", "5"]) != 0
    assert "it takes no --scope" in capsys.readouterr().err
    host = FakeQueueHost(working_through(_judged), model="claude-opus-5-5")
    monkeypatch.setattr(seating, "queue_hosts", lambda seated: [host])
    assert main([*args, "--budget", "5"]) == 0
    out = capsys.readouterr().out
    assert "1 tasks settled, 0 open" in out and "1 judgments, 1 findings" in out
    assert main([*args, "--budget", "5"]) == 0
    assert "$0.00 spent; 0 tasks settled, 0 open" in capsys.readouterr().out
