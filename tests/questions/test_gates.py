"""What every gate shares: its rounds, its log, its reason, and its files,
read and written through no link (a session can write beside them)."""

from __future__ import annotations

import builtins
import importlib
import io
import json
from pathlib import Path

import pytest

from axiomation.questions import GATE_LOG, gates

#: Every module the hosts run as a gate.
GATE_MODULES = (
    "axiomation.questions.decide_file",
    "axiomation.questions.edges_file",
    "axiomation.questions.front_lines",
    "axiomation.questions.groups_file",
    "axiomation.questions.review_file",
    "axiomation.questions.vocabulary_file",
)
SECRET = "a key's line, never to be read back"


def test_a_gate_counts_its_rounds_then_lets_go(tmp_path: Path) -> None:
    spec = tmp_path / "gate.json"
    assert [gates.round_left(spec) for _ in range(gates.ROUNDS + 1)] == [
        *[True] * gates.ROUNDS,
        False,
    ]
    assert (tmp_path / gates.TRIES).read_text() == str(gates.ROUNDS)


def test_a_gate_that_cannot_count_lets_go(tmp_path: Path) -> None:
    closed = tmp_path / "closed.md"
    closed.write_text(SECRET)
    (tmp_path / gates.TRIES).symlink_to(closed)
    assert gates.round_left(tmp_path / "gate.json") is False
    assert closed.read_text() == SECRET


def test_the_log_appends_and_follows_no_link(tmp_path: Path) -> None:
    spec = tmp_path / "gate.json"
    gates.log(spec, {"mode": "stop", "problems": [], "blocked": False})
    gates.log(spec, {"mode": "stop", "problems": ["x"], "blocked": True})
    logged = (tmp_path / GATE_LOG).read_text().splitlines()
    assert [json.loads(line)["blocked"] for line in logged] == [False, True]
    other = tmp_path / "other"
    other.mkdir()
    closed = tmp_path / "closed.md"
    closed.write_text(SECRET)
    (other / GATE_LOG).symlink_to(closed)
    gates.log(other / "gate.json", {"mode": "stop"})
    assert closed.read_text() == SECRET


def test_a_check_on_a_write_runs_once_a_file(tmp_path: Path) -> None:
    spec = tmp_path / "gate.json"
    assert gates.unchanged(spec, "a") is False
    assert gates.unchanged(spec, "a") is True
    assert gates.unchanged(spec, "b") is False


def test_the_reason_names_the_file_and_the_first_problems() -> None:
    said = gates.reason("roles.md", [f"p{n}" for n in range(35)], "and go on")
    assert said.startswith("roles.md does not read yet:\n\n- p0\n")
    assert "- and 5 more" in said and "- p30" not in said
    assert said.endswith("Fix these in roles.md, keep the rest, and go on.")


def _opened_by_name(monkeypatch: pytest.MonkeyPatch, under: Path) -> list[str]:
    """Every file under `under` opened by name, not through `gates`: the
    ways of opening a file that follow a link, recorded as they are used."""
    opened: list[str] = []

    def spied(original):
        def call(*args, **kwargs):
            given = args[0]
            if isinstance(given, (str, Path)) and str(given).startswith(str(under)):
                opened.append(str(given))
            return original(*args, **kwargs)

        return call

    for owner, name in (
        (builtins, "open"),
        (io, "open"),
        (Path, "open"),
        (Path, "read_text"),
        (Path, "read_bytes"),
        (Path, "write_text"),
        (Path, "write_bytes"),
    ):
        monkeypatch.setattr(owner, name, spied(getattr(owner, name)))
    return opened


@pytest.mark.parametrize("module", GATE_MODULES)
@pytest.mark.parametrize("spec_linked", [True, False])
def test_no_gate_reads_or_writes_through_a_link(
    module: str,
    spec_linked: bool,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The session's file, the round count and the log made links to a
    closed file, and the spec too or a regular file: the gate opens none of
    them by name, only through `gates`, which refuses a link; so it reads
    none of it back, writes nothing there, and lets the session end. The
    rounds and the log refuse theirs on their own (above)."""
    closed = tmp_path / "closed.md"
    closed.write_text(SECRET)
    work = tmp_path / "host"
    work.mkdir()
    for name in ("answer.md", gates.TRIES, GATE_LOG):
        (work / name).symlink_to(closed)
    if spec_linked:
        (work / "gate.json").symlink_to(closed)
    else:
        (work / "gate.json").write_text("{}")
    opened = _opened_by_name(monkeypatch, work)

    code = importlib.import_module(module).main(
        [str(work / "gate.json"), str(work / "answer.md")]
    )

    said = capsys.readouterr()
    assert code == 0 and said.out == ""
    assert SECRET not in said.err
    assert closed.read_text() == SECRET
    assert opened == []


def test_a_hard_link_or_a_pipe_is_no_file_and_is_left_as_it_was(
    tmp_path: Path,
) -> None:
    """A hard link to a closed file reads nothing and is not emptied; a
    pipe planted in a file's place is refused at once, never waited on."""
    import os

    closed = tmp_path / "closed.md"
    closed.write_text(SECRET)
    hard = tmp_path / "answer.md"
    os.link(closed, hard)
    with pytest.raises(gates.NotRegularFile):
        gates.read_regular(hard)
    with pytest.raises(gates.NotRegularFile):
        gates.write_regular(hard, "over")
    assert closed.read_text() == SECRET
    pipe = tmp_path / "pipe.md"
    os.mkfifo(pipe)
    with pytest.raises(gates.NotRegularFile):
        gates.read_regular(pipe)
    with pytest.raises(gates.NotRegularFile):
        gates.write_regular(pipe, "over")


def test_a_written_file_is_replaced_whole(tmp_path: Path) -> None:
    path = tmp_path / "gate-tries"
    gates.write_regular(path, "12345")
    gates.write_regular(path, "2")
    assert path.read_text() == "2"
