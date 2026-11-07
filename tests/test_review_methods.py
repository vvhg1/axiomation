"""The methods session on the review machinery: its
subjects, each block's uses edge onto a code file; the scripts laid in;
the judge line naming its run and its values as pairs; the gate refusing
what needs no run, and running the named script (cached) to refuse a value
the run did not print, handing the output back; a finding on a script's
lines; the sheet filed with its run seen, the judgment held.

On the edits' synthetic paper, built by a fake host with a script beside
it and `thm:bound`'s uses edge onto it, then kept as a state. Scripts run
by this interpreter; no model is called."""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

import pytest

import test_person
from axiomation import computations, edits, judgments, review, versions
from axiomation.cli import main
from axiomation.questions import GATE_LOG, load_template
from axiomation.questions import review_file as rf
from axiomation.records import AnchorKind, Aspect, ValuePair, Verdict
from axiomation.resolver import Resolver
from axiomation.runner import Runner
from axiomation.settings import CONFIG_NAME
from questions.test_edges_file import WRITTEN
from runner.fakes import FakeHost
from test_edits import built_state
from test_update import FLAT

METHODS = rf.Session.METHODS
TEMPLATE = load_template("review-methods")
KEY = "checks/bound.py"
#: prints the bound, then its arguments; exits 3 when asked to fail
SCRIPT = (
    "import sys\n"
    "if 'fail' in sys.argv[1:]:\n"
    "    sys.exit(3)\n"
    "print('The output is bounded by 1.')\n"
    "for a in sys.argv[1:]:\n"
    "    print(a)\n"
)
LINES = {
    **WRITTEN,
    "thm:bound": [
        *WRITTEN["thm:bound"],
        f'→ uses file:{KEY} · "The output is bounded."',
    ],
}
#: the script's line that prints the bound, as SCRIPT has it
COMPUTES = "print('The output is bounded by 1.')"
VERIFIED = (
    f"judge s1 verified computational run {KEY} — "
    '"The output is bounded" ↔ "bounded by 1" — '
    f'code "{COMPUTES}" — the script prints the bound'
)
ON_THE_SCRIPT = (
    f"find computed file:{KEY} thm:bound 1 \"print('The output is bounded by 1.')\" "
    "— the bound is printed, not computed — compute it"
)


@pytest.fixture
def paper_with_a_script(tmp_path, capsys, monkeypatch):
    """The synthetic paper with `checks/bound.py` and its runner, built and
    kept, `thm:bound` using the script."""
    made = test_person.make_paper

    def with_script(root, *args, **kwargs):
        paper = made(root, *args, **kwargs)
        (root / "checks").mkdir()
        (root / KEY).write_text(SCRIPT, encoding="utf-8")
        (root / CONFIG_NAME).write_text(
            f"[computations.runners]\npy = {shlex.quote(sys.executable)!r}\n",
            encoding="utf-8",
        )
        return paper

    monkeypatch.setattr(test_person, "make_paper", with_script)
    store, args = built_state(tmp_path, capsys, LINES)
    return store, Path(args[0])


@pytest.fixture
def state(paper_with_a_script):
    store, paper = paper_with_a_script
    kept = versions.latest(store)
    assert kept is not None
    with edits.opened(store, kept) as opened:
        found = review.brief(opened, METHODS, TEMPLATE, None, directory=paper)
        yield store, opened, found, paper


def sheet_with(found: review.Brief, *lines: str) -> str:
    assert found.question.workspace is not None
    return found.question.workspace.files[rf.REVIEW_FILE] + "\n".join(lines) + "\n"


def problems(found: review.Brief, *lines: str) -> list[str]:
    return list(rf.read(found.spec, sheet_with(found, *lines)).problems)


# --- the brief -------------------------------------------------------------------


def test_the_subjects_are_the_uses_edges_onto_code_files_and_the_scripts_laid_in(
    state,
) -> None:
    _, opened, found, paper = state
    (s,) = found.subjects.values()
    assert s.aspect is Aspect.COMPUTED and s.subject.file == KEY
    assert s.sheet.lines() == [
        f"⟦s1 computed⟧ thm:bound → file:{KEY}",
        f'  ↳ thm:bound → uses file:{KEY} · "The output is bounded."',
    ]
    assert found.question.workspace is not None
    files = found.question.workspace.files
    assert files[f"code/{KEY}"] == SCRIPT
    assert rf.REVIEW_FILE in files and rf.PAPER_FILE in files
    assert found.spec["runs"] == {
        "paper": str(paper.resolve()),
        "cache": str((opened.store.directory / computations.RUN_CACHE).resolve()),
    }
    assert found.spec["subjects"]["s1"]["file"] == KEY
    assert "run <file> [args…]" in files[rf.REVIEW_FILE]
    # the argument session reads no code: no `code/`, no runs
    argument = review.brief(
        opened,
        rf.Session.ARGUMENT,
        load_template("review-argument"),
        None,
        directory=paper,
    )
    assert argument.question.workspace is not None
    assert not any(n.startswith("code/") for n in argument.question.workspace.files)
    assert argument.spec["runs"] is None and argument.spec["code"] == {}


def test_a_workspace_key_is_laid_under_code_workspace() -> None:
    assert review.code_path("checks/x.py") == "code/checks/x.py"
    assert review.code_path("workspace:shared/x.sh") == "code/workspace/shared/x.sh"


# --- the gate, before any run ----------------------------------------------------


def test_a_verified_line_with_its_run_and_pairs_reads(state) -> None:
    _, _, found, _ = state
    read = rf.read(found.spec, sheet_with(found, VERIFIED, ON_THE_SCRIPT))
    assert not read.problems
    (j,) = read.judges
    assert j.run == (KEY,) and j.values == (("The output is bounded", "bounded by 1"),)
    assert j.code == (COMPUTES,)
    (f,) = read.finds
    assert f.anchors == (f"file:{KEY}", "thm:bound")


@pytest.mark.parametrize(
    ("line", "wanted"),
    [
        (
            'judge s1 verified computational run checks/other.py — "The output is '
            'bounded" ↔ "bounded" — x',
            "this subject's file is checks/bound.py",
        ),
        ("judge s1 verified computational — the script prints it", "names the run"),
        (f"judge s1 verified computational run {KEY} — prints it", "each value"),
        (
            f'judge s1 verified computational run {KEY} — "The bound is sharp" ↔ '
            '"bounded" — x',
            "not in the block's text",
        ),
        (
            'judge s1 inconclusive informal — "The output is bounded" ↔ "bounded" — x',
            "name the run",
        ),
        ("judge s1 verified formal — x", "depth one of"),
        (
            f"judge s1 not-shown informal pages a:1 run {KEY} — x",
            "a judge line is",
        ),
        (
            'find computed thm:bound file:checks/bound.py 2 "The output" — x — y',
            "a `file:KEY` anchor comes first",
        ),
        ('find computed file:checks/bound.py 2 "print" — x — y', "found 2 times"),
        ('find computed file:checks/nope.py 2 "print" — x — y', "no block"),
        ('find valid thm:bound 2 "The output" — x — y', "finds computed"),
        (
            f'judge s1 not-shown informal run {KEY} — "The output" ↔ bounded — x',
            "do not read as pairs",
        ),
        (f"judge s1 inconclusive informal run {KEY} /etc/passwd — x", "outside"),
        (
            f'judge s1 verified computational run {KEY} — "The output is bounded" '
            '↔ "bounded by 1" — the script prints the bound',
            "quotes the script's line",
        ),
        (
            f'judge s1 verified computational run {KEY} — "The output is bounded" '
            '↔ "bounded by 1" — code "print the bound" — it prints it',
            "is not a line of checks/bound.py",
        ),
        (
            f'judge s1 verified computational run {KEY} — "The output is bounded" '
            '↔ "bounded by 1" — code print(1) — it prints it',
            "the code does not read",
        ),
        (f"judge s1 inconclusive informal run {KEY} --in=../../k — x", "outside"),
        (f"judge s1 inconclusive informal run {KEY} ~/keys.md — x", "outside"),
    ],
)
def test_the_gate_refuses_each_bad_line(state, line, wanted) -> None:
    _, _, found, _ = state
    found_problems = problems(found, line)
    assert any(wanted in p for p in found_problems), found_problems


def test_an_argument_inside_the_paper_and_a_quote_across_a_marker_read(
    state,
) -> None:
    _, _, found, _ = state
    read = rf.read(
        found.spec,
        sheet_with(
            found,
            f"judge s1 verified computational run {KEY} --in=data/x.csv 5 — "
            '"The output ⟦+ claim⟧ is bounded" ↔ "bounded by 1" — '
            f'code "{COMPUTES}" — prints it',
        ),
    )
    assert not read.problems
    (j,) = read.judges
    assert j.run == (KEY, "--in=data/x.csv", "5")
    assert j.values == (("The output is bounded", "bounded by 1"),)


def test_an_argument_sheet_takes_no_file_anchor(state) -> None:
    _, opened, _, paper = state
    argument = review.brief(
        opened,
        rf.Session.ARGUMENT,
        load_template("review-argument"),
        None,
        directory=paper,
    )
    found_problems = problems(argument, f'find valid file:{KEY} 2 "print" — x — y')
    assert any("no block, group or cited work" in p for p in found_problems)


# --- the gate's runs -------------------------------------------------------------


def test_a_value_the_run_did_not_print_comes_back_with_the_output(state) -> None:
    _, _, found, _ = state
    sheet = rf.read(
        found.spec,
        sheet_with(
            found,
            f'judge s1 verified computational run {KEY} --level 5 — "The output is '
            f'bounded" ↔ "bounded by 2" — code "{COMPUTES}" — the script prints '
            "the bound",
        ),
    )
    assert not sheet.problems

    ran, log = rf.run_problems(found.spec, sheet, rf.GATE_RUN_SECONDS)

    (problem,) = ran
    assert '"bounded by 2" not in what' in problem
    assert "The output is bounded by 1.\n--level\n5" in problem
    assert log[0]["run"] == f"{KEY} --level 5" and log[0]["status"] == "ran"
    assert log[0]["seconds"] is not None


def test_a_run_that_did_not_run_bears_only_inconclusive(state) -> None:
    _, _, found, _ = state
    for verdict, refused in (("not-shown", True), ("inconclusive", False)):
        sheet = rf.read(
            found.spec,
            sheet_with(
                found, f"judge s1 {verdict} computational run {KEY} fail — it exits"
            ),
        )
        ran, _ = rf.run_problems(found.spec, sheet)
        assert bool(ran) is refused, ran
        assert not refused or "never evidence" in ran[0]


def test_a_script_changed_on_disk_since_the_state_is_refused(state) -> None:
    _, _, found, paper = state
    (paper / KEY).write_text(SCRIPT + "# edited\n", encoding="utf-8")
    sheet = rf.read(found.spec, sheet_with(found, VERIFIED))
    ran, _ = rf.run_problems(found.spec, sheet)
    assert any("changed on disk since the state" in p for p in ran)


def test_a_run_past_the_gate_s_time_is_left_to_the_check_uncached(
    tmp_path,
) -> None:
    paper = tmp_path / "paper"
    paper.mkdir()
    (paper / "slow.py").write_text("import time\ntime.sleep(5)\nprint(1)\n")
    (paper / CONFIG_NAME).write_text(
        f"[computations.runners]\npy = {shlex.quote(sys.executable)!r}\n"
    )
    cache = tmp_path / "cache"
    assert computations.run_within(paper, "slow.py", (), cache, 1) is None
    assert not cache.exists() or not list(cache.iterdir())
    (paper / "fast.py").write_text("print(1)\n")
    assert computations.run_within(paper, "fast.py", (), cache, 0.5) is None, (
        "the time spent: not started"
    )
    assert computations.run(paper, "fast.py", (), cache).stdout == "1\n"
    cached = computations.run_within(paper, "fast.py", (), cache, 0)
    assert cached is not None and cached.stdout == "1\n", "read from the cache"


def test_the_gate_runs_the_script_at_the_end_and_logs_its_time(
    state, tmp_path, capsys
) -> None:
    _, _, found, _ = state
    spec = tmp_path / "gate.json"
    spec.write_text(json.dumps(found.spec), encoding="utf-8")
    sheet = tmp_path / rf.REVIEW_FILE
    sheet.write_text(
        sheet_with(found, VERIFIED.replace('↔ "bounded by 1"', '↔ "bounded by 9"')),
        encoding="utf-8",
    )

    assert rf.main([str(spec), str(sheet)]) == 0

    decision = json.loads(capsys.readouterr().out)
    assert decision["decision"] == "block"
    assert "bounded by 9" in decision["reason"]
    (entry,) = [
        json.loads(line) for line in (tmp_path / GATE_LOG).read_text().splitlines()
    ]
    assert entry["runs"][0]["status"] == "ran"

    sheet.write_text(sheet_with(found, VERIFIED), encoding="utf-8")
    assert rf.main([str(spec), str(sheet)]) == 0
    assert capsys.readouterr().out == ""


# --- what the session files ------------------------------------------------------


def test_a_fake_host_s_sheet_is_filed_with_its_run_and_its_finding_on_the_script(
    state,
) -> None:
    store, opened, found, _ = state
    host = FakeHost(
        lambda p: "",
        edits={rf.REVIEW_FILE: lambda _: sheet_with(found, VERIFIED, ON_THE_SCRIPT)},
    )
    report = Runner(host, Resolver(store), 10.0, FLAT).run(
        [found.question],
        review.checker(opened, found, None, edits.agent(host.model, None)),
    )
    assert report.answered == (found.question.key,), report

    held = store.judgment_records()
    (j,) = held.judgments
    assert j.aspect is Aspect.COMPUTED and j.verdict is Verdict.VERIFIED
    assert j.run is not None and j.run.key == KEY and j.run.ran
    assert j.values == (ValuePair("The output is bounded", "bounded by 1"),)
    assert j.code == (COMPUTES,)
    (seen_run,) = held.runs
    assert seen_run.stamp == j.run and seen_run.answer == found.question.key
    (f,) = held.findings
    assert [a.kind for a in f.anchors] == [AnchorKind.FILE, AnchorKind.BLOCK]
    assert f.anchors[0].file == KEY
    (status,) = [
        s
        for s in judgments.readings(store, opened.state).statuses
        if s.aspect is Aspect.COMPUTED
    ]
    assert status.status is judgments.StatusKind.HELD


def test_review_skips_a_seated_session_with_no_subject_unless_named(
    tmp_path, capsys
) -> None:
    store, args = built_state(tmp_path, capsys)
    paper = args[0]
    where = [
        "--store",
        str(store.directory),
        "--dry-run",
        "--prompts",
        str(tmp_path / "d"),
    ]
    assert main(["review", paper, *where]) == 0
    out = capsys.readouterr().out
    assert "methods: state" in out and "offers the methods session no subject" in out
    assert "argument at state" in out
    assert main(["review", paper, *where, "--session", "methods"]) == 1
    assert "no subject" in capsys.readouterr().err


def test_an_update_keeps_a_script_changed_alone_as_a_state_asking_nothing(
    paper_with_a_script, capsys
) -> None:
    """A planted copy whose script alone changed and a
    paper whose build predates its script: `update` keeps the files as a
    state of their own, the export carried, so the script can be judged
    on it; run again, it is nothing to update."""
    store, paper = paper_with_a_script
    before = versions.latest(store)
    assert before is not None
    (paper / KEY).write_text(SCRIPT.replace("by 1", "by 2"), encoding="utf-8")
    where = ["--store", str(store.directory), "--host", "none"]

    assert main(["update", str(paper), *where]) == 0

    out = capsys.readouterr().out
    after = versions.latest(store)
    assert after is not None and after.parent == before.id
    assert f"files changed alone ({KEY})" in out
    assert after.version == before.version and after.files[KEY] != before.files[KEY]
    assert versions.kept_export(store, after) == versions.kept_export(store, before)
    assert main(["update", str(paper), *where]) == 1
    assert "nothing to update" in capsys.readouterr().err


def test_a_bare_update_of_a_script_changed_alone_asks_for_nothing(
    paper_with_a_script, capsys
) -> None:
    """Under the default profile and with no --budget, an update that
    asks nothing runs; the next, with nothing to update, says so rather
    than asking for a budget."""
    store, paper = paper_with_a_script
    (paper / KEY).write_text(SCRIPT.replace("by 1", "by 2"), encoding="utf-8")
    where = ["--store", str(store.directory)]

    assert main(["update", str(paper), *where]) == 0
    assert f"files changed alone ({KEY})" in capsys.readouterr().out
    assert main(["update", str(paper), *where]) == 1
    assert "nothing to update" in capsys.readouterr().err


def test_an_update_from_a_state_not_the_latest_of_its_version_is_refused(
    paper_with_a_script, capsys
) -> None:
    """Its export carried onto the latest would undo what came between."""
    store, paper = paper_with_a_script
    before = versions.latest(store)
    assert before is not None
    where = ["--store", str(store.directory), "--host", "none"]
    (paper / KEY).write_text(SCRIPT.replace("by 1", "by 2"), encoding="utf-8")
    assert main(["update", str(paper), *where]) == 0
    capsys.readouterr()
    (paper / KEY).write_text(SCRIPT.replace("by 1", "by 3"), encoding="utf-8")

    assert main(["update", str(paper), *where, "--state", before.id]) == 1

    assert "not the latest of its version" in capsys.readouterr().err


def test_several_code_lines_read_and_a_quote_s_quotes_stay(state) -> None:
    _, _, found, _ = state
    read = rf.read(
        found.spec,
        sheet_with(
            found,
            f"judge s1 verified computational run {KEY} — "
            '"The output is bounded" ↔ "bounded by 1" — '
            f'code "{COMPUTES}"; "for a in sys.argv[1:]:" — prints the bound',
        ),
    )
    assert not read.problems
    (j,) = read.judges
    assert j.code == (COMPUTES, "for a in sys.argv[1:]:")
