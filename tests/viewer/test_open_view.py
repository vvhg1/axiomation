"""The open view: `queries.open_report`, one
list for an agent (`axiomation open`) and the page, each kind equal to a
direct reading of the export and the records, each item's anchor on the
state.

On the queries' rich graph, on `test_marks`'s chain updated and kept (its
stale marks), and with a finding filed on a state. No model is called.
"""

from __future__ import annotations

import json

from axiomation.check import unsettled
from axiomation.cli import main
from axiomation.judgments import find
from axiomation.marks import open_marks
from axiomation.queries import (
    Filter,
    OpenKind,
    View,
    matching,
    open_report,
    render,
)
from axiomation.records import Anchor, AnchorKind, Aspect
from axiomation.rulings import Support
from axiomation.versions import pending_answers
from test_marks import kept_update
from test_queries import rich  # noqa: F401 - the fixture


def data(view: View, kind: OpenKind) -> list[dict]:
    return [i.data for i in open_report(view).sections[kind.section]]


def anchored(view: View, item: dict) -> bool:
    """An item's anchor names a block, a part of one, a group or the paper
    of the state, and its block is where it shows."""
    anchor = item["anchor"]
    if anchor["kind"] == "paper":
        return item["block"] is None
    ident = view.nodes and {n: i for i, n in view.nodes.items()}.get(anchor["node"])
    if anchor["kind"] in ("part", "span"):
        return ident == item["block"] and bool(anchor["words"])
    if anchor["kind"] == "group":
        return ident in view.groups
    return ident == item["block"] and ident in view.blocks


def test_unsupported_claims_and_their_users_are_the_filters(rich) -> None:  # noqa: F811
    view, _, payload = rich
    unsupported = data(view, OpenKind.UNSUPPORTED)
    assert [(i["block"], i["anchor"]["words"]) for i in unsupported] == [
        (s["block"], s["claim"]["text"])
        for s, support in zip(view.standings, view.support, strict=True)
        if support is not None and support.status is Support.UNSUPPORTED
    ]
    assert any(
        s["status"] != "unsupported" and support.status is Support.UNSUPPORTED
        for s, support in zip(view.standings, view.support, strict=True)
        if support is not None
    ), "the rich graph holds a justified claim no justifies edge reaches"
    assert [i["block"] for i in data(view, OpenKind.USES_UNSUPPORTED)] == matching(
        view, Filter(uses_unsupported=True)
    )
    assert data(view, OpenKind.USES_UNSUPPORTED), (
        "the rich graph's closing uses the sharp claim"
    )
    for kind in OpenKind:
        for item in data(view, kind):
            assert item["kind"] == kind.value
            assert anchored(view, item), item
            assert item["clears"]


def test_the_stale_marks_and_the_pending_are_the_records(tmp_path) -> None:
    v, state = kept_update(tmp_path)
    view = View(v.store, state)
    stale = data(view, OpenKind.STALE)
    marks = open_marks(v.store, state)
    assert marks, "the update marks what its Δ asks"
    assert [(i["node"], i["since"], i["cause"]) for i in stale] == [
        (m.node, m.since, [dict(c) for c in m.causes]) for m in marks
    ]
    assert len(data(view, OpenKind.PENDING)) == len(
        pending_answers(v.store, state.version)
    ) + len(view.export["review"].get("unanswered", []))
    flags = [f for f in view.export["review"].get("flags", []) if not f.get("decided")]
    assert len(data(view, OpenKind.FLAG)) == len(flags)
    for kind in OpenKind:
        for item in data(view, kind):
            assert anchored(view, item), item


def test_an_open_finding_is_listed_and_an_answered_one_is_not(rich) -> None:  # noqa: F811
    view, _, _ = rich
    block = next(iter(view.blocks))
    found = find(
        view.store,
        view.state,
        Aspect.WELL_POSED,
        [Anchor(AnchorKind.BLOCK, view.node(block))],
        2,
        "a made-up problem",
    )
    fresh = View(view.store, view.state)
    (item,) = data(fresh, OpenKind.FINDING)
    assert item["block"] == block and item["status"] == "severity 2"
    assert found.records.findings[0].id in item["line"]


def test_kinds_and_sections_narrow_it_and_the_command_prints_it(rich, capsys) -> None:  # noqa: F811
    view, _, _ = rich
    only = open_report(view, kinds=["unsupported"])
    assert list(only.sections) == ["unsupported"]
    whole = open_report(view)
    narrowed = open_report(view, sections=["1"])
    for kind in OpenKind:
        kept = [i.data for i in narrowed.sections[kind.section]]
        assert kept == [
            i.data
            for i in whole.sections[kind.section]
            if i.data["section"] is not None and i.data["section"].startswith("§1")
        ]
    directory = view.store.directory.parent / "paper"
    capsys.readouterr()
    code = main(
        [
            "open",
            str(directory),
            "--store",
            str(view.store.directory),
            "--json",
            "--limit",
            "0",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    # the command lists what the gates list that no other kind holds
    whole = open_report(view, gated=unsettled(view))
    assert json.loads(out) == json.loads(render(whole, 0, as_json=True))
    assert whole.data["counts"]["unjudged"] == 2
