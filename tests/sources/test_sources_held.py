"""A work whose file is not its entry's work, held back unread: the
library's read, `status`, `confirm`, a replaced file and `ingest`. The
fake Qdrant holds another author's book with the entry's title; the
entry, its bib and its confirmations are the fixture paper's.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("qdrant_client")

from axiomation.evidence import ReadStatus  # noqa: E402
from axiomation.sources.cli import main  # noqa: E402
from axiomation.sources.config import ResolvedSources  # noqa: E402
from axiomation.sources.confirm import path_for  # noqa: E402
from axiomation.sources.contract import TextSource  # noqa: E402
from axiomation.sources.ingest import run as run_module  # noqa: E402
from axiomation.sources.ingest.extract import PageText  # noqa: E402
from axiomation.sources.library import client as client_module  # noqa: E402
from axiomation.sources.library import qdrant as qdrant_module  # noqa: E402
from axiomation.sources.providers import sources_for  # noqa: E402
from sources.fakes import FakeQdrant  # noqa: E402
from sources.test_sources_match import OTHER_BOOK, RIGHT_BOOK  # noqa: E402

KEY = "halvorsen2011"
OLD = "a" * 64
NEW = "c" * 64
ENTRY = (
    f"@book{{{KEY},\n"
    "  title = {Shaken Flows of Smooth Maps},\n"
    "  author = {Halvorsen, Ines K. and Quist, Tobias},\n"
    "  year = {2011},\n"
    "  file = {FILE},\n"
    "}\n"
)


def stock(fake: FakeQdrant, collection: str, pages: dict[int, str], sha: str) -> None:
    for index, text in pages.items():
        fake.add_chunk(
            collection,
            bib_key=KEY,
            title="Shaken Flows of Smooth Maps",
            author="Halvorsen, Ines K. and Quist, Tobias",
            page_number=index,
            chunk_index=index - 1,
            text=text + " " + "z" * 60,
            file_sha256=sha,
        )


@pytest.fixture
def held(
    monkeypatch: pytest.MonkeyPatch,
    sources_paper: Path,
    fake_qdrant: FakeQdrant,
    settings: ResolvedSources,
    fixture_pdf: Path,
) -> FakeQdrant:
    """The paper's bib names the entry; the corpus holds the wrong book."""
    monkeypatch.setenv("AXIOMATION_VOYAGE_API_KEY", "fixture-key")
    monkeypatch.setattr(qdrant_module, "connect", lambda _: fake_qdrant)
    monkeypatch.setattr(client_module, "connect", lambda _: fake_qdrant)
    (sources_paper / "references.bib").write_text(
        ENTRY.replace("FILE", str(fixture_pdf)), encoding="utf-8"
    )
    stock(fake_qdrant, settings.collection, OTHER_BOOK, OLD)
    return fake_qdrant


def run(root: Path, *argv: str) -> int:
    action, *rest = argv
    return main([action, str(root), *rest, "--store", str(root.parent / "store")])


def test_the_library_holds_back_a_file_that_is_not_its_entrys(
    held: FakeQdrant, sources_paper: Path
) -> None:
    read = sources_for(sources_paper).page(KEY, 3)
    assert read.read_status is ReadStatus.MISMATCH and not read.pages
    assert "names none of its entry's authors (Halvorsen, Quist)" in read.detail
    assert f"axiomation sources confirm DIR {KEY}" in read.detail
    assert "Ivo Marten" not in read.detail  # page text never leaves unfenced


def test_status_lists_it_with_its_title_page_fenced(
    held: FakeQdrant, sources_paper: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(sources_paper, "status")
    out = capsys.readouterr().out
    assert "1 source(s) whose file is not their entry's work" in out
    assert f"  {KEY}: its title page (p. 3) names none" in out
    assert f'<source-text key="{KEY}" page-index="3" status="mismatch"' in out
    assert "Ivo Marten Shaken Flows of Smooth Maps" in out
    assert "--verbose lists them" in out  # 1994, not 2011


def test_a_confirmation_reads_the_file_and_a_replaced_one_is_checked_again(
    held: FakeQdrant,
    sources_paper: Path,
    settings: ResolvedSources,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert run(sources_paper, "confirm", KEY, "--why", "the translation we cite") == 0
    out = capsys.readouterr().out
    assert f"confirmed {KEY}'s file (aaaaaaaaaaaa)" in out
    kept = json.loads(path_for(sources_paper / "references.bib").read_text())
    assert kept[KEY]["file_sha256"] == OLD
    assert kept[KEY]["why"] == "the translation we cite"
    assert sources_for(sources_paper).page(KEY, 3).read_status is ReadStatus.OK

    for point in held.points(settings.collection):  # another file ingested
        point.payload["file_sha256"] = NEW
    assert sources_for(sources_paper).page(KEY, 3).read_status is (ReadStatus.MISMATCH)


def test_a_file_that_matches_needs_no_confirmation(
    held: FakeQdrant,
    sources_paper: Path,
    settings: ResolvedSources,
    capsys: pytest.CaptureFixture[str],
) -> None:
    held.collections[settings.collection].clear()
    stock(held, settings.collection, RIGHT_BOOK, NEW)
    assert sources_for(sources_paper).page(KEY, 3).read_status is ReadStatus.OK
    assert run(sources_paper, "confirm", KEY, "--why", "x") == 0
    assert "matches its entry: nothing to confirm" in capsys.readouterr().out
    assert not path_for(sources_paper / "references.bib").exists()


def test_ingest_reports_a_file_it_holds_back(
    held: FakeQdrant,
    sources_paper: Path,
    settings: ResolvedSources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fresh = FakeQdrant()  # a collection the ingest creates, so its own

    def extract(path: Path, **_: object) -> list[PageText]:
        return [
            PageText(i, text + " " + "z" * 60, TextSource.NONE)
            for i, text in OTHER_BOOK.items()
        ]

    def embed(texts: list[str], **_: object) -> tuple[list, list, int]:
        vectors = [[0.0] * settings.voyage_dims] * len(texts)
        return vectors, [{"indices": [1], "values": [1.0]}] * len(texts), 10

    monkeypatch.setattr(run_module, "extract_pdf", extract)
    monkeypatch.setattr(run_module, "embed_documents", embed)
    plan = run_module.plan_ingest(
        fresh, settings, bib_path=sources_paper / "references.bib"
    )
    report = run_module.apply_ingest(fresh, settings, plan, api_key="k")
    ((key, why),) = report.held
    assert key == KEY and "names none of its entry's authors" in why
    assert report.as_dict()["held"] == [{"bib_key": KEY, "why": why}]


def test_a_bibliography_missing_here_checks_nothing(
    held: FakeQdrant, sources_paper: Path
) -> None:
    """Pages still read where the declared bibliography is not on this
    machine: there is no entry to hold a file to."""
    (sources_paper / "references.bib").unlink()
    assert sources_for(sources_paper).page(KEY, 3).read_status is ReadStatus.OK


def test_a_fetched_copy_is_checked_as_a_library_file_is(
    sources_paper: Path, settings: ResolvedSources, fixture_pdf: Path
) -> None:
    """A copy fetched before the check, or past it, is held back the same
    way: the wrong book under the key reads as `mismatch`."""
    from axiomation.evidence import Sources
    from axiomation.sources.providers import Entries, Transient
    from axiomation.sources.read.transient import store_pages

    (sources_paper / "references.bib").write_text(
        ENTRY.replace("FILE", str(fixture_pdf)), encoding="utf-8"
    )
    cache = sources_paper.parent / "cache"
    pages = [
        {"bib_key": KEY, "page_index": i, "text": t + " " + "z" * 60}
        for i, t in OTHER_BOOK.items()
    ]
    store_pages(cache, KEY, pages)
    copy = Transient(cache, Entries(sources_paper / "references.bib"))
    read = Sources([copy]).page(KEY, 3)
    assert read.read_status is ReadStatus.MISMATCH
    assert "names none of its entry's authors" in read.detail
    store_pages(
        cache,
        KEY,
        [
            {**p, "text": RIGHT_BOOK.get(p["page_index"], "") + " " + "z" * 60}
            for p in pages
        ],
    )
    fresh = Transient(cache, Entries(sources_paper / "references.bib"))
    assert Sources([fresh]).page(KEY, 3).read_status is ReadStatus.TRANSIENT


def test_status_says_when_no_file_is_checked(
    held: FakeQdrant, sources_paper: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (sources_paper / "references.bib").unlink()
    run(sources_paper, "status")
    out = capsys.readouterr().out
    assert "no file is checked against its entry, and none is held" in out


def test_an_ingest_asked_about_an_unchanged_held_work_says_why_it_skips(
    held: FakeQdrant,
    sources_paper: Path,
    settings: ResolvedSources,
    fixture_pdf: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A reference manager replaces a file by the same bytes at its own
    path: the ingest says the file is the one it read, and that the work is
    still held, with the remedy."""
    from axiomation.sources.contract import PAGE_KIND_FIELD
    from axiomation.sources.files import file_sha256
    from axiomation.sources.ingest.bib import parse
    from axiomation.sources.library.upsert import ensure_collection

    same = file_sha256(fixture_pdf)
    (entry,) = parse(sources_paper / "references.bib")
    held.collections.pop(settings.collection)
    ensure_collection(held, settings)  # the corpus axiomation made
    stock(held, settings.collection, OTHER_BOOK, same)
    for point in held.points(settings.collection):
        point.payload["bib_sha256"] = entry.payload_hash()
        point.payload[PAGE_KIND_FIELD] = "digital"  # written by today's ingest
    assert run(sources_paper, "ingest", "--key", KEY, "--dry-run") == 0
    out = capsys.readouterr().out
    assert (
        f"unchanged  {KEY}: its file is the one ingested (SHA-256 {same[:12]})" in out
    )
    assert "`--force` reads it again" in out
    assert f"HELD {KEY}'s file is not its entry's work: its title page (p. 3)" in out
    assert f"axiomation sources confirm DIR {KEY}" in out
    assert out.rstrip().endswith("nothing to do")

    assert run(sources_paper, "ingest", "--dry-run") == 0  # no key: nothing listed
    assert "unchanged" not in capsys.readouterr().out


def test_a_held_work_says_a_person_confirmed_its_earlier_file(
    held: FakeQdrant,
    sources_paper: Path,
    settings: ResolvedSources,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert run(sources_paper, "confirm", KEY, "--why", "the translation we cite") == 0
    capsys.readouterr()
    for point in held.points(settings.collection):  # another file ingested
        point.payload["file_sha256"] = NEW
    read = sources_for(sources_paper).page(KEY, 3)
    earlier = f"A person confirmed another file under {KEY} (aaaaaaaaaaaa, "
    assert earlier in read.detail and "not this one (cccccccccccc)" in read.detail
    run(sources_paper, "status")
    out = capsys.readouterr().out
    assert f"  {earlier}" in out and "the translation we cite" in out


def test_a_confirm_during_an_outage_names_the_failure_not_a_missing_work(
    held: FakeQdrant,
    sources_paper: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def down(*_: object, **__: object) -> None:
        raise ConnectionError("Qdrant refused the connection")

    monkeypatch.setattr(held, "scroll", down)
    assert run(sources_paper, "confirm", KEY, "--why", "x") == 1
    err = capsys.readouterr().err
    assert f"cannot tell whether a source holds {KEY}: the library failed" in err
    assert "Qdrant refused the connection" in err and "ingest" not in err


def test_a_key_given_first_is_the_key_of_the_paper_it_is_run_in(
    held: FakeQdrant,
    sources_paper: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The paper's directory left out, run inside the paper: a key given
    first is the key, never taken for a directory."""
    monkeypatch.chdir(sources_paper)
    store = str(sources_paper.parent / "store")
    assert main(["confirm", KEY, "--why", "x", "--store", store]) == 0
    assert f"confirmed {KEY}'s file" in capsys.readouterr().out
    kept = json.loads(path_for(sources_paper / "references.bib").read_text())
    assert kept[KEY]["why"] == "x"


def test_the_library_hashes_the_file_it_holds_where_the_ingest_read_it(
    tmp_path: Path,
    sources_paper: Path,
    fake_qdrant: FakeQdrant,
    settings: ResolvedSources,
    fixture_pdf: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The content hash is read from the file the ingest read, named by any
    stored page (a work stored from a later page has no page 1), while its
    SHA-256 is the one held; a file changed since is not hashed."""
    from axiomation.sources.content import content_hash
    from axiomation.sources.files import file_sha256

    monkeypatch.setattr(client_module, "connect", lambda _: fake_qdrant)
    sha = file_sha256(fixture_pdf)
    for index in (3, 4):
        fake_qdrant.add_chunk(
            settings.collection,
            bib_key=KEY,
            page_number=index,
            chunk_index=index - 1,
            file_path=str(fixture_pdf),
            file_sha256=sha,
        )
    (sources_paper / "references.bib").write_text("", encoding="utf-8")
    sources = sources_for(sources_paper, tmp_path / "store", fetch=False)
    stamp, outage = sources.file_stamp(KEY)
    assert not outage and stamp.file == sha
    assert stamp.content == content_hash(fixture_pdf)

    fake_qdrant.collections.clear()
    stock(fake_qdrant, settings.collection, {3: "A page."}, NEW)
    again = sources_for(sources_paper, tmp_path / "store", fetch=False)
    stamp, outage = again.file_stamp(KEY)
    assert not outage and stamp.file == NEW and stamp.content == ""
