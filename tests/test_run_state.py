"""A run's working state, kept beside the store (`docs/architecture.md`
§ 4)."""

from pathlib import Path

import pytest

from axiomation import run_state
from axiomation.run_state import Cursor, RunState


def _state(**cursor) -> RunState:
    return RunState(
        command="build",
        cursor=Cursor(version="v1", **cursor),
        texts={"k-edges": "a sheet\n"},
    )


def test_a_written_state_reads_back_whole(tmp_path: Path) -> None:
    state = _state(
        stage="edges",
        step=2,
        edges_answer="k-edges",
        person_rounds=("p1",),
    )
    run_state.write(tmp_path, state)
    assert run_state.read(tmp_path, "build", "v1") == state


def test_another_command_s_or_version_s_run_is_none_to_resume(tmp_path: Path) -> None:
    run_state.write(tmp_path, _state())
    assert run_state.read(tmp_path, "review-cut", "v1") is None
    assert run_state.read(tmp_path, "build", "v2") is None


def test_a_run_made_from_other_settings_or_code_is_none_to_resume(
    tmp_path: Path,
) -> None:
    run_state.write(
        tmp_path, RunState(command="build", cursor=Cursor("v1", made_from="h1"))
    )
    assert run_state.read(tmp_path, "build", "v1", "h1") is not None
    assert run_state.read(tmp_path, "build", "v1", "h2") is None


def test_a_file_of_another_format_is_no_run(tmp_path: Path) -> None:
    run_state.path(tmp_path, "build").parent.mkdir()
    run_state.path(tmp_path, "build").write_text('{"format": 0}', encoding="utf-8")
    assert run_state.read(tmp_path, "build", "v1") is None


def test_a_removed_state_is_no_run_and_removing_none_is_fine(tmp_path: Path) -> None:
    run_state.write(tmp_path, _state())
    run_state.remove(tmp_path, "build")
    run_state.remove(tmp_path, "build")
    assert run_state.read(tmp_path, "build", "v1") is None


def test_a_write_leaves_no_temporary_file(tmp_path: Path) -> None:
    run_state.write(tmp_path, _state())
    run_state.write(tmp_path, _state(step=3))
    runs = tmp_path / run_state.RUNS
    assert sorted(p.name for p in runs.iterdir()) == ["build.json"]
    assert run_state.read(tmp_path, "build", "v1").cursor.step == 3


def test_two_commands_runs_are_kept_apart(tmp_path: Path) -> None:
    run_state.write(tmp_path, _state())
    run_state.write(tmp_path, RunState("edit", Cursor("v1"), {"k": "t"}))
    assert run_state.read(tmp_path, "build", "v1") == _state()
    assert run_state.read(tmp_path, "edit", "v1").texts == {"k": "t"}


def test_a_text_the_run_does_not_hold_is_named(tmp_path: Path) -> None:
    with pytest.raises(KeyError, match="k-other"):
        _state().text("k-other")
