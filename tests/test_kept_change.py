"""A value kept as its change of an earlier one (`docs/architecture.md`
§ 4): each change gives the value back exactly."""

from __future__ import annotations

import pytest

from axiomation.kept_change import (
    ChangeError,
    counted,
    counted_ids,
    text_applied,
    text_change,
    value_applied,
    value_change,
    written,
)


def test_a_text_is_its_lines_runs() -> None:
    before = "one\ntwo\nthree\n"
    after = "one\n2\nthree\nfour"

    changed = text_change(before, after)

    assert changed == [[1, 2, ["2\n"]], [3, 3, ["four"]]]
    assert text_applied(before, changed) == after


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ({"a": 1, "b": [1, 2, 3]}, {"a": 1, "b": [1, 3, 4]}),
        ({"a": 1, "b": 2}, {"b": 2, "a": 1}),  # the keys' order is the value's
        ({"a": 1, "b": 2}, {"a": 1}),
        ({"n": 1}, {"n": True}),
        ({"n": 1}, {"n": 1.0}),
        ({"sheet": "a\nb\nc\n"}, {"sheet": "a\nB\nc\n"}),
        ([1, 2], "whole"),
    ],
)
def test_a_value_s_change_gives_it_back_exactly(before, after) -> None:
    changed = value_change(before, after)

    assert changed is not None
    assert written(value_applied(before, changed)) == written(after)


def test_the_same_value_is_no_change() -> None:
    assert value_change({"a": [1, {"b": None}]}, {"a": [1, {"b": None}]}) is None


def test_a_change_applied_to_another_value_is_refused() -> None:
    with pytest.raises(ChangeError):
        value_applied("text", value_change([1], [2]))
    with pytest.raises(ChangeError, match="out of place"):
        text_applied("one\n", [[3, 4, ["x\n"]]])


def test_ids_that_count_up_are_kept_as_their_rule() -> None:
    ids = ["ab12-2-7", "ab12-2-8", "ab12-2-9"]

    assert counted(ids) == ("ab12", 2, 7)
    assert counted_ids(("ab12", 2, 7), 3) == ids
    assert counted(["ab12-2-7", "ab12-2-9"]) is None
    assert counted(["c1", "c2"]) is None
