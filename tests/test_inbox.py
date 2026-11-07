"""The author's inbox (`inbox.inbox`, `docs/guide.md` § 3): the decisions at
a state that only a person makes, a section each, and each unsupported
claim's cheapest route, on the synthetic papers of `test_check` and
`test_judgments`."""

from __future__ import annotations

import json

from axiomation import inbox, rulings
from axiomation.cli import main
from axiomation.inbox import Route, Section, route
from axiomation.judgments import (
    anchored,
    answer_finding,
    find,
    reaffirm_finding,
    seen,
)
from axiomation.queries import View
from axiomation.records import (
    AnchorKind,
    Aspect,
    Declared,
    FindingStatus,
    RulingKind,
    Status,
    Verdict,
)
from test_check import AGENT, OTHER, PERSON, built, judged
from test_rulings import ruled


def handles(report, section: Section) -> list[str]:
    return [i.data["handle"] for i in report.sections.get(section.value, [])]


def test_each_decision_is_in_its_section_and_unsupported_claims_by_route(
    tmp_path,
) -> None:
    """`t`'s `true` disputed by two agents: to judge. `l` read not shown:
    read negative. `c` unruled: a ruling decides it. `x` held verified and
    ruled owed by an agent: whether it is owed is the author's. `y` ruled
    owed by a person: an argument. `n`, justified with no justifies line:
    a line."""
    store, kept = built(tmp_path)
    judged(store, kept, Aspect.TRUE, "t", Verdict.VERIFIED, AGENT)
    judged(store, kept, Aspect.TRUE, "t", Verdict.NOT_SHOWN, OTHER)
    judged(store, kept, Aspect.TRUE, "l", Verdict.NOT_SHOWN)
    judged(store, kept, Aspect.TRUE, "x", Verdict.VERIFIED)
    ruled(store, kept, "x", RulingKind.OWED)
    ruled(store, kept, "y", RulingKind.OWED, PERSON)
    found = inbox.inbox(View(store, kept))
    assert handles(found, Section.TO_JUDGE) == ["t"]
    assert handles(found, Section.READ_NEGATIVE) == ["l"]
    assert handles(found, Section.BY_A_LINE) == ["n"]
    # `x`, owed, weighs more than `c`, unruled
    assert handles(found, Section.BY_A_RULING) == ["x", "c"]
    assert handles(found, Section.BY_AN_ARGUMENT) == ["y"]
    (y,) = found.sections[Section.BY_AN_ARGUMENT.value]
    assert y.line.endswith("\n  next: argue it (its ruling's fix)")
    (x,) = [
        i for i in found.sections[Section.BY_A_RULING.value] if i.data["handle"] == "x"
    ]
    assert x.data["route"] == "ruling"
    assert [r["ruling"] for r in x.data["support"]["speaks"]] == ["owed"]
    assert x.line.endswith(
        "\n  next: its `true` holds: rule whether the paper must argue it (`known` "
        "or `immediate` over the agents' `owed`, `axiomation rule`), else argue it"
    )
    assert found.head == [
        f"inbox at state {kept.id}: 6 decisions of yours: 1 to judge, 1 read "
        "negative, 1 unsupported by a line, 2 unsupported by a ruling, 1 "
        "unsupported by an argument"
    ]


def test_a_finding_is_to_answer_and_one_carrying_a_proposal_awaits_its_review(
    tmp_path,
) -> None:
    store, kept = built(tmp_path)
    at = seen(store, kept)
    on = [anchored(at, AnchorKind.BLOCK, "q")]
    plain = find(store, kept, Aspect.VALID, on, 2, "a gap", author=AGENT)
    proposed = find(store, kept, Aspect.VALID, on, 1, "a slip", author=AGENT)
    plain_id = plain.records.findings[0].id
    proposed_id = proposed.records.findings[0].id
    answer_finding(store, proposed_id, FindingStatus.FIXED, "reworded", AGENT)
    found = inbox.inbox(View(store, kept))
    ids = lambda s: [i.data["finding"] for i in found.sections.get(s.value, [])]  # noqa: E731
    assert ids(Section.TO_ANSWER) == [plain_id]
    assert ids(Section.AWAITING_REVIEW) == [proposed_id]


def test_a_finding_that_stood_again_names_its_first_id_and_what_was_answered(
    tmp_path,
) -> None:
    """Postponed by the author with a note, lapsed by a rewording, refiled:
    it stood again, under its first id, the author's note carried."""
    from test_judgments import _reworded_d
    from test_judgments import built as judged_paper

    store, first = judged_paper(tmp_path)
    at = seen(store, first)
    on_d = [anchored(at, AnchorKind.BLOCK, "d")]
    filed = find(store, first, Aspect.WELL_POSED, on_d, 2, "no sign").records
    original = filed.findings[0].id
    answer_finding(store, original, FindingStatus.POSTPONED, "after the rewrite")
    second = _reworded_d(store, first)
    (refiled,) = reaffirm_finding(store, second, original, "still").records.findings
    found = inbox.inbox(View(store, second))
    (item,) = found.sections[Section.STOOD_AGAIN.value]
    assert item.data["finding"] == refiled.id and item.data["first"] == original
    assert item.line.endswith(
        f"\n  first filed as {original}\n  on {original}: postponed: after the rewrite"
    )
    assert Section.TO_ANSWER.value not in found.sections


def test_the_routes_by_what_the_records_allow() -> None:
    S, G = rulings.Support, rulings.Guard
    unruled = rulings.ClaimSupport(S.UNSUPPORTED)
    assert route(Status.UNSUPPORTED, unruled)[0] is Route.RULING
    assert route(Status.RESTATED, unruled)[0] is Route.LINE
    # no ruling clears what the paper promises to argue, filed or not
    promised = rulings.ClaimSupport(S.UNSUPPORTED, declared=Declared.PROMISED)
    assert route(Status.UNSUPPORTED, promised)[0] is Route.ARGUMENT
    not_held = rulings.ClaimSupport(
        S.UNSUPPORTED, guard=G.TRUE_NOT_HELD, ruling=RulingKind.IMMEDIATE
    )
    way, step = route(Status.UNSUPPORTED, not_held)
    assert way is Route.RULING and step.startswith("rule it `immediate` yourself")


def test_the_command_prints_and_answers_json(tmp_path, capsys) -> None:
    store, kept = built(tmp_path)
    paper = tmp_path / "paper"
    paper.mkdir()
    base = ["inbox", str(paper), "--store", str(store.directory)]
    assert main(base) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"inbox at state {kept.id}: ")
    assert "unsupported by a ruling (" in out and "  next: " in out
    assert main([*base, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert set(data["counts"]) == {s.value for s in Section}
    assert data["unsupported_by_a_ruling"]["items"][0]["route"] == "ruling"


def test_a_person_s_negative_an_agent_disputes_is_read_negative_only(tmp_path) -> None:
    """The person's reading stands over the agent's: nothing to judge, the
    reading negative; listed once."""
    store, kept = built(tmp_path)
    judged(store, kept, Aspect.TRUE, "l", Verdict.CONTRADICTED, PERSON)
    judged(store, kept, Aspect.TRUE, "l", Verdict.VERIFIED, AGENT)
    found = inbox.inbox(View(store, kept))
    assert handles(found, Section.READ_NEGATIVE) == ["l"]
    assert handles(found, Section.TO_JUDGE) == []


def test_a_proposed_ruling_awaits_its_review_where_one_can_ask_it(tmp_path) -> None:
    """A person's proposal over the agents' owed on `x` awaits a review,
    saying it waits for the support search, which has not read `x`; on a
    claim argued since, it is listed nowhere."""
    store, kept = built(tmp_path)
    ruled(store, kept, "x", RulingKind.OWED)
    on = rulings.subject(seen(store, kept), "x")
    rulings.rule(store, kept, on, RulingKind.KNOWN, "a textbook fact", propose=True)
    found = inbox.inbox(View(store, kept))
    (item,) = found.sections[Section.AWAITING_REVIEW.value]
    assert "proposed ruling known by person: a textbook fact" in item.line
    assert "\n  waits: the support search reads it first" in item.line
    assert item.data["proposed"] == "ruling"
    assert item.data["proposal"]["ruling"] == "known"
    # the claim is the proposal's alone, no decision of the person's: the
    # head counts it apart
    assert "x" not in handles(found, Section.BY_A_RULING)
    assert "x" not in handles(found, Section.BY_AN_ARGUMENT)
    assert found.head[0].endswith("; 1 awaiting a review")
    assert "awaiting" not in found.head[0].split(";")[0]
