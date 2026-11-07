"""The cited works as the store knows them (`corpus`): each file stamped,
its content hashed once, filed where it changed; a file that moved to
another key of its work lapses nothing; a reading that did not verify is
asked again where its evidence grew, by the round and in its status
(docs/model.md § 8).

`test_citations`' paper and the fake providers of `test_evidence`; no
network, no corpus.
"""

from __future__ import annotations

from dataclasses import replace

from axiomation import check, corpus, evidence, queries, review
from axiomation.evidence import Origin, PageStamp, ReadStatus, Sources
from axiomation.judgments import (
    Growth,
    StatusKind,
    file_records,
    judge,
    judged,
    judgment_record,
    readings,
    seen,
    subject,
)
from axiomation.questions import review_file as rf
from axiomation.records import Aspect, Depth, Verdict, WorkUnread
from test_citations import ABLE, AGENT, BAKER, BLOCKS, built, cited, status, status_of
from test_evidence import PAGE, Shelf

OTHER = "able1998@0a1b2c3d"
#: another edition's page 12, in other words
EDITION = {12: PAGE + " The second edition.", 13: "A plate."}


def shelf(able=ABLE, other=None, down=False) -> Sources:
    """The library: able1998 under its key, and another file of it where
    given; baker1984."""
    texts = {"able1998": dict(able), "baker1984": dict(BAKER)}
    others = {}
    if other is not None:
        texts[OTHER] = dict(other)
        others["able1998"] = (OTHER,)
    return Sources([Shelf(Origin.LIBRARY, texts, down=down, others=others)])


def observed(store, kept, sources: Sources):
    found = corpus.observe(store, kept, sources)
    corpus.file(store, found, corpus.OBSERVER)
    return found


def unsure(store, kept, on: str, author=AGENT):
    at = seen(store, kept)
    return judge(
        store,
        kept,
        Aspect.TRUE,
        subject(at, Aspect.TRUE, on),
        Verdict.INCONCLUSIVE,
        Depth.INFORMAL,
        "the pages say nothing of it",
        author,
    ).records.judgments[0]


# --- the observation ------------------------------------------------------------


def test_the_first_observation_files_every_cited_file_and_the_next_nothing(
    tmp_path,
) -> None:
    store, kept = built(tmp_path)
    first = observed(store, kept, shelf())
    assert sorted(s.key for s in first.changed) == ["able1998", "baker1984"]
    assert all(s.read_status is ReadStatus.OK and s.file for s in first.changed)
    assert first.lines()[0] == "2 files seen, the store's first sight of them"
    again = corpus.observe(store, kept, shelf())
    assert not again.news and corpus.file(store, again, corpus.OBSERVER) is None
    # a file held back is a change of its stamp, its SHA-256 the same
    held = shelf()
    held.providers[0].mismatched["baker1984"] = "not its entry's work"
    (changed,) = corpus.observe(store, kept, held).changed
    assert changed.key == "baker1984" and changed.read_status is ReadStatus.MISMATCH


def test_an_outage_files_nothing_and_says_so(tmp_path) -> None:
    store, kept = built(tmp_path)
    found = corpus.observe(store, kept, shelf(down=True))
    assert not found.news
    assert found.undecided == {
        "able1998": "the library failed: the shelf is down",
        "baker1984": "the library failed: the shelf is down",
    }
    # a file the store knows, which an outage cannot list: never absent
    observed(store, kept, shelf(other=EDITION))
    found = corpus.observe(store, kept, shelf(down=True))
    assert not found.news and OTHER in found.undecided


class Counted(Shelf):
    """A shelf that counts the files it hashes."""

    hashed: list[str]

    def content(self, key: str) -> str:
        self.hashed.append(key)
        return super().content(key)


def test_a_file_s_content_is_hashed_once_and_a_stamp_without_it_gains_it(
    tmp_path,
) -> None:
    """The store keeps each file's content hash with its SHA-256: a later
    observation reads it there. A file stamped without it (not on this
    machine to hash then) gains it at the next observation, said as such,
    and its readings stand."""
    store, kept = built(tmp_path)
    texts = {"able1998": dict(ABLE), "baker1984": dict(BAKER)}
    counted = Counted(Origin.LIBRARY, texts)
    counted.hashed = []
    observed(store, kept, Sources([counted]))
    assert sorted(counted.hashed) == ["able1998", "baker1984"]
    counted.hashed.clear()
    assert not observed(store, kept, Sources([counted])).news
    assert counted.hashed == []

    # stamps of files the store could not hash carry none
    store, kept = built(tmp_path / "older")
    stamps = [replace(shelf().file_stamp(k)[0], content="") for k in texts]
    corpus.file(store, corpus.Observed(tuple(stamps), tuple(stamps)), corpus.OBSERVER)
    at = seen(store, kept)
    words = dict(BLOCKS)["s"]
    on = subject(at, Aspect.CITED, "s", span=words, text=words, work="able1998")
    page = PageStamp("able1998", 12, ReadStatus.OK)
    record = judgment_record(
        at, Aspect.CITED, on, Verdict.VERIFIED, Depth.INFORMAL, "p. 12", (page,)
    )
    file_records(
        store,
        "review-citationcheck",
        judged(replace(record, given=(stamps[0],))),
        AGENT,
    )
    found = observed(store, kept, shelf())
    assert found.first_hashed == {"able1998", "baker1984"}
    assert found.lines() == ["2 files' content hashed, the store's first"]
    assert status(store, kept, "s", "able1998").status is StatusKind.HELD


# --- a reading is its file's --------------------------------------------------------


def test_a_file_moved_to_another_key_lapses_nothing(tmp_path) -> None:
    """The file a reading read moves under another key of its work and
    another edition takes the work's key: the file it read prints the same,
    so it stands. Once no file of the work prints it, it lapses."""
    store, kept = built(tmp_path)
    first = cited(store, kept, "s", "able1998", [("able1998", 12)], shelf())
    observed(store, kept, shelf())
    observed(store, kept, shelf(able=EDITION, other=ABLE))
    s = status(store, kept, "s", "able1998")
    assert s.status is StatusKind.HELD and s.held.judgment.id == first.id

    observed(store, kept, shelf(able=EDITION, other=EDITION))
    assert status(store, kept, "s", "able1998").status is StatusKind.LAPSED


def test_a_page_of_a_file_not_given_rests_on_the_one_the_store_saw(
    tmp_path,
) -> None:
    """A reading given no file of the work it read a page of (a source's
    outage left it unstamped when it was laid in): the page is of the file
    the store last saw under its key when it was filed, so that file
    printing other content lapses it."""
    store, kept = built(tmp_path)
    observed(store, kept, shelf())
    record = cited(store, kept, "s", "able1998", [("able1998", 12)], shelf())
    bare = replace(record, id="", answer=None, given=())
    file_records(store, "review-citationcheck", judged(bare), AGENT)
    observed(store, kept, shelf())
    assert all(r.live for r in readings(store, kept).reads)
    observed(store, kept, shelf(able=EDITION))
    assert not any(r.live for r in readings(store, kept).reads)


def test_a_reading_filed_before_the_store_saw_its_file_rests_on_the_first_seen_after(
    tmp_path,
) -> None:
    # given no file, filed before the store saw any
    store, kept = built(tmp_path)
    at = seen(store, kept)
    words = dict(BLOCKS)["s"]
    on = subject(at, Aspect.CITED, "s", span=words, text=words, work="able1998")
    page = PageStamp("able1998", 12, ReadStatus.OK)
    record = judgment_record(
        at, Aspect.CITED, on, Verdict.VERIFIED, Depth.INFORMAL, "p. 12", (page,)
    )
    file_records(store, "review-citationcheck", judged(record), AGENT)
    observed(store, kept, shelf())
    observed(store, kept, shelf())
    (read,) = readings(store, kept).reads
    assert read.live and read.judgment.given == ()
    observed(store, kept, shelf(able=EDITION))
    (read,) = readings(store, kept).reads
    assert not read.live


# --- the evidence grew --------------------------------------------------------------


def test_a_reading_that_did_not_verify_is_asked_again_when_a_file_reads(
    tmp_path,
) -> None:
    """A `not-shown` on able1998, given its one file: a translation
    filed beside it asks it again, the reading still speaking; a
    `verified` one stands; a later reading given the new file settles it."""
    store, kept = built(tmp_path)
    observed(store, kept, shelf())
    missed = cited(
        store,
        kept,
        "s",
        "able1998",
        [("able1998", 12)],
        shelf(),
        verdict=Verdict.NOT_SHOWN,
    )
    assert missed.given == (shelf().file_stamp("able1998")[0],)
    held = cited(store, kept, "m", "able1998", [("able1998", 12)], shelf())
    assert status(store, kept, "s", "able1998").grown == ()

    observed(store, kept, shelf(other=EDITION))
    s = status(store, kept, "s", "able1998")
    assert s.status is StatusKind.HELD and s.held.judgment.id == missed.id
    assert s.grown == (Growth("able1998", f"{OTHER} reads a file it was not given"),)
    assert status(store, kept, "m", "able1998").grown == ()
    assert held.id == status(store, kept, "m", "able1998").held.judgment.id

    # another author's later reading, given the corpus as it is, settles it
    from axiomation.judgments import Author

    cited(
        store,
        kept,
        "s",
        "able1998",
        [("able1998", 12)],
        shelf(other=EDITION),
        verdict=Verdict.INCONCLUSIVE,
        author=Author("agent", "model-b", "s2"),
    )
    assert status(store, kept, "s", "able1998").grown == ()


def test_a_work_unread_that_reads_and_a_cited_edition_that_changed_ask_again(
    tmp_path,
) -> None:
    store, kept = built(tmp_path)
    held = shelf()
    held.providers[0].mismatched["baker1984"] = "not its entry's work"
    observed(store, kept, held)
    blind = unsure(store, kept, "m")
    assert {g.key: g.read_status for g in blind.given} == {
        "able1998": ReadStatus.OK,
        "baker1984": ReadStatus.MISMATCH,
    }
    observed(store, kept, shelf())
    s = status_of(store, kept, Aspect.TRUE, "m")
    assert s.grown == (
        Growth("baker1984", "baker1984 reads a file it was not given (mismatch then)"),
    )

    # given both files, the cited edition then becomes the other one
    store, kept = built(tmp_path / "edition")
    observed(store, kept, shelf(other=EDITION))
    unsure(store, kept, "s")
    observed(store, kept, shelf(able=EDITION, other=ABLE))
    assert status_of(store, kept, Aspect.TRUE, "s").grown == (
        Growth(
            "able1998",
            f"able1998's cited edition is now the file it was given as {OTHER}",
        ),
    )


def test_a_reading_that_records_no_files_keeps_the_earlier_rule(tmp_path) -> None:
    """Filed before readings recorded their files: given each work's cited
    edition alone, asked again where a work it named unread reads or a
    work holds another file; nothing else is known of it."""
    store, kept = built(tmp_path)
    at = seen(store, kept)
    record = replace(
        judgment_record(
            at,
            Aspect.TRUE,
            subject(at, Aspect.TRUE, "m"),
            Verdict.INCONCLUSIVE,
            Depth.INFORMAL,
            "baker1984 unread",
        ),
        unread=(WorkUnread("baker1984", ReadStatus.ABSENT),),
    )
    file_records(store, "review-argument", judged(record), AGENT)
    observed(store, kept, shelf(able=EDITION))
    assert status_of(store, kept, Aspect.TRUE, "m").grown == (
        Growth("baker1984", "read with baker1984 unread (absent); it reads now"),
    )
    observed(store, kept, shelf(able=EDITION, other=ABLE))
    assert [g.work for g in status_of(store, kept, Aspect.TRUE, "m").grown] == [
        "able1998",
        "baker1984",
    ]


# --- what the round asks, and what the author sees ---------------------------------


def test_the_round_asks_a_reading_whose_evidence_grew(tmp_path) -> None:
    """The default asks the reading that did not verify once its evidence
    grew, and holds it back before."""
    store, kept = built(tmp_path)
    observed(store, kept, shelf())
    blind = unsure(store, kept, "s")
    for on in ("m", "u"):
        unsure(store, kept, on)

    def asked(select: review.Selection) -> list:
        view = queries.View(store, kept)
        found, _ = review.selected(view, rf.Session.ARGUMENT, select)
        return [s.subject for s in found if s.sheet.aspect is Aspect.TRUE]

    assert asked(review.Selection.OPEN) == []
    observed(store, kept, shelf(other=EDITION))
    # "m" cites able1998 too; "u" cites nothing, and stands held back
    grew = [blind.subject, status_of(store, kept, Aspect.TRUE, "m").subject]
    assert asked(review.Selection.OPEN) == grew


def test_check_and_the_status_say_a_reading_is_asked_again(tmp_path) -> None:
    store, kept = built(tmp_path)
    observed(store, kept, shelf())
    (site,) = [
        s.subject
        for s in review.citation_sites(queries.View(store, kept))[0]
        if s.subject.node == kept.node_of("s")
    ]
    judge(
        store,
        kept,
        Aspect.CITED,
        site,
        Verdict.INCONCLUSIVE,
        Depth.INFORMAL,
        "page 12 says nothing of f",
        AGENT,
        pages=[("able1998", 12)],
        sources=shelf(),
    )
    observed(store, kept, shelf(other=EDITION))
    view = queries.View(store, kept)
    (item,) = [
        i
        for r in check.gates(view).results
        if r.gate is check.Gate.UNREVIEWED
        for i in r.items
        if i.data["kind"] == "inconclusive"
    ]
    assert item.data["cause"]["grown"] == [
        {"work": "able1998", "why": f"{OTHER} reads a file it was not given"}
    ]
    assert item.data["clears"].startswith(
        "asked again by the next round (`axiomation sync`): "
    )
    nexts = [f.next or "" for f in view.claim_statuses]
    assert any(n.startswith("asked again by the next round") for n in nexts)
    assert [s.status for s in readings(store, kept).statuses if s.grown] == [
        StatusKind.HELD
    ]


def test_a_dry_run_compares_on_a_scratch_copy_and_files_nothing(
    tmp_path, monkeypatch, capsys
) -> None:
    from axiomation.cli import main

    store, kept = built(tmp_path)
    paper = tmp_path / "paper"
    paper.mkdir()
    monkeypatch.setattr(evidence, "sources_for", lambda paper, store: shelf())
    before = {p.name: p.read_bytes() for p in store.directory.iterdir() if p.is_file()}
    where = [str(paper), "--store", str(store.directory), "--session", "citationcheck"]
    main(["review", *where, "--dry-run", "--prompts", str(tmp_path / "d")])
    out = capsys.readouterr().out
    assert "cited works: 2 files seen, the store's first sight of them" in out
    after = {p.name: p.read_bytes() for p in store.directory.iterdir() if p.is_file()}
    assert after == before


def test_a_key_the_work_no_longer_lists_is_absent_and_asks_nothing(tmp_path) -> None:
    """The corpus keeps a key's pages the work no longer lists as a file (a
    cited edition not yet moved, a file dropped from its entry): a review
    lays it in no more, so it is filed `absent`, its pages with it, and no
    reading is asked again for it, round after round."""
    store, kept = built(tmp_path)
    observed(store, kept, shelf(other=EDITION))
    unsure(store, kept, "s")
    unlisted = shelf(other=EDITION)
    unlisted.providers[0].others.clear()
    found = observed(store, kept, unlisted)
    assert [(s.key, s.read_status) for s in found.changed] == [
        (OTHER, ReadStatus.ABSENT)
    ]
    assert status_of(store, kept, Aspect.TRUE, "s").grown == ()
    assert not corpus.observe(store, kept, unlisted).news


def test_words_moved_to_a_file_that_left_the_work_lapse_their_reading(
    tmp_path,
) -> None:
    store, kept = built(tmp_path)
    cited(store, kept, "s", "able1998", [("able1998", 12)], shelf())
    observed(store, kept, shelf())
    observed(store, kept, shelf(able=EDITION, other=ABLE))
    assert status(store, kept, "s", "able1998").status is StatusKind.HELD
    gone = shelf(able=EDITION, other=ABLE)
    gone.providers[0].others.clear()
    observed(store, kept, gone)
    assert status(store, kept, "s", "able1998").status is StatusKind.LAPSED


def test_an_outage_never_files_a_fetched_copy_for_the_library_s_file(
    tmp_path,
) -> None:
    """The library down and a copy fetched before still cached: the copy
    is no stand-in for the file the library holds, so nothing is filed and
    no reading lapses."""
    store, kept = built(tmp_path)
    cited(store, kept, "s", "able1998", [("able1998", 12)], shelf())
    observed(store, kept, shelf())
    down = Sources(
        [
            Shelf(Origin.LIBRARY, {}, down=True),
            Shelf(Origin.TRANSIENT, {"able1998": dict(EDITION)}),
        ]
    )
    stamp, outage = down.file_stamp("able1998")
    assert stamp.read_status is ReadStatus.INFRASTRUCTURE and outage
    found = corpus.observe(store, kept, down)
    assert not found.news and "able1998" in found.undecided
    assert status(store, kept, "s", "able1998").status is StatusKind.HELD


def test_a_file_changed_before_its_reading_was_filed_lapses_it(tmp_path) -> None:
    """A review lays in one file; another is filed under the key before
    its reading is: the reading rests on the file it was given, and the
    store's latest, which it never read, lapses it."""
    store, kept = built(tmp_path)
    observed(store, kept, shelf())
    laid = shelf().file_stamp("able1998")[0]
    observed(store, kept, shelf(able=EDITION))
    at = seen(store, kept)
    words = dict(BLOCKS)["s"]
    on = subject(at, Aspect.CITED, "s", span=words, text=words, work="able1998")
    page = PageStamp("able1998", 12, ReadStatus.OK)
    record = judgment_record(
        at, Aspect.CITED, on, Verdict.VERIFIED, Depth.INFORMAL, "p. 12", (page,)
    )
    file_records(
        store, "review-citationcheck", judged(replace(record, given=(laid,))), AGENT
    )
    (read,) = readings(store, kept).reads
    assert not read.live


def test_a_reading_by_hand_is_given_its_file_as_it_reads_now(tmp_path) -> None:
    """The store last saw the work absent; a person reads its page now: the
    reading is given the file as it reads, filed with it, and the next
    observation lapses nothing."""
    store, kept = built(tmp_path)
    observed(store, kept, Sources([]))
    record = cited(store, kept, "s", "able1998", [("able1998", 12)], shelf())
    (given,) = [g for g in record.given if g.key == "able1998"]
    assert given.read_status is ReadStatus.OK and given.content
    observed(store, kept, shelf())
    assert status(store, kept, "s", "able1998").status is StatusKind.HELD
