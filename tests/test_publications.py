"""A cited work's publication in the core: its record read and written
(an old one, its retraction alone, included), a person's setting speaking
over the lookups, a person's acceptance, the works a claim rests on, the
`cited-publications` gate and the claim's status.

`test_citations`' paper: `s` cites able1998, `m` able1998 and
baker1984, `u` uses `s`. No network, no model.
"""

from __future__ import annotations

import pytest

from axiomation import publications, queries
from axiomation.check import Cause, Gate, Mode, Outcome, gates, report
from axiomation.claim_status import ClaimStatus
from axiomation.established import Missing
from axiomation.judgments import Author, JudgmentError, readings, see_work
from axiomation.records import (
    Match,
    Publication,
    RecordKind,
    VersionOfRecord,
)
from record_file import written
from test_citations import AGENT, built, cited, shelves, truly, verdicts

LOOKUP = Author("axiomation", "sources publications")
OWNER = Author("person", "Ada")
ICLR = VersionOfRecord(
    "ICLR 2024 poster",
    "2024",
    None,
    "https://openreview.net/forum?id=x",
    "openreview",
    Match.TITLE_AND_AUTHORS,
)


def established_all(tmp_path):
    """`s` by its citation of able1998; `u` by its `true`, resting on `s`;
    `m` by a `true` stamped with baker1984's page."""
    store, kept = built(tmp_path)
    cited(store, kept, "s", "able1998", [("able1998", 12)], shelves())
    truly(store, kept, "u", (), shelves())
    truly(store, kept, "m", [("baker1984", 4)], shelves())
    return store, kept


def gate(store, kept, mode=Mode.WARN):
    verdict = gates(queries.View(store, kept), {Gate.CITED_PUBLICATIONS: mode})
    return next(r for r in verdict.results if r.gate is Gate.CITED_PUBLICATIONS)


# --- the records ----------------------------------------------------------------------


def test_a_publication_is_written_and_read_with_its_versions_of_record(
    tmp_path,
) -> None:
    store, kept = built(tmp_path)
    see_work(
        store,
        "able1998",
        "10.48550/arxiv.2105.21050",
        Publication.PREPRINT_PUBLISHED,
        LOOKUP,
        "openalex: preprint in a repository",
        [ICLR],
    )
    (seen,) = store.judgment_records().works
    assert seen.publication is Publication.PREPRINT_PUBLISHED
    assert seen.records == (ICLR,) and not seen.retracted
    line = written(store.directory, RecordKind.WORKS_SEEN)[0]
    assert line["publication"] == "preprint-published" and "retracted" not in line


def test_a_person_s_publication_speaks_over_every_lookup(tmp_path) -> None:
    store, kept = built(tmp_path)
    see_work(store, "able1998", None, Publication.PREPRINT, LOOKUP, "openalex")
    see_work(store, "able1998", None, Publication.REFEREED, OWNER, "a journal")
    see_work(store, "able1998", None, Publication.PREPRINT, LOOKUP, "openalex")
    assert readings(store, kept).works["able1998"].publication is (Publication.REFEREED)
    see_work(store, "able1998", None, Publication.BOOK, OWNER, "a monograph")
    assert readings(store, kept).works["able1998"].publication is Publication.BOOK


def test_an_acceptance_is_a_person_s_and_its_latest_speaks(tmp_path) -> None:
    store, _ = built(tmp_path)
    with pytest.raises(JudgmentError, match="only a person"):
        publications.accept(store, "able1998", "fine", AGENT)
    with pytest.raises(JudgmentError, match="say why"):
        publications.accept(store, "able1998", " ", OWNER)
    publications.accept(store, "able1998", "the result is standard", OWNER)
    ((word, author),) = publications.accepted(store).values()
    assert word.reason == "the result is standard" and author == OWNER
    publications.accept(store, "able1998", "cite the book", OWNER, withdrawn=True)
    assert publications.accepted(store) == {}


# --- what a claim rests on ------------------------------------------------------------


def test_a_claim_rests_on_the_works_its_way_reads(tmp_path) -> None:
    """By its citations, each work its block cites; by a reading, the works
    whose pages it stamped; and the works of each claim it asks."""
    store, kept = established_all(tmp_path)
    found = verdicts(store, kept)
    assert found["s"].works == ("able1998",)
    assert found["u"].works == ("able1998",)
    assert found["m"].works == ("baker1984",)


@pytest.mark.parametrize(
    "publication", [p for p in Publication if p is not Publication.RETRACTED]
)
@pytest.mark.parametrize("author", [LOOKUP, OWNER])
def test_a_publication_but_a_retraction_never_moves_a_verdict(
    tmp_path, publication, author
) -> None:
    store, kept = established_all(tmp_path)
    before = {b: e.reason for b, e in verdicts(store, kept).items()}
    for key in ("able1998", "baker1984"):
        see_work(store, key, None, publication, author, "set")
    assert {b: e.reason for b, e in verdicts(store, kept).items()} == before


def test_a_retraction_found_after_a_person_s_setting_speaks(tmp_path) -> None:
    """A person set the work refereed; a lookup later files its retraction:
    the retraction speaks until the person sets the work again."""
    store, kept = established_all(tmp_path)
    see_work(store, "able1998", None, Publication.REFEREED, OWNER, "a journal")
    see_work(store, "able1998", None, Publication.RETRACTED, LOOKUP, "crossref")
    assert verdicts(store, kept)["s"].reason.kind is Missing.RETRACTED
    defects = gates(queries.View(store, kept)).results[1]
    assert defects.gate is Gate.DEFECTS
    assert [i.data["kind"] for i in defects.items] == ["retracted"]
    see_work(store, "able1998", None, Publication.REFEREED, OWNER, "a wrong match")
    assert verdicts(store, kept)["s"].established


# --- the gate -------------------------------------------------------------------------


def test_a_preprint_cited_warns_once_with_the_claims_resting_on_it(tmp_path) -> None:
    store, kept = established_all(tmp_path)
    see_work(store, "able1998", None, Publication.PREPRINT, LOOKUP, "openalex")
    see_work(store, "baker1984", None, Publication.REFEREED, LOOKUP, "crossref")
    found = gate(store, kept)
    assert found.outcome is Outcome.WARN and found.mode is Mode.WARN
    (item,) = found.items
    assert item.data["kind"] == Cause.PUBLICATION.value
    assert item.line == (
        "able1998 is a preprint, no version of record found; cited by s, m; "
        "2 established claims rest on it"
    )
    assert [a["node"] for a in item.data["anchors"]] == [
        kept.node_of("s"),
        kept.node_of("m"),
    ]
    assert item.data["cause"]["resting"] == 2


@pytest.mark.parametrize(
    "publication",
    [
        Publication.REFEREED,
        Publication.BOOK,
        Publication.PREPRINT_PUBLISHED,
        Publication.UNKNOWN,
    ],
)
def test_ground_a_referee_takes_does_not_warn(tmp_path, publication) -> None:
    store, kept = established_all(tmp_path)
    for key in ("able1998", "baker1984"):
        see_work(store, key, None, publication, LOOKUP, "crossref")
    assert gate(store, kept).outcome is Outcome.PASS


def test_a_thesis_and_an_erratum_warn_ordered_by_what_rests_on_them(tmp_path) -> None:
    store, kept = established_all(tmp_path)
    see_work(store, "baker1984", None, Publication.ERRATUM, LOOKUP, "crossref")
    see_work(store, "able1998", None, Publication.THESIS_OR_REPORT, LOOKUP, "x")
    found = gate(store, kept)
    assert [i.data["cause"]["work"] for i in found.items] == ["able1998", "baker1984"]
    assert "erratum read against what the paper takes" in found.items[1].data["clears"]


def test_a_work_accepted_is_listed_apart_and_the_gate_passes(tmp_path) -> None:
    store, kept = established_all(tmp_path)
    see_work(store, "able1998", None, Publication.PREPRINT, LOOKUP, "openalex")
    see_work(store, "baker1984", None, Publication.REFEREED, LOOKUP, "crossref")
    publications.accept(store, "able1998", "its result is standard", OWNER)
    found = gate(store, kept)
    assert found.outcome is Outcome.PASS and found.items == ()
    (item,) = found.accepted
    assert item.data["cause"]["accepted"] == {
        "by": "person Ada",
        "reason": "its result is standard",
    }
    verdict = gates(queries.View(store, kept))
    text = report(verdict)
    assert any("1 work the author accepts resting on" in h for h in text.head)
    (section,) = [
        items for name, items in text.sections.items() if name.endswith(": accepted")
    ]
    assert section[0].line.endswith("accepted by person Ada: its result is standard")


def test_a_cited_work_never_looked_up_is_counted_never_listed(tmp_path) -> None:
    store, kept = established_all(tmp_path)
    see_work(store, "able1998", None, Publication.REFEREED, LOOKUP, "crossref")
    found = gate(store, kept)
    assert found.outcome is Outcome.PASS and found.unlooked == 1
    head = report(gates(queries.View(store, kept))).head
    (line,) = [h for h in head if "cited-publications" in h]
    assert line.endswith(
        "not counted: 1 cited work never looked up (`axiomation sources "
        "publications PAPER`)"
    )


def test_the_gate_off_reads_nothing(tmp_path) -> None:
    store, kept = established_all(tmp_path)
    see_work(store, "able1998", None, Publication.PREPRINT, LOOKUP, "openalex")
    found = gate(store, kept, Mode.OFF)
    assert found.outcome is Outcome.OFF and found.items == ()


# --- the claim's status ---------------------------------------------------------------


def test_an_established_claim_says_it_rests_on_a_preprint(tmp_path) -> None:
    store, kept = established_all(tmp_path)
    see_work(store, "able1998", None, Publication.PREPRINT, LOOKUP, "openalex")
    view = queries.View(store, kept)
    folded = {
        kept.block_of(e.claim.node): f
        for e, f in zip(view.established, view.claim_statuses, strict=True)
    }
    assert folded["u"].status is ClaimStatus.ESTABLISHED
    assert folded["u"].detail == "argued, on a preprint: able1998"
    assert folded["m"].detail == "argued"
