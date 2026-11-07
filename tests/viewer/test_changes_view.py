"""Change mode's reads: `changes` between a state and
the update made from it carries the update's own record, each Δ line and
each block asked again, as the kept export holds them; between any other
two states it carries none."""

from __future__ import annotations

import pytest
from test_viewer_server import built_and_updated

from axiomation import versions
from axiomation.queries import View, changes_report


@pytest.mark.usefixtures("develop_mode")
def test_an_update_s_changes_carry_its_deltas_and_what_it_asked_again(
    tmp_path, capsys
) -> None:
    _, store = built_and_updated(tmp_path, capsys)
    kept = versions.states(store)
    update = next(s for s in kept if s.command is versions.Command.UPDATE)
    parent = next(s for s in kept if s.id == update.parent)
    old, new = View(store, parent), View(store, update)
    record = new.export["review"]["update"]

    report = changes_report(store, old, new)

    deltas = [item.data for item in report.sections["deltas"]]
    assert [(d["block"], d["class"], d["note"]) for d in deltas] == [
        (d["block"], d["class"], d["note"]) for d in record["deltas"]
    ]
    assert any(d["handle"] == "we-fix-a-field" for d in deltas)
    reopened = [item.data for item in report.sections["reopened"]]
    assert [r["handle"] for r in reopened] == [r["handle"] for r in record["reopened"]]
    assert all(r["block"] in new.blocks or r["block"] in new.groups for r in reopened)


@pytest.mark.usefixtures("develop_mode")
def test_two_states_not_one_update_apart_carry_no_update_record(
    tmp_path, capsys
) -> None:
    _, store = built_and_updated(tmp_path, capsys)
    kept = versions.states(store)
    update = next(s for s in kept if s.command is versions.Command.UPDATE)

    same = changes_report(store, View(store, update), View(store, update))

    assert "deltas" not in same.sections and "reopened" not in same.sections
