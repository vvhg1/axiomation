"""`check`: eight gates over a kept state, each listing what a
person acts on, once; the paper passes when no gate fails.

A synthetic one-line paper: an assumption `a` qualifying a definition `d`;
a lemma `l` justified by `q`; a theorem `t` justified by `p`, which uses
`l`; an unsupported remark `c` and a claim `r` justified by `s`, which uses
it; a conjecture `k`; a justified claim `n` nothing justifies; unsupported
claims `x` and `y`; a `self` claim `w` citing `w1998`, `v` citing
`v2000`; a block `z` whose citation stands twice. Its version is not its
text's, so its document layer is never read: the label gates say so. The
soundness gates on a paper whose document reads back are `test_queries`'.
No model is called.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from hashlib import sha256
from typing import Any

import pytest

from axiomation import check, queries, versions
from axiomation.check import Cause, Gate, Mode, Outcome, gates, modes
from axiomation.cli import main
from axiomation.evidence import Sources
from axiomation.judgments import (
    Author,
    JudgmentError,
    anchored,
    answer_finding,
    find,
    judge,
    reaffirm,
    reaffirm_finding,
    see_work,
    seen,
    subject,
)
from axiomation.queries import Item, View
from axiomation.records import (
    REVIEWER,
    AnchorKind,
    Aspect,
    Depth,
    FindingStatus,
    Publication,
    RecordStore,
    Verdict,
)
from axiomation.settings import SettingsError
from axiomation.versions import Command
from test_evidence import PAGE, transient
from test_judgments import edge, graph
from test_versions import V1, _span, keep

#: Code's author for a work's publication, as `sources` files it.
SOURCES = Author("axiomation", "sources")
W_CITE = "\\cite{w1998}"
V_CITE = "\\cite{v2000}"
Z_CITE = "See \\cite{z2001}."
BLOCKS = [
    ("a", "Assume f is positive."),
    ("d", "Let f be a gain."),
    ("l", "Then f is small."),
    ("q", "Since f is a gain, f is small."),
    ("t", "Then f is bounded."),
    ("p", "By the lemma, f is bounded."),
    ("c", "Surely f is finite."),
    ("r", "Hence f is near."),
    ("s", "As f is finite, f is near."),
    ("k", "Perhaps f is tiny."),
    ("n", "Then f is null."),
    ("x", "Clearly f is extra."),
    ("y", "Plainly f is young."),
    ("w", f"The work shows f is wide {W_CITE}."),
    ("v", f"The other work shows f is vast {V_CITE}."),
    ("z", f"{Z_CITE} {Z_CITE}"),
]
TEXT = " ".join(w for _, w in BLOCKS)
EDGES = [
    edge("a", "qualifies", "d"),
    edge("q", "uses", "d"),
    edge("q", "justifies", "l"),
    edge("p", "uses", "l"),
    edge("p", "justifies", "t"),
    edge("s", "uses", "c"),
    edge("s", "justifies", "r"),
]
STANDINGS = {
    "l": "justified",
    "t": "justified",
    "c": "unsupported",
    "r": "justified",
    "k": "conjectured",
    "n": "justified",
    "x": "unsupported",
    "y": "unsupported",
    "w": "self",
    "v": "self",
}
AGENT = Author("agent", "model-a", "s1")
OTHER = Author("agent", "model-b", "s2")
PERSON = Author()
FLAG = {
    "signal": "edge-unsure",
    "blocks": [{"id": "t", "handle": "t"}],
    "evidence": [{"text": "f is bounded"}],
    "note": "maybe it uses d",
    "by": "edges",
    "stage": "edges",
}


def payload(flags: Sequence[dict[str, Any]] = ()) -> dict[str, Any]:
    words = dict(BLOCKS)
    found = graph(TEXT, BLOCKS, EDGES, [(b, words[b]) for b in STANDINGS])
    for s in found["standings"]:
        s["status"] = STANDINGS[s["block"]]
    for b in found["blocks"]:
        if b["id"] == "a":
            b["roles"] = ["assumption"]
    found["document"]["citations"] = [
        {"command": "cite", "keys": [key], "locator": None, "at": at}
        for key, at in (
            ("w1998", _span(TEXT, "p.md", W_CITE)),
            ("v2000", _span(TEXT, "p.md", V_CITE)),
        )
    ]
    # the citation `z` holds twice, each its own site
    first = TEXT.index(Z_CITE) + len("See ")
    second = TEXT.index(Z_CITE, first) + len("See ")
    for at in (first, second):
        span = _span(TEXT[:at] + "\0" * (len(TEXT) - at), "p.md", "\0")
        span["start"] = [1, at + 1]
        span["end"] = [1, at + 1 + len("\\cite{z2001}")]
        found["document"]["citations"].append(
            {"command": "cite", "keys": ["z2001"], "locator": None, "at": span}
        )
    found["review"]["flags"] = list(flags)
    return found


def built(tmp_path, flags=()):
    store = RecordStore(tmp_path / "store")
    return store, keep(store, V1, TEXT, payload(list(flags)))


def judged(store, kept, aspect, on, verdict, author=AGENT, **chosen):
    at = seen(store, kept)
    pages = chosen.pop("pages", ())
    sources = chosen.pop("sources", None)
    return judge(
        store,
        kept,
        aspect,
        subject(at, aspect, on, **chosen),
        verdict,
        Depth.INFORMAL,
        "read",
        author,
        pages=pages,
        sources=sources,
    ).records.judgments[0]


def results(store, kept, chosen=check.DEFAULTS) -> dict[Gate, check.GateResult]:
    return {r.gate: r for r in gates(View(store, kept), chosen).results}


def listed(result: check.GateResult, cause: Cause | None = None) -> list[str]:
    """Each item's handle (`-` at the paper), of one cause where named."""
    return [
        i.data["handle"] or "-"
        for i in result.items
        if cause is None or i.data["kind"] == cause.value
    ]


def subjects(result: check.GateResult, cause: Cause) -> list[str]:
    """Each item's subject, between its handle and its status, of one
    cause."""
    return [
        i.line.split("  ")[1] for i in result.items if i.data["kind"] == cause.value
    ]


# --- the partition -----------------------------------------------------------------


def test_a_fresh_build_lists_each_gap_once_and_each_route_unread(tmp_path) -> None:
    """The unsupported claims and `n`, justified with nothing justifying
    it, are gaps; the conjecture is never one. Every other subject a
    session judges is unread, the gaps' and the conjecture's `true` left
    out."""
    store, kept = built(tmp_path)
    found = results(store, kept)
    gaps = found[Gate.UNSUPPORTED_CLAIMS]
    assert listed(gaps) == ["c", "n", "x", "y"]
    assert [i.data["kind"] for i in gaps.items] == ["unsupported"] * 4
    assert gaps.outcome is Outcome.FAIL
    assert "no justifies edge comes in" in gaps.items[1].line
    unread = subjects(found[Gate.UNREVIEWED], Cause.UNJUDGED)
    assert unread == [
        'true · "Then f is small."',
        'valid q → l · "Then f is small."',
        'true · "Then f is bounded."',
        'valid p → t · "Then f is bounded."',
        'true · "Hence f is near."',
        'valid s → r · "Hence f is near."',
        'true · "The work shows f is wide \\cite{w1998}."',
        'cited w1998 · "\\cite{w1998}"',
        'true · "The other work shows f is vast \\cite{v2000}."',
        'cited v2000 · "\\cite{v2000}"',
    ]
    assert found[Gate.UNREVIEWED].outcome is Outcome.WARN
    assert found[Gate.DEFECTS].outcome is Outcome.PASS


def test_a_claim_resting_on_a_gap_is_listed_upstream_only(tmp_path) -> None:
    """`r`'s J holds, but `r` rests on `c`, which stands unsupported: `c`
    is listed, `r` is not, and `r`'s `valid` is read."""
    store, kept = built(tmp_path)
    judged(store, kept, Aspect.VALID, "s", Verdict.VERIFIED)
    found = results(store, kept)
    assert "r" not in listed(found[Gate.UNSUPPORTED_CLAIMS])
    assert "c" in listed(found[Gate.UNSUPPORTED_CLAIMS])
    assert not any(
        line.startswith("valid s → r")
        for line in subjects(found[Gate.UNREVIEWED], Cause.UNJUDGED)
    )


def test_an_unsupported_claim_s_true_never_takes_it_out_of_its_gate(
    tmp_path,
) -> None:
    """Held not-shown, `x` is a defect alone; held inconclusive, `y` stays a
    gap and is never unread; held verified, by a person even, `c` stays a
    gap: it holds, and the paper still argues nothing for it."""
    store, kept = built(tmp_path)
    judged(store, kept, Aspect.TRUE, "x", Verdict.NOT_SHOWN)
    judged(store, kept, Aspect.TRUE, "y", Verdict.INCONCLUSIVE)
    judged(store, kept, Aspect.TRUE, "c", Verdict.VERIFIED, PERSON)
    found = results(store, kept)
    gaps = found[Gate.UNSUPPORTED_CLAIMS]
    assert listed(gaps) == ["c", "n", "y"]
    assert gaps.items[0].line == (
        'c  ⊢ unsupported · "Surely f is finite."  unruled  · `true` held verified'
    )
    assert gaps.items[0].data["cause"] == {"standing": "unsupported", "why": None}
    assert listed(found[Gate.DEFECTS]) == ["x"]
    assert found[Gate.DEFECTS].items[0].data["kind"] == "not-shown"
    assert listed(found[Gate.UNREVIEWED], Cause.INCONCLUSIVE) == []


def test_a_circle_of_unsupported_claims_held_true_is_listed(tmp_path) -> None:
    """`x` and `y` stand unsupported, each held true by a person, and each
    uses the other: neither is established, and their circle is listed
    once, `circular`, never left out because their `true` holds."""
    store = RecordStore(tmp_path / "store")
    data = payload()
    data["edges"] += [edge("x", "uses", "y"), edge("y", "uses", "x")]
    kept = keep(store, V1, TEXT, data)
    judged(store, kept, Aspect.TRUE, "x", Verdict.VERIFIED, PERSON)
    judged(store, kept, Aspect.TRUE, "y", Verdict.VERIFIED, PERSON)
    gaps = results(store, kept)[Gate.UNSUPPORTED_CLAIMS]
    (circle,) = [i for i in gaps.items if i.data["kind"] == "circular"]
    assert circle.data["handle"] == "x"
    assert [c["handle"] for c in circle.data["cause"]["claims"]] == ["x", "y"]
    assert [a["node"] for a in circle.data["anchors"]] == [
        n.node for n in kept.nodes if n.block in ("x", "y")
    ]
    # the gate's line counts the circle as one item, so its standings, its
    # circles, the ruled and the negative ones still add up to `open`'s
    # count; the head counts the claims in it
    report = check.report(gates(View(store, kept)))
    (line,) = [h for h in report.head if h.split()[0] == "unsupported-claims"]
    assert "1 circle (2 claims in them)" in line
    assert (
        "circles: 1, 2 claims in them, 0 resting on them (`open --kind circle` "
        "lists them)"
    ) in report.head


def test_a_person_s_negative_stands_though_a_later_agent_disputes_it(
    tmp_path,
) -> None:
    """A person's `contradicted` on `l` disputed by a later agent is a
    defect; two agents differing on `t` are review outstanding."""
    store, kept = built(tmp_path)
    judged(store, kept, Aspect.TRUE, "l", Verdict.CONTRADICTED, PERSON)
    judged(store, kept, Aspect.TRUE, "l", Verdict.VERIFIED, AGENT)
    judged(store, kept, Aspect.TRUE, "t", Verdict.VERIFIED, AGENT)
    judged(store, kept, Aspect.TRUE, "t", Verdict.NOT_SHOWN, OTHER)
    found = results(store, kept)
    defects = found[Gate.DEFECTS]
    assert [(i.data["handle"], i.data["kind"]) for i in defects.items] == [
        ("l", "contradicted")
    ]
    assert listed(found[Gate.UNREVIEWED], Cause.DISPUTED) == ["t"]


def test_unsupported_claims_and_open_count_the_same_claims(tmp_path) -> None:
    """The gate's line splits its count by why each claim stands there, and
    names what it leaves to other gates: a claim read negative is a defect
    (`docs/model.md` § 11), so `open` lists it under `negative`, not
    `unsupported`, and the two list the same unsupported claims."""
    store, kept = built(tmp_path)
    before = queries.open_report(View(store, kept)).sections["unsupported"]
    read = before[0].data["handle"]
    judged(store, kept, Aspect.TRUE, read, Verdict.CONTRADICTED)
    view = View(store, kept)
    verdict = gates(view)
    (line,) = [
        h for h in check.report(verdict).head if h.split()[0] == "unsupported-claims"
    ]
    assert line.endswith(
        "fail  3: 2 standing unsupported, 1 justified with no justifies edge onto "
        "it; not counted: 0 no argument owed (rulings), 1 defect (in defects)"
    )
    gate = verdict.results[0]
    opened = queries.open_report(view).sections
    assert [i.data["handle"] for i in opened["unsupported"]] == [
        i.data["handle"] for i in gate.items
    ]
    assert len(before) == len(gate.items) + gate.to_defects
    assert [i.data["handle"] for i in opened["negative"]] == [read]
    assert opened["negative"][0].data["status"] == "contradicted"


def test_an_unsupported_claim_s_line_ends_with_its_reading_and_groups_by_it(
    tmp_path,
) -> None:
    """`open` and the gate print one line per unsupported claim, its `true`'s
    reading last; `--by reading` groups each section's items by it, and
    every item carries where it stands."""
    store, kept = built(tmp_path)
    judged(store, kept, Aspect.TRUE, "c", Verdict.VERIFIED, PERSON)
    view = View(store, kept)
    opened = queries.open_report(view).sections["unsupported"]
    gate = gates(view).results[0]
    assert [i.line for i in opened] == [i.line for i in gate.items]
    (c,) = [i for i in opened if i.data["handle"] == "c"]
    assert c.line.endswith("unruled  · `true` held verified")
    assert c.data["reading"] == "held verified"
    assert {"section", "top_section", "roles"} <= set(c.data)
    grouped = queries.grouped(
        queries.open_report(view, ["unsupported"]), queries.GroupBy.READING
    )
    assert {k: [i.data["handle"] for i in v] for k, v in grouped.sections.items()} == {
        "unsupported · held verified": ["c"],
        "unsupported · unjudged": ["n", "x", "y"],
    }


def test_a_severity_3_finding_open_or_postponed_is_a_defect(tmp_path) -> None:
    store, kept = built(tmp_path)
    at = seen(store, kept)
    on = [anchored(at, AnchorKind.BLOCK, "q")]
    kept_open = find(store, kept, Aspect.VALID, on, 3, "a gap").records.findings[0]
    dismissed = find(store, kept, Aspect.VALID, on, 3, "a slip").records.findings[0]
    minor = find(store, kept, Aspect.VALID, on, 2, "a typo").records.findings[0]
    answer_finding(store, kept_open.id, FindingStatus.POSTPONED)
    answer_finding(store, dismissed.id, FindingStatus.DISMISSED)
    found = results(store, kept)[Gate.DEFECTS]
    assert [i.data["finding"] for i in found.items] == [kept_open.id]
    assert minor.id not in json.dumps([i.data for i in found.items])


def test_an_agent_proposes_and_a_review_or_a_person_closes_a_finding(
    tmp_path,
) -> None:
    """An agent's answer by hand is a proposal: the finding stays a
    defect, the proposal beside it. A review's answer closes it; a
    person's closes it too, and stands over an agent's or a review's, before
    or after."""
    store, kept = built(tmp_path)
    on = [anchored(seen(store, kept), AnchorKind.BLOCK, "q")]
    proposed, by_review, by_person, kept_by_person, postponed = (
        find(store, kept, Aspect.VALID, on, 3, f"gap {n}").records.findings[0].id
        for n in range(5)
    )
    review = Author("claude-code", "model-r")
    for f in (proposed, by_review, by_person, kept_by_person):
        answer_finding(
            store, f, FindingStatus.FIXED, "the step now cites a lemma", AGENT
        )
    answer_finding(store, by_review, FindingStatus.FIXED, "the lemma covers it", review)
    answer_finding(store, by_person, FindingStatus.FIXED)
    answer_finding(store, kept_by_person, FindingStatus.POSTPONED)
    answer_finding(store, kept_by_person, FindingStatus.DISMISSED, "not a gap", OTHER)
    answer_finding(store, postponed, FindingStatus.POSTPONED)
    answer_finding(store, postponed, FindingStatus.FIXED, "the lemma covers it", review)
    defects = results(store, kept)[Gate.DEFECTS]
    found = {i.data["finding"]: i.data for i in defects.items}
    assert set(found) == {proposed, kept_by_person, postponed}
    assert found[proposed]["proposal"] == {
        "status": "fixed",
        "of": None,
        "note": "the step now cites a lemma",
        "author": "agent model-a",
    }
    assert "a person's `axiomation answer-finding`" in found[proposed]["clears"]
    assert found[kept_by_person]["proposal"]["status"] == "dismissed"
    (line,) = [i.line for i in defects.items if i.data["finding"] == kept_by_person]
    assert "  postponed  " in line
    assert line.endswith("proposed dismissed by agent model-b: not a gap")
    with pytest.raises(JudgmentError, match="say why with --note"):
        answer_finding(store, proposed, FindingStatus.FIXED, None, AGENT)


def test_a_review_s_finding_with_a_proposal_names_the_session_that_settles_it(
    tmp_path,
) -> None:
    """The review of its kind is named by its session, since a review with
    no `--session` may not ask it (the field's sessions are asked on
    request)."""
    from dataclasses import replace

    from axiomation.claim_status import finding_clears
    from axiomation.questions.review_file import Session

    store, kept = built(tmp_path)
    on = [anchored(seen(store, kept), AnchorKind.BLOCK, "q")]
    f = find(store, kept, Aspect.VALID, on, 3, "a gap").records.findings[0].id
    answer_finding(store, f, FindingStatus.FIXED, "the step now cites a lemma", AGENT)
    (read,) = [r for r in View(store, kept).readings.findings if r.finding.id == f]
    by_review = replace(
        read, author=Author("claude-code", "model-r"), filed_by=Session.CONTRIBUTION
    )
    assert finding_clears(by_review).startswith(
        "a review of its kind settling the proposal "
        "(`axiomation review --session contribution`)"
    )


def test_a_withdrawn_answer_leaves_the_finding_as_the_answers_before_it(
    tmp_path,
) -> None:
    """A person's `fixed` withdrawn: their `postponed` before it speaks
    again, and the finding is a defect again."""
    from axiomation.judgments import withdraw_answer

    store, kept = built(tmp_path)
    on = [anchored(seen(store, kept), AnchorKind.BLOCK, "q")]
    (gap,) = find(store, kept, Aspect.VALID, on, 3, "a gap").records.findings
    answer_finding(store, gap.id, FindingStatus.POSTPONED)
    (fixed,) = answer_finding(store, gap.id, FindingStatus.FIXED).records.answers
    assert not results(store, kept)[Gate.DEFECTS].items
    withdraw_answer(store, fixed.id[:14], "the step still skips the bound")
    defects = results(store, kept)[Gate.DEFECTS]
    assert [i.data["finding"] for i in defects.items] == [gap.id]
    assert "  postponed  " in defects.items[0].line


def test_a_review_s_answer_withdrawn_reopens_the_finding_and_its_proposal(
    tmp_path,
) -> None:
    """A review's `fixed` that settled an agent's proposal, withdrawn by a
    person by the finding's id: the finding is a defect again, the proposal
    beside it waiting for the next review."""
    from axiomation.judgments import withdraw_answer

    store, kept = built(tmp_path)
    on = [anchored(seen(store, kept), AnchorKind.BLOCK, "q")]
    review = Author("claude-code", "model-r")
    (gap,) = find(
        store, kept, Aspect.VALID, on, 3, "a gap", "", review
    ).records.findings
    answer_finding(store, gap.id, FindingStatus.FIXED, "cites a lemma", AGENT)
    answer_finding(store, gap.id, FindingStatus.FIXED, "the lemma covers it", review)
    assert not results(store, kept)[Gate.DEFECTS].items
    with pytest.raises(JudgmentError, match="a withdrawal is a person's"):
        withdraw_answer(store, gap.id, "not fixed", review)
    withdraw_answer(store, gap.id, "the lemma is for another bound")
    (item,) = results(store, kept)[Gate.DEFECTS].items
    assert item.data["finding"] == gap.id
    assert item.data["proposal"]["note"] == "cites a lemma"


def test_an_agent_s_answer_neither_closes_nor_counts_for_a_duplicate_cycle(
    tmp_path,
) -> None:
    """A proposal on a closed finding is refused; a proposed `duplicate`
    merges nothing, so a person may answer the other finding a duplicate
    of the proposing one, and only the answers that speak form a cycle."""
    store, kept = built(tmp_path)
    on = [anchored(seen(store, kept), AnchorKind.BLOCK, "q")]
    a, b, c = (
        find(store, kept, Aspect.VALID, on, 3, f"gap {n}").records.findings[0].id
        for n in range(3)
    )
    answer_finding(store, c, FindingStatus.DISMISSED)
    with pytest.raises(
        JudgmentError, match="is answered dismissed: nothing to propose"
    ):
        answer_finding(store, c, FindingStatus.FIXED, "the step now holds", AGENT)
    answer_finding(store, a, FindingStatus.DUPLICATE, "the same gap", AGENT, of=b)
    answer_finding(store, b, FindingStatus.DUPLICATE, of=a)
    with pytest.raises(JudgmentError, match=f"{b} is answered a duplicate of it"):
        answer_finding(store, a, FindingStatus.DUPLICATE, of=b)
    defects = results(store, kept)[Gate.DEFECTS]
    assert [i.data["finding"] for i in defects.items] == [a]


def test_a_severity_3_finding_on_how_the_paper_reads_is_a_defect(tmp_path) -> None:
    """Never status, still a defect: its severity says a main result is
    unreadable, vacuous or known."""
    store, kept = built(tmp_path)
    at = seen(store, kept)
    on = [anchored(at, AnchorKind.BLOCK, "t")]
    filed = [
        find(store, kept, aspect, on, 3, f"{aspect.value} problem").records.findings[0]
        for aspect in (Aspect.PRESENTATION, Aspect.SIGNIFICANCE, Aspect.NOVELTY)
    ]
    verdict = results(store, kept)
    assert {i.data["finding"] for i in verdict[Gate.DEFECTS].items} == {
        f.id for f in filed
    }
    assert verdict[Gate.DEFECTS].outcome is Outcome.FAIL


def test_a_severity_3_finding_that_lapsed_stays_a_defect_until_settled(
    tmp_path,
) -> None:
    """The text changed under it, and nobody settled it: it stays a
    defect, marked lapsed, until it is answered, or reaffirmed and live
    again. A minor one that lapsed is in no gate."""
    from test_judgments import _reworded_d
    from test_judgments import built as judged_paper

    store, first = judged_paper(tmp_path)
    at = seen(store, first)
    on_d = [anchored(at, AnchorKind.BLOCK, "d")]
    severe, minor, answered = (
        find(store, first, Aspect.WELL_POSED, on_d, n, f"problem {n}")
        .records.findings[0]
        .id
        for n in (3, 2, 3)
    )
    second = _reworded_d(store, first)
    answer_finding(store, answered, FindingStatus.FIXED, "reworded")

    def found() -> dict[Gate, check.GateResult]:
        return {r.gate: r for r in gates(View(store, second)).results}

    verdict = found()
    defects = [i for i in verdict[Gate.DEFECTS].items if "finding" in i.data]
    assert [(i.data["finding"], i.data.get("lapsed")) for i in defects] == [
        (severe, True)
    ]
    assert defects[0].data["kind"] == "finding"
    everything = json.dumps([i.data for r in verdict.values() for i in r.items])
    assert minor not in everything and answered not in everything

    again = reaffirm_finding(store, second, severe, "the gain still has no sign")
    (refiled,) = again.records.findings
    assert refiled.reaffirms == severe
    defects = [i for i in found()[Gate.DEFECTS].items if "finding" in i.data]
    assert [(i.data["finding"], i.data.get("lapsed")) for i in defects] == [
        (refiled.id, None)
    ]
    answer_finding(store, refiled.id, FindingStatus.FIXED)
    assert not [i for i in found()[Gate.DEFECTS].items if "finding" in i.data]


def test_a_reviewer_s_finding_stays_in_reviews_until_cleared_even_refiled(
    tmp_path,
) -> None:
    """An outside reviewer's finding dismissed without a note stands in
    `reviews` (listed, not a crash); one that lapsed and was refiled by
    the author stays the reviewer's, under its refiling, until cleared."""
    from test_judgments import _reworded_d
    from test_judgments import built as judged_paper

    store, first = judged_paper(tmp_path)
    at = seen(store, first)
    on_d = [anchored(at, AnchorKind.BLOCK, "d")]
    reviewer = Author(REVIEWER, "R2")
    bare, lapsing = (
        find(store, first, Aspect.WELL_POSED, on_d, 2, problem, author=reviewer)
        .records.findings[0]
        .id
        for problem in ("which gain?", "of what?")
    )
    answer_finding(store, bare, FindingStatus.DISMISSED)
    listed = [i.data["finding"] for i in results(store, first)[Gate.REVIEWS].items]
    assert listed == [bare, lapsing]
    # the author's lists agree with the gate: a dismissal without the
    # author's argument leaves a reviewer's finding open
    assert {f.finding.id: f.open for f in View(store, first).readings.findings}[bare]
    # a duplicate of a finding still open addresses nothing, until that
    # one is addressed
    own = find(store, first, Aspect.WELL_POSED, on_d, 1, "a gain?").records
    copied = find(store, first, Aspect.WELL_POSED, on_d, 1, "gain?", author=reviewer)
    (own_id,), (copy_id,) = [own.findings[0].id], [copied.records.findings[0].id]
    answer_finding(store, copy_id, FindingStatus.DUPLICATE, of=own_id)
    listed = [i.data["finding"] for i in results(store, first)[Gate.REVIEWS].items]
    assert listed == [bare, lapsing, copy_id]
    answer_finding(store, own_id, FindingStatus.FIXED)
    listed = [i.data["finding"] for i in results(store, first)[Gate.REVIEWS].items]
    assert listed == [bare, lapsing]

    second = _reworded_d(store, first)
    (refiled,) = reaffirm_finding(store, second, lapsing, "still").records.findings
    listed = [i.data["finding"] for i in results(store, second)[Gate.REVIEWS].items]
    assert listed == [bare, refiled.id]
    answer_finding(store, refiled.id, FindingStatus.FIXED)
    listed = [i.data["finding"] for i in results(store, second)[Gate.REVIEWS].items]
    assert listed == [bare]


def test_the_reviews_line_shows_once_an_outside_review_is_received(
    tmp_path,
) -> None:
    """With no outside reviewer's finding in the store, `check`'s text
    has no `reviews` line or section, and `--json` holds the gate with
    `received` 0; a reviewer's finding received shows it, failing while
    open and passing once addressed."""
    from test_judgments import built as judged_paper

    store, first = judged_paper(tmp_path)

    def read() -> tuple[dict[str, Any], list[str], list[str]]:
        verdict = gates(View(store, first))
        found = check.report(verdict)
        (gate,) = [g for g in verdict.data()["gates"] if g["gate"] == "reviews"]
        lines = [line.split()[0] for line in found.head[1:]]
        return gate, lines, [key.split()[0] for key in found.sections]

    gate, lines, sections = read()
    assert (gate["received"], gate["outcome"]) == (0, "pass")
    assert "reviews" not in lines and "reviews" not in sections
    assert "defects" in lines

    on_d = [anchored(seen(store, first), AnchorKind.BLOCK, "d")]
    reviewer = Author(REVIEWER, "R2")
    (finding,) = find(
        store, first, Aspect.WELL_POSED, on_d, 2, "which gain?", author=reviewer
    ).records.findings
    gate, lines, sections = read()
    assert (gate["received"], gate["outcome"]) == (1, "fail")
    assert "reviews" in lines and "reviews" in sections

    answer_finding(store, finding.id, FindingStatus.FIXED)
    gate, lines, _ = read()
    assert (gate["received"], gate["outcome"]) == (1, "pass")
    assert "reviews" in lines


def _lapsed_true(tmp_path, *readings):
    """The judgments paper's `t` read `true` on its first state by each
    `(author, verdict)`, then its definition reworded: the second state."""
    from test_judgments import _reworded_d, filed_true
    from test_judgments import built as judged_paper

    store, first = judged_paper(tmp_path)
    for author, verdict in readings:
        filed_true(store, first, author, verdict)
    return store, _reworded_d(store, first)


def test_a_lapsed_negative_stays_a_defect_until_a_reading_concludes(
    tmp_path,
) -> None:
    """`t`'s `contradicted` lapsed with its definition's rewording: it stays
    in `defects`, marked lapsed, and leaves `unreviewed`; a reading that
    could not tell keeps it there; a reaffirmation keeps it a defect,
    live; a reading that concludes otherwise settles it."""
    from test_judgments import filed_true

    store, second = _lapsed_true(tmp_path, (AGENT, Verdict.CONTRADICTED))

    def found() -> dict[Gate, check.GateResult]:
        return results(store, second)

    def on_t(result: check.GateResult) -> list[check.Item]:
        return [i for i in result.items if i.data["handle"] == "t"]

    verdict = found()
    (item,) = on_t(verdict[Gate.DEFECTS])
    assert item.data["kind"] == "contradicted" and item.data["lapsed"]
    assert "lapsed, unresolved: contradicted" in item.line
    assert item.data["cause"]["lapsed"]["kind"] == "text"
    assert not on_t(verdict[Gate.UNREVIEWED])
    assert verdict[Gate.DEFECTS].outcome is Outcome.FAIL

    filed_true(store, second, OTHER, Verdict.INCONCLUSIVE)
    (item,) = on_t(found()[Gate.DEFECTS])
    assert item.data["lapsed"]

    (lapsed,) = [j for j in store.judgment_records().judgments if j.state != second.id]
    reaffirm(store, second, lapsed.id, Depth.INFORMAL, "still false", AGENT)
    (item,) = on_t(found()[Gate.DEFECTS])
    assert item.data["kind"] == "contradicted" and "lapsed" not in item.data

    filed_true(store, second, AGENT, Verdict.VERIFIED)
    filed_true(store, second, OTHER, Verdict.VERIFIED)
    verdict = found()
    assert not on_t(verdict[Gate.DEFECTS]) and not on_t(verdict[Gate.UNREVIEWED])


def test_the_author_s_own_later_inconclusive_settles_nothing(tmp_path) -> None:
    """The re-ask path: the same reader, asked again, could not tell. Its
    `contradicted` still stands, lapsed and unresolved."""
    from test_judgments import filed_true

    store, second = _lapsed_true(tmp_path, (AGENT, Verdict.CONTRADICTED))
    filed_true(store, second, AGENT, Verdict.INCONCLUSIVE)
    verdict = results(store, second)
    assert [
        (i.data["handle"], i.data.get("lapsed")) for i in verdict[Gate.DEFECTS].items
    ] == [("t", True)]
    assert verdict[Gate.DEFECTS].outcome is Outcome.FAIL


def test_an_author_s_later_inconclusive_never_clears_its_live_negative(
    tmp_path,
) -> None:
    """The same reader, its host upgraded, reads `c` again and could not
    tell: its live `not-shown` still holds and `defects` lists it; the
    `inconclusive` stands beside it."""
    store, kept = built(tmp_path)
    judged(
        store,
        kept,
        Aspect.TRUE,
        "c",
        Verdict.NOT_SHOWN,
        Author("claude-code 2.1.309", "model-a", "s1"),
    )
    judged(
        store,
        kept,
        Aspect.TRUE,
        "c",
        Verdict.INCONCLUSIVE,
        Author("claude-code 2.1.310", "model-a", "s2"),
    )
    defects = results(store, kept)[Gate.DEFECTS]
    assert ("c", "not-shown") in [
        (i.data["handle"], i.data["kind"]) for i in defects.items
    ]
    read = queries.View(store, kept).readings
    (status,) = [
        s
        for s in read.statuses
        if s.aspect is Aspect.TRUE and read.at.handle(s.subject.node) == "c"
    ]
    assert [r.judgment.verdict for r in status.reads] == [
        Verdict.NOT_SHOWN,
        Verdict.INCONCLUSIVE,
    ]


def test_a_person_s_lapsed_negative_decides_over_an_earlier_live_verified(
    tmp_path,
) -> None:
    """An agent's `verified` filed before the person's `not-shown` was decided
    over: it does not settle the person's reading when that lapses; only a
    reading after it does."""
    from test_judgments import _reworded_d, filed_true
    from test_judgments import built as judged_paper

    store, first = judged_paper(tmp_path)
    second = _reworded_d(store, first)
    filed_true(store, second, AGENT, Verdict.VERIFIED)
    filed_true(store, first, PERSON, Verdict.NOT_SHOWN)
    defects = results(store, second)[Gate.DEFECTS]
    assert [(i.data["handle"], i.data["kind"]) for i in defects.items] == [
        ("t", "not-shown")
    ]
    filed_true(store, second, OTHER, Verdict.VERIFIED)
    assert not results(store, second)[Gate.DEFECTS].items


@pytest.mark.parametrize("change", ["proof deleted", "justifies edge retracted"])
def test_a_lapsed_negative_leaves_with_its_subject(tmp_path, change) -> None:
    """The proof read `contradicted`, then deleted or no longer justifying
    the theorem: nothing at the state can read that `valid` again, so it is
    no item; the theorem it argued is listed as unsupported."""
    from test_judgments import BLOCKS as J_BLOCKS
    from test_judgments import EDGES as J_EDGES
    from test_judgments import TEXT as J_TEXT
    from test_judgments import built as judged_paper
    from test_judgments import filed_valid, graph, same, updated

    store, first = judged_paper(tmp_path)
    filed_valid(store, first, AGENT, Verdict.CONTRADICTED)
    if change == "proof deleted":
        text = J_TEXT.replace(" By the gain, f is bounded.", "")
        blocks = [b for b in J_BLOCKS if b[0] != "p"]
        edges = [e for e in J_EDGES if e["source"] != "p"]
        links = [*same("d", "t", "r"), (["p"], [], "deleted", None)]
    else:
        text, blocks = J_TEXT, J_BLOCKS
        edges = [e for e in J_EDGES if e["kind"] != "justifies"]
        links = same("d", "t", "p", "r")
    second = updated(store, first, text, graph(text, blocks, edges), links)
    verdict = results(store, second)
    assert not verdict[Gate.DEFECTS].items
    assert listed(verdict[Gate.UNSUPPORTED_CLAIMS]) == ["t"]


def test_a_lapsed_reading_counts_where_it_counted_live(tmp_path) -> None:
    """A person's `not-shown` a later agent disputed was a defect, and
    stays one when it lapses; two agents differing were unread, and stay
    unread."""
    store, second = _lapsed_true(
        tmp_path / "person",
        (PERSON, Verdict.NOT_SHOWN),
        (AGENT, Verdict.VERIFIED),
    )
    defects = results(store, second)[Gate.DEFECTS]
    assert [(i.data["handle"], i.data["kind"]) for i in defects.items] == [
        ("t", "not-shown")
    ]
    store, second = _lapsed_true(
        tmp_path / "agents",
        (AGENT, Verdict.NOT_SHOWN),
        (OTHER, Verdict.VERIFIED),
    )
    verdict = results(store, second)
    assert not verdict[Gate.DEFECTS].items
    assert listed(verdict[Gate.UNREVIEWED], Cause.LAPSED) == ["t"]


def test_a_retracted_work_is_a_defect_on_each_block_citing_it(tmp_path) -> None:
    store, kept = built(tmp_path)
    see_work(store, "w1998", "10.1/w", Publication.RETRACTED, SOURCES)
    # a retracted work the state cites nowhere asks nothing of this paper
    see_work(store, "u2003", "10.1/u", Publication.RETRACTED, SOURCES)
    (item,) = results(store, kept)[Gate.DEFECTS].items
    assert item.data["kind"] == "retracted" and item.data["handle"] == "w"
    assert item.line == "w1998 is retracted; cited by w"


def test_an_inconclusive_or_provisional_reading_is_unread(tmp_path) -> None:
    store, kept = built(tmp_path)
    words = dict(BLOCKS)
    judged(
        store,
        kept,
        Aspect.CITED,
        "w",
        Verdict.VERIFIED,
        span=W_CITE,
        text=words["w"],
        work="w1998",
        pages=[("w1998", 12)],
        sources=Sources([transient(w1998={12: PAGE})]),
    )
    judged(
        store,
        kept,
        Aspect.CITED,
        "v",
        Verdict.INCONCLUSIVE,
        span=V_CITE,
        text=words["v"],
        work="v2000",
    )
    found = results(store, kept)[Gate.UNREVIEWED]
    assert listed(found, Cause.PROVISIONAL) == ["w"]
    assert listed(found, Cause.INCONCLUSIVE) == ["v"]


def test_every_item_a_gate_lists_is_open(tmp_path) -> None:
    """What a gate lists that no other open kind holds, the open view lists
    under its cause (`docs/model.md` § 10): a retracted work, a provisional
    and an inconclusive reading, the unjudged; each the gate's own item."""
    store, kept = built(tmp_path)
    see_work(store, "w1998", "10.1/w", Publication.RETRACTED, SOURCES)
    words = dict(BLOCKS)
    judged(
        store,
        kept,
        Aspect.CITED,
        "v",
        Verdict.INCONCLUSIVE,
        span=V_CITE,
        text=words["v"],
        work="v2000",
    )
    view = View(store, kept)
    verdict = gates(view)
    # a negative reading is `negative`'s, a circle `circle`'s
    elsewhere = {"not-shown", "contradicted", "circular"}
    kinds = {k.value for k in queries.OpenKind}
    listed = [i for r in verdict.results for i in r.items]
    assert {i.data["kind"] for i in listed} - kinds <= elsewhere
    opened = queries.open_report(view, gated=check.unsettled(view))
    for kind in queries.GATE_KINDS:
        lines = [i.line for i in listed if i.data["kind"] == kind.value]
        if kind is queries.OpenKind.UNREAD:
            # what each document gate says it could not read is one open item
            lines = list(dict.fromkeys(lines))
        assert [i.line for i in opened.sections[kind.section]] == lines
    assert opened.data["counts"]["retracted"] == 1
    assert opened.data["counts"]["inconclusive"] == 1
    assert opened.data["counts"]["unjudged"] > 0
    assert "gate" not in opened.sections["unjudged"][0].data


def test_a_sketch_s_unread_j_says_it_is_a_sketch(tmp_path) -> None:
    """`p` argues `t` only in a sketch: its unread `valid` says
    so, and once held verified the sketch is read through."""
    store = RecordStore(tmp_path / "store")
    data = payload()
    for e in data["edges"]:
        if e["source"] == "p" and e["kind"] == "justifies":
            e["partial"] = "sketch"
    kept = keep(store, V1, TEXT, data)
    unread = results(store, kept)[Gate.UNREVIEWED]
    (item,) = [i for i in unread.items if i.line.startswith("p  valid")]
    assert item.line.endswith("unjudged (sketch)")
    assert item.data["completeness"] == "sketch"
    judged(store, kept, Aspect.VALID, "p", Verdict.VERIFIED)
    unread = results(store, kept)[Gate.UNREVIEWED]
    assert not [i for i in unread.items if i.line.startswith("p  valid")]


def test_a_site_no_block_can_hold_and_an_undecided_flag_are_unread(
    tmp_path,
) -> None:
    store, kept = built(tmp_path, [FLAG, {**FLAG, "decided": True}])
    found = results(store, kept)[Gate.UNREVIEWED]
    unplaced = [i.line for i in found.items if i.data["kind"] == "unplaced"]
    assert (
        unplaced
        == ["citation of z2001 at p.md:1: its words stand 2 times in z, not once"] * 2
    )
    assert listed(found, Cause.FLAG) == ["t"]


def test_an_open_stale_mark_is_unread(tmp_path) -> None:
    from axiomation.marks import open_marks
    from test_marks import kept_update

    v, state = kept_update(tmp_path)
    found = {r.gate: r for r in gates(View(v.store, state)).results}
    stale = [
        i.data["node"]
        for i in found[Gate.UNREVIEWED].items
        if i.data["kind"] == "stale"
    ]
    assert stale == [m.node for m in open_marks(v.store, state)]
    assert stale


def test_a_negative_reading_carries_the_marks_on_its_own_lines(tmp_path) -> None:
    """The `contradicted` reading of the sharpness's claim holds its marked
    line: one defect, the mark under it, an item in no gate of its own;
    the theorem's mark, on a line the reading does not hold, stays
    unread."""
    from axiomation.judgments import offered
    from axiomation.marks import open_marks
    from test_marks import SHARP, THEOREM, kept_update

    v, state = kept_update(tmp_path)
    marks = {m.handle: m for m in open_marks(v.store, state)}
    sharp = marks[SHARP]
    aspect, on = next(
        (a, s)
        for a, s in offered(seen(v.store, state))
        if a is Aspect.TRUE and s.node == sharp.node
    )
    filed = judge(
        v.store, state, aspect, on, Verdict.CONTRADICTED, Depth.INFORMAL, "no"
    ).records.judgments[0]

    found = {r.gate: r for r in gates(View(v.store, state)).results}
    (defect,) = found[Gate.DEFECTS].items
    assert defect.data["cause"]["judgment"] == filed.id
    assert defect.data["marked"] == [{"edge": sharp.edge, "since": sharp.since}]
    assert not [i for i in found[Gate.DEFECTS].items if i.data["kind"] == "stale"]
    unread = [
        i.data["owner"]
        for i in found[Gate.UNREVIEWED].items
        if i.data["kind"] == "stale"
    ]
    assert unread == [marks[THEOREM].node]


def test_a_document_not_read_fails_the_label_gates(tmp_path) -> None:
    """The labels cannot be read, so they cannot pass: an item on each
    gate that reads the document, `unreviewed` for its quotations. Here
    the export names a file today's reader does not read."""
    store = RecordStore(tmp_path / "store")
    named = payload([])
    named["document"]["files"].append(
        {"path": "q.md", "sha256": sha256(b"Q.").hexdigest()}
    )
    kept, _ = versions.record(
        store,
        {"p.md": TEXT.encode(), "q.md": b"Q."},
        V1,
        "p.md",
        json.dumps(named).encode(),
        Command.BUILD,
    )
    found = results(store, kept)
    for gate in (*check.DOCUMENT_GATES, Gate.UNREVIEWED):
        assert listed(found[gate], Cause.UNREAD) == ["-"], gate
    assert found[Gate.DANGLING_REFERENCES].outcome is Outcome.FAIL
    assert found[Gate.UNUSED_PREMISES].outcome is Outcome.WARN


# --- modes, outcomes, the command --------------------------------------------------


def test_a_gate_off_lists_nothing_so_its_claims_true_is_unread(tmp_path) -> None:
    """With `unsupported-claims` off and `unreviewed` blocking, an
    unsupported claim whose `true` is unjudged still stands in the way:
    a reading the argument session owes."""
    store, kept = built(tmp_path)
    chosen = {
        **check.DEFAULTS,
        Gate.UNSUPPORTED_CLAIMS: Mode.OFF,
        Gate.UNREVIEWED: Mode.BLOCK,
    }
    found = results(store, kept, chosen)
    assert found[Gate.UNSUPPORTED_CLAIMS].outcome is Outcome.OFF
    assert found[Gate.UNSUPPORTED_CLAIMS].items == ()
    assert 'true · "Surely f is finite."' in subjects(
        found[Gate.UNREVIEWED], Cause.UNJUDGED
    )
    assert found[Gate.UNREVIEWED].outcome is Outcome.FAIL


def test_the_paper_s_gates_over_the_workspace_s_over_the_defaults(tmp_path) -> None:
    root = tmp_path / "workspace"
    paper = root / "papers" / "one"
    paper.mkdir(parents=True)
    (root / "axiomation.toml").write_text(
        '[workspace]\n\n[gates]\nunreviewed = "block"\ndefects = "warn"\n'
    )
    (paper / "axiomation.toml").write_text('[gates]\ndefects = "off"\n')
    found = modes(paper)
    assert found[Gate.UNREVIEWED] is Mode.BLOCK
    assert found[Gate.DEFECTS] is Mode.OFF
    assert found[Gate.DANGLING_REFERENCES] is Mode.BLOCK
    assert modes(tmp_path) == check.DEFAULTS


@pytest.mark.parametrize(
    ("table", "said"),
    [
        ('unsupported = "warn"', "names no gate 'unsupported'; the gates: "),
        ('defects = "loud"', "defects = 'loud' is no mode; the modes: block, warn"),
    ],
)
def test_an_unknown_gate_or_mode_is_refused(tmp_path, table, said) -> None:
    (tmp_path / "axiomation.toml").write_text(f"[gates]\n{table}\n")
    with pytest.raises(SettingsError, match=said):
        modes(tmp_path)


def test_the_command_prints_each_gate_and_exits_1_when_one_fails(
    tmp_path, capsys
) -> None:
    store, kept = built(tmp_path)
    paper = tmp_path / "paper"
    paper.mkdir()
    args = ["check", str(paper), "--store", str(store.directory)]
    assert main(args) == 1
    out = capsys.readouterr().out
    assert out.startswith(
        f"state {kept.id}: fails on unsupported-claims, dangling-references, "
        "duplicate-labels\n"
    )
    assert "unsupported-claims  block → fail (4):" in out
    # q reaches a through d, which l's statement never names
    assert "undeclared-assumptions  warn → warn (1):" in out
    # every gate's line on the first screen, before any gate's items; but
    # `reviews`, with no outside review received
    lines = out.splitlines()
    first_items = lines.index("unsupported-claims  block → fail (4):")
    heads = [line.split()[0] for line in lines[1:first_items] if "→" in line]
    assert heads == [g.value for g in Gate if g is not Gate.REVIEWS]
    assert "  undeclared-assumptions  warn → warn  1" in lines[:first_items]

    assert main([*args, "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["state"] == kept.id and data["passes"] is False
    assert [g["gate"] for g in data["gates"]] == [g.value for g in Gate]
    first = data["gates"][0]
    assert set(first) == {
        "gate",
        "mode",
        "outcome",
        "count",
        "items",
        "to_defects",
        "to_unreviewed",
    }
    assert first["count"] == len(first["items"]) == 4
    assert first["items"][0]["gate"] == "unsupported-claims"

    (paper / "axiomation.toml").write_text(
        "[gates]\n"
        + "".join(
            f'{g} = "warn"\n'
            for g in ("unsupported-claims", "dangling-references", "duplicate-labels")
        )
    )
    assert main(args) == 0
    assert capsys.readouterr().out.startswith(f"state {kept.id}: passes\n")


def test_a_restated_claim_read_as_saying_more_is_a_defect_once(tmp_path) -> None:
    """`established`'s `restated`
    route already stops at a held negative `accurate` on the restating
    block, a reading on a node its walk starts from. `check` lists the
    reading as a defect, and the claim nowhere else."""
    from test_established import built as established_paper
    from test_established import valid

    store, kept = established_paper(tmp_path)
    valid(store, kept, "p")
    valid(store, kept, "q")
    view = View(store, kept)
    (r,) = [e for e in view.established if e.claim.words.startswith("So f")]
    assert r.established
    judged(store, kept, Aspect.ACCURATE, "r", Verdict.NOT_SHOWN, PERSON)
    view = View(store, kept)
    (r,) = [e for e in view.established if e.claim.words.startswith("So f")]
    assert r.reason is not None and r.reason.kind.value == "not-shown"
    assert r.reason.why == "accurate on r is held not-shown, read by person"
    found = {g.gate: g for g in gates(view).results}
    assert [(i.data["handle"], i.data["kind"]) for i in found[Gate.DEFECTS].items] == [
        ("r", "not-shown")
    ]
    assert "r" not in listed(found[Gate.UNSUPPORTED_CLAIMS])


def test_the_gate_delta_tells_two_subjects_on_one_block_apart() -> None:
    """Two `valid` subjects of one J share its anchor: an edit that adds the
    second is a `+1`, never hidden by the first."""
    anchor = {"kind": "block", "node": "n1", "words": None, "work": None, "file": None}

    def item(target: str) -> Item:
        subject = {"aspect": "valid", "node": "n1", "target": target}
        return Item(
            f"j → {target} unjudged",
            {"kind": "unjudged", "anchors": [anchor], "subject": subject},
        )

    def verdict(*items: Item) -> check.Verdict:
        return check.Verdict(
            "s",
            (check.GateResult(Gate.UNREVIEWED, Mode.WARN, Outcome.WARN, items),),
        )

    (moved,) = check.delta(verdict(item("t1")), verdict(item("t1"), item("t2")))
    assert moved.line.splitlines() == ["unreviewed −0 +1", "  + j → t2 unjudged"]


def test_a_claim_established_under_a_conjecture_its_statement_assumes_is_audited(
    tmp_path,
) -> None:
    """x's statement takes the conjecture k as a hypothesis: held true, x
    is established under k, and `check` names it, with whether a person
    wrote the flag."""

    def verdict(path, by: str):
        data = payload()
        data["edges"] = [
            *data["edges"],
            {**edge("x", "uses", "k"), "assumed": True, "provenance": {"by": by}},
        ]
        store = RecordStore(path / "store")
        kept = keep(store, V1, TEXT, data)
        judged(store, kept, Aspect.TRUE, "x", Verdict.VERIFIED)
        return gates(View(store, kept))

    found = verdict(tmp_path, "agent")
    (item,) = found.conditional
    assert (
        item.line
        == 'x  "Clearly f is extra." under k "Perhaps f is tiny.", by an agent alone'
    )
    assert item.data["under"] == [
        {"at": "k", "claim": "Perhaps f is tiny.", "by_person": False}
    ]
    head = check.report(found).head
    assert (
        "conditional: 1 claim established under a conjecture its statement assumes, "
        "1 of them by an agent's flag alone (listed last)" in head
    )
    assert found.data()["conditional"] == [item.data]

    (item,) = verdict(tmp_path / "person", "person").conditional
    assert item.line.endswith(", by a person") and item.data["by_person"]


def test_an_assumed_edge_uses_no_unsupported_claim(tmp_path) -> None:
    """`s` takes `c` as a hypothesis: it rests on no truth of `c`, so
    `uses-unsupported` leaves it out."""

    def users(data):
        store = RecordStore(tmp_path / str(len(list(tmp_path.iterdir()))))
        kept = keep(store, V1, TEXT, data)
        opened = queries.open_report(View(store, kept)).sections["uses_unsupported"]
        return [i.data["handle"] for i in opened]

    assert "s" in users(payload())
    data = payload()
    data["edges"] = [
        {**e, "assumed": True} if (e["source"], e["kind"]) == ("s", "uses") else e
        for e in data["edges"]
    ]
    assert "s" not in users(data)


def test_a_claim_under_a_blocking_finding_is_in_defects_alone(tmp_path) -> None:
    """`c`, unruled, is a gap; a severity-3 finding on it makes it a defect
    (`docs/model.md` § 11): `defects` lists the finding, and neither
    `unsupported-claims` nor `unreviewed` lists `c` or its `true`."""
    store, kept = built(tmp_path)
    at = seen(store, kept)
    find(store, kept, Aspect.TRUE, [anchored(at, AnchorKind.BLOCK, "c")], 3, "wrong")
    found = results(store, kept)
    assert "c" not in listed(found[Gate.UNSUPPORTED_CLAIMS])
    assert found[Gate.UNSUPPORTED_CLAIMS].to_defects == 1
    assert ("c", "finding") in [
        (i.data["handle"], i.data["kind"]) for i in found[Gate.DEFECTS].items
    ]
    assert "c" not in listed(found[Gate.UNREVIEWED])
