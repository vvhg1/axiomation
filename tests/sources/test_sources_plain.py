"""The plain tier: each cited work's PDF on this machine, read when asked,
with no service, no OCR and nothing stored. Runs in the core."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from axiomation.evidence import Origin, ReadStatus, Sources
from axiomation.sources import providers
from axiomation.sources.config import SourcesError, load_settings, resolve
from axiomation.sources.files import file_key, file_sha256
from axiomation.sources.providers import UNCHECKED, Entries, Layers, Pdfs, Transient
from sources.pdfs import write_pdf
from user_files import write_user_file

TITLE = "A Stable Result"
#: A title page that prints its entry's title and author.
OPENING = f"{TITLE}\nAnn Able\nWe prove that the stable series converges quickly."
BODY = (
    "Theorem 2.1. Every bounded gain keeps the stable series within its "
    "constant, and the rate is sharp."
)
OTHER = (
    "On Quite Another Matter\nBob Baker\nThis treatise studies harbours, "
    "tides and their keepers."
)


def entry(key: str, *files: Path, title: str = TITLE, pages: str = "") -> str:
    listed = ";".join(str(f) for f in files)
    lines = [f"@article{{{key},", f"  title = {{{title}}},", "  author = {Able, Ann},"]
    if listed:
        lines.append(f"  file = {{{listed}}},")
    if pages:
        lines.append(f"  pages = {{{pages}}},")
    return "\n".join([*lines, "  year = {1998}", "}"]) + "\n"


def bib(tmp_path: Path, *entries: str) -> Path:
    path = tmp_path / "references.bib"
    path.write_text("".join(entries), encoding="utf-8")
    return path


def plain(bib_path: Path, pdf_dir: Path | None = None) -> Sources:
    entries = Entries(bib_path)
    layers = Layers()
    return Sources([Pdfs(entries, pdf_dir, layers), Transient(None, entries, layers)])


def test_a_work_s_pdf_is_read_when_asked_and_stamped_ok(tmp_path) -> None:
    pdf = write_pdf(tmp_path / "able.pdf", [OPENING, BODY])
    sources = plain(bib(tmp_path, entry("able1998", pdf)))
    read = sources.pages("able1998", 1, 2)
    assert read.read_status is ReadStatus.OK
    assert [p.page_index for p in read.pages] == [1, 2]
    second = read.pages[1]
    assert "Theorem 2.1" in second.text and second.title == TITLE
    assert second.file_path == str(pdf) and second.text_source == "none"
    stamp, why = sources.file_stamp("able1998")
    assert not why and stamp.read_status is ReadStatus.OK
    assert stamp.file == file_sha256(pdf) and stamp.content.startswith("c1:")
    assert sources.page("able1998", 3).read_status is ReadStatus.INFRASTRUCTURE


def test_the_pdf_directory_serves_a_work_whose_entry_names_no_file(tmp_path) -> None:
    shelf = tmp_path / "pdfs"
    shelf.mkdir()
    write_pdf(shelf / "able1998.pdf", [OPENING])
    write_pdf(shelf / "nobody2000.pdf", [OPENING])
    named = write_pdf(tmp_path / "named.pdf", [OPENING, BODY])
    sources = plain(bib(tmp_path, entry("able1998"), entry("baker1984", named)), shelf)
    assert sources.page("able1998", 1).pages[0].file_path == str(shelf / "able1998.pdf")
    # an entry's own file comes first; a key the bibliography lacks reads nothing
    assert sources.page("baker1984", 2).read_status is ReadStatus.OK
    missed = sources.page("nobody2000", 1)
    assert missed.read_status is ReadStatus.ABSENT
    assert "neither its PDF on this machine nor a fetched copy" in missed.detail
    assert plain(bib(tmp_path, entry("able1998"))).page("able1998", 1).read_status is (
        ReadStatus.ABSENT
    )


def test_an_entry_s_other_file_is_read_under_its_own_key(tmp_path) -> None:
    cited = write_pdf(tmp_path / "cited.pdf", [OPENING, BODY])
    other = write_pdf(tmp_path / "translation.pdf", [OTHER])
    sources = plain(bib(tmp_path, entry("able1998", cited, other)))
    key = file_key("able1998", file_sha256(other))
    assert sources.work_files("able1998") == ("able1998", key)
    assert sources.page("able1998", 2).pages[0].file_path == str(cited)
    assert "Bob Baker" in sources.page(key, 1).pages[0].text
    assert "able1998's cited edition is cited.pdf" in sources.copy("able1998")


def test_a_file_that_is_not_its_entry_s_work_is_held_back(tmp_path) -> None:
    wrong = write_pdf(tmp_path / "wrong.pdf", [OTHER, BODY])
    sources = plain(bib(tmp_path, entry("able1998", wrong)))
    read = sources.page("able1998", 2)
    assert read.read_status is ReadStatus.MISMATCH and not read.pages
    assert sources.file_stamp("able1998")[0].read_status is ReadStatus.MISMATCH


def test_a_scan_is_served_unchecked_each_page_to_read_as_an_image(tmp_path) -> None:
    """No text layer: the match cannot decide, so the file is not held back;
    each page reads `content`, and every read says to check the file."""
    scan = write_pdf(tmp_path / "scan.pdf", ["", ""], images=[1, 2])
    sources = plain(bib(tmp_path, entry("able1998", scan)))
    read = sources.pages("able1998", 1, 2)
    assert read.read_status is ReadStatus.CONTENT
    assert [p.read_status for p in read.pages] == [ReadStatus.CONTENT] * 2
    assert "read the image" in read.detail
    assert sources.held("able1998") == ""
    assert sources.copy("able1998") == UNCHECKED


def test_a_scan_whose_old_ocr_misses_its_title_is_served_unchecked(
    tmp_path,
) -> None:
    """A scan's layer that fails the match may be its old OCR missing the
    title: the match cannot decide, so the file is served, unchecked. A
    digital file that fails is held back."""
    misread = "A Stahle Resnlt\nAnn Ahle\nWe prove the stable series converges."
    scan = write_pdf(
        tmp_path / "scan.pdf", [misread, BODY], images=[1, 2], hidden=[1, 2]
    )
    digital = write_pdf(tmp_path / "digital.pdf", [misread, BODY])
    sources = plain(bib(tmp_path, entry("able1998", scan), entry("baker1984", digital)))
    assert sources.held("able1998") == "" and sources.copy("able1998") == UNCHECKED
    assert "Theorem 2.1" in sources.page("able1998", 2).pages[0].text
    assert "not its entry's work" in sources.held("baker1984")


def test_an_archive_s_cover_sheet_before_a_scan_leaves_it_unchecked(
    tmp_path,
) -> None:
    """One digital line on a cover sheet, then scanned pages with no
    layer: the title stands on a page the match cannot read."""
    cover = "An archive keeps this journal and extends access to its readers."
    scan = write_pdf(tmp_path / "scan.pdf", [cover, "", ""], images=[2, 3])
    sources = plain(bib(tmp_path, entry("able1998", scan)))
    assert sources.held("able1998") == "" and sources.copy("able1998") == UNCHECKED
    assert sources.page("able1998", 2).read_status is ReadStatus.CONTENT


def test_a_page_under_the_text_floor_reads_as_an_image(tmp_path) -> None:
    pdf = write_pdf(tmp_path / "able.pdf", [OPENING, "Figure 3."])
    read = plain(bib(tmp_path, entry("able1998", pdf))).pages("able1998", 1, 2)
    assert [p.read_status for p in read.pages] == [ReadStatus.OK, ReadStatus.CONTENT]
    assert "able1998 page 2 has no usable text" in read.detail


def test_nothing_searches_and_a_search_says_so(tmp_path) -> None:
    pdf = write_pdf(tmp_path / "able.pdf", [OPENING])
    found = plain(bib(tmp_path, entry("able1998", pdf))).search("stable series")
    assert found.read_status is ReadStatus.INFRASTRUCTURE and not found.pages
    assert "axiomation[library]" in found.detail


def test_a_quotation_without_a_locator_is_left_unsettled_saying_why(tmp_path) -> None:
    from axiomation.sites import Site, SiteKind
    from axiomation.sources.verify import quotation

    pdf = write_pdf(tmp_path / "able.pdf", [OPENING, BODY])
    words = "every bounded gain keeps the stable series within its constant"
    site = Site(
        kind=SiteKind.QUOTATION,
        block="b1",
        handle="sec:setup",
        key="able1998",
        locator=None,
        words=words,
        text=words,
        file="paper.md",
        line=1,
    )
    sources = plain(bib(tmp_path, entry("able1998", pdf)))
    check = quotation.check(sources, site)
    assert check.verdict is None and "axiomation[library]" in check.note
    by_page = quotation.check(sources, replace(site, locator="p.~2"))
    assert by_page.page_index == 2


def test_a_work_s_pages_are_read_once_while_its_file_stands(
    tmp_path, monkeypatch
) -> None:
    from axiomation.sources import pdf as pdf_module

    opened: list[Path] = []
    real = pdf_module.open_pdf

    def counted(path: Path):
        opened.append(path)
        return real(path)

    monkeypatch.setattr(pdf_module, "open_pdf", counted)
    pdf = write_pdf(tmp_path / "able.pdf", [OPENING, BODY])
    sources = plain(bib(tmp_path, entry("able1998", pdf)))
    # the file check reads its first pages' text alone: no page kinds
    assert not sources.held("able1998")
    assert len(opened) == 1
    sources.source_pages("able1998")
    sources.source_pages("able1998")
    sources.page("able1998", 2)
    # its pages served (a short file, its text read whole already): its
    # page kinds read once
    assert len(opened) == 2
    write_pdf(pdf, [OPENING, BODY + " A second edition."])
    assert "second edition" in sources.page("able1998", 2).pages[0].text


def test_without_the_extra_a_fetched_copy_is_kept_as_its_pdf(
    tmp_path, monkeypatch
) -> None:
    """The plain tier's fetch: no OCR, no pages stored beside the copy; its
    pages are its text layer, read when asked; a scan fetched is kept
    unchecked."""
    from axiomation.sources.bib import BibRecord
    from axiomation.sources.read.transient import pages_path, pdf_path
    from axiomation.sources.resolvers import fetch
    from axiomation.sources.resolvers.chain import ResolutionStatus, ResolvedSource

    monkeypatch.setattr(fetch, "library_on", lambda settings: False)
    cache = tmp_path / "cache"
    body = write_pdf(tmp_path / "copy.pdf", [OPENING, BODY]).read_bytes()
    scan = write_pdf(tmp_path / "scan.pdf", [""], images=[1]).read_bytes()
    misread = write_pdf(
        tmp_path / "misread.pdf",
        ["A Stahle Resnlt by Ann Ahle, prose."],
        images=[1],
        hidden=[1],
    ).read_bytes()
    bodies = {
        "https://a.example/copy.pdf": body,
        "https://a.example/scan.pdf": scan,
        "https://a.example/misread.pdf": misread,
    }
    monkeypatch.setattr(fetch, "download", lambda url: (bodies[url], url))
    settings = resolve(load_settings(tmp_path))
    settings = replace(settings, cache_dir=cache)
    work = ResolvedSource("10.1/x", status=ResolutionStatus.EXACT, title=TITLE)
    record = BibRecord("able1998", TITLE, "Able, Ann")
    got = fetch.fetch_source(settings, record, work, url="https://a.example/copy.pdf")
    assert got.read_status is ReadStatus.TRANSIENT and got.pages == 2
    assert pdf_path(cache, "able1998").is_file()
    assert not pages_path(cache, "able1998").exists()
    copy = Sources([Transient(cache, Entries(None), Layers())])
    read = copy.page("able1998", 2)
    assert read.read_status is ReadStatus.TRANSIENT and "Theorem 2.1" in (
        read.pages[0].text
    )
    scanned = fetch.fetch_source(
        settings,
        BibRecord("baker1984", TITLE, "Able, Ann"),
        work,
        url="https://a.example/scan.pdf",
    )
    assert scanned.read_status is ReadStatus.TRANSIENT
    assert copy.copy("baker1984") == UNCHECKED
    assert copy.page("baker1984", 1).read_status is ReadStatus.CONTENT
    # a scan whose old OCR misses the title: kept unchecked, not refused
    kept = fetch.fetch_source(
        settings,
        BibRecord("carr2001", TITLE, "Able, Ann"),
        work,
        url="https://a.example/misread.pdf",
    )
    assert kept.read_status is ReadStatus.TRANSIENT, kept.detail
    # the same in the full tier, whose OCR would read it: refused
    monkeypatch.setattr(fetch, "library_on", lambda settings: True)
    monkeypatch.setattr(fetch, "_extract_pages", fetch._layer_pages)
    refused = fetch.fetch_source(
        settings,
        BibRecord("dunn2002", TITLE, "Able, Ann"),
        work,
        url="https://a.example/misread.pdf",
    )
    assert "not the entry's work" in refused.detail


def test_sources_for_reads_the_pdfs_without_the_library(
    tmp_path, monkeypatch, user_file
) -> None:
    shelf = tmp_path / "pdfs"
    shelf.mkdir()
    write_pdf(shelf / "able1998.pdf", [OPENING, BODY])
    write_user_file(user_file, pdf_dir=str(shelf))
    bib(tmp_path, entry("able1998"))
    monkeypatch.setattr(providers, "library_on", lambda settings: False)
    sources = providers.sources_for(tmp_path)
    assert [p.origin for p in sources.providers] == [Origin.FILE, Origin.TRANSIENT]
    assert sources.page("able1998", 2).read_status is ReadStatus.OK


def test_the_pdf_directory_is_an_absolute_path(tmp_path, user_file) -> None:
    write_user_file(user_file, pdf_dir="~/papers")
    assert resolve(load_settings(tmp_path)).pdf_dir == Path.home() / "papers"
    write_user_file(user_file, pdf_dir="papers")
    with pytest.raises(SourcesError, match="pdf_dir, set in .*must be an absolute"):
        resolve(load_settings(tmp_path))


def test_the_library_s_actions_are_refused_without_it(
    tmp_path, monkeypatch, capsys
) -> None:
    from axiomation.sources import cli

    monkeypatch.setattr(cli, "library_on", lambda settings: False)
    monkeypatch.setattr(providers, "library_on", lambda settings: False)
    bib(tmp_path, entry("able1998"))
    assert cli.main(["ingest", str(tmp_path)]) == 1
    said = capsys.readouterr().err
    assert "`ingest` needs the library, and no qdrant_url is set" in said
    assert "pip install 'axiomation[library]'" in said and "qdrant_url = " in said
    # a search asks no Voyage key here: it says there is no search
    assert cli.main(["search", str(tmp_path), "stable"]) == 1
    assert "axiomation[library]" in capsys.readouterr().out


def test_a_scan_s_layer_is_of_unknown_origin_so_an_ocr_slip_is_unsettled(
    tmp_path,
) -> None:
    """A scan's layer is an old OCR's: a letter it misread is left to a
    reader, never filed as a misquotation; a digital page's is the PDF's
    own text."""
    from axiomation.sites import Site, SiteKind
    from axiomation.sources.verify import quotation

    printed = "the modem theory of the stable series holds within its constant"
    quoted = "the modern theory of the stable series holds within its constant"
    scan = write_pdf(
        tmp_path / "scan.pdf",
        [OPENING, printed + " " + BODY],
        images=[1, 2],
        hidden=[1, 2],
    )
    digital = write_pdf(tmp_path / "digital.pdf", [OPENING, printed + " " + BODY])
    sources = plain(bib(tmp_path, entry("able1998", scan), entry("baker1984", digital)))
    assert sources.page("able1998", 2).pages[0].text_source == ""
    assert sources.page("baker1984", 2).pages[0].text_source == "none"

    def site(key: str) -> Site:
        return Site(
            kind=SiteKind.QUOTATION,
            block="b1",
            handle="sec:setup",
            key=key,
            locator="p.~2",
            words=quoted,
            text=quoted,
            file="paper.md",
            line=1,
        )

    assert quotation.check(sources, site("able1998")).verdict is None
    assert quotation.check(sources, site("baker1984")).verdict is not None


def test_a_found_work_kept_unchecked_says_so_on_every_read(
    tmp_path, monkeypatch
) -> None:
    """A found work has no entry to check it against again: the fetch's
    word that it kept the copy unchecked is kept beside it."""
    from axiomation.sources.resolvers import fetch
    from axiomation.sources.resolvers.chain import (
        Candidate,
        ResolutionStatus,
        ResolvedSource,
    )

    monkeypatch.setattr(fetch, "library_on", lambda settings: False)
    misread = write_pdf(
        tmp_path / "misread.pdf",
        ["A Stahle Resnlt by Ann Ahle, prose.", BODY],
        images=[1, 2],
        hidden=[1, 2],
    ).read_bytes()
    monkeypatch.setattr(fetch, "download", lambda url: (misread, url))
    cache = tmp_path / "cache"
    settings = replace(resolve(load_settings(tmp_path)), cache_dir=cache)
    work = ResolvedSource(
        "10.1/x",
        status=ResolutionStatus.EXACT,
        title=TITLE,
        creators=("Ann Able",),
        candidates=(Candidate("https://a.example/x.pdf", "unpaywall"),),
    )
    got = fetch.fetch_found(settings, "doi:10.1/x", lambda doi: work)
    assert got.read_status is ReadStatus.TRANSIENT, got.detail
    copy = Sources([Transient(cache, Entries(None), Layers())])
    assert copy.copy("doi:10.1/x") == UNCHECKED
    assert copy.page("doi:10.1/x", 2).read_status is ReadStatus.TRANSIENT


def test_a_directory_not_on_this_machine_is_an_outage_not_an_absence(
    tmp_path,
) -> None:
    """A drive not mounted lapses nothing: the read cannot tell."""
    gone = tmp_path / "unmounted"
    sources = plain(bib(tmp_path, entry("able1998")), gone)
    read = sources.page("able1998", 1)
    assert read.read_status is ReadStatus.INFRASTRUCTURE
    assert "pdf_dir" in read.detail
    stamp, why = sources.file_stamp("able1998")
    assert stamp.read_status is ReadStatus.INFRASTRUCTURE and why
    lost = plain(tmp_path / "elsewhere" / "references.bib").page("able1998", 1)
    assert lost.read_status is ReadStatus.INFRASTRUCTURE
    assert "not on this machine" in lost.detail


def test_a_file_replaced_under_its_key_is_checked_again(tmp_path) -> None:
    pdf = write_pdf(tmp_path / "able.pdf", [OPENING, BODY])
    sources = plain(bib(tmp_path, entry("able1998", pdf)))
    assert sources.held("able1998") == ""
    write_pdf(pdf, [OTHER, BODY])
    assert "not its entry's work" in sources.held("able1998")
    assert sources.page("able1998", 2).read_status is ReadStatus.MISMATCH


def test_a_person_s_word_on_an_unchecked_file_is_kept(
    tmp_path, monkeypatch, capsys
) -> None:
    from axiomation.sources import cli

    monkeypatch.setattr(cli, "library_on", lambda settings: False)
    monkeypatch.setattr(providers, "library_on", lambda settings: False)
    scan = write_pdf(tmp_path / "scan.pdf", [""], images=[1])
    bib_path = bib(tmp_path, entry("able1998", scan))
    (tmp_path / "axiomation.toml").write_text(
        '[paper]\nbib = "references.bib"\n', encoding="utf-8"
    )
    assert plain(bib_path).copy("able1998") == UNCHECKED
    code = cli.main(
        ["confirm", str(tmp_path), "able1998", "--why", "its title page reads so"]
    )
    out = capsys.readouterr().out
    assert code == 0, out
    assert "it was unchecked" in out
    assert plain(bib_path).copy("able1998") == (
        "a person confirmed its file as its entry's work: its title page reads so"
    )


def test_an_undecided_cited_edition_reads_each_file_under_its_own_key(
    tmp_path,
) -> None:
    """Two files and nothing telling which the entry cites: the work's key
    reads nothing, each file reads under its own, and a read says why."""
    one = write_pdf(tmp_path / "one.pdf", [OPENING, BODY])
    two = write_pdf(tmp_path / "two.pdf", [OPENING, BODY + " Revised."])
    sources = plain(bib(tmp_path, entry("able1998", one, two)))
    assert sources.page("able1998", 1).read_status is ReadStatus.ABSENT
    assert "cited edition is not decided" in sources.copy("able1998")
    for path in (one, two):
        key = file_key("able1998", file_sha256(path))
        assert sources.page(key, 2).read_status is ReadStatus.OK


def test_the_pdf_directory_serves_an_entry_whose_file_is_elsewhere(tmp_path) -> None:
    shelf = tmp_path / "pdfs"
    shelf.mkdir()
    write_pdf(shelf / "able1998.pdf", [OPENING, BODY])
    sources = plain(
        bib(tmp_path, entry("able1998", tmp_path / "gone" / "able.pdf")), shelf
    )
    assert sources.page("able1998", 2).pages[0].file_path == str(shelf / "able1998.pdf")


def test_a_scan_s_page_renders_as_an_image(tmp_path) -> None:
    from axiomation.sources.read.render import render

    scan = write_pdf(tmp_path / "scan.pdf", [""], images=[1])
    rendered = render(plain(bib(tmp_path, entry("able1998", scan))), "able1998", 1)
    assert rendered.png.startswith(b"\x89PNG")
    assert rendered.read_status is ReadStatus.CONTENT


def test_with_the_extra_the_library_is_read_not_the_pdfs(tmp_path, monkeypatch) -> None:
    pytest.importorskip("qdrant_client")
    monkeypatch.setattr(providers, "library_on", lambda settings: True)
    sources = providers.sources_for(tmp_path)
    assert [p.origin for p in sources.providers] == [
        Origin.LIBRARY,
        Origin.TRANSIENT,
    ]


def test_qdrant_url_with_the_extra_switches_the_library_on(
    tmp_path, monkeypatch, user_file
) -> None:
    """No Qdrant is assumed: the library is read where the person names
    one and the extra is installed; else each work's PDF, and why."""
    import importlib.util

    from axiomation.sources import config

    bib(tmp_path, entry("able1998"))
    settings = resolve(load_settings(tmp_path))
    assert settings.qdrant_url == ""
    named = replace(settings, qdrant_url="http://localhost:65535")
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    assert config.library_off(settings) == "no qdrant_url is set"
    assert config.library_on(named) and not config.library_off(named)
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert not config.library_on(named)
    assert config.library_off(named) == (
        "qdrant_url is set, and the `[library]` extra is not installed"
    )
    assert config.library_off(settings) == (
        "no qdrant_url is set, and the `[library]` extra is not installed"
    )


def test_status_lists_each_entry_by_where_its_pdf_is_read(
    tmp_path, capsys, user_file
) -> None:
    import json
    import re

    from axiomation.sources import cli

    good = write_pdf(tmp_path / "able.pdf", [OPENING, BODY])
    wrong = write_pdf(tmp_path / "wrong.pdf", [OTHER, BODY])
    scan = write_pdf(tmp_path / "scan.pdf", [""], images=[1])
    bib(
        tmp_path,
        entry("able1998", good),
        entry("baker1984", wrong),
        entry("carr2001", scan),
        entry("dunn2010"),
    )
    (tmp_path / "axiomation.toml").write_text(
        '[paper]\nbib = "references.bib"\n', encoding="utf-8"
    )
    assert cli.main(["status", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "plain: each cited work's PDF, read here (no qdrant_url is set" in out
    assert re.search(r"entries\s+4", out) and re.search(r"read here\s+1", out)
    assert "carr2001" in out.split("not checked against its entry")[1]
    assert "baker1984: baker1984's file is not its entry's work" in out
    assert "1 source(s) with no PDF here" in out and "dunn2010" in out
    # a PDF directory not on this machine is an outage, not a work absent
    write_user_file(user_file, pdf_dir=str(tmp_path / "unmounted"))
    assert cli.main(["status", str(tmp_path), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert not payload["library"] and payload["contract"] is None
    by_key = {w["bib_key"]: w for w in payload["works"]}
    assert by_key["able1998"]["read_status"] == "ok"
    assert by_key["able1998"]["origin"] == "file"
    assert by_key["carr2001"]["unchecked"]
    assert by_key["baker1984"]["read_status"] == "mismatch"
    assert by_key["dunn2010"]["read_status"] == "infrastructure"
    assert "is not on this machine" in by_key["dunn2010"]["detail"]


def test_resolve_says_a_pdf_read_here_is_not_indexed(tmp_path) -> None:
    from axiomation.sources.read.resolve import resolve as resolve_key

    pdf = write_pdf(tmp_path / "able.pdf", [OPENING, BODY])
    found = resolve_key(
        plain(bib(tmp_path, entry("able1998", pdf))),
        "able1998",
        zotero_url="http://127.0.0.1:1",
    )
    assert found.read_status is ReadStatus.OK and not found.indexed
    assert found.file_path == str(pdf) and found.file_exists


def test_status_names_an_undecided_edition_a_fetched_copy_and_a_lost_bibliography(
    tmp_path, capsys, user_file
) -> None:
    import json

    from axiomation.sources import cli
    from axiomation.sources.read.transient import pdf_path

    one = write_pdf(tmp_path / "one.pdf", [OPENING, BODY])
    two = write_pdf(tmp_path / "two.pdf", [OPENING, BODY + " Revised."])
    bib(tmp_path, entry("able1998", one, two), entry("fay2011"))
    (tmp_path / "axiomation.toml").write_text(
        '[paper]\nbib = "references.bib"\n', encoding="utf-8"
    )
    cache = resolve(load_settings(tmp_path)).cache_dir
    assert cache is not None
    pdf_path(cache, "fay2011").parent.mkdir(parents=True)
    write_pdf(pdf_path(cache, "fay2011"), [OPENING, BODY])
    assert cli.main(["status", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "1 source(s) whose entry lists several files" in out
    assert "--cited" in out and "with no PDF here" not in out
    assert "fetched    1: fay2011" in out
    # a bibliography named and not on this machine: an outage, exit 1
    (tmp_path / "axiomation.toml").write_text(
        '[paper]\nbib = "gone.bib"\n', encoding="utf-8"
    )
    assert cli.main(["status", str(tmp_path), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["works"] == [] and "gone.bib" in payload["outage"]
