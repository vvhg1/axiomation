"""A paper's script run and stamped: the person's runner
and the arguments a judgment names, as an argument list and never through a
shell, from the paper's directory, under the timeout; cached by the
script's blob, runner and arguments. A `computed` judgment names its run:
of the kept state's script, a run that did not run never evidence, a
`verified` one naming each value it matched, the text's words found in the
block and the output's in the run's stdout; a later run of the same
whose output moved lapses it. Synthetic scripts run by this interpreter;
no model is called."""

from __future__ import annotations

import shlex
import sys
from pathlib import Path
from typing import Any

import pytest

from axiomation import computations, versions
from axiomation.cli import main
from axiomation.established import established
from axiomation.evidence import RunStatus
from axiomation.exchange import dumps
from axiomation.judgments import (
    Author,
    CauseKind,
    JudgmentError,
    StatusKind,
    judge,
    readings,
    reaffirm,
    see_runs,
    seen,
    subject,
)
from axiomation.marks import open_marks
from axiomation.records import Aspect, Depth, RecordStore, ValuePair, Verdict
from axiomation.settings import CONFIG_NAME, SettingsError
from axiomation.versions import Command
from test_judgments import TEXT, graph
from test_versions import V1

KEY = "checks/bound.py"
PERSON = Author()
#: prints the bound and echoes its arguments, one per line, as it got them
BOUND = 'import sys\nprint("f is bounded")\nfor a in sys.argv[1:]:\n    print(a)\n'


def file_edge(source: str, key: str = KEY) -> dict[str, Any]:
    return {
        "id": f"{source}>uses>{key}",
        "source": source,
        "kind": "uses",
        "target": {"file": key},
        "evidence": [{"text": "f is bounded"}],
    }


def a_paper(tmp_path: Path, script: str = BOUND, timeout: int = 60) -> Path:
    paper = tmp_path / "paper"
    (paper / "checks").mkdir(parents=True)
    (paper / "p.md").write_text(TEXT)
    (paper / KEY).write_text(script)
    (paper / CONFIG_NAME).write_text(
        f"[computations]\nrun_timeout_seconds = {timeout}\n"
        f"[computations.runners]\npy = {shlex.quote(sys.executable)!r}\n"
    )
    return paper


def kept(paper: Path, store: RecordStore, payload=None) -> versions.State:
    """A build's state of the paper as it is on disk."""
    files = {"p.md": TEXT.encode(), KEY: (paper / KEY).read_bytes()}
    payload = payload or graph(edges=[file_edge("t")])
    for s in payload["standings"]:
        s["status"] = "self"
    state, _ = versions.record(
        store, files, V1, "p.md", dumps(payload).encode(), Command.BUILD
    )
    return state


def ran(paper: Path, store: RecordStore, *arguments: str, fresh: bool = False):
    return computations.run(
        paper, KEY, arguments, store.directory / computations.RUN_CACHE, fresh
    )


BOUNDED = (ValuePair("f is bounded.", "f is bounded"),)


#: the script's line that computes the value, as BOUND has it
COMPUTES = ('print("f is bounded")',)


def judged(store, state, ran_, verdict=Verdict.VERIFIED, values=BOUNDED, code=None):
    if code is None:
        code = COMPUTES if verdict is Verdict.VERIFIED else ()
    on = subject(seen(store, state), Aspect.COMPUTED, "t")
    return judge(
        store,
        state,
        Aspect.COMPUTED,
        on,
        verdict,
        Depth.COMPUTATIONAL,
        "ran it",
        ran=ran_,
        values=values,
        code=code,
    ).records.judgments[0]


def computed(store, state):
    (status,) = [
        s for s in readings(store, state).statuses if s.aspect is Aspect.COMPUTED
    ]
    return status


# --- the run -------------------------------------------------------------------------


def test_a_run_is_an_argument_list_never_a_shell(tmp_path) -> None:
    paper = a_paper(tmp_path)
    store = RecordStore(tmp_path / "store")
    words = ["$(touch pwned)", "a b; touch pwned2", "*"]

    found = ran(paper, store, *words)

    assert found.stamp.status is RunStatus.RAN
    assert found.stdout.splitlines() == ["f is bounded", *words]
    assert not list(paper.glob("pwned*"))
    assert found.stamp.directory == str(paper.resolve())
    assert found.stamp.runner == shlex.quote(sys.executable)
    assert found.stamp.arguments == tuple(words)


def test_a_run_is_cached_by_blob_runner_and_arguments(tmp_path) -> None:
    script = (
        "from pathlib import Path\n"
        "count = Path('count'); n = int(count.read_text()) if count.exists() else 0\n"
        "count.write_text(str(n + 1)); print(n + 1)\n"
    )
    paper = a_paper(tmp_path, script)
    store = RecordStore(tmp_path / "store")

    first, again = ran(paper, store), ran(paper, store)
    other = ran(paper, store, "x")
    fresh = ran(paper, store, fresh=True)

    assert first == again and first.stdout == "1\n", "read from the cache"
    assert other.stdout == "2\n" and fresh.stdout == "3\n"
    assert (paper / "count").read_text() == "3", "the side effects land in the paper"


def test_a_file_outside_the_code_files_and_one_with_no_runner_are_refused(
    tmp_path,
) -> None:
    paper = a_paper(tmp_path)
    store = RecordStore(tmp_path / "store")
    (paper / "notes.txt").write_text("x")
    (paper / "checks" / "fit.R").write_text("print(1)\n")
    with pytest.raises(SettingsError, match="not one of the paper's code files"):
        computations.run(paper, "notes.txt", (), store.directory)
    with pytest.raises(SettingsError, match="no runner for .R files"):
        computations.run(paper, "checks/fit.R", (), store.directory)


@pytest.mark.parametrize(
    ("script", "timeout", "status"),
    [
        ("import sys\nprint('f is bounded')\nsys.exit(3)\n", 60, RunStatus.FAILED),
        (
            "import time\nprint('f is bounded', flush=True)\ntime.sleep(30)\n",
            1,
            RunStatus.TIMED_OUT,
        ),
    ],
)
def test_a_run_that_did_not_run_is_never_evidence(
    tmp_path, script, timeout, status
) -> None:
    paper = a_paper(tmp_path, script, timeout)
    store = RecordStore(tmp_path / "store")
    state = kept(paper, store)

    found = ran(paper, store)

    assert found.stamp.status is status and not found.stamp.ran
    for verdict in (Verdict.VERIFIED, Verdict.NOT_SHOWN, Verdict.CONTRADICTED):
        with pytest.raises(JudgmentError, match="never evidence"):
            judged(store, state, found, verdict, values=())
    j = judged(store, state, found, Verdict.INCONCLUSIVE, values=())
    assert j.run == found.stamp


def test_a_run_of_a_script_changed_since_the_state_is_refused(tmp_path) -> None:
    paper = a_paper(tmp_path)
    store = RecordStore(tmp_path / "store")
    state = kept(paper, store)
    (paper / KEY).write_text(BOUND + "# edited\n")

    with pytest.raises(JudgmentError, match="changed since state .* was kept"):
        judged(store, state, ran(paper, store))


def test_a_verified_judgment_names_each_value_it_found(tmp_path) -> None:
    paper = a_paper(tmp_path)
    store = RecordStore(tmp_path / "store")
    state = kept(paper, store)
    found = ran(paper, store)
    for values, problem in (
        ((), "names each value it matched"),
        ((ValuePair("f is unbounded", "f is bounded"),), "not in t's text"),
        ((ValuePair("f is bounded", "f is unbounded"),), "not in the run's output"),
        ((ValuePair("", "f is bounded"),), "not in t's text"),
    ):
        with pytest.raises(JudgmentError, match=problem):
            judged(store, state, found, values=values)
    # its reading of the code is the code's: a line the file has, never a
    # paraphrase of what it does
    for code, problem in (
        ((), "quotes the script's line"),
        (("print(f is bounded)",), "not in file:checks/bound.py"),
    ):
        with pytest.raises(JudgmentError, match=problem):
            judged(store, state, found, code=code)

    j = judged(store, state, found)

    assert j.values == BOUNDED and j.run == found.stamp and j.code == COMPUTES
    (seen_run,) = store.judgment_records().runs
    assert seen_run.stamp == found.stamp
    assert computed(store, state).status is StatusKind.HELD
    # `computed` is asked beside the claim's own route, never in its place
    judge(
        store,
        state,
        Aspect.TRUE,
        subject(seen(store, state), Aspect.TRUE, "t"),
        Verdict.VERIFIED,
        Depth.INFORMAL,
        "read it",
    )
    node = state.node_of("t")
    (claim,) = [
        e
        for e in established(readings(store, state), open_marks(store, state))
        if e.claim.node == node
    ]
    assert claim.established


def test_a_run_whose_output_moves_lapses_the_judgment_that_read_it(tmp_path) -> None:
    script = "import time\nprint('f is bounded', time.time_ns())\n"
    paper = a_paper(tmp_path, script)
    store = RecordStore(tmp_path / "store")
    state = kept(paper, store)
    judged(
        store, state, ran(paper, store), code=("print('f is bounded', time.time_ns())",)
    )
    see_runs(store, [ran(paper, store).stamp], PERSON)
    assert computed(store, state).status is StatusKind.HELD, "the cache: one run"

    see_runs(store, [ran(paper, store, fresh=True).stamp], PERSON)

    status = computed(store, state)
    assert status.status is StatusKind.LAPSED
    assert status.cause.kind is CauseKind.RUN
    with pytest.raises(JudgmentError, match="run it again and file a judgment"):
        reaffirm(store, state, status.reads[-1].judgment.id, Depth.INFORMAL, "x")


def test_a_deterministic_script_run_again_lapses_nothing(tmp_path) -> None:
    paper = a_paper(tmp_path)
    store = RecordStore(tmp_path / "store")
    state = kept(paper, store)
    judged(store, state, ran(paper, store))

    see_runs(store, [ran(paper, store, fresh=True).stamp], PERSON)

    assert computed(store, state).status is StatusKind.HELD


# --- the commands ------------------------------------------------------------------


def test_judge_and_run_from_the_command_line(tmp_path, capsys) -> None:
    paper = a_paper(tmp_path)
    store = RecordStore(paper / "axiomation")
    state = kept(paper, store)
    base = [str(paper), "--store", str(store.directory)]

    code = main(
        [
            "judge",
            *base,
            "--aspect",
            "computed",
            "--on",
            "t",
            "--run",
            KEY,
            "--value",
            "Then f is bounded.",
            "f is bounded",
            "--code",
            'print("f is bounded")',
            "--verdict",
            "verified",
            "--depth",
            "computational",
            "--evidence",
            "the script derives the bound",
            "--",
            "--looks-like-an-option",
        ]
    )

    out = capsys.readouterr()
    assert code == 0, out.err
    assert f"run {KEY} --looks-like-an-option: ran" in out.out
    (j,) = store.judgment_records().judgments
    assert j.run.arguments == ("--looks-like-an-option",) and j.state == state.id
    assert main(["show", *base, "t"]) == 0
    assert '· "Then f is bounded." ↔ "f is bounded"' in capsys.readouterr().out

    code = main(["run", *base, KEY, "--fresh", "--", "--looks-like-an-option"])

    out = capsys.readouterr()
    assert code == 0, out.err
    assert "stdout:\nf is bounded\n--looks-like-an-option" in out.out
    assert len(store.judgment_records().runs) == 2
    assert computed(store, state).status is StatusKind.HELD, "the same output"

    assert main(["run", *base, "p.md"]) == 1
    assert "not one of the paper's code files" in capsys.readouterr().err
