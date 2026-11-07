"""Develop mode: an answer's text kept beside the record, never in it
(`docs/architecture.md` § 4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from axiomation import develop
from axiomation.questions import Question, Stage, Template, Validation
from axiomation.records import RecordStore
from axiomation.resolver import Attempt, Resolver
from axiomation.run_state import RunFile, text_of


def _filed(store: RecordStore, text: str) -> None:
    question = Question("k1", "edges", Stage.EDGES, Template("t", "a"), "a prompt")
    Resolver(store).record(Attempt(question, "fake", "m", text, 0.0, 0.0, Validation()))


@pytest.mark.usefixtures("develop_mode")
def test_develop_mode_keeps_each_attempt_s_text_and_gives_the_latest(
    tmp_path: Path,
) -> None:
    store = RecordStore(tmp_path)
    _filed(store, "first")
    _filed(store, "second")

    assert develop.text(tmp_path, "k1") == "second"
    assert develop.text(tmp_path, "k1", 1) == "first"


def test_without_develop_mode_no_text_is_kept_anywhere(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv(develop.ENV, raising=False)
    store = RecordStore(tmp_path)
    _filed(store, "the answer's own words")

    assert not (tmp_path / develop.FILE).exists()
    with pytest.raises(develop.DevelopError, match=develop.ENV):
        develop.text(tmp_path, "k1")


def test_a_filed_answer_s_text_is_its_run_s_and_never_the_record_s(
    tmp_path: Path, monkeypatch
) -> None:
    """Outside develop mode, a stored answer's text is read from the run
    that holds it, and refused where none does: never read as empty."""
    monkeypatch.delenv(develop.ENV, raising=False)
    store = RecordStore(tmp_path)
    _filed(store, "the sheet")
    (answer,) = store.answers()
    run = RunFile(tmp_path, "build", "v", "")

    with pytest.raises(develop.DevelopError, match="no run holds the text"):
        text_of(answer, run, tmp_path)
    run.hold(answer.key, "edges", "the sheet")
    assert text_of(answer, run, tmp_path) == "the sheet"
