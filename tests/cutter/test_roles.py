"""A block's roles and segments (`docs/model.md` § 2):
the set in canonical order, and the invariants a block is built under."""

from __future__ import annotations

import pytest

from axiomation.cutter import Role, Segment
from axiomation.cutter.model import role_set, roles_problem, segments_of
from axiomation.questions.cut_review import role_key

D, A, C, M = Role.DEFINITION, Role.ASSUMPTION, Role.CLAIM, Role.MOTIVATION


def test_a_role_set_is_each_role_once_in_the_enum_s_order() -> None:
    assert role_set([M, C, D, C]) == (D, C, M)
    assert role_set([]) == ()


@pytest.mark.parametrize(
    ("roles", "segments"),
    [
        ((), ()),
        ((C,), segments_of((C,))),
        ((A, M), ()),  # mixed, unsegmented
        ((A, M), (Segment("", A), Segment("since the studies", M))),
        # a span that plays none, inside a block or at its start
        ((D,), (Segment("", D), Segment("To make it", None))),
        ((C,), (Segment("", None), Segment("Then", C))),
    ],
)
def test_well_formed_roles_pass(roles, segments) -> None:
    assert roles_problem(roles, segments) is None


@pytest.mark.parametrize(
    ("roles", "segments", "problem"),
    [
        ((C, D), (), "canonical order"),
        ((C, C), (), "canonical order"),
        ((C,), (), "one segment"),
        ((), (Segment("", C),), "no role"),
        ((A, M), (Segment("", A),), "segments' roles"),
        ((A, M), (Segment("x", A), Segment("y", M)), "first segment"),
        ((A, M), (Segment("", A), Segment("y", M), Segment("y", A)), "same excerpt"),
        ((), (Segment("", None),), "no role"),
        ((C,), (Segment("", None), Segment("y", D)), "segments' roles"),
    ],
)
def test_ill_formed_roles_are_named(roles, segments, problem) -> None:
    found = roles_problem(roles, segments)
    assert found is not None and problem in found


def test_the_question_keys_bind_a_block_s_roles_as_a_list() -> None:
    assert role_key(()) == []
    assert role_key((C,)) == ["claim"]
    assert role_key((D, C)) == ["definition", "claim"]
