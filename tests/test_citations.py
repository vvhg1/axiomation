"""A cited judgment's seam: it names the work its block
cites, the core reads each page it names and records the stamp, a failed
read is never evidence, a file it read that prints other content, or is
absent or held back, lapses it, and another reading of an unchanged file
does not; a transient page makes it provisional, and `established` takes a `self` claim
by its citations unless a work it cites is retracted.

A synthetic one-line paper: `s` and `m`, `self` claims citing one work and
two; `u`, a `self` claim that uses `s`; `d`, a definition citing nothing.
The providers are `test_evidence`'s fakes. No model is called.
"""

from __future__ import annotations

from typing import Any

import pytest

import record_file
from axiomation import evidence, queries, records, review, rulings
from axiomation.cli import main
from axiomation.established import Missing, established
from axiomation.evidence import FileStamp, Origin, ReadStatus, Sources
from axiomation.judgments import (
    Author,
    CauseKind,
    JudgmentError,
    StatusKind,
    judge,
    offered,
    readings,
    reaffirm,
    see_files,
    see_work,
    seen,
    subject,
)
from axiomation.marks import open_marks
from axiomation.questions import review_file as rf
from axiomation.records import (
    Aspect,
    Depth,
    Publication,
    RecordKind,
    RecordStore,
    RulingKind,
    Subject,
    Verdict,
)
from test_evidence import PAGE, Shelf, library, transient
from test_judgments import _run, edge, graph
from test_support_search import search_all
from test_versions import V1, _span, keep

S_CITE = "\\cite{able1998}"
M_CITE = "\\cite{able1998,baker1984}"
BLOCKS = [
    ("d", "Let f be a gain."),
    ("s", f"Able shows f is small {S_CITE}."),
    ("m", f"Both f and g are known {M_CITE}."),
    ("u", "Then f is bounded."),
]
TEXT = " ".join(w for _, w in BLOCKS)
STANDINGS = {
    "s": f"Able shows f is small {S_CITE}.",
    "m": f"Both f and g are known {M_CITE}.",
    "u": "Then f is bounded.",
}
AGENT = Author("agent", "model-a", "s1")
CODE = Author("axiomation", "sources recheck")
ABLE = {12: PAGE, 13: "A plate."}
BAKER = {4: PAGE + " Baker."}


def payload() -> dict[str, Any]:
    found = graph(
        TEXT, BLOCKS, [edge("u", "uses", "s")], [(b, w) for b, w in STANDINGS.items()]
    )
    for s in found["standings"]:
        s["status"] = "self"
        s["claim"]["at"] = _span(TEXT, "p.md", s["claim"]["text"])
    found["document"]["citations"] = [
        {"command": "cite", "keys": keys, "locator": None, "at": _span(TEXT, "p.md", c)}
        for c, keys in ((S_CITE, ["able1998"]), (M_CITE, ["able1998", "baker1984"]))
    ]
    return found


def built(tmp_path):
    store = RecordStore(tmp_path / "store")
    return store, keep(store, V1, TEXT, payload())


def cited(
    store,
    kept,
    on: str,
    work: str,
    pages=(),
    sources: Sources | None = None,
    verdict=Verdict.VERIFIED,
    author=AGENT,
):
    at = seen(store, kept)
    words = dict(BLOCKS)[on]
    return judge(
        store,
        kept,
        Aspect.CITED,
        subject(at, Aspect.CITED, on, span=words, text=words, work=work),
        verdict,
        Depth.INFORMAL,
        "the page says it",
        author,
        pages=pages,
        sources=sources,
    ).records.judgments[0]


def status(store, kept, on: str, work: str):
    node = kept.node_of(on)
    return next(
        s
        for s in readings(store, kept).statuses
        if s.aspect is Aspect.CITED
        and s.subject.node == node
        and s.subject.work == work
    )


def verdicts(store, kept):
    found = established(readings(store, kept), open_marks(store, kept))
    by_block = {n.node: n.block for n in kept.nodes}
    return {by_block[e.claim.node]: e for e in found}


def shelves(able=ABLE, origin=Origin.LIBRARY, **shelf: Any) -> Sources:
    return Sources(
        [Shelf(origin, {"able1998": dict(able), "baker1984": BAKER}, **shelf)]
    )


def see(store, sources: Sources, *keys: str) -> None:
    """Each key's file as `sources` hold it now, filed as a file seen: what
    a review's observation files."""
    see_files(store, [sources.file_stamp(k)[0] for k in keys], CODE)


# --- the subject ----------------------------------------------------------------------


def test_a_block_s_cited_works_come_from_the_document_half(tmp_path) -> None:
    store, kept = built(tmp_path)
    at = seen(store, kept)
    works = {kept.node_of(b): w for b, w in (("s", ("able1998",)),)}
    assert at.works_of[kept.node_of("s")] == works[kept.node_of("s")]
    assert at.works_of[kept.node_of("m")] == ("able1998", "baker1984")
    assert kept.node_of("d") not in at.works_of


def test_a_cited_judgment_names_a_work_its_block_cites(tmp_path) -> None:
    store, kept = built(tmp_path)
    at = seen(store, kept)
    words = dict(BLOCKS)["s"]
    with pytest.raises(JudgmentError, match="names its work with --work: s cites"):
        subject(at, Aspect.CITED, "s", span=words, text=words)
    with pytest.raises(JudgmentError, match="s cites able1998, not baker1984"):
        subject(at, Aspect.CITED, "s", span=words, text=words, work="baker1984")
    with pytest.raises(JudgmentError, match="--work names a cited judgment"):
        subject(at, Aspect.TRUE, "s", work="able1998")
    on = subject(at, Aspect.CITED, "m", span="known", text="known", work="baker1984")
    assert (on.words, on.work) == ("known", "baker1984")


# --- the pages ------------------------------------------------------------------------


def test_a_page_is_read_and_stamped_at_filing(tmp_path) -> None:
    """The core reads the page again and records its own stamp; the file it
    read, which the store never saw, is stamped too (its SHA-256 and its
    content hash), given and filed as a file seen with it."""
    store, kept = built(tmp_path)
    shelf = shelves()
    record = cited(store, kept, "s", "able1998", [("able1998", 12)], shelf)
    (stamp,) = record.pages
    assert (stamp.name, stamp.read_status) == ("able1998:12", ReadStatus.OK)
    (file,) = record.given
    assert file == shelf.file_stamp("able1998")[0] and file.content
    (file_seen,) = store.judgment_records().files
    assert file_seen.stamp == file and file_seen.answer == record.answer
    s = status(store, kept, "s", "able1998")
    assert s.status is StatusKind.HELD and not s.held.provisional


def test_a_failed_read_is_never_evidence(tmp_path) -> None:
    """A verdict on what the work holds needs a page read; a copy's plate
    under the floor, a work no source holds, or no page at all is refused.
    Could not tell needs none."""
    store, kept = built(tmp_path)
    copy = shelves(origin=Origin.TRANSIENT)
    with pytest.raises(JudgmentError, match="a failed read is never evidence"):
        cited(store, kept, "s", "able1998", [("able1998", 13)], copy)
    with pytest.raises(JudgmentError, match="able1998:12 absent"):
        cited(store, kept, "s", "able1998", [("able1998", 12)], Sources([]))
    with pytest.raises(JudgmentError, match="rests on a page read"):
        cited(store, kept, "s", "able1998", verdict=Verdict.NOT_SHOWN)
    record = cited(
        store,
        kept,
        "s",
        "able1998",
        [("able1998", 13)],
        copy,
        verdict=Verdict.INCONCLUSIVE,
    )
    assert record.pages[0].read_status is ReadStatus.CONTENT
    assert (
        cited(store, kept, "m", "baker1984", verdict=Verdict.INCONCLUSIVE).pages == ()
    )


def test_a_page_of_record_with_no_text_is_evidence_as_its_image(tmp_path) -> None:
    """A plate of the version of record (a scan with no text layer) is read
    as its image: the judgment rests on its file, as any page's does."""
    store, kept = built(tmp_path)
    record = cited(store, kept, "s", "able1998", [("able1998", 13)], shelves())
    assert record.pages[0].read_status is ReadStatus.IMAGE
    assert record.pages[0].read
    assert not evidence.provisional(record.pages)
    truly(store, kept, "s", [("able1998", 13)], shelves(origin=Origin.FILE))


def test_a_page_of_another_file_of_the_work_is_its_works(tmp_path) -> None:
    """A translation held under `KEY@…` is a file of the cited work: its
    page is stamped under its own key, so a replaced file lapses it."""
    store, kept = built(tmp_path)
    other = "able1998@0a1b2c3d"
    shelf = Shelf(Origin.LIBRARY, {"able1998": dict(ABLE), other: {1: PAGE}})
    record = cited(store, kept, "s", "able1998", [(other, 1)], Sources([shelf]))
    assert [p.name for p in record.pages] == [f"{other}:1"]
    with pytest.raises(JudgmentError, match="not of the cited work able1998"):
        cited(store, kept, "s", "able1998", [("baker1984@0a1b2c3d", 4)], shelves())


def test_the_session_is_given_each_other_file_beside_its_work(tmp_path) -> None:
    """Another file of a cited work is read beside it, by its own key:
    each read says which file it is (`QueueReads`)."""
    other = "able1998@0a1b2c3d"
    shelf = Shelf(
        Origin.LIBRARY,
        {"able1998": dict(ABLE), other: {1: PAGE}, "baker1984": BAKER},
        copies={
            "able1998": "able1998's cited edition is original.pdf",
            other: "another file of able1998, not its cited edition: "
            f"`{other}`, the English translation: translation.pdf",
        },
        others={"able1998": (other,)},
    )
    found = citation_brief(tmp_path, shelf)
    workspace = found.question.workspace
    assert workspace is not None
    served = workspace.served
    assert served is not None and served.reads is not None
    assert other in served.reads.works


def test_a_page_of_another_work_or_on_another_aspect_is_refused(tmp_path) -> None:
    store, kept = built(tmp_path)
    with pytest.raises(JudgmentError, match="is a page of baker1984, not of"):
        cited(store, kept, "s", "able1998", [("baker1984", 4)], shelves())
    with pytest.raises(JudgmentError, match="read through the paper's sources"):
        cited(store, kept, "s", "able1998", [("able1998", 12)])
    at = seen(store, kept)
    # a `true` reads any work the paper cites, and none it cites nowhere
    with pytest.raises(JudgmentError, match="which the paper cites nowhere"):
        judge(
            store,
            kept,
            Aspect.TRUE,
            subject(at, Aspect.TRUE, "u"),
            Verdict.VERIFIED,
            Depth.INFORMAL,
            "x",
            pages=[("carter2001", 1)],
            sources=shelves(),
        )


# --- pages on `valid` and `true` ----------------------------------------


def truly(store, kept, on: str, pages, sources: Sources, author=AGENT):
    at = seen(store, kept)
    return judge(
        store,
        kept,
        Aspect.TRUE,
        subject(at, Aspect.TRUE, on),
        Verdict.VERIFIED,
        Depth.INFORMAL,
        "the cited theorem's hypotheses hold here",
        author,
        pages=pages,
        sources=sources,
    ).records.judgments[0]


def test_the_argument_session_lays_in_every_claim_s_cited_works(tmp_path) -> None:
    """Every subject's cited works are laid in, a proof's step (one
    through Billingsley) and a claim that is no step
    alike, so a claim about a cited work can be verified on its pages and
    never from recall; the methods session reads none."""
    found = payload()
    for roles in (["justification", "claim"], ["claim"]):
        for b in found["blocks"]:
            b["roles"] = roles if b["id"] == "m" else ["claim"]
        store = RecordStore(tmp_path / f"store-{len(roles)}")
        view = queries.View(store, keep(store, V1, TEXT, found))
        assert review.laid_works(view, rf.Session.ARGUMENT) == [
            "able1998",
            "baker1984",
        ]
        assert review.laid_works(view, rf.Session.METHODS) == []


def test_a_claim_s_own_sentence_names_the_works_it_cites(tmp_path) -> None:
    """A `true` claim's sentence carries its citations (a claim about a
    cited work, never verified from recall); a claim citing nothing has
    none."""
    store, kept = built(tmp_path)
    view = queries.View(store, kept)
    cites = {
        s.sheet.line.split(" ")[0]: s.sheet.cites
        for s in review.subjects(view, rf.Session.ARGUMENT)
        if s.aspect is Aspect.TRUE
    }
    assert cites == {
        "s": ("able1998",),
        "m": ("able1998", "baker1984"),
        "u": (),
    }


def test_the_gate_refuses_a_cited_claim_verified_from_recall() -> None:
    """A `verified` `true` whose claim's sentence cites a work, naming no
    page of it, is handed back: name the pages laid in, or judge it
    inconclusive where the work has none (never from
    memory). A page named, an `inconclusive`, and a claim citing nothing
    pass."""

    def problems(works: tuple[str, ...], *lines: str) -> list[str]:
        subjects = [
            rf.SheetSubject(
                "s1",
                Aspect.TRUE,
                's · "Able shows f is small"',
                works=works,
                cites=("able1998",),
            ),
            rf.SheetSubject("s2", Aspect.TRUE, 'u · "Then f is bounded."'),
        ]
        sheet = rf.render(rf.Session.ARGUMENT, subjects)
        spec = rf.spec(rf.Session.ARGUMENT, sheet, subjects, {}, {}, list(works))
        edited = sheet + "\n".join(lines) + "\n"
        return [p for p in rf.read(spec, edited).problems if "recall" in p]

    recalled = "judge s1 verified informal — Able's lemma, as I know it"
    (none_here,) = problems((), recalled)
    assert "able1998 has no pages here: judge it inconclusive" in none_here
    (pages_here,) = problems(("able1998",), recalled)
    assert (
        "able1998's pages are read by your `cited_*` tools: name the ones you read"
        in pages_here
    )
    assert not problems(
        ("able1998",),
        "judge s1 verified informal pages able1998:12 — read on its page",
        "judge s2 verified informal — f is bounded",
    )
    assert not problems((), "judge s1 inconclusive informal — the citation unread")


def test_a_true_judgment_reads_a_page_its_block_cites(tmp_path) -> None:
    """An argument's reader checks a cited result against its page: the page
    is stamped as a cited judgment's is, and a new edition lapses it."""
    store, kept = built(tmp_path)
    record = truly(store, kept, "m", [("baker1984", 4)], shelves())
    assert [p.name for p in record.pages] == ["baker1984:4"]
    assert status_of(store, kept, Aspect.TRUE, "m").status is StatusKind.HELD

    edition = Sources([Shelf(Origin.LIBRARY, {"baker1984": {4: PAGE + " Revised."}})])
    see(store, edition, "baker1984")
    s = status_of(store, kept, Aspect.TRUE, "m")
    assert s.status is StatusKind.LAPSED and s.cause.kind is CauseKind.CITED_FILE

    again = reaffirm(
        store,
        kept,
        record.id,
        Depth.INFORMAL,
        "the revision keeps the hypotheses",
        AGENT,
        pages=[("baker1984", 4)],
        sources=edition,
    ).records.judgments[0]
    assert again.reaffirms == record.id
    assert status_of(store, kept, Aspect.TRUE, "m").status is StatusKind.HELD


def test_a_failed_page_on_a_true_judgment_is_refused(tmp_path) -> None:
    """A `true` judgment has no verdict a page must carry, so every page it
    names must read: a failed read is never evidence."""
    store, kept = built(tmp_path)
    copy = shelves(origin=Origin.TRANSIENT)
    with pytest.raises(JudgmentError, match="a failed read is never evidence"):
        truly(store, kept, "s", [("able1998", 13)], copy)
    with pytest.raises(JudgmentError, match="only a cited, valid or true"):
        judge(
            store,
            kept,
            Aspect.ACCURATE,
            Subject(kept.node_of("s")),
            Verdict.VERIFIED,
            Depth.INFORMAL,
            "x",
            pages=[("able1998", 12)],
            sources=shelves(),
        )


def status_of(store, kept, aspect: Aspect, on: str):
    node = kept.node_of(on)
    return next(
        s
        for s in readings(store, kept).statuses
        if s.aspect is aspect and s.subject.node == node
    )


def test_a_transient_page_makes_it_provisional(tmp_path) -> None:
    store, kept = built(tmp_path)
    copy = shelves(origin=Origin.TRANSIENT)
    record = cited(store, kept, "s", "able1998", [("able1998", 12)], copy)
    assert record.pages[0].read_status is ReadStatus.TRANSIENT
    s = status(store, kept, "s", "able1998")
    assert s.status is StatusKind.HELD and s.held.provisional


# --- the file lapse ------------------------------------------------------------


def test_a_file_that_prints_other_content_lapses_it(tmp_path) -> None:
    """The library's file prints other content than the copy read: the
    judgment lapses by its file, and is reaffirmed on its page as it reads
    now."""
    store, kept = built(tmp_path)
    copy = shelves(origin=Origin.TRANSIENT)
    record = cited(store, kept, "s", "able1998", [("able1998", 12)], copy)

    # the same file again: nothing lapses
    see(store, copy, "able1998")
    assert status(store, kept, "s", "able1998").status is StatusKind.HELD

    edition = shelves({12: PAGE + " Second edition."})
    see(store, edition, "able1998")
    s = status(store, kept, "s", "able1998")
    assert s.status is StatusKind.LAPSED
    assert s.cause.kind is CauseKind.CITED_FILE
    assert s.cause.why.startswith("the cited file able1998 prints other content")
    assert s.cause.data["was"] == record.given[0].content[:12]

    with pytest.raises(JudgmentError, match="name a page of each work"):
        reaffirm(store, kept, record.id, Depth.INFORMAL, "still says it", AGENT)
    again = reaffirm(
        store,
        kept,
        record.id,
        Depth.INFORMAL,
        "the edition says it too",
        AGENT,
        pages=[("able1998", 12)],
        sources=edition,
    ).records.judgments[0]
    assert again.reaffirms == record.id
    assert again.pages[0].read_status is ReadStatus.OK
    s = status(store, kept, "s", "able1998")
    assert s.status is StatusKind.HELD and not s.held.provisional


def test_another_reading_of_the_same_file_lapses_nothing(tmp_path) -> None:
    """A page read again with another text (a new ingest, another OCR, a
    tier switched) is our reading of the file, not the file: a judgment on
    an unchanged file stands; and a file annotated or downloaded again
    (another SHA-256, the same content) stands too."""
    store, kept = built(tmp_path)
    shelf = shelves(files={"able1998": "a" * 64}, contents={"able1998": "printed"})
    cited(store, kept, "s", "able1998", [("able1998", 12)], shelf)

    ocr = shelves(
        {12: PAGE + " Read again.", 13: "A plate."},
        files={"able1998": "a" * 64},
        contents={"able1998": "printed"},
    )
    see(store, ocr, "able1998")
    assert status(store, kept, "s", "able1998").status is StatusKind.HELD

    annotated = shelves(files={"able1998": "b" * 64}, contents={"able1998": "printed"})
    see(store, annotated, "able1998")
    assert status(store, kept, "s", "able1998").status is StatusKind.HELD

    rewritten = shelves(files={"able1998": "c" * 64}, contents={"able1998": "other"})
    see(store, rewritten, "able1998")
    assert status(store, kept, "s", "able1998").cause.kind is CauseKind.CITED_FILE


def test_a_file_gone_or_held_back_lapses_it_and_an_outage_says_nothing(
    tmp_path,
) -> None:
    store, kept = built(tmp_path)
    cited(store, kept, "s", "able1998", [("able1998", 12)], shelves())
    cited(store, kept, "m", "baker1984", [("baker1984", 4)], shelves())

    down = Sources([Shelf(Origin.LIBRARY, {"able1998": ABLE}, down=True)])
    stamp, outage = down.file_stamp("able1998")
    assert stamp.read_status is ReadStatus.INFRASTRUCTURE and outage
    assert status(store, kept, "s", "able1998").status is StatusKind.HELD

    see(store, Sources([]), "able1998")
    s = status(store, kept, "s", "able1998")
    assert s.status is StatusKind.LAPSED
    assert s.cause.why == "the cited file able1998 is absent now"

    see(store, shelves(mismatched={"baker1984": "another title"}), "baker1984")
    s = status(store, kept, "m", "baker1984")
    assert s.status is StatusKind.LAPSED
    assert s.cause.data["read_status"] == "mismatch"


def test_a_file_that_moved_key_lapses_nothing(tmp_path) -> None:
    """The file read as another file of the work becomes its cited edition:
    the old key is absent, the new one prints the same content, and the
    reading stands."""
    store, kept = built(tmp_path)
    other = "able1998@aaaaaaaa"
    before = Sources(
        [
            Shelf(
                Origin.LIBRARY,
                {"able1998": {1: PAGE + " Translated."}, other: dict(ABLE)},
                others={"able1998": (other,)},
            )
        ]
    )
    cited(store, kept, "s", "able1998", [(other, 12)], before)
    after = shelves(files={"able1998": before.file_stamp(other)[0].file})
    gone = FileStamp(other, ReadStatus.ABSENT, "")
    see_files(store, [after.file_stamp("able1998")[0], gone], CODE)
    assert status(store, kept, "s", "able1998").status is StatusKind.HELD


def test_a_held_cited_judgment_is_not_reaffirmed(tmp_path) -> None:
    store, kept = built(tmp_path)
    record = cited(store, kept, "s", "able1998", [("able1998", 12)], shelves())
    with pytest.raises(JudgmentError, match="nothing to reaffirm"):
        reaffirm(store, kept, record.id, Depth.INFORMAL, "x", AGENT)


# --- established by its citations -----------------------------------------------------


def test_a_self_claim_is_established_by_its_citations(tmp_path) -> None:
    """Each work its block cites needs a held verified cited judgment; the
    reason names the first that lacks one. A claim resting on it waits."""
    store, kept = built(tmp_path)
    found = verdicts(store, kept)
    assert found["s"].reason.kind is Missing.UNJUDGED
    assert found["s"].reason.why == "`cited` of able1998 on s is unjudged"

    cited(store, kept, "s", "able1998", [("able1998", 12)], shelves())
    cited(store, kept, "m", "able1998", [("able1998", 12)], shelves())
    found = verdicts(store, kept)
    assert found["s"].established
    assert found["m"].reason.why == "`cited` of baker1984 on m is unjudged"
    assert found["u"].reason.why == '`true` of u "Then f is bounded." is unjudged'

    cited(store, kept, "m", "baker1984", [("baker1984", 4)], shelves())
    assert verdicts(store, kept)["m"].established


def test_a_negative_or_lapsed_reading_names_itself(tmp_path) -> None:
    store, kept = built(tmp_path)
    cited(
        store,
        kept,
        "s",
        "able1998",
        [("able1998", 12)],
        shelves(),
        verdict=Verdict.NOT_SHOWN,
    )
    reason = verdicts(store, kept)["s"].reason
    assert reason.kind is Missing.NOT_SHOWN
    assert reason.why.startswith(
        "`cited` of able1998 on s is held not-shown, read by agent"
    )


def test_a_retracted_work_leaves_its_claim_unestablished(tmp_path) -> None:
    """The cited judgment holds, but the work is retracted (worked
    case 8): the claim is not established by its citations, and a later
    resolution that is not retracted speaks instead."""
    store, kept = built(tmp_path)
    cited(store, kept, "s", "able1998", [("able1998", 12)], shelves())
    see_work(store, "able1998", "10.0000/example.1998", Publication.RETRACTED, CODE)
    s = verdicts(store, kept)["s"]
    assert s.reason.kind is Missing.RETRACTED
    assert s.reason.why == 's "' + STANDINGS["s"] + '" cites a retracted work: able1998'
    assert status(store, kept, "s", "able1998").status is StatusKind.HELD

    see_work(store, "able1998", "10.0000/example.1998", Publication.REFEREED, CODE)
    assert verdicts(store, kept)["s"].established


# --- the records and the command ------------------------------------------------------


def test_a_cited_judgment_reads_back_and_one_with_no_page_writes_none(tmp_path) -> None:
    store, kept = built(tmp_path)
    record = cited(store, kept, "s", "able1998", [("able1998", 12)], shelves())
    (back,) = RecordStore(tmp_path / "store").judgment_records().judgments
    assert back == record
    lines = record_file.written(tmp_path / "store", RecordKind.JUDGMENTS)
    assert lines[0]["subject"]["work"] == "able1998" and lines[0]["pages"]
    # a judgment that read no page writes none
    other = cited(store, kept, "m", "baker1984", verdict=Verdict.INCONCLUSIVE)
    assert (
        "pages" not in record_file.written(tmp_path / "store", RecordKind.JUDGMENTS)[1]
    )
    assert other.pages == ()


def test_the_command_reads_a_page_through_the_paper_s_sources(
    tmp_path, capsys, monkeypatch
) -> None:
    store, kept = built(tmp_path)
    paper, where = str(tmp_path), ["--store", str(tmp_path / "store")]
    words = dict(BLOCKS)["s"]
    args = [
        "judge",
        paper,
        *where,
        "--aspect",
        "cited",
        "--on",
        "s",
        "--span",
        words,
        "--work",
        "able1998",
        "--page",
        "able1998:12",
        "--verdict",
        "verified",
        "--depth",
        "informal",
        "--evidence",
        "p. 12 says it",
        "--agent",
        "model-a",
        "--agent-session",
        "s1",
    ]
    from axiomation.sources import providers

    # the plain tier, and no PDF of the work here: it cannot be judged
    monkeypatch.setattr(providers, "library_on", lambda settings: False)
    code, _, err = _run(capsys, *args)
    assert code == 1 and "able1998:12 absent" in err, err

    monkeypatch.setattr(
        evidence,
        "sources_for",
        lambda path, store=None: Sources([transient(able1998=ABLE)]),
    )
    code, out, err = _run(capsys, *args)
    assert code == 0, err
    assert "pages (1), provisional:" in out
    assert "able1998:12 transient" in out

    code, out, err = _run(capsys, "show", paper, *where, "s")
    assert code == 0, err
    assert "cited able1998" in out and "pages able1998:12, provisional" in out

    code, _, err = _run(capsys, *args[:-8], "--page", "able1998:x", *args[-8:])
    assert code == 1 and "KEY:INDEX" in err


def test_a_valid_judgment_s_page_lapses_it_when_the_file_changes(tmp_path) -> None:
    """A proof invoking a cited result: the argument reader reads its page,
    and the source's new edition lapses the `valid`."""
    proof = f"By Able {S_CITE}, f is bounded."
    blocks = [("d", "Let f be a gain."), ("t", "Then f is bounded."), ("p", proof)]
    text = " ".join(w for _, w in blocks)
    found = graph(
        text,
        blocks,
        [edge("p", "uses", "d"), edge("p", "justifies", "t")],
        [("t", "Then f is bounded.")],
    )
    found["document"]["citations"] = [
        {
            "command": "cite",
            "keys": ["able1998"],
            "locator": None,
            "at": _span(text, "p.md", S_CITE),
        }
    ]
    store = RecordStore(tmp_path / "store")
    kept = keep(store, V1, text, found)
    at = seen(store, kept)
    on = subject(at, Aspect.VALID, "p")
    with pytest.raises(JudgmentError, match="cites nowhere"):
        judge(
            store,
            kept,
            Aspect.VALID,
            on,
            Verdict.VERIFIED,
            Depth.INFORMAL,
            "x",
            AGENT,
            pages=[("baker1984", 4)],
            sources=shelves(),
        )
    judge(
        store,
        kept,
        Aspect.VALID,
        on,
        Verdict.VERIFIED,
        Depth.INFORMAL,
        "Able's Theorem 2 holds here: d is a gain",
        AGENT,
        pages=[("able1998", 12)],
        sources=shelves(),
    )
    assert status_of(store, kept, Aspect.VALID, "p").status is StatusKind.HELD
    errata = shelves({12: PAGE + " Errata."})
    see(store, errata, "able1998")
    s = status_of(store, kept, Aspect.VALID, "p")
    assert s.status is StatusKind.LAPSED and s.cause.kind is CauseKind.CITED_FILE
    # it stands again only on its work's pages read again
    lapsed = s.reads[-1].judgment.id
    with pytest.raises(JudgmentError, match="name a page of each work"):
        reaffirm(store, kept, lapsed, Depth.INFORMAL, "still holds", AGENT)
    reaffirm(
        store,
        kept,
        lapsed,
        Depth.INFORMAL,
        "the errata keep Theorem 2",
        AGENT,
        pages=[("able1998", 12)],
        sources=errata,
    )
    assert status_of(store, kept, Aspect.VALID, "p").status is StatusKind.HELD


# --- a reading or a ruling on a work its block does not cite ----------------------


def unsupported_u(tmp_path):
    """`built`'s paper with `u` ("Then f is bounded.", citing nothing)
    standing unsupported: a claim a ruling speaks for."""
    found = payload()
    for st in found["standings"]:
        if st["claim"]["text"] == STANDINGS["u"]:
            st["status"] = "unsupported"
    store = RecordStore(tmp_path / "store")
    return store, keep(store, V1, TEXT, found)


def true_on(store, kept, on: str, pages, sources, verdict=Verdict.VERIFIED):
    at = seen(store, kept)
    return judge(
        store,
        kept,
        Aspect.TRUE,
        subject(at, Aspect.TRUE, on),
        verdict,
        Depth.INFORMAL,
        "Able's bound gives it",
        AGENT,
        pages=pages,
        sources=sources,
    ).records.judgments[0]


def test_a_true_reading_rests_on_a_page_of_any_work_the_paper_cites(tmp_path) -> None:
    """`u` cites nothing; its `true` verifies on a page of `able1998`,
    which `s` and `m` cite: the reading records that work's file, `show`
    names the work as not cited at its block, and a change to the file
    lapses it. A work the paper cites nowhere is refused."""
    store, kept = built(tmp_path)
    with pytest.raises(JudgmentError, match="which the paper cites nowhere"):
        true_on(store, kept, "u", [("carter2001", 1)], shelves())
    record = true_on(store, kept, "u", [("able1998", 12)], shelves())
    assert [p.name for p in record.pages] == ["able1998:12"]
    assert [f.key for f in record.given] == ["able1998"]
    s = status_of(store, kept, Aspect.TRUE, "u")
    assert s.status is StatusKind.HELD
    line = queries.status_line(queries.View(store, kept), s)
    assert "pages able1998:12, able1998 not cited at this block" in line
    # a reading on a work its block cites names none
    true_on(store, kept, "s", [("able1998", 12)], shelves())
    s = status_of(store, kept, Aspect.TRUE, "s")
    assert "not cited" not in queries.status_line(queries.View(store, kept), s)

    see(store, shelves({12: PAGE + " Errata."}), "able1998")
    s = status_of(store, kept, Aspect.TRUE, "u")
    assert s.status is StatusKind.LAPSED and s.cause.kind is CauseKind.CITED_FILE


def test_a_reading_rests_on_a_bibliography_entry_the_paper_cites_nowhere(
    tmp_path,
) -> None:
    """The bibliography holds `carter2001`, which no block cites: `u`'s
    `true` verifies on its page, records its file, and `show` says the
    paper cites it nowhere; a key the bibliography does not hold is
    refused."""
    store, kept = built(tmp_path)
    library = Sources(
        [Shelf(Origin.LIBRARY, {"able1998": ABLE, "carter2001": {1: PAGE}})],
        listed=lambda: ("able1998", "baker1984", "carter2001"),
    )
    with pytest.raises(JudgmentError, match="its bibliography does not hold"):
        true_on(store, kept, "u", [("dunn1950", 1)], library)
    record = true_on(store, kept, "u", [("carter2001", 1)], library)
    assert [f.key for f in record.given] == ["carter2001"]
    s = status_of(store, kept, Aspect.TRUE, "u")
    line = queries.status_line(queries.View(store, kept), s)
    assert "pages carter2001:1, carter2001 cited nowhere in the paper" in line


def test_a_review_observes_a_work_a_reading_rests_on_that_the_paper_cites_nowhere(
    tmp_path,
) -> None:
    """`u`'s `true` verified on `carter2001`, an entry no block cites: the
    review's observation of the corpus reads that work's files too
    (`corpus.rested_keys`), so its changed file lapses the reading."""
    from axiomation import corpus

    store, kept = built(tmp_path)

    def library(text: str) -> Sources:
        return Sources(
            [Shelf(Origin.LIBRARY, {"able1998": ABLE, "carter2001": {1: text}})],
            listed=lambda: ("able1998", "baker1984", "carter2001"),
        )

    true_on(store, kept, "u", [("carter2001", 1)], library(PAGE))
    held = store.judgment_records()
    assert "carter2001" in corpus.rested_keys(held)
    observed = corpus.observe(
        store, kept, library(PAGE + " Errata."), corpus.rested_keys(held)
    )
    corpus.file(store, observed, corpus.OBSERVER)
    s = status_of(store, kept, Aspect.TRUE, "u")
    assert s.status is StatusKind.LAPSED and s.cause.kind is CauseKind.CITED_FILE


def test_a_proposal_s_changed_page_never_drops_it_unsettled(tmp_path) -> None:
    """A person's proposed `known` on `u`, on able1998's page: the file
    changes, and the proposal still asks the next review, which reads its
    pages again."""
    store, kept = unsupported_u(tmp_path)
    rulings.rule(
        store,
        kept,
        rulings.subject(seen(store, kept), "u"),
        RulingKind.KNOWN,
        "Able's bound",
        propose=True,
        pages=[("able1998", 12)],
        sources=shelves(),
    )
    see(store, shelves({12: PAGE + " Errata."}), "able1998")
    (ruled,) = rulings.rulings(store, kept).values()
    assert ruled.proposal is not None and ruled.proposal.asks


def test_a_cited_judgment_still_reads_its_own_work_alone(tmp_path) -> None:
    store, kept = built(tmp_path)
    with pytest.raises(JudgmentError, match="is a page of baker1984, not of"):
        cited(store, kept, "s", "able1998", [("baker1984", 4)], shelves())


def test_a_block_s_work_absent_at_the_reading_still_asks_it_again(tmp_path) -> None:
    """`s` cites `able1998`, absent when its `true` was read on `baker1984`
    alone and left inconclusive: the reading records both works' files, so
    `able1998` gaining a file asks it again."""
    store, kept = built(tmp_path)
    baker_only = Sources([Shelf(Origin.LIBRARY, {"baker1984": BAKER})])
    see(store, baker_only, "able1998", "baker1984")
    record = true_on(
        store, kept, "s", [("baker1984", 4)], baker_only, Verdict.INCONCLUSIVE
    )
    assert {f.key: f.read for f in record.given} == {
        "able1998": False,
        "baker1984": True,
    }
    assert not status_of(store, kept, Aspect.TRUE, "s").grown
    see(store, shelves(), "able1998")
    (grew,) = status_of(store, kept, Aspect.TRUE, "s").grown
    assert grew.work == "able1998"
    assert "absent then" in grew.why


def test_a_work_a_reading_read_that_gains_a_file_asks_it_again(tmp_path) -> None:
    """`u` cites nothing; its `true`, read on `baker1984` and left
    inconclusive, records that work's files: another file of `baker1984`
    asks it again, and `sources recheck` lists it on that work."""
    from axiomation.sources.recheck import ask_again

    store, kept = built(tmp_path)
    see(store, shelves(), "baker1984")
    true_on(store, kept, "u", [("baker1984", 4)], shelves(), Verdict.INCONCLUSIVE)
    assert not status_of(store, kept, Aspect.TRUE, "u").grown
    other = "baker1984@0a1b2c3d"
    see(store, Sources([Shelf(Origin.LIBRARY, {other: {1: PAGE}})]), other)
    (grew,) = status_of(store, kept, Aspect.TRUE, "u").grown
    assert grew.work == "baker1984" and other in grew.why
    (item,) = ask_again(readings(store, kept), {"baker1984"})
    assert item.work == "baker1984"
    assert not ask_again(readings(store, kept), {"able1998"})


def known_on_u(store, kept, pages=(), sources=None, **given: Any):
    at = seen(store, kept)
    return rulings.rule(
        store,
        kept,
        rulings.subject(at, "u"),
        RulingKind.KNOWN,
        "Able's bound",
        AGENT,
        pages=pages,
        sources=sources,
        **given,
    ).records.rulings[0]


def test_a_ruling_with_no_pages_files_and_speaks_as_before(tmp_path) -> None:
    store, kept = unsupported_u(tmp_path)
    ruling = known_on_u(store, kept)
    assert not ruling.pages and not ruling.given
    # its record's shape is the one filed before rulings took pages
    assert not {"pages", "given"} & set(records._ruling_data(ruling))
    (ruled,) = rulings.rulings(store, kept).values()
    assert [r.ruling.id for r in ruled.speaks] == [ruling.id]


def test_a_ruling_rests_on_its_pages_and_their_file_asks_it_again(tmp_path) -> None:
    """A `known` ruling on `u` read on `able1998`: its page stamped and its
    file given; `show` names the page; a change to the file lapses it, and
    a lapsed ruling speaks no more, so the next review asks the claim
    again. A page that failed to read, or of a work the paper cites
    nowhere, is refused."""
    store, kept = unsupported_u(tmp_path)
    with pytest.raises(JudgmentError, match="cites nowhere"):
        known_on_u(store, kept, [("carter2001", 1)], shelves())
    with pytest.raises(JudgmentError, match="a failed read is never evidence"):
        known_on_u(store, kept, [("able1998", 99)], shelves())
    ruling = known_on_u(store, kept, [("able1998", 12)], shelves())
    assert [(p.name, p.read) for p in ruling.pages] == [("able1998:12", True)]
    assert [f.key for f in ruling.given] == ["able1998"]
    view = queries.View(store, kept)
    (ruled,) = view.rulings.values()
    assert [r.ruling.id for r in ruled.speaks] == [ruling.id]
    line = queries.ruling_line(ruled.reads[0], ruled)
    assert ", pages able1998:12, " in line

    # another reading of the same file lapses nothing
    see(store, shelves(), "able1998")
    (ruled,) = rulings.rulings(store, kept).values()
    assert ruled.speaks
    see(store, shelves({12: PAGE + " Errata."}), "able1998")
    (ruled,) = rulings.rulings(store, kept).values()
    assert not ruled.speaks
    (cause,) = ruled.reads[0].walked.causes
    assert cause.kind is CauseKind.CITED_FILE


def asked_subjects(store, kept) -> set[str]:
    """The `true` subjects the argument session asks by default, by block."""
    view = queries.View(store, kept)
    return {
        view.readings.at.handle(s.subject.node)
        for s in review.subjects(view, rf.Session.ARGUMENT)
        if s.aspect is Aspect.TRUE
    }


@pytest.mark.parametrize("person", [False, True])
def test_a_ruling_lapsed_by_its_file_never_hands_back_to_an_earlier_one(
    tmp_path, person
) -> None:
    """An author rules `u` owed, then known on able1998's page; the page's
    file changes: the known lapses, the earlier owed speaks no more in its
    place, and the next review asks `u` again."""
    store, kept = unsupported_u(tmp_path)
    search_all(store, kept)
    author = Author() if person else AGENT
    true_on(store, kept, "u", [], None)
    at = seen(store, kept)
    on = rulings.subject(at, "u")
    rulings.rule(
        store, kept, on, RulingKind.OWED, "not argued", author, severity=2, fix="argue"
    )
    known = rulings.rule(
        store,
        kept,
        on,
        RulingKind.KNOWN,
        "Able's bound",
        author,
        pages=[("able1998", 12)],
        sources=shelves(),
    ).records.rulings[0]
    (ruled,) = rulings.rulings(store, kept).values()
    assert [r.ruling.id for r in ruled.speaks] == [known.id]
    assert "u" not in asked_subjects(store, kept)

    see(store, shelves({12: PAGE + " Errata."}), "able1998")
    (ruled,) = rulings.rulings(store, kept).values()
    assert not ruled.speaks and ruled.asked_again
    assert "u" in asked_subjects(store, kept)
    # the brief shows the ruling it asks again, its pages and why
    view = queries.View(store, kept)
    under = review.hint_lines(view, rf.Session.ARGUMENT, {(Aspect.TRUE, ruled.subject)})
    (line,) = [x for x in under["u"] if "ruled before" in x]
    assert f"known by {author.name} (lapsed): Able's bound, pages able1998:12" in line
    assert "; the cited file able1998 prints other content (" in line


def test_a_person_s_ruling_takes_its_pages_by_flag(tmp_path, capsys) -> None:
    """`rule --page KEY:INDEX`, read through the paper's sources and
    stamped; `--withdraw` takes none."""
    store, _ = unsupported_u(tmp_path)
    paper = tmp_path / "paper"
    paper.mkdir()
    base = [str(paper), "--store", str(store.directory)]
    sources = shelves()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(evidence, "sources_for", lambda paper, store: sources)
        code = main(
            ["rule", *base, "--on", "u", "--ruling", "known", "--reason", "Able"]
            + ["--page", "able1998:12"]
        )
    assert code == 0
    assert "able1998:12 ok" in capsys.readouterr().out
    (filed,) = store.judgment_records().rulings
    assert [p.name for p in filed.pages] == ["able1998:12"]
    assert (
        main(
            ["rule", *base, "--withdraw", filed.id, "--reason", "no"]
            + ["--page", "able1998:12"]
        )
        != 0
    )
    assert "--page" in capsys.readouterr().err


def test_the_argument_session_lays_in_its_subjects_works_and_reads_the_rest(
    tmp_path,
) -> None:
    """Its lay-in is the works its subjects cite: `d`, a definition with no
    subject, cites `carter2001`, which is not laid in. Its tools serve the
    works laid in and every other entry of the bibliography, read on
    demand; the citation session's serve its sites' works alone."""
    from types import SimpleNamespace

    found = payload()
    d_text = dict(BLOCKS)["d"]
    found["document"]["citations"].append(
        {
            "command": "cite",
            "keys": ["carter2001"],
            "locator": None,
            "at": _span(TEXT, "p.md", d_text),
        }
    )
    store = RecordStore(tmp_path / "store")
    view = queries.View(store, keep(store, V1, TEXT, found))
    at = view.readings.at
    assert all(on.node != at.node_of["d"] for _, on in offered(at))
    assert review.laid_works(view, rf.Session.ARGUMENT) == ["able1998", "baker1984"]
    laid = SimpleNamespace(
        works={"able1998": 10},
        entries=("able1998", "baker1984", "carter2001", "dunn1950"),
        reads_from=("paper", "store"),
    )
    reads = review._reads(laid, ["able1998"])
    assert reads is not None
    assert reads.works == ("able1998", "baker1984", "carter2001", "dunn1950")
    citing = SimpleNamespace(
        works={"able1998": 10}, entries=(), reads_from=("paper", "store")
    )
    served = review._reads(citing, ["able1998"])
    assert served is not None and served.works == ("able1998",)


def test_a_proposed_ruling_s_pages_reach_the_review_that_settles_it(
    tmp_path,
) -> None:
    store, kept = unsupported_u(tmp_path)
    rulings.rule(
        store,
        kept,
        rulings.subject(seen(store, kept), "u"),
        RulingKind.KNOWN,
        "Able's bound",
        propose=True,
        pages=[("able1998", 12)],
        sources=shelves(),
    )
    (ruled,) = rulings.rulings(store, kept).values()
    shown = review._proposed(ruled)
    assert shown is not None and shown.pages == ("able1998:12",)
    assert "— on pages able1998:12, which your `cited_*` tools read;" in shown.line()


def test_the_citation_session_reads_each_citation_of_each_work(tmp_path) -> None:
    """The citationcheck session's subjects: a `cited` site
    per citation and work, its span the citation's own words, the sentence
    holding it under the line, the work's pages laid in whole and only the
    cited works'; the argument session's subjects are none of these."""
    store, kept = built(tmp_path)
    view = queries.View(store, kept)
    sites, unplaced = review.citation_sites(view)
    assert not unplaced
    assert [
        (view.handle(view.readings.at.ident_of[s.subject.node]), s.subject.work)
        for s in sites
    ] == [("s", "able1998"), ("m", "able1998"), ("m", "baker1984")]
    assert sites[0].subject.words == S_CITE
    assert sites[0].sentence == f"Able shows f is small {S_CITE}."
    assert review.laid_works(view, rf.Session.CITATIONCHECK) == [
        "able1998",
        "baker1984",
    ]

    shelf = library(able1998=ABLE)
    subjects = review.subjects(
        view,
        rf.Session.CITATIONCHECK,
        works=lambda aspect, on: [on.work] if on.work in shelf.texts else [],
        candidates=lambda key, locator: (12,) if key == "able1998" else (),
    )
    assert [s.aspect for s in subjects] == [Aspect.CITED] * 3
    first, _, third = subjects
    assert first.sheet.line == f's · "{S_CITE}" → able1998'
    assert first.sheet.hops == (
        f'  ↳ "Able shows f is small {S_CITE}."',
        "  ↳ candidate pages able1998:12",
    )
    assert first.sheet.works == ("able1998",) and first.sheet.cites == ("able1998",)
    assert third.sheet.works == () and len(third.sheet.hops) == 1
    assert all(
        s.aspect is not Aspect.CITED for s in review.subjects(view, rf.Session.ARGUMENT)
    )


def test_the_gate_hands_back_a_citation_judged_with_no_page_of_its_work() -> None:
    """A `cited` verdict other than `inconclusive` names a page of the
    site's own work: where its pages are laid in, name them; where they
    are not, judge it inconclusive. A page named, and an `inconclusive`,
    pass."""

    def problems(works: tuple[str, ...], *lines: str) -> list[str]:
        subjects = [
            rf.SheetSubject(
                "s1",
                Aspect.CITED,
                f's · "{S_CITE}" → able1998',
                works=works,
                cites=("able1998",),
            )
        ]
        sheet = rf.render(rf.Session.CITATIONCHECK, subjects)
        spec = rf.spec(rf.Session.CITATIONCHECK, sheet, subjects, {}, {}, list(works))
        found = rf.read(spec, sheet + "\n".join(lines) + "\n").problems
        return [p for p in found if not p.startswith("no judge line")]

    (here,) = problems(("able1998",), "judge s1 not-shown informal — not in it")
    assert "a not-shown rests on able1998's pages: name the ones you read" in here
    (none,) = problems((), "judge s1 verified informal — Able's lemma")
    assert "able1998 has no pages here, so what it says is unread" in none
    assert not problems(
        ("able1998",), "judge s1 verified informal pages able1998:12 — it says so"
    )
    assert not problems((), "judge s1 inconclusive informal — no copy to read")


def test_the_gate_takes_another_file_of_the_work_but_not_for_a_page() -> None:
    """A page of another file of the cited work (`KEY@…`, a translation)
    is the work's: what it says may be verified on it. A page locator is
    one edition's, so a `verified` on one names a page of the cited
    edition; on the other file alone it is refused, `inconclusive` passes."""

    def problems(page: bool, *lines: str) -> list[str]:
        subjects = [
            rf.SheetSubject(
                "s1",
                Aspect.CITED,
                f's · "{S_CITE}" → able1998',
                works=("able1998",),
                cites=("able1998",),
                page_locator=page,
            )
        ]
        sheet = rf.render(rf.Session.CITATIONCHECK, subjects)
        spec = rf.spec(rf.Session.CITATIONCHECK, sheet, subjects, {}, {}, ["able1998"])
        found = rf.read(spec, sheet + "\n".join(lines) + "\n").problems
        return [p for p in found if not p.startswith("no judge line")]

    other = "able1998@0a1b2c3d"
    assert not problems(False, f"judge s1 verified informal pages {other}:2 — it says")
    (pinned,) = problems(True, f"judge s1 verified informal pages {other}:2 — it says")
    assert "a page locator is checked on the cited edition's pages" in pinned
    assert not problems(
        True, f"judge s1 verified informal pages able1998:12, {other}:2 — it says"
    )
    assert not problems(True, f"judge s1 inconclusive informal pages {other}:2 — x")
    (stray,) = problems(False, "judge s1 verified informal pages baker1984@0a:2 — x")
    assert "baker1984@0a is not the cited work (able1998)" in stray
    assert rf.page_locator("p.~207") and rf.page_locator("pp. 3--5")
    assert not rf.page_locator("\\S3") and not rf.page_locator("Proposition 2")


def test_the_citation_session_takes_no_subagent(tmp_path) -> None:
    """The citationcheck session is a queue, its finder a step of its own
    (`sources.citation_queue`): its brief defines no subagent and asks the
    same question with a forager's model or without; the forager is the
    contribution and relatedwork sessions'."""
    from types import SimpleNamespace

    from axiomation.questions import load_template

    store, state = built(tmp_path)
    # what a brief reads of an opened state: its store, the state, its sheet
    kept = SimpleNamespace(store=store, state=state, sheet="the paper's sheet")
    template = load_template("review-citationcheck")
    helped = review.brief(
        kept, rf.Session.CITATIONCHECK, template, None, forager="haiku"
    )
    plain = review.brief(kept, rf.Session.CITATIONCHECK, template, None)
    assert not helped.question.workspace.helpers
    assert helped.question.key == plain.question.key
    assert review.helpers_for(rf.Session.CITATIONCHECK, "haiku") == ()
    for session in (rf.Session.CONTRIBUTION, rf.Session.RELATEDWORK):
        (forager,) = review.helpers_for(session, "haiku")
        assert (forager.name, forager.model) == ("forager", "haiku")
    # the fieldresults session's sources are found by its queue's finder
    assert review.helpers_for(rf.Session.FIELDRESULTS, "haiku") == ()


# --- the reads ---------------------------------------------------------


def test_show_gives_each_reading_s_evidence_and_its_pages_stamps(tmp_path) -> None:
    store, kept = built(tmp_path)
    copy = shelves(origin=Origin.TRANSIENT)
    cited(store, kept, "s", "able1998", [("able1998", 12)], copy)
    view = queries.View(store, kept)

    report = queries.show(view, "s")
    (held,) = [
        i.data for i in report.sections["judgments"] if i.data["aspect"] == "cited"
    ]
    (reading,) = held["judgments"]
    assert reading["evidence"] == "the page says it" and reading["provisional"]
    assert reading["pages"] == [
        {
            "name": "able1998:12",
            "key": "able1998",
            "page_index": 12,
            "read_status": "transient",
        }
    ]


def test_a_file_changed_between_two_states_lapses_nothing_between_them(
    tmp_path,
) -> None:
    """A file that prints other content lapses the judgment at every state,
    so between two states of one version nothing newly lapsed, though the
    file's cause names the state read."""
    from axiomation.versions import Command

    store, first = built(tmp_path)
    cited(store, first, "s", "able1998", [("able1998", 12)], shelves())
    see(store, shelves({12: PAGE + " Revised."}), "able1998")
    found = payload()
    found["edges"].append(edge("d", "uses", "s"))
    second = keep(store, V1, TEXT, found, Command.EDIT, first)
    assert status(store, second, "s", "able1998").cause.state == second.id

    changes = queries.changes_report(
        store, queries.View(store, first), queries.View(store, second)
    )
    assert changes.sections["lapsed"] == []
    view = queries.View(store, second)
    (lapsed,) = queries.open_report(view).sections["lapsed"]
    assert lapsed.data["cause"]["element"] == {"kind": "cited-file", "name": "able1998"}


def test_blocks_filters_the_blocks_with_a_provisional_reading(tmp_path) -> None:
    """`blocks --provisional`, a live reading read off a copy that
    is not the version of record; one read off the library is not."""
    store, kept = built(tmp_path)
    cited(
        store,
        kept,
        "s",
        "able1998",
        [("able1998", 12)],
        shelves(origin=Origin.TRANSIENT),
    )
    cited(store, kept, "m", "baker1984", [("baker1984", 4)], shelves())
    view = queries.View(store, kept)

    found = queries.matching(view, queries.Filter(provisional=True))

    assert [view.handle(b) for b in found] == ["s"]
    assert len(queries.matching(view, queries.Filter())) == len(view.blocks)


def test_a_citation_review_records_the_works_it_could_not_read(tmp_path) -> None:
    """The library holds able1998 and not baker1984, so
    the citationcheck session reads one and is laid the other `absent`. Each
    judgment the filing makes records the works its subject may name that
    came with no page, never the agent: baker1984's `inconclusive` says
    why. It reads back, shows on the judgment's line and in `check`, and a
    second brief keys and files the same."""
    from types import SimpleNamespace

    from axiomation import check
    from axiomation.questions import load_template
    from axiomation.records import WorkUnread
    from axiomation.resolver import Resolver

    store, state = built(tmp_path)
    kept = SimpleNamespace(store=store, state=state, sheet="the paper's sheet")
    sources = Sources([library(able1998=ABLE)])
    template = load_template("review-citationcheck")
    found = review.brief(kept, rf.Session.CITATIONCHECK, template, sources)
    assert {k: r.read_status for k, r in found.unread.items()} == {
        "baker1984": ReadStatus.ABSENT
    }
    assert found.question.workspace is not None
    sheet = found.question.workspace.files[rf.REVIEW_FILE] + (
        "judge s1 verified informal pages able1998:12 — Theorem 2 says it\n"
        "judge s2 verified informal pages able1998:12 — Theorem 2 says it\n"
        "judge s3 inconclusive informal — no copy of baker1984 to read\n"
    )
    read = rf.read(found.spec, sheet)
    assert not read.problems
    view = queries.View(store, state)
    filed, problems = review.records(view, found, read, sources)
    assert not problems
    assert [j.unread for j in filed.judgments] == [
        (),
        (),
        (WorkUnread("baker1984", ReadStatus.ABSENT),),
    ]
    # what each was given: every file of each work its subject cites
    able = sources.file_stamp("able1998")[0]
    assert able.content
    absent = FileStamp("baker1984", ReadStatus.ABSENT, "")
    assert [j.given for j in filed.judgments] == [(able,), (able,), (absent,)]

    again = review.brief(kept, rf.Session.CITATIONCHECK, template, sources)
    assert again.question.key == found.question.key
    assert review.records(view, again, rf.read(again.spec, sheet), sources)[0] == filed

    Resolver(store).file(
        "review-citationcheck", "1", sheet, filed, "fake", "fake/model"
    )
    held = store.judgment_records().judgments
    assert held[-1].unread == (WorkUnread("baker1984", ReadStatus.ABSENT),)
    view = queries.View(store, state)
    shown = queries.render(queries.show(view, "m"), limit=0)
    assert "inconclusive (informal) by fake fake/model" in shown
    assert "unread baker1984 absent" in shown
    (item,) = [
        i
        for r in check.gates(view).results
        if r.gate is check.Gate.UNREVIEWED
        for i in r.items
        if i.data["kind"] == "inconclusive"
    ]
    assert item.data["cause"]["unread"] == [
        {"key": "baker1984", "read_status": "absent"}
    ]
    assert "unread baker1984 absent" in item.line


def test_a_work_held_back_is_laid_in_unread_as_mismatch(tmp_path) -> None:
    """A work whose file is not its entry's work reads as no page: the
    review lists it unread, `mismatch`, its entry saying why, and its
    judgments name it so."""
    from types import SimpleNamespace

    from axiomation.questions import load_template

    store, state = built(tmp_path)
    kept = SimpleNamespace(store=store, state=state, sheet="the paper's sheet")
    why = (
        "baker1984's file is not its entry's work: its title page (p. 4) "
        "names none of its entry's authors (Baker)"
    )
    shelf = library(able1998=ABLE, baker1984=BAKER)
    shelf.mismatched["baker1984"] = why
    found = review.brief(
        kept,
        rf.Session.CITATIONCHECK,
        load_template("review-citationcheck"),
        Sources([shelf]),
    )
    assert found.unread["baker1984"].read_status is ReadStatus.MISMATCH
    assert found.unread["baker1984"].detail == why
    served = found.question.workspace.served
    assert served is not None
    assert served.reads is None or "baker1984" not in served.reads.works


def citation_brief(tmp_path, shelf) -> review.Brief:
    from types import SimpleNamespace

    from axiomation.questions import load_template

    store, state = built(tmp_path)
    kept = SimpleNamespace(store=store, state=state, sheet="the paper's sheet")
    return review.brief(
        kept,
        rf.Session.CITATIONCHECK,
        load_template("review-citationcheck"),
        Sources([shelf]),
        directory=tmp_path / "paper",
    )


def test_the_session_reads_its_works_through_its_tool_server(tmp_path) -> None:
    """No page is laid in: what it is served names the paper, its store
    and the works that read, which its queue's server reads."""
    from axiomation.questions import Reads

    found = citation_brief(tmp_path, library(able1998=ABLE, baker1984=BAKER))

    workspace = found.question.workspace
    assert workspace is not None and workspace.served is not None
    assert not any(n.startswith("sources/") for n in workspace.files)
    assert all(PAGE not in text for text in workspace.files.values())
    assert workspace.served.reads == Reads(
        paper=str((tmp_path / "paper").resolve()),
        store=str((tmp_path / "store").resolve()),
        works=("able1998", "baker1984"),
    )


def test_the_key_moves_with_a_cited_page_and_not_otherwise(tmp_path, node_ids) -> None:
    def key(texts: dict[int, str], at: str) -> str:
        node_ids()
        shelf = library(able1998=texts, baker1984=BAKER)
        return citation_brief(tmp_path / at, shelf).question.key

    assert key(ABLE, "a") == key(ABLE, "b")
    assert key(ABLE, "a") != key({**ABLE, 13: "Another plate."}, "c")


def test_a_work_anchor_says_whether_the_paper_cites_it(tmp_path) -> None:
    """`show` marks a work anchor's key the paper cites nowhere, never one
    it cites (`able1998`, a file of it too) nor one found on the web, as a
    person may type its identifier."""
    found = payload()
    # a citation no block owns is the paper's still
    found["document"]["citations"].append(
        {
            "command": "cite",
            "keys": ["dunn1950"],
            "locator": None,
            "at": {"file": "elsewhere.md", "start": [1, 0], "end": [1, 5]},
        }
    )
    store = RecordStore(tmp_path / "store")
    view = queries.View(store, keep(store, V1, TEXT, found))
    nowhere = queries.cited_nowhere(view)
    assert nowhere("carter2001")
    assert not nowhere("dunn1950")
    for key in (
        "able1998",
        "able1998@cd",
        "arxiv:2101.00001",
        "arXiv:2101.00001",
        "doi:10.1/x",
        "DOI:10.1/X",
        "https://a.org/b",
    ):
        assert not nowhere(key), key
    assert queries.work_words("carter2001", nowhere) == (
        "work:carter2001 (cited nowhere in the paper)"
    )
