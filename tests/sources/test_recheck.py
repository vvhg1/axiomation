"""`sources recheck` over the judgments. Each cited work's files are
stamped and filed where they changed; a provisional judgment whose file the
library now holds with the same content is reaffirmed by code; a file that
prints other content, or is gone, lapses the judgments that read it.

`test_citations`' paper and its fake providers; no network, no corpus.
"""

from __future__ import annotations

import pytest

pytest.importorskip("qdrant_client")

from dataclasses import replace  # noqa: E402

from axiomation.evidence import Origin, ReadStatus, Sources  # noqa: E402
from axiomation.judgments import (  # noqa: E402
    StatusKind,
    anchored,
    file_records,
    find,
    judge,
    judged,
    judgment_record,
    seen,
    subject,
)
from axiomation.records import (  # noqa: E402
    AnchorKind,
    Aspect,
    Depth,
    Verdict,
    WorkUnread,
)
from axiomation.sources import recheck  # noqa: E402
from test_citations import (  # noqa: E402
    ABLE,
    AGENT,
    BAKER,
    built,
    cited,
    shelves,
    status,
    status_of,
    truly,
    verdicts,
)
from test_evidence import PAGE, Shelf  # noqa: E402


def _reading(monkeypatch, sources: Sources) -> None:
    monkeypatch.setattr(recheck, "sources_for", lambda paper, store=None: sources)


def both(library_text: dict[int, str] | None) -> Sources:
    """The library (holding able1998 where given) before the copy."""
    shelves_ = [Shelf(Origin.TRANSIENT, {"able1998": dict(ABLE)})]
    if library_text is not None:
        shelves_.insert(0, Shelf(Origin.LIBRARY, {"able1998": library_text}))
    return Sources(shelves_)


def test_a_copy_the_library_now_holds_unchanged_is_reaffirmed(
    tmp_path, monkeypatch
) -> None:
    store, kept = built(tmp_path)
    copy = shelves(origin=Origin.TRANSIENT)
    first = cited(store, kept, "s", "able1998", [("able1998", 12)], copy)
    assert status(store, kept, "s", "able1998").held.provisional

    _reading(monkeypatch, both(dict(ABLE)))
    payload, lines = recheck.recheck_lines(tmp_path, tmp_path / "store", None, None)
    assert lines[0].startswith("able1998  ok") and lines[0].endswith(
        "ok now, transient before"
    )
    (done,) = payload["reaffirmed"]
    assert done["judgment"] == first.id
    s = status(store, kept, "s", "able1998")
    assert s.status is StatusKind.HELD and not s.held.provisional
    assert s.held.judgment.reaffirms == first.id
    assert s.held.author.name == "axiomation sources recheck"
    assert verdicts(store, kept)["s"].established

    # nothing more to carry: a second recheck reaffirms nothing
    payload, _ = recheck.recheck_lines(tmp_path, tmp_path / "store", None, None)
    assert payload["reaffirmed"] == []


def test_a_file_the_library_holds_otherwise_lapses_its_judgment(
    tmp_path, monkeypatch
) -> None:
    store, kept = built(tmp_path)
    first = cited(
        store,
        kept,
        "s",
        "able1998",
        [("able1998", 12)],
        shelves(origin=Origin.TRANSIENT),
    )
    _reading(monkeypatch, both({12: PAGE}))
    payload, lines = recheck.recheck_lines(tmp_path, tmp_path / "store", None, None)
    assert lines[0].endswith("prints other content")
    assert payload["reaffirmed"] == [] and payload["lapsed"] == [first.id]
    assert status(store, kept, "s", "able1998").status is StatusKind.LAPSED


def test_a_file_gone_lapses_and_an_outage_lapses_nothing(tmp_path, monkeypatch) -> None:
    store, kept = built(tmp_path)
    first = cited(store, kept, "s", "able1998", [("able1998", 12)], shelves())
    _reading(monkeypatch, Sources([Shelf(Origin.LIBRARY, {}, down=True)]))
    payload, _ = recheck.recheck_lines(tmp_path, tmp_path / "store", None, None)
    assert payload["lapsed"] == [] and "able1998" in payload["undecided"]
    assert status(store, kept, "s", "able1998").status is StatusKind.HELD
    _reading(monkeypatch, Sources([]))
    payload, lines = recheck.recheck_lines(tmp_path, tmp_path / "store", None, None)
    assert lines[0] == "able1998  absent  -  absent now, ok before"
    assert payload["lapsed"] == [first.id]


def test_a_key_names_its_files_alone_and_no_store_says_so(
    tmp_path, monkeypatch
) -> None:
    store, kept = built(tmp_path)
    cited(store, kept, "s", "able1998", [("able1998", 12)], shelves())
    _reading(monkeypatch, shelves())
    payload, lines = recheck.recheck_lines(
        tmp_path, tmp_path / "store", ["baker1984"], None
    )
    assert [f["key"] for f in payload["files"]] == ["baker1984"]
    assert lines[0].startswith("baker1984  ok") and lines[0].endswith("first seen")
    _, lines = recheck.recheck_lines(tmp_path / "elsewhere", None, None, None)
    assert lines[0].endswith("keeps no state: nothing judged to recheck")


def test_the_command_rechecks(tmp_path, monkeypatch, capsys) -> None:
    from axiomation.cli import main

    store, kept = built(tmp_path)
    cited(
        store,
        kept,
        "s",
        "able1998",
        [("able1998", 12)],
        shelves(origin=Origin.TRANSIENT),
    )
    _reading(monkeypatch, both(dict(ABLE)))
    code = main(
        [
            "sources",
            "recheck",
            str(tmp_path),
            "able1998",
            "--store",
            str(tmp_path / "store"),
        ]
    )
    out = capsys.readouterr().out
    assert code == 0 and "provisional no more" in out


def unsure(store, kept, on: str, evidence: str = "the pages say nothing of it"):
    """An agent's `true` reading of `on` that did not conclude, by hand."""
    at = seen(store, kept)
    return judge(
        store,
        kept,
        Aspect.TRUE,
        subject(at, Aspect.TRUE, on),
        Verdict.INCONCLUSIVE,
        Depth.INFORMAL,
        evidence,
        AGENT,
    ).records.judgments[0]


def test_a_reading_that_did_not_verify_is_listed_once_its_work_reads_otherwise(
    tmp_path, monkeypatch
) -> None:
    """able1998's file replaced: a `true` reading of a claim whose block
    cites it that did not verify was given the old file, and the next round
    asks it again; it is listed, never lapsed. A `verified` one stands."""
    store, kept = built(tmp_path)
    _reading(monkeypatch, shelves())
    recheck.recheck_lines(tmp_path, tmp_path / "store", None, None)
    blind = unsure(store, kept, "s")
    truly(store, kept, "m", (), shelves())
    _reading(monkeypatch, shelves(able={12: PAGE + " The second edition."}))
    payload, lines = recheck.recheck_lines(
        tmp_path, tmp_path / "store", ["able1998"], None
    )
    (again,) = payload["ask_again"]
    assert again["record"] == blind.id and again["work"] == "able1998"
    assert again["command"] == recheck.NEXT_ROUND
    assert lines[-1] == (
        f"ask again: {blind.id} (true {kept.node_of('s')}, inconclusive): "
        f"able1998 reads a file it was not given; {recheck.NEXT_ROUND}"
    )
    assert status_of(store, kept, Aspect.TRUE, "s").status is StatusKind.HELD
    # the store knows the new file now: a whole recheck lists it alike
    payload, _ = recheck.recheck_lines(tmp_path, tmp_path / "store", None, None)
    assert [a["record"] for a in payload["ask_again"]] == [blind.id]


def test_a_work_its_subject_does_not_cite_asks_nothing_again(
    tmp_path, monkeypatch
) -> None:
    """A reading may name a work in its evidence that its block does not
    cite; no page of that work can settle it, so its new file asks nothing."""
    store, kept = built(tmp_path)
    _reading(monkeypatch, shelves())
    recheck.recheck_lines(tmp_path, tmp_path / "store", None, None)
    unsure(store, kept, "u", "able1998's pages here say nothing of a bound")
    _reading(monkeypatch, shelves(able={12: PAGE + " The second edition."}))
    payload, _ = recheck.recheck_lines(tmp_path, tmp_path / "store", ["able1998"], None)
    assert payload["ask_again"] == []


def test_a_reading_that_named_a_work_unread_is_listed_once_it_reads(
    tmp_path, monkeypatch
) -> None:
    """baker1984 was laid in `mismatch`, its file another work's; once the
    file reads, the reading that named it unread is listed, a work named
    or not."""
    store, kept = built(tmp_path)
    at = seen(store, kept)
    record = replace(
        judgment_record(
            at,
            Aspect.TRUE,
            subject(at, Aspect.TRUE, "m"),
            Verdict.INCONCLUSIVE,
            Depth.INFORMAL,
            "baker1984's pages say nothing of g",
        ),
        unread=(WorkUnread("baker1984", ReadStatus.MISMATCH),),
    )
    filed = file_records(store, "review-argument", judged(record), AGENT)
    j = filed.records.judgments[0]
    held = Shelf(Origin.LIBRARY, {"able1998": ABLE, "baker1984": BAKER})
    held.mismatched["baker1984"] = "baker1984's file is not its entry's work"
    _reading(monkeypatch, Sources([held]))
    payload, _ = recheck.recheck_lines(tmp_path, tmp_path / "store", None, None)
    assert payload["ask_again"] == []

    _reading(monkeypatch, shelves())
    payload, lines = recheck.recheck_lines(tmp_path, tmp_path / "store", None, None)
    (again,) = payload["ask_again"]
    assert again["record"] == j.id
    assert again["why"] == "read with baker1984 unread (mismatch); it reads now"


def test_a_true_reading_s_file_changed_lapses_it(tmp_path, monkeypatch) -> None:
    """A `true` reading's files are checked as a `cited` one's: another
    file under the key lapses what read its pages."""
    store, kept = built(tmp_path)
    paged = truly(store, kept, "m", [("able1998", 12)], shelves())
    _reading(monkeypatch, both({12: PAGE + " Another book's page."}))
    payload, lines = recheck.recheck_lines(
        tmp_path, tmp_path / "store", ["able1998"], None
    )
    assert payload["lapsed"] == [paged.id]
    assert lines[0].startswith("able1998  ok") and lines[0].endswith(
        "prints other content"
    )
    assert status_of(store, kept, Aspect.TRUE, "m").status is StatusKind.LAPSED


def test_an_open_finding_on_a_work_named_is_listed_to_settle_again(
    tmp_path, monkeypatch
) -> None:
    """A locator found printed nowhere in the old file may be printed in
    the new one: the finding is listed with what settles it."""
    store, kept = built(tmp_path)
    at = seen(store, kept)
    words = "Able shows f is small \\cite{able1998}."
    filed = find(
        store,
        kept,
        Aspect.CITED,
        [
            anchored(at, AnchorKind.SPAN, "s", words, text=words),
            anchored(at, AnchorKind.WORK, work="able1998"),
        ],
        2,
        "Theorem 9 is printed nowhere in able1998",
        "cite the theorem the work prints",
        AGENT,
    ).records.findings[0]
    _reading(monkeypatch, shelves())
    payload, _ = recheck.recheck_lines(tmp_path, tmp_path / "store", ["able1998"], None)
    (again,) = payload["ask_again"]
    assert again["record"] == filed.id and again["on"] == "finding, cited"
    assert again["command"] == f"`axiomation answer-finding` {filed.id}"


def test_a_work_re_read_unchanged_lists_nothing_beside_its_pages(
    tmp_path, monkeypatch
) -> None:
    """A forced re-ingest of the same file: its files are checked again,
    the readings that may have read it otherwise are not listed."""
    store, kept = built(tmp_path)
    truly(store, kept, "s", (), shelves())
    _reading(monkeypatch, shelves())
    payload, _ = recheck.recheck_lines(
        tmp_path, tmp_path / "store", ["able1998"], None, asked=[]
    )
    assert payload["ask_again"] == []
    with pytest.raises(TypeError):
        recheck.recheck_lines(tmp_path, tmp_path / "store", "able1998", None)


def test_an_outage_is_told_not_taken_for_a_work_that_does_not_read(
    tmp_path, monkeypatch
) -> None:
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
    _reading(monkeypatch, Sources([Shelf(Origin.LIBRARY, {}, down=True)]))
    payload, lines = recheck.recheck_lines(tmp_path, tmp_path / "store", None, None)
    assert payload["ask_again"] == []
    assert payload["undecided"] == {
        "able1998": "the library failed: the shelf is down",
        "baker1984": "the library failed: the shelf is down",
    }
    assert (
        "could not tell whether baker1984 reads: the library failed: the shelf is down"
    ) in lines


def _workspace(tmp_path):
    """A workspace sharing one bibliography: two papers keeping a state,
    one keeping none, one citing from a bibliography of its own."""
    from axiomation.reader.includes import STORE_DIRECTORY
    from axiomation.records import RecordStore
    from test_citations import TEXT, payload
    from test_versions import V1, keep

    (tmp_path / "axiomation.toml").write_text(
        '[workspace]\npapers = "papers/*"\n\n[sources]\nbib = "references.bib"\n',
        encoding="utf-8",
    )
    (tmp_path / "references.bib").write_text("", encoding="utf-8")
    stores = {}
    for name in ("alpha", "beta", "gamma", "own"):
        (tmp_path / "papers" / name).mkdir(parents=True)
    for name in ("alpha", "beta", "own"):
        store = RecordStore(tmp_path / "papers" / name / STORE_DIRECTORY)
        stores[name] = (store, keep(store, V1, TEXT, payload()))
    (tmp_path / "papers" / "own" / "axiomation.toml").write_text(
        '[sources]\nbib = "mine.bib"\n', encoding="utf-8"
    )
    return stores


def test_a_work_confirmed_anywhere_lists_what_to_ask_again_in_every_paper(
    tmp_path, monkeypatch
) -> None:
    """The confirmation is the shared bibliography's: run from the
    workspace's root or from one paper, every paper reading through it is
    rechecked, each named; the one with a bibliography of its own is not."""
    stores = _workspace(tmp_path)
    _reading(monkeypatch, shelves())
    recheck.recheck_shared(tmp_path, None, ["able1998"])
    recheck.recheck_lines(tmp_path / "papers" / "own", None, None, None)
    blind = {name: unsure(*stores[name], "s") for name in ("alpha", "beta")}
    unsure(*stores["own"], "s")
    _reading(monkeypatch, shelves(able={12: PAGE + " The second edition."}))
    for directory in (tmp_path, tmp_path / "papers" / "beta"):
        payload, lines = recheck.recheck_shared(directory, None, ["able1998"])
        assert sorted(payload["papers"]) == ["papers/alpha", "papers/beta"]
        assert payload["no_state"] == ["papers/gamma"]
        for name in ("alpha", "beta"):
            asked = payload["papers"][f"papers/{name}"]["ask_again"]
            assert [a["record"] for a in asked] == [blind[name].id]
            assert f"papers/{name}:" in lines
        assert lines[-1] == "keeping no state, nothing judged to recheck: papers/gamma"
    _, lines = recheck.recheck_shared(tmp_path / "papers" / "beta", None, ["able1998"])
    assert lines[0] == "papers/beta:"  # the paper the command was given first


def test_a_paper_outside_a_workspace_rechecks_itself(tmp_path, monkeypatch) -> None:
    store, kept = built(tmp_path)
    _reading(monkeypatch, shelves())
    recheck.recheck_lines(tmp_path, tmp_path / "store", None, None)
    blind = unsure(store, kept, "s")
    _reading(monkeypatch, shelves(able={12: PAGE + " The second edition."}))
    payload, lines = recheck.recheck_shared(tmp_path, tmp_path / "store", ["able1998"])
    (only,) = payload["papers"].values()
    assert [a["record"] for a in only["ask_again"]] == [blind.id]
    assert lines[0] == f"{tmp_path.name}:"


def test_what_an_ingest_may_lapse_is_listed_in_every_paper_and_nothing_filed(
    tmp_path,
) -> None:
    """Before an ingest reads works again, the judgments in force that read
    their pages are named in each paper sharing the bibliography; no store
    is written."""
    stores = _workspace(tmp_path)
    held = cited(*stores["alpha"], "s", "able1998", [("able1998", 12)], shelves())
    files = {
        path: path.read_bytes()
        for path in (tmp_path / "papers").rglob("*")
        if path.is_file()
    }

    payload, lines = recheck.judged_shared(tmp_path, None, ["able1998"])
    unread, _ = recheck.judged_shared(tmp_path, None, ["baker1984"])

    assert payload["papers"] == {"papers/alpha": {"able1998": [held.id]}}
    assert lines == [
        "papers/alpha: the judgments that read a page of a work read again, "
        "which lapse where its file prints other content: 1",
        f"  able1998: {held.id}",
    ]
    assert unread["papers"] == {}
    assert files == {
        path: path.read_bytes()
        for path in (tmp_path / "papers").rglob("*")
        if path.is_file()
    }


def test_a_work_given_a_file_asks_again_what_did_not_verify_it(
    tmp_path, monkeypatch
) -> None:
    """A translation ingested beside the cited edition: a reading that did
    not find the claim may find it there and is listed; one that verified
    it on the cited edition stands. A reading that records no files was
    given the cited edition alone."""
    store, kept = built(tmp_path)
    _reading(monkeypatch, shelves())
    recheck.recheck_lines(tmp_path, tmp_path / "store", None, None)
    missed = cited(
        store,
        kept,
        "s",
        "able1998",
        [("able1998", 12)],
        shelves(),
        verdict=Verdict.NOT_SHOWN,
    )
    held = cited(store, kept, "m", "able1998", [("able1998", 12)], shelves())
    at = seen(store, kept)
    before = file_records(
        store,
        "review-argument",
        judged(
            judgment_record(
                at,
                Aspect.TRUE,
                subject(at, Aspect.TRUE, "s"),
                Verdict.INCONCLUSIVE,
                Depth.INFORMAL,
                "able1998 says nothing of it",
            )
        ),
        AGENT,
    ).records.judgments[0]
    other = "able1998@0a1b2c3d"
    shelf = Shelf(
        Origin.LIBRARY,
        {"able1998": dict(ABLE), "baker1984": BAKER, other: {2: PAGE}},
        others={"able1998": (other,)},
    )
    _reading(monkeypatch, Sources([shelf]))
    payload, lines = recheck.recheck_lines(tmp_path, tmp_path / "store", [other], None)
    again = {a["record"]: a for a in payload["ask_again"]}
    assert sorted(again) == sorted([missed.id, before.id])
    assert again[missed.id]["why"] == f"{other} reads a file it was not given"
    assert again[before.id]["why"] == f"able1998 holds a file it was not given, {other}"
    assert held.id not in again


def test_a_ruling_on_a_file_the_library_holds_otherwise_is_listed_lapsed(
    tmp_path, monkeypatch
) -> None:
    """A `known` ruling on `u` rests on able1998's page: a recheck that
    finds the file printing other content lists it lapsed."""
    from test_citations import known_on_u, unsupported_u

    store, kept = unsupported_u(tmp_path)
    ruling = known_on_u(store, kept, [("able1998", 12)], shelves())
    _reading(monkeypatch, both({12: PAGE + " Errata."}))
    payload, lines = recheck.recheck_lines(tmp_path, tmp_path / "store", None, None)
    assert payload["rulings_lapsed"] == [ruling.id]
    assert f"lapsed by a file: ruling {ruling.id}, which speaks no more" in lines
