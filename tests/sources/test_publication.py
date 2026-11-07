"""`sources publications`: a cited work's publication read from what the
resolvers answer, a preprint's versions of record looked up, both filed,
and a published preprint's finding filed once, a dismissal holding.

`test_citations`' paper (`s` cites able1998, `m` able1998 and
baker1984, `u` uses `s`) and stubbed answers for every source: no
network, no model.
"""

from __future__ import annotations

import io
import json
import urllib.error
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("qdrant_client")

from axiomation.check import Gate, gates  # noqa: E402
from axiomation.established import Missing  # noqa: E402
from axiomation.judgments import Author, answer_finding  # noqa: E402
from axiomation.queries import View  # noqa: E402
from axiomation.records import (  # noqa: E402
    AnchorKind,
    FindingStatus,
    Match,
    Publication,
    VersionOfRecord,
)
from axiomation.sources import cli as sources_cli  # noqa: E402
from axiomation.sources import publication  # noqa: E402
from axiomation.sources.bib import BibRecord, arxiv_id  # noqa: E402
from axiomation.sources.config import load_settings  # noqa: E402
from axiomation.sources.publication import (  # noqa: E402
    Found,
    classify,
    file_found,
    look_up_all,
    look_up_work,
    pass_lines,
)
from axiomation.sources.resolvers import (  # noqa: E402
    ResolutionStatus,
    ResolvedSource,
    Resolver,
    base,
    providers,
    record,
)
from axiomation.sources.resolvers.record import (  # noqa: E402
    family_name,
    look_up,
    words,
)
from test_citations import built, cited, shelves, truly, verdicts  # noqa: E402

PREPRINT_DOI = "10.48550/arxiv.2105.21050"
ICLR = VersionOfRecord(
    "ICLR 2024",
    "2024",
    None,
    "https://openreview.net/forum?id=x",
    "openreview",
    Match.TITLE_AND_AUTHORS,
)
TITLE = "The Dark Matter Brownie Theorem"
BIB = f"""@article{{able1998,
  title = {{Pooled estimates: principles and practice}},
  author = {{Able, Ann and Smith, Bo}},
  doi = {{10.0000/example.1998}},
}}
@misc{{baker1984,
  title = {{{TITLE}}},
  author = {{Squeakinger, Ottoline and Crumble, Bartholomew}},
  eprint = {{2105.21050}},
  archiveprefix = {{arXiv}},
}}
"""
ATOM = (
    '<feed xmlns="http://www.w3.org/2005/Atom" '
    'xmlns:arxiv="http://arxiv.org/schemas/atom"><entry><title>{title}</title>'
    "{extra}</entry></feed>"
)


def resolved(**fields: Any) -> ResolvedSource:
    return ResolvedSource(
        "10.1/x", ResolutionStatus.EXACT, resolvers=("crossref",), **fields
    )


# --- what the resolvers answer --------------------------------------------------------


@pytest.mark.parametrize(
    ("work", "expected"),
    [
        (resolved(item_type="journalArticle", source_type="journal"), "refereed"),
        (resolved(item_type="conferencePaper"), "refereed"),
        (resolved(item_type="journalArticle", source_type="repository"), "preprint"),
        (resolved(item_type="preprint"), "preprint"),
        (resolved(item_type="bookSection"), "book"),
        (resolved(item_type="monograph"), "book"),
        (resolved(item_type="thesis"), "thesis-or-report"),
        (resolved(item_type="report"), "thesis-or-report"),
        (resolved(item_type="dataset"), "unknown"),
        (resolved(item_type="journalArticle", corrected=True), "erratum"),
        (
            resolved(item_type="journalArticle", corrected=True, retracted=True),
            "retracted",
        ),
    ],
)
def test_a_publication_is_read_from_the_resolvers_never_the_entry(
    work, expected
) -> None:
    assert classify(work)[0].value == expected


def test_the_source_says_which_resolver_and_type() -> None:
    work = resolved(item_type="preprint", source_type="repository")
    assert classify(work) == (
        Publication.PREPRINT,
        "crossref: preprint in a repository",
    )
    work = resolved(item_type="journalArticle", corrected=True, corrections=("10.1/c",))
    assert classify(work) == (Publication.ERRATUM, "crossref: corrected by doi:10.1/c")


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ({"eprint": "2105.21050v2", "archiveprefix": "arXiv"}, "2105.21050"),
        ({"eprint": "math/0601001", "eprinttype": "arxiv"}, "math/0601001"),
        ({"doi": "10.48550/ARXIV.1404.0404"}, "1404.0404"),
        ({"url": "https://arxiv.org/abs/2212.31415"}, "2212.31415"),
        ({"eprint": "2105.21050"}, ""),
        ({"doi": "10.1016/j.fudge.2020.107239"}, ""),
    ],
)
def test_an_arxiv_identifier_is_read_from_the_entry(entry, expected) -> None:
    assert arxiv_id(entry) == expected


def test_names_and_titles_compare_without_tex_accents_or_case() -> None:
    assert family_name("Br{\\'o}wnie, J{\\'o}zef and Fudge, Ramona") == "brownie"
    assert family_name("Ottoline Squeakinger and Bartholomew Crumble") == "squeakinger"
    assert family_name("{Fudge-Ripple}, Barnaby") == "fudge ripple"
    assert words("The Dark {{Matter Brownie}} Theorem-Revisited.") == (
        "the dark matter brownie theorem revisited"
    )


# --- the versions of record -----------------------------------------------------------


class Answers:
    """Every source's stubbed answer, by URL fragment; a fragment mapped
    to None did not answer. What was asked is kept."""

    def __init__(self, monkeypatch, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.asked: list[str] = []
        monkeypatch.setattr(record, "fetch_text", self.text)
        monkeypatch.setattr(record, "fetch_json", self.json)

    def _find(self, url: str) -> Any:
        self.asked.append(url)
        for fragment, answer in self.answers.items():
            if fragment in url:
                return answer
        return {}

    def text(self, connection, url, **_: Any) -> str | None:
        found = self._find(url)
        return (
            found
            if found is None or isinstance(found, str)
            else ATOM.format(title=TITLE, extra="")
        )

    def json(self, connection, url, **_: Any) -> dict[str, Any] | None:
        return self._find(url)


def openreview_note(venue: str, venueid: str, first: str, newer: bool = False):
    def wrap(value: Any) -> Any:
        return {"value": value} if newer else value

    return {
        "id": "abc",
        "content": {
            "title": wrap(TITLE),
            "venue": wrap(venue),
            "venueid": wrap(venueid),
            "authors": wrap([first]),
        },
    }


def asked(answers: Answers, **fields: Any):
    # 10.1/deposit is published as no refereed work: `resolve` says None
    venues = {"10.1/vor": ("Annals of Tests", "2025"), "10.1/deposit": None}
    return look_up(
        None,
        doi=fields.get("doi", PREPRINT_DOI),
        arxiv=fields.get("arxiv", "2105.21050"),
        title=TITLE,
        authors="Squeakinger, Ottoline and Crumble, Bartholomew",
        resolve=lambda doi: venues.get(doi, ("", "")),
    )


def test_the_authors_doi_on_arxiv_is_the_version_of_record(monkeypatch) -> None:
    monkeypatch.setattr(providers, "crossref", lambda *a, **k: {})
    extra = (
        "<arxiv:doi>10.1/VOR</arxiv:doi><arxiv:journal_ref>Ann. 1</arxiv:journal_ref>"
    )
    answers = Answers(
        monkeypatch, {"export.arxiv.org": ATOM.format(title=TITLE, extra=extra)}
    )
    found = asked(answers)
    (version,) = found.versions
    assert (version.venue, version.year, version.doi) == (
        "Annals of Tests",
        "2025",
        "10.1/vor",
    )
    assert (version.source, version.match) == ("arxiv", Match.IDENTIFIER)
    # the record's own word: no title search
    assert not any("openreview" in u or "title.search" in u for u in answers.asked)


def test_a_journal_reference_alone_is_the_authors_word(monkeypatch) -> None:
    monkeypatch.setattr(providers, "crossref", lambda *a, **k: {})
    extra = "<arxiv:journal_ref>Published at ICLR 2021</arxiv:journal_ref>"
    answers = Answers(
        monkeypatch, {"export.arxiv.org": ATOM.format(title=TITLE, extra=extra)}
    )
    (version,) = asked(answers).versions
    assert (version.venue, version.year, version.match) == (
        "Published at ICLR 2021",
        "2021",
        Match.REFERENCE,
    )


@pytest.mark.parametrize(
    "extra",
    [
        "<arxiv:journal_ref>Submitted to Annals of Tests</arxiv:journal_ref>",
        "<arxiv:journal_ref>Under review at TMLR</arxiv:journal_ref>",
        "<arxiv:doi>10.1/deposit</arxiv:doi>",
    ],
)
def test_what_arxiv_names_that_is_no_publication_is_passed(monkeypatch, extra) -> None:
    monkeypatch.setattr(providers, "crossref", lambda *a, **k: {})
    answers = Answers(
        monkeypatch,
        {
            "export.arxiv.org": ATOM.format(title=TITLE, extra=extra),
            "openreview.net": {"notes": []},
        },
    )
    found = asked(answers)
    assert found.versions == () and len(found.passed) == 1


@pytest.mark.parametrize(
    ("venue", "venueid"),
    [
        ("Under review for TMLR", "TMLR/Under_Review"),
        ("TMLR", "TMLR/Submission"),
        ("Decision pending", "ICLR.cc/2025/Conference/Submission"),
        ("ICLR 2024 retracted", "ICLR.cc/2024/Conference/Retracted_Acceptance"),
    ],
)
def test_an_openreview_record_not_accepted_is_no_version_of_record(
    monkeypatch, venue, venueid
) -> None:
    monkeypatch.setattr(providers, "crossref", lambda *a, **k: {})
    note = openreview_note(venue, venueid, "Ottoline Squeakinger", newer=True)
    answers = Answers(monkeypatch, {"openreview.net": {"notes": [note]}})
    assert asked(answers).versions == ()


def test_crossref_s_preprint_relation_is_the_version_of_record(monkeypatch) -> None:
    monkeypatch.setattr(
        providers,
        "crossref",
        lambda *a, **k: {
            "relation": {"is-preprint-of": [{"id-type": "doi", "id": "10.1/VOR"}]}
        },
    )
    answers = Answers(monkeypatch, {})
    (version,) = asked(answers, doi="10.31234/osf.io/x", arxiv="").versions
    assert (version.doi, version.source, version.match) == (
        "10.1/vor",
        "crossref",
        Match.IDENTIFIER,
    )


def test_a_title_hit_needs_the_first_author_and_an_accepted_venue(
    monkeypatch,
) -> None:
    monkeypatch.setattr(providers, "crossref", lambda *a, **k: {})
    answers = Answers(
        monkeypatch,
        {
            "export.arxiv.org": ATOM.format(title=TITLE, extra=""),
            "title.search": {
                "results": [
                    {
                        "title": TITLE,
                        "type": "article",
                        "doi": "https://doi.org/10.1/journal",
                        "publication_year": 2025,
                        "authorships": [{"author": {"display_name": "A. Other"}}],
                        "primary_location": {
                            "source": {"display_name": "J. Tests", "type": "journal"}
                        },
                    }
                ]
            },
            "api.openreview.net": {
                "notes": [
                    openreview_note(
                        "Submitted to ICLR 2024",
                        "ICLR.cc/2024/Conference/Rejected_Submission",
                        "Ottoline Squeakinger",
                    ),
                    openreview_note(
                        "CoRR 2023",
                        "dblp.org/journals/CORR/2023",
                        "Ottoline Squeakinger",
                    ),
                    openreview_note(
                        "ICLR 2024", "dblp.org/conf/ICLR/2024", "Ottoline Squeakinger"
                    ),
                    openreview_note(
                        "ICLR 2024 poster",
                        "ICLR.cc/2024/Conference",
                        "Ottoline Squeakinger",
                    ),
                ]
            },
        },
    )
    found = asked(answers)
    (version,) = found.versions
    assert (version.venue, version.year, version.source) == (
        "ICLR 2024",
        "2024",
        "openreview",
    )
    assert version.url == "https://openreview.net/forum?id=abc"
    assert version.match is Match.TITLE_AND_AUTHORS
    assert found.passed == ("J. Tests (openalex): its first author is A. Other",)
    # the older API found it: the newer is not asked
    assert not any("api2.openreview.net" in u for u in answers.asked)


def test_the_newer_openreview_is_asked_where_the_older_finds_none(
    monkeypatch,
) -> None:
    monkeypatch.setattr(providers, "crossref", lambda *a, **k: {})
    answers = Answers(
        monkeypatch,
        {
            "api2.openreview.net": {
                "notes": [
                    openreview_note(
                        "Accepted by TMLR", "TMLR", "Ottoline Squeakinger", newer=True
                    )
                ]
            },
            "api.openreview.net": {"notes": []},
        },
    )
    (version,) = asked(answers).versions
    assert version.venue == "Accepted by TMLR"


def test_a_source_that_does_not_answer_is_named_never_read_as_none(
    monkeypatch,
) -> None:
    monkeypatch.setattr(providers, "crossref", lambda *a, **k: {})
    answers = Answers(
        monkeypatch,
        {
            "export.arxiv.org": None,
            "title.search": None,
            "openreview.net": None,
        },
    )
    found = asked(answers)
    assert found.versions == ()
    assert found.failed == (
        "the arXiv API did not answer",
        "OpenAlex's title search did not answer (asked without a contact address)",
        "OpenReview (https://api.openreview.net) did not answer",
        "OpenReview (https://api2.openreview.net) did not answer",
    )


def test_a_429_is_waited_out_as_it_asks(monkeypatch) -> None:
    calls: list[str] = []
    waits: list[float] = []

    def urlopen(request, timeout):
        calls.append(request.full_url)
        if len(calls) == 1:
            raise urllib.error.HTTPError(
                request.full_url, 429, "slow down", {"Retry-After": "7"}, None
            )
        return io.BytesIO(b"{}")

    monkeypatch.setattr(base.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(base.time, "sleep", waits.append)
    assert base._http_get("https://api.openreview.net/x", {}) == b"{}"
    assert waits == [7.0] and len(calls) == 2


# --- one work, and the pass -----------------------------------------------------------


@pytest.fixture
def network(monkeypatch) -> Answers:
    """able1998 a journal article on Crossref; baker1984's arXiv DOI a
    preprint on OpenAlex, published at ICLR by OpenReview's record."""

    def crossref(connection, doi, **_: Any) -> dict[str, Any]:
        if doi.startswith("10.0000/"):
            return {"type": "journal-article", "title": ["Pooled estimates"]}
        return {}

    def openalex(connection, doi, **_: Any) -> dict[str, Any]:
        if doi == PREPRINT_DOI:
            return {
                "type": "preprint",
                "primary_location": {"source": {"type": "repository"}},
            }
        return {"type": "article"}

    monkeypatch.setattr(providers, "crossref", crossref)
    monkeypatch.setattr(providers, "openalex", openalex)
    for name in ("unpaywall", "europe_pmc", "core"):
        monkeypatch.setattr(providers, name, lambda *a, **k: {})
    return Answers(
        monkeypatch,
        {
            "api.openreview.net": {
                "notes": [
                    openreview_note(
                        "ICLR 2024 poster",
                        "ICLR.cc/2024/Conference",
                        "Ottoline Squeakinger",
                    )
                ]
            }
        },
    )


@pytest.fixture
def paper(tmp_path: Path):
    """The paper with its bibliography; its store kept, `s` established by
    its citation and `u` resting on it."""
    store, kept = built(tmp_path)
    (tmp_path / "references.bib").write_text(BIB, encoding="utf-8")
    cited(store, kept, "s", "able1998", [("able1998", 12)], shelves())
    truly(store, kept, "u", (), shelves())
    return tmp_path, store, kept


def entry(key: str, **fields: Any) -> BibRecord:
    return BibRecord(key, title=TITLE, author="Squeakinger, Ottoline", **fields)


def test_an_entry_with_no_identifier_is_unknown(tmp_path, network) -> None:
    with Resolver(tmp_path / "sources.db") as resolver:
        found = look_up_work(resolver, entry("book2001"))
    assert found.publication is Publication.UNKNOWN
    assert found.source == "no DOI or arXiv identifier in the entry"


def test_an_arxiv_entry_is_asked_by_arxiv_s_doi(tmp_path, network) -> None:
    with Resolver(tmp_path / "sources.db") as resolver:
        found = look_up_work(resolver, entry("reed2024", arxiv="2105.21050"))
    assert found.doi == PREPRINT_DOI
    assert found.publication is Publication.PREPRINT_PUBLISHED
    assert found.source == "openalex: preprint in a repository"
    assert found.versions[0].venue == "ICLR 2024 poster"


def test_a_lookup_no_resolver_answered_files_nothing(paper, monkeypatch) -> None:
    _, store, _ = paper
    for name in ("crossref", "openalex", "unpaywall", "europe_pmc", "core"):
        monkeypatch.setattr(providers, name, lambda *a, **k: {})
    with Resolver(store.directory / "sources.db") as resolver:
        found = look_up_work(resolver, entry("x", doi="10.1/x"))
    assert not found.answered and found.publication is Publication.UNKNOWN
    assert file_found(store, found) is None


def test_a_miss_while_a_source_failed_files_nothing_over_a_record(paper) -> None:
    _, store, _ = paper
    published = Found(
        "baker1984", "10.1/x", Publication.PREPRINT_PUBLISHED, "openalex", (ICLR,)
    )
    assert file_found(store, published)
    failed = Found(
        "baker1984",
        "10.1/x",
        Publication.PREPRINT,
        "openalex",
        failed=("OpenReview (https://api.openreview.net) did not answer",),
    )
    assert file_found(store, failed) is None
    assert file_found(store, replace(failed, failed=()))


def test_a_lookup_is_filed_where_it_moved(paper) -> None:
    _, store, _ = paper
    found = Found("able1998", "10.1/x", Publication.REFEREED, "crossref: article")
    assert file_found(store, found)
    assert file_found(store, found) is None
    assert file_found(store, Found("able1998", "10.1/x", Publication.BOOK, "x"))
    assert len(store.judgment_records().works) == 2


def test_a_retraction_looked_up_is_read_by_established(paper) -> None:
    _, store, kept = paper
    assert verdicts(store, kept)["s"].established
    file_found(store, Found("able1998", "10.1/x", Publication.RETRACTED, "openalex"))
    assert verdicts(store, kept)["s"].reason.kind is Missing.RETRACTED


def test_a_pass_files_publications_and_a_published_preprint_s_finding(
    paper, network
) -> None:
    root, store, kept = paper
    config = load_settings(root, store.directory)
    run = look_up_all(config, store.directory)
    by_key = {f.key: f for f in run.found}
    assert by_key["able1998"].publication is Publication.REFEREED
    assert by_key["baker1984"].publication is Publication.PREPRINT_PUBLISHED
    assert set(run.filed) == {"able1998", "baker1984"}
    (finding,) = store.judgment_records().findings
    assert finding.severity == 1 and finding.aspect.value == "cited"
    assert finding.problem == (
        "baker1984 is cited as a preprint, published since: ICLR 2024 poster, "
        "https://openreview.net/forum?id=abc (found by openreview, by "
        "title-and-authors). A match by title and first author can be wrong: "
        "check it is this work."
    )
    kinds = [(a.kind, a.work or a.node) for a in finding.anchors]
    assert kinds == [
        (AnchorKind.WORK, "baker1984"),
        (AnchorKind.SPAN, kept.node_of("m")),
    ]
    # a published preprint counts as refereed: the gate does not warn
    verdict = gates(View(store, kept))
    (cited_gate,) = [r for r in verdict.results if r.gate is Gate.CITED_PUBLICATIONS]
    assert cited_gate.items == () and cited_gate.unlooked == 0

    again = look_up_all(config, store.directory)
    assert again.filed == {} and again.findings == {}
    assert again.held == ["baker1984"]


def test_a_dismissed_finding_holds(paper, network) -> None:
    root, store, _ = paper
    config = load_settings(root, store.directory)
    look_up_all(config, store.directory)
    (finding,) = store.judgment_records().findings
    answer_finding(store, finding.id, FindingStatus.DISMISSED, "we cite the arXiv one")
    again = look_up_all(config, store.directory)
    assert again.findings == {} and len(store.judgment_records().findings) == 1


def test_a_person_s_setting_speaks_and_files_no_finding(paper, network) -> None:
    root, store, kept = paper
    publication.set_by_person(
        store, "baker1984", Publication.PREPRINT, "the ICLR one differs", Author()
    )
    config = load_settings(root, store.directory)
    run = look_up_all(config, store.directory)
    assert run.findings == {} and "baker1984" in run.set_by
    _, lines = pass_lines(run, False)
    assert "  a person's setting speaks: preprint (the ICLR one differs)" in lines
    verdict = gates(View(store, kept))
    (cited_gate,) = [r for r in verdict.results if r.gate is Gate.CITED_PUBLICATIONS]
    assert [i.data["cause"]["work"] for i in cited_gate.items] == ["baker1984"]


def test_a_dry_run_files_nothing(paper, network) -> None:
    root, store, _ = paper
    run = look_up_all(
        load_settings(root, store.directory), store.directory, dry_run=True
    )
    assert len(run.found) == 2
    records = store.judgment_records()
    assert records.works == () and records.findings == ()


def test_a_pass_without_a_contact_address_says_what_it_lost(
    paper, network, monkeypatch, user_file
) -> None:
    root, store, _ = paper
    config = load_settings(root, store.directory)
    run = look_up_all(config, store.directory, dry_run=True)
    payload, lines = pass_lines(run, True)
    said = (
        "no contact address: Unpaywall is skipped, and Crossref and OpenAlex "
        "are asked outside their polite pools, where they throttle: set "
        f'contact_email = "…" in {user_file}, or AXIOMATION_CONTACT_EMAIL in '
        "the shell"
    )
    assert lines[1] == said
    assert payload["no_contact"] == said.removeprefix("no contact address: ")

    monkeypatch.setenv("AXIOMATION_CONTACT_EMAIL", "me@example.org")
    config = load_settings(root, store.directory)
    run = look_up_all(config, store.directory, dry_run=True)
    payload, lines = pass_lines(run, True)
    assert payload["no_contact"] is None
    assert not any("contact address" in line for line in lines)


def test_a_key_the_paper_does_not_cite_is_refused(paper, network) -> None:
    from axiomation.sources.config import SourcesError

    root, store, _ = paper
    with pytest.raises(SourcesError, match="cites no nobody2000"):
        look_up_all(
            load_settings(root, store.directory), store.directory, ["nobody2000"]
        )


# --- the command ----------------------------------------------------------------------


def run(*argv: str) -> int:
    return sources_cli.main(list(argv))


def test_the_commands_look_up_set_and_accept(paper, network, capsys) -> None:
    root, store, kept = paper
    where = ["--store", str(store.directory)]
    assert run("publications", str(root), "--json", *where) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["counts"] == {"refereed": 1, "preprint-published": 1}

    assert (
        run(
            "publications",
            str(root),
            "able1998",
            "--set",
            "book",
            "--why",
            "a monograph",
            "--person",
            "Ada",
            *where,
        )
        == 0
    )
    assert "able1998 is book, by a person: a monograph" in capsys.readouterr().out
    assert run("publications", str(root), "--set", "book", "--why", "x", *where) == 1
    assert "give its key alone" in capsys.readouterr().err

    assert run("accept", str(root), "baker1984", "--why", "standard", *where) == 0
    assert "resting on baker1984 accepted: standard" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        run("accept", str(root), "baker1984", *where)  # --why is required


# --- the chain ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("updates", "retracted", "corrected"),
    [
        ([{"type": "correction", "DOI": "10.1/FIX"}], False, True),
        (
            [{"type": "retraction"}, {"type": "correction", "DOI": "10.1/fix"}],
            True,
            True,
        ),
        ([], False, False),
    ],
)
def test_crossref_s_updates_on_the_work_are_read(
    tmp_path, monkeypatch, updates, retracted, corrected
) -> None:
    """Crossref's `updated-by` (a correction or a retraction of the work)
    is read beside OpenAlex's retraction flag, which may lag it."""
    monkeypatch.setattr(
        providers,
        "crossref",
        lambda *a, **k: {"type": "journal-article", "updated-by": updates},
    )
    monkeypatch.setattr(
        providers,
        "openalex",
        lambda *a, **k: {
            "is_retracted": False,
            "primary_location": {"source": {"type": "journal"}},
        },
    )
    for name in ("unpaywall", "europe_pmc", "core"):
        monkeypatch.setattr(providers, name, lambda *a, **k: {})
    with Resolver(tmp_path / "sources.db") as resolver:
        work = resolver.resolve("10.1/x")
    assert (work.retracted, work.corrected, work.source_type) == (
        retracted,
        corrected,
        "journal",
    )
    assert work.corrections == (("10.1/fix",) if corrected else ())


def test_a_resolution_cached_before_publications_is_made_again(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(providers, "crossref", lambda *a, **k: {"type": "book"})
    for name in ("openalex", "unpaywall", "europe_pmc", "core"):
        monkeypatch.setattr(providers, name, lambda *a, **k: {})
    with Resolver(tmp_path / "sources.db") as resolver:
        resolver.resolve("10.1/x")
        body = json.loads(
            resolver.connection.execute("SELECT body FROM sources").fetchone()[0]
        )
        del body["source_type"]
        resolver.connection.execute("UPDATE sources SET body = ?", (json.dumps(body),))
        assert resolver._cached("10.1/x") is None
        assert resolver.resolve("10.1/x").item_type == "book"


def test_the_bibliography_sources_declares_is_read(paper, network) -> None:
    """A workspace names its bibliography in `[sources]`, not `[paper]`."""
    root, store, _ = paper
    (root / "lib").mkdir()
    (root / "references.bib").rename(root / "lib" / "refs.bib")
    (root / "axiomation.toml").write_text('[sources]\nbib = "lib/refs.bib"\n')
    run = look_up_all(load_settings(root, store.directory), store.directory)
    assert {f.key for f in run.found} == {"able1998", "baker1984"}


def test_an_openalex_location_with_no_source_is_read(tmp_path, monkeypatch) -> None:
    """OpenAlex answers `"source": null` on some locations of an arXiv
    record; the chain reads it as no source, never fails."""
    monkeypatch.setattr(providers, "crossref", lambda *a, **k: {})
    monkeypatch.setattr(
        providers,
        "openalex",
        lambda *a, **k: {
            "type": "preprint",
            "primary_location": {"source": None, "pdf_url": "https://x.org/a.pdf"},
            "best_oa_location": {"source": None, "pdf_url": "https://x.org/b.pdf"},
        },
    )
    for name in ("unpaywall", "europe_pmc", "core"):
        monkeypatch.setattr(providers, name, lambda *a, **k: {})
    with Resolver(tmp_path / "sources.db") as resolver:
        work = resolver.resolve(PREPRINT_DOI)
    assert work.item_type == "preprint" and work.source_type == ""
    assert {c.host_type for c in work.candidates} == {""}
