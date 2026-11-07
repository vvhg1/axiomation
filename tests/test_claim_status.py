"""A claim's one status (`claim_status.fold`, docs/model.md § 11), on its
inputs alone: its established verdict, its support, the readings on its
route and the open findings on them. One case per status, and one per
clash of two, where the status that asks something comes first."""

from __future__ import annotations

import re
from pathlib import Path

from axiomation import judgments
from axiomation.claim_status import ClaimStatus, Folded, fold, status_clears
from axiomation.established import Claim, Established, Missing, Reason
from axiomation.judgments import (
    NEGATIVE_CLEARS,
    Author,
    Cause,
    CauseKind,
    FindingRead,
    Read,
    StatusKind,
    SubjectStatus,
    Walked,
)
from axiomation.records import (
    Anchor,
    AnchorKind,
    Aspect,
    BasisElement,
    BasisKind,
    Declared,
    Depth,
    Finding,
    FindingAnswer,
    FindingStatus,
    Judgment,
    Ruling,
    RulingKind,
    Status,
    Subject,
    Verdict,
)
from axiomation.rulings import ClaimSupport, Guard, Ruled, RulingRead, Support

ROOT = Path(__file__).parents[1]
NODE = "n1"
WORDS = "every chain settles"
TRUE = Subject(NODE, None, WORDS)
AGENT = Author("claude-code 2.1.300", "claude-opus-5-5", "s1")
LAPSE = Cause(
    CauseKind.TEXT, "s0", "s1", BasisElement(BasisKind.NODE, NODE), "its text changed"
)


def claim(status: Status = Status.UNSUPPORTED) -> Claim:
    return Claim(NODE, WORDS, status)


def established(status: Status, reason: Reason | None = None) -> Established:
    return Established(claim(status), reason)


def because(kind: Missing, node: str = NODE) -> Reason:
    return Reason(kind, node, f"{kind.value} on {node}")


def read(
    verdict: Verdict,
    order: int = 0,
    author: Author = AGENT,
    lapsed: bool = False,
    on: Subject = TRUE,
    aspect: Aspect = Aspect.TRUE,
) -> Read:
    judgment = Judgment(
        f"j{order}", None, "s0", aspect, on, verdict, Depth.INFORMAL, "e", ()
    )
    walked = Walked((LAPSE,) if lapsed else (), (on.node,), on.words)
    return Read(judgment, author, order, walked)


def held(verdict: Verdict, aspect: Aspect = Aspect.TRUE, on: Subject = TRUE):
    r = read(verdict, on=on, aspect=aspect)
    return SubjectStatus(aspect, on, StatusKind.HELD, r, (r,), concluded=(r,))


def finding(
    severity: int,
    aspect: Aspect = Aspect.TRUE,
    answered: FindingStatus | None = None,
    ident: str = "f1",
) -> FindingRead:
    found = Finding(
        ident,
        None,
        "s0",
        aspect,
        severity,
        "a problem",
        None,
        (Anchor(AnchorKind.PART, NODE, WORDS),),
        (),
    )
    answer = (
        None
        if answered is None
        else FindingAnswer("a1", None, ident, answered, "a note")
    )
    return FindingRead(found, AGENT, 0, Walked((), (NODE,), WORDS), answer)


def ruled(kind: RulingKind, author: Author = AGENT) -> Ruled:
    ruling = Ruling("r1", None, "s0", TRUE, kind, "a reason")
    reading = RulingRead(ruling, author, 0, Walked((), (NODE,), WORDS))
    return Ruled(TRUE, (reading,), (reading,))


def support(status: Support, **kwargs) -> ClaimSupport:
    return ClaimSupport(status, **kwargs)


ARGUED = support(Support.SUPPORTED)
UNRULED = support(Support.UNSUPPORTED, gap=Missing.UNSUPPORTED)
OWED = support(
    Support.UNSUPPORTED, gap=Missing.UNSUPPORTED, ruled=ruled(RulingKind.OWED)
)
KNOWN = support(
    Support.NO_ARGUMENT_OWED, ruled=ruled(RulingKind.KNOWN), ruling=RulingKind.KNOWN
)


def folded(e, s, route=(), findings=()) -> Folded:
    return fold(e, s, list(route), list(findings), lambda n: f"h-{n}")


def test_an_argued_claim_whose_route_holds_is_established() -> None:
    f = folded(established(Status.JUSTIFIED), ARGUED, [held(Verdict.VERIFIED)])
    assert (f.status, f.detail, f.next) == (ClaimStatus.ESTABLISHED, "argued", None)


def test_a_ruling_that_clears_it_says_whose_in_the_detail() -> None:
    f = folded(established(Status.UNSUPPORTED), KNOWN, [held(Verdict.VERIFIED)])
    assert f.status is ClaimStatus.ESTABLISHED
    assert f.detail == "no argument owed: ruled known by agents alone"


def test_an_owed_ruling_is_unsupported_however_its_true_reads() -> None:
    # established by a reading, but the paper still owes its argument
    f = folded(established(Status.UNSUPPORTED), OWED, [held(Verdict.VERIFIED)])
    assert f.status is ClaimStatus.UNSUPPORTED
    assert f.detail == "ruled owed by agents alone"
    assert "a person's ruling (`rule`), which replaces the agents' owed" in (
        f.next or ""
    )


def test_an_unruled_claim_says_so() -> None:
    f = folded(established(Status.UNSUPPORTED, because(Missing.UNSUPPORTED)), UNRULED)
    assert (f.status, f.detail) == (ClaimStatus.UNSUPPORTED, "unruled")
    assert "(`rule`)" in (f.next or "")


def test_a_guarded_ruling_names_its_guard() -> None:
    guarded = support(
        Support.UNSUPPORTED,
        gap=Missing.UNSUPPORTED,
        ruled=ruled(RulingKind.KNOWN),
        ruling=RulingKind.KNOWN,
        guard=Guard.TRUE_NOT_HELD,
    )
    f = folded(established(Status.UNSUPPORTED), guarded)
    assert f.detail == "ruled known, guarded: agents alone, `true` not held verified"


def test_a_negative_reading_is_a_defect_before_its_owed_ruling() -> None:
    e = established(Status.UNSUPPORTED, because(Missing.NOT_SHOWN))
    f = folded(e, OWED, [held(Verdict.NOT_SHOWN)])
    assert f.status is ClaimStatus.DEFECT
    assert f.next == NEGATIVE_CLEARS


def test_a_negative_on_its_proof_is_its_defect() -> None:
    proof = Subject("n2", NODE, WORDS)
    f = folded(
        established(Status.JUSTIFIED, because(Missing.NOT_SHOWN, "n2")),
        ARGUED,
        [held(Verdict.VERIFIED), held(Verdict.NOT_SHOWN, Aspect.VALID, proof)],
    )
    assert f.status is ClaimStatus.DEFECT
    assert f.detail.startswith("`valid` of its proof not-shown")


def test_a_lapsed_negative_stays_a_defect_until_a_reading_concludes() -> None:
    r = read(Verdict.CONTRADICTED, lapsed=True)
    lapsed = SubjectStatus(
        Aspect.TRUE, TRUE, StatusKind.LAPSED, None, (r,), concluded=(r,)
    )
    f = folded(established(Status.SELF, because(Missing.LAPSED)), ARGUED, [lapsed])
    assert f.status is ClaimStatus.DEFECT
    assert f.detail.endswith("contradicted (lapsed), read by " + AGENT.name)
    assert f.next == "`axiomation reask` j0, then a judgment"


def test_an_open_severity_3_finding_is_a_defect() -> None:
    blocking = finding(3)
    f = folded(
        established(Status.SELF, because(Missing.FINDING)),
        ARGUED,
        [held(Verdict.VERIFIED)],
        [blocking],
    )
    assert f.status is ClaimStatus.DEFECT
    assert f.next == "`axiomation answer-finding` f1"
    assert f.findings == ("f1",)


def test_a_dispute_comes_before_an_owed_ruling_and_offers_no_answered_finding() -> None:
    a = read(Verdict.VERIFIED, 0)
    b = read(Verdict.NOT_SHOWN, 1, Author("claude-code 2.1.300", "other", "s2"))
    disputed = SubjectStatus(
        Aspect.TRUE, TRUE, StatusKind.DISPUTED, None, (a, b), concluded=(a, b)
    )
    f = folded(
        established(Status.UNSUPPORTED, because(Missing.DISPUTED)), OWED, [disputed]
    )
    assert f.status is ClaimStatus.DISPUTED
    # its finding was dismissed: not counted, so never offered again
    assert f.next == "a person's judgment after them (`axiomation judge`)"


def test_a_disputed_subject_names_the_finding_that_still_counts() -> None:
    standing = finding(3, ident="f7")
    s = SubjectStatus(Aspect.TRUE, TRUE, StatusKind.DISPUTED, counted=(standing,))
    assert status_clears(s) == (
        "a person's judgment after them, or finding f7 answered"
    )


def test_a_conjecture_is_not_asserted() -> None:
    f = folded(established(Status.CONJECTURED, because(Missing.UNJUDGED)), None)
    assert f.status is ClaimStatus.CONJECTURED


def test_its_own_reason_names_the_reading_it_waits_on() -> None:
    for kind, status in (
        (Missing.LAPSED, ClaimStatus.LAPSED),
        (Missing.STALE, ClaimStatus.STALE),
        (Missing.UNJUDGED, ClaimStatus.UNJUDGED),
        (Missing.INCONCLUSIVE, ClaimStatus.INCONCLUSIVE),
    ):
        f = folded(established(Status.SELF, because(kind)), ARGUED)
        assert (f.status, f.detail) == (status, f"{kind.value} on {NODE}")
        assert f.next


def test_a_reason_on_what_it_rests_on_names_that_claim() -> None:
    e = established(Status.RESTATED, because(Missing.LAPSED, "n9"))
    f = folded(e, ARGUED, [held(Verdict.VERIFIED)])
    assert (f.status, f.detail, f.at, f.next) == (
        ClaimStatus.RESTS_ON,
        "h-n9: lapsed",
        "n9",
        None,
    )


def test_a_minor_finding_stops_established_but_not_a_status_of_how_it_reads() -> None:
    e = established(Status.SELF)
    f = folded(e, ARGUED, [held(Verdict.VERIFIED)], [finding(1, Aspect.WELL_POSED)])
    assert f.status is ClaimStatus.FINDING_OPEN
    assert f.detail == "finding f1 (severity 1, well-posed)"
    presentation = folded(e, ARGUED, [], [finding(2, Aspect.PRESENTATION)])
    assert presentation.status is ClaimStatus.ESTABLISHED
    assert presentation.findings == ("f1",)


def anchored_on(kind: AnchorKind, words: str | None) -> FindingRead:
    found = Finding(
        "f1",
        None,
        "s0",
        Aspect.TRUE,
        2,
        "a problem",
        None,
        (Anchor(kind, NODE, words),),
        (),
    )
    return FindingRead(found, AGENT, 0, Walked((), (NODE,), words), None)


def test_a_worded_finding_bears_on_the_claim_its_words_meet_not_its_neighbours() -> (
    None
):
    """A block of several claims: a finding quoting one of them marks that
    claim alone; the block's other claims keep their own status."""
    other = Subject(NODE, None, "the bound is sharp")
    for kind in (AnchorKind.SPAN, AnchorKind.PART):
        on_it = anchored_on(kind, WORDS)
        assert judgments.bears_on(on_it, TRUE)
        assert not judgments.bears_on(on_it, other)
        # quoting a few of its words, or a sentence holding it, still meets it
        assert judgments.bears_on(anchored_on(kind, "chain settles"), TRUE)
        assert judgments.bears_on(anchored_on(kind, f"So {WORDS}."), TRUE)
    whole = anchored_on(AnchorKind.BLOCK, None)
    assert judgments.bears_on(whole, TRUE) and judgments.bears_on(whole, other)


def test_an_answered_finding_is_no_issue() -> None:
    f = folded(
        established(Status.SELF),
        ARGUED,
        [],
        [finding(1, answered=FindingStatus.DISMISSED)],
    )
    assert (f.status, f.findings) == (ClaimStatus.ESTABLISHED, ())


def test_the_author_leaving_it_unargued_is_their_status() -> None:
    left = support(
        Support.LEFT_UNARGUED,
        ruled=ruled(RulingKind.LEFT_UNARGUED, Author()),
        ruling=RulingKind.LEFT_UNARGUED,
    )
    f = folded(established(Status.UNSUPPORTED), left, [held(Verdict.VERIFIED)])
    assert f.status is ClaimStatus.LEFT_UNARGUED


def test_the_viewer_colours_every_status() -> None:
    palette = (ROOT / "src/axiomation/viewer/page/src/palette.js").read_text()
    block = palette[palette.index("const CLAIM_STATUS = {") :]
    block = block[: block.index("};")]
    names = re.findall(r'^\s+"?([a-z-]+)"?: \{ label', block, re.M)
    assert names == [s.value for s in ClaimStatus]


def test_a_claim_ruled_clear_with_its_true_unread_is_unjudged() -> None:
    """A ruling clears the argument, never the reading: its own reason is
    `unsupported` (nothing yet to rest on), which reads as unjudged."""
    e = established(Status.UNSUPPORTED, because(Missing.UNSUPPORTED))
    f = folded(e, KNOWN)
    assert f.status is ClaimStatus.UNJUDGED
    assert f.at is None and "`axiomation review`" in (f.next or "")


def test_a_promised_argument_is_never_offered_a_ruling() -> None:
    promised = support(
        Support.UNSUPPORTED, gap=Missing.UNSUPPORTED, declared=Declared.PROMISED
    )
    f = folded(established(Status.UNSUPPORTED), promised)
    assert f.next == "the argument the paper promises, or the promise taken out"


def test_show_carries_each_claim_s_status_as_the_fold_reads_it(tmp_path) -> None:
    """On the gates' paper: a person's `known` on `y` with its `true`
    unread is `unjudged`; `c`, unruled, `unsupported`; `show --json`
    carries both."""
    from axiomation.queries import View, show
    from test_check import PERSON, built
    from test_rulings import ruled

    store, kept = built(tmp_path)
    ruled(store, kept, "y", RulingKind.KNOWN, PERSON)
    view = View(store, kept)
    status = {
        h: show(view, h).sections["standings"][0].data["claim_status"]["status"]
        for h in ("y", "c")
    }
    assert status == {"y": "unjudged", "c": "unsupported"}
