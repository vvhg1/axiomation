"""A kept state as its lines (`docs/architecture.md` § 4): an export split
into sections by node, rendered back byte for byte, and a state's lines
kept as their change against its parent's. Synthetic exports."""

from __future__ import annotations

import json
from typing import Any

import pytest

from axiomation import state_lines
from axiomation.exchange import dumps
from axiomation.state_lines import MARK, LinesError


def _at(line: int, start: int, end: int, file: str = "p.md") -> dict[str, Any]:
    return {"file": file, "start": [line, start], "end": [line, end]}


def _export(first_line: int, evidence_file: str = "p.md") -> dict[str, Any]:
    """Two blocks on lines `first_line` and the next, an edge from the
    first quoting it, a part of the second and a document list."""
    a, b = first_line, first_line + 1
    return {
        "format": "axiomation-exchange",
        "document": {"files": [{"path": "p.md"}], "labels": [{"at": _at(a, 1, 4)}]},
        "blocks": [
            {"id": f"p.md:{a}:1", "extent": _at(a, 1, 20), "owned": [_at(a, 1, 20)]},
            {"id": f"p.md:{b}:1", "extent": _at(b, 1, 20), "owned": [_at(b, 1, 20)]},
        ],
        "parts": [{"block": f"p.md:{b}:1", "label": "i", "at": _at(b, 5, 9)}],
        "groups": [{"id": f"g@p.md:{a}:1", "blocks": [f"p.md:{a}:1", f"p.md:{b}:1"]}],
        "edges": [
            {
                "id": "abcdef-2-7",
                "source": f"p.md:{a}:1",
                "evidence": [{"text": "words", "at": _at(a, 3, 8, evidence_file)}],
                "target": {"block": f"p.md:{b}:1"},
                "kind": "uses",
            },
            {
                "id": "abcdef-2-8",
                "source": f"g@p.md:{a}:1",
                "evidence": [{"text": "more", "at": _at(b, 2, 6)}],
                "target": {"block": f"p.md:{a}:1"},
                "kind": "uses",
            },
        ],
        "handles": {f"p.md:{a}:1": "first", f"p.md:{b}:1": "second"},
        "review": {"update": {"old": "v0", "excerpts": []}},
    }


def _lines(payload: dict[str, Any]) -> state_lines.Lines:
    a, b = payload["blocks"][0]["id"], payload["blocks"][1]["id"]
    return state_lines.extract(
        [[a, "n1", "t1"], [b, "n2", "t2"]],
        {payload["groups"][0]["id"]: "g1"},
        dumps(payload).encode(),
    )


def test_an_export_renders_back_byte_for_byte_by_node() -> None:
    payload = _export(3)
    lines = _lines(payload)

    assert state_lines.render(lines) == dumps(payload).encode()
    edges = lines.sections["edges"]
    edge = edges.items[edges.order[0]]
    # by node, placed from its owner's start, its id rendered from its place
    assert edge["source"] == MARK + "n1"
    assert edge["evidence"][0]["at"] == {MARK: [0, 2, 0, 7]}
    assert edge["id"] is None
    assert lines.edge_ids == ("abcdef", 2, 7)
    assert lines.node_rows() == [["p.md:3:1", "n1", "t1"], ["p.md:4:1", "n2", "t2"]]
    assert lines.group_nodes() == {"g@p.md:3:1": "g1"}


def test_a_line_whose_owner_moved_is_the_same_line() -> None:
    """The text before both blocks grew by two lines: every block's
    extent, every block's and group's id moved (the node table changed),
    but no line placed from its owner, and no mapping by node."""
    before, after = _lines(_export(3)), _lines(_export(5))
    change = state_lines.change(before, after)

    assert set(change) == {"nodes", "groups", "sections"}
    assert set(change["sections"]) == {"blocks", "document/labels"}
    moved = state_lines.applied(before, json.loads(json.dumps(change)))
    assert state_lines.render(moved) == dumps(_export(5)).encode()


def test_a_span_outside_its_owner_is_kept_as_written() -> None:
    """Evidence in another file than its owner's stays absolute."""
    payload = _export(3, evidence_file="q.md")
    lines = _lines(payload)
    edges = lines.sections["edges"]

    assert edges.items[edges.order[0]]["evidence"][0]["at"] == _at(3, 3, 8, "q.md")
    assert state_lines.render(lines) == dumps(payload).encode()


def test_edge_ids_off_the_rule_are_kept_as_written() -> None:
    payload = _export(3)
    payload["edges"][1]["id"] = "other-1-1"
    lines = _lines(payload)

    assert lines.edge_ids is None
    assert state_lines.render(lines) == dumps(payload).encode()


def test_an_item_written_twice_keeps_both_places() -> None:
    payload = _export(3)
    excerpts = [{"cls": "found"}, {"cls": "lost"}, {"cls": "found"}]
    payload["review"]["update"]["excerpts"] = excerpts
    lines = _lines(payload)
    section = lines.sections["review/update/excerpts"]

    assert len(section.items) == 2 and len(section.order) == 3
    assert state_lines.render(lines) == dumps(payload).encode()


def test_a_change_naming_an_item_neither_holds_is_refused() -> None:
    before, after = _lines(_export(3)), _lines(_export(5))
    change = state_lines.change(before, after)
    change["sections"]["blocks"]["put"] = {}

    with pytest.raises(LinesError, match="no item"):
        state_lines.applied(before, change)
    with pytest.raises(LinesError, match="no skeleton"):
        state_lines.applied(None, {"sections": {}})
