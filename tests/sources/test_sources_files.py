"""An entry holding several files: which is the edition it cites, what
the others are, their keys, the confirmations that name them, and what
prune drops. The papers are written for these tests: a cited article in
one language, its translation, a paper and its supplement.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("pypdfium2")

from axiomation.evidence import work_of  # noqa: E402
from axiomation.sources import confirm  # noqa: E402
from axiomation.sources.bib import (  # noqa: E402
    BibRecord,
    listed_pdfs,
    pdf_paths,
    records,
)
from axiomation.sources.files import (  # noqa: E402
    UNDECIDED_LABEL,
    UNLABELLED,
    Chosen,
    choose,
    file_key,
    file_sha256,
    stale,
)
from axiomation.sources.match import page_range, prints_pages  # noqa: E402
from sources.pdfs import write_pdf  # noqa: E402

TITLE = "Bounds on Shuffled Walks"
KEY = "quist1974"

#: The cited edition: another language, its pages 30 to 32 printed.
ORIGINAL = [
    "Schranken fur gemischte Wege\nT. Quist\nEine Arbeit uber Wege.\n30",
    "Schranken fur gemischte Wege 31\nDer zweite Abschnitt zeigt die Schranke.",
    "32 T. Quist\nDer dritte Abschnitt schliesst.",
]
#: Its translation: the entry's title and author, its own page numbers.
TRANSLATION = [
    f"{TITLE}\nTobias Quist\nTranslated from the journal, pp. 30-32.\n206",
    "Tobias Quist 207\nThe second section shows the bound.",
    "208 Bounds on Shuffled Walks\nThe third section closes.",
]
#: A paper and its supplement, which prints no title page.
PAPER = [f"{TITLE}\nTobias Quist\nWe bound shuffled walks.", "The proof follows."]
SUPPLEMENT = ["A. Proofs of the lemmas\nThe first lemma follows from the second."]


def record(*files: Path, pages: str = "30--35") -> BibRecord:
    return BibRecord(
        KEY,
        TITLE,
        "Quist, Tobias",
        year="1974",
        kind="article",
        pages=pages,
        files=files,
    )


@pytest.fixture
def two(tmp_path: Path) -> tuple[Path, Path]:
    """The translation listed first, the original second, apart."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    return (
        write_pdf(tmp_path / "a" / "translation.pdf", TRANSLATION),
        write_pdf(tmp_path / "b" / "original.pdf", ORIGINAL),
    )


# --- the bibliography's file field ---


def test_a_file_field_names_each_pdf_once_in_its_order(tmp_path: Path) -> None:
    one = write_pdf(tmp_path / "one.pdf", ["x"])
    two = write_pdf(tmp_path / "two.pdf", ["y"])
    field = f"First:{one}:application/pdf;{two};{one};{tmp_path}/page.html"
    assert pdf_paths(field) == (one, two)
    gone = tmp_path / "gone.pdf"
    assert listed_pdfs(f"{one};{gone}") == (one, gone)
    assert pdf_paths(f"{one};{gone}") == (one,)


def test_the_entry_records_its_files_pages_and_the_ones_not_here(
    tmp_path: Path, two: tuple[Path, Path]
) -> None:
    bib = tmp_path / "references.bib"
    bib.write_text(
        f"@article{{{KEY},\n  title = {{{TITLE}}},\n  pages = {{30--35}},\n"
        f"  file = {{{two[0]};{two[1]};{tmp_path}/gone.pdf}},\n}}\n",
        encoding="utf-8",
    )
    found = records(bib)[KEY]
    assert found.files == two and found.unseen == 1 and found.pages == "30--35"


# --- the cited edition ---


def test_the_cited_edition_is_the_one_printing_the_entrys_pages(
    two: tuple[Path, Path],
) -> None:
    """The translation comes first and prints the entry's title; the
    original prints the entry's pages, so it is the cited edition."""
    choice = choose(record(*two), {})
    assert choice.chosen is Chosen.PAGES
    assert choice.cited is not None and choice.cited.path == two[1]
    assert choice.cited.key == KEY
    (other,) = choice.others
    assert other.path == two[0] and other.label == UNLABELLED
    assert other.key == file_key(KEY, file_sha256(two[0]))
    assert other.key == f"{KEY}@{file_sha256(two[0])[:8]}"
    assert work_of(other.key) == KEY
    said = choice.said()
    assert "the one printing the entry's pages, 30--35" in said
    assert str(two[0]) in said and "another file of the entry" in said


def test_the_order_of_the_files_decides_nothing(two: tuple[Path, Path]) -> None:
    first = choose(record(*two), {})
    turned = choose(record(two[1], two[0]), {})
    assert first.cited == turned.cited
    assert {f.key for f in first.files} == {f.key for f in turned.files}


def test_the_one_title_page_decides_where_no_file_prints_the_pages(
    tmp_path: Path,
) -> None:
    paper = write_pdf(tmp_path / "paper.pdf", PAPER)
    supplement = write_pdf(tmp_path / "supplement.pdf", SUPPLEMENT)
    choice = choose(record(supplement, paper, pages=""), {})
    assert choice.chosen is Chosen.TITLE
    assert choice.cited is not None and choice.cited.path == paper


def test_two_title_pages_leave_it_undecided(tmp_path: Path) -> None:
    one = write_pdf(tmp_path / "one.pdf", PAPER)
    other = write_pdf(tmp_path / "other.pdf", [*PAPER, "A last page."])
    choice = choose(record(one, other, pages=""), {})
    assert choice.chosen is Chosen.UNDECIDED and choice.cited is None
    said = choice.said()
    assert "cited edition is not decided" in said
    assert f"sources confirm DIR {KEY} --file PATH --cited" in said
    assert all(f.key.startswith(f"{KEY}@") for f in choice.files)


def test_a_file_whose_text_does_not_read_leaves_the_title_rule_undecided(
    tmp_path: Path,
) -> None:
    """A scan without a layer may be the cited edition: the one title page
    found among the files read does not decide it."""
    paper = write_pdf(tmp_path / "paper.pdf", PAPER)
    scan = write_pdf(tmp_path / "scan.pdf", ["", ""])
    assert choose(record(scan, paper, pages=""), {}).chosen is Chosen.UNDECIDED


def test_files_of_the_same_bytes_are_one(tmp_path: Path) -> None:
    one = write_pdf(tmp_path / "one.pdf", PAPER)
    copy = tmp_path / "copy.pdf"
    copy.write_bytes(one.read_bytes())
    choice = choose(record(one, copy), {})
    assert not choice.several and choice.chosen is Chosen.ONE
    assert choice.cited is not None and choice.cited.key == KEY


def test_a_person_names_the_cited_edition_and_labels_the_other(
    tmp_path: Path, two: tuple[Path, Path]
) -> None:
    bib = tmp_path / "references.bib"
    confirm.write(bib, KEY, file_sha256(two[0]), "the cited translation")
    confirm.write(bib, KEY, file_sha256(two[1]), "the original", cited=False)
    choice = choose(record(*two), confirm.load(bib))
    assert choice.chosen is Chosen.PERSON and choice.cited is not None
    assert choice.cited.path == two[0] and choice.why == "the cited translation"
    (other,) = choice.others
    assert other.label == "the original"


def test_page_numbers_are_read_at_a_pages_edges() -> None:
    assert page_range("30--35") == (30, 35)
    assert page_range("{206}".strip("{}")) == (206, 206)
    assert page_range("e1234") is None
    edges = {1: "Title\n30", 2: "Running head 31\nbody", 3: "body\n9"}
    assert prints_pages(edges, 30, 35)
    middle = "Running head\nfirst line\nbody 31 in the middle\nnext line\nend"
    assert not prints_pages({1: "Title\n30", 2: middle}, 30, 35)


# --- confirmations, one a file ---


def test_an_earlier_confirmation_reads_as_the_cited_edition(tmp_path: Path) -> None:
    bib = tmp_path / "references.bib"
    confirm.path_for(bib).write_text(
        json.dumps({KEY: {"file_sha256": "a" * 64, "why": "a scan", "when": "x"}}),
        encoding="utf-8",
    )
    found = confirm.load(bib)
    held = confirm.cited(found, KEY)
    assert held is not None and held.why == "a scan"


def test_a_cited_confirmation_replaces_the_earlier_cited_one(tmp_path: Path) -> None:
    bib = tmp_path / "references.bib"
    confirm.write(bib, KEY, "a" * 64, "the first")
    path = confirm.write(bib, KEY, "b" * 64, "the label", cited=False)
    assert isinstance(json.loads(path.read_text())[KEY], list)
    confirm.write(bib, KEY, "c" * 64, "the second")
    found = confirm.load(bib)
    assert [c.file_sha256[0] for c in found[KEY]] == ["c", "b"]
    held = confirm.cited(found, KEY)
    assert held is not None and held.why == "the second"
    confirm.write(bib, "other", "d" * 64, "one")
    assert "cited" not in json.loads(path.read_text())["other"]  # the old shape


# --- prune ---


def test_a_files_key_is_stale_once_the_file_leaves_its_entry(
    tmp_path: Path, two: tuple[Path, Path]
) -> None:
    translation = file_key(KEY, file_sha256(two[0]))
    held = {KEY: record(*two)}
    assert not stale(KEY, held, {}) and not stale(translation, held, {})
    assert stale(translation, {KEY: record(two[1])}, {})  # the file removed
    unseen = {KEY: BibRecord(KEY, TITLE, files=(two[1],), unseen=1)}
    assert not stale(translation, unseen, {})  # it may be the one not here
    assert stale(translation, {}, {}) and stale(KEY, {}, {})


# --- the corpus: ingest, the library's reads, confirm and status ---


@pytest.fixture
def shelved(
    monkeypatch: pytest.MonkeyPatch,
    sources_paper: Path,
    fake_qdrant,
    settings,
    two: tuple[Path, Path],
):
    """The entry lists the translation first and the original second; the
    corpus holds the translation under the entry's key, as an ingest that
    took the first file did."""
    from axiomation.sources.library import client as client_module
    from axiomation.sources.library import qdrant as qdrant_module

    monkeypatch.setenv("AXIOMATION_VOYAGE_API_KEY", "fixture-key")
    monkeypatch.setattr(qdrant_module, "connect", lambda _: fake_qdrant)
    monkeypatch.setattr(client_module, "connect", lambda _: fake_qdrant)
    (sources_paper / "references.bib").write_text(
        f"@article{{{KEY},\n  title = {{{TITLE}}},\n  author = {{Quist, Tobias}},\n"
        f"  year = {{1974}},\n  pages = {{30--35}},\n"
        f"  file = {{{two[0]};{two[1]}}},\n}}\n",
        encoding="utf-8",
    )
    stock(fake_qdrant, settings.collection, KEY, TRANSLATION, file_sha256(two[0]))
    return fake_qdrant


def stock(fake, collection: str, key: str, pages: list[str], sha: str) -> None:
    """Pages of one file under `key`, each with the vectors a move carries."""
    from qdrant_client import models

    for index, text in enumerate(pages, start=1):
        point = fake.add_chunk(
            collection,
            bib_key=key,
            title=TITLE,
            author="Quist, Tobias",
            page_number=index,
            chunk_index=index - 1,
            text=text + " " + "z" * 60,
            file_sha256=sha,
        )
        point.vector = {
            "": [float(index)] * 8,
            "sparse": models.SparseVector(indices=[index], values=[1.0]),
        }


def run(root: Path, *argv: str) -> int:
    from axiomation.sources.cli import main

    action, *rest = argv
    return main([action, str(root), *rest, "--store", str(root.parent / "store")])


def test_an_ingest_reads_the_cited_edition_under_the_key_and_the_other_beside(
    shelved, sources_paper: Path, settings, two: tuple[Path, Path]
) -> None:
    from axiomation.sources.ingest import run as run_module
    from axiomation.sources.ingest.bib import read

    entries, undecided = read(sources_paper / "references.bib")
    assert not undecided
    translation = file_key(KEY, file_sha256(two[0]))
    assert [(e.key, e.file_path, e.label) for e in entries] == [
        (translation, two[0], UNLABELLED),
        (KEY, two[1], ""),
    ]
    plan = run_module.plan_ingest(
        shelved, settings, bib_path=sources_paper / "references.bib", key=KEY
    )
    reasons = {item.entry.key: item.reason.value for item in plan.pending}
    assert reasons == {KEY: "changed"}  # the original, read
    # the translation the corpus holds under the key moves to its own
    assert [(m.source, m.entry.key) for m in plan.moves] == [(KEY, translation)]


def test_the_library_says_its_pages_are_not_the_cited_edition(
    shelved, sources_paper: Path, two: tuple[Path, Path]
) -> None:
    """The stored translation still reads, its pages kept: readings stand
    on them. What it is, is said."""
    from axiomation.evidence import ReadStatus
    from axiomation.sources.providers import sources_for

    sources = sources_for(sources_paper)
    assert sources.page(KEY, 1).read_status is ReadStatus.OK
    said = sources.copy(KEY)
    assert said.startswith(f"{KEY}'s files in the corpus: `{KEY}`: translation.pdf")
    assert "not the cited edition, which is original.pdf" in said
    assert f"not yet read under `{KEY}`" in said
    translation = file_key(KEY, file_sha256(two[0]))
    assert translation not in said  # its pages are under the key
    assert sources.other_files(KEY) == ()


def test_once_both_are_held_each_reads_as_what_it_is(
    shelved, sources_paper: Path, settings, two: tuple[Path, Path]
) -> None:
    """The original's title page is in its own language, but it prints the
    entry's pages as its own: it reads as the cited edition, no person
    asked. The translation, another file, is never held to the entry's
    title page."""
    from axiomation.evidence import ReadStatus
    from axiomation.sources.providers import sources_for

    translation = file_key(KEY, file_sha256(two[0]))
    shelved.collections[settings.collection].clear()
    stock(shelved, settings.collection, KEY, ORIGINAL, file_sha256(two[1]))
    stock(shelved, settings.collection, translation, TRANSLATION, file_sha256(two[0]))
    sources = sources_for(sources_paper)
    assert sources.page(KEY, 1).read_status is ReadStatus.OK
    assert sources.page(translation, 1).read_status is ReadStatus.OK
    assert sources.other_files(KEY) == (translation,)
    said = sources.copy(translation)
    assert said == sources.copy(KEY)  # every key of the work says the whole set
    assert f"`{KEY}`: original.pdf" in said and "the cited edition, the one" in said
    assert f"`{translation}`: translation.pdf" in said and UNLABELLED in said

    bib = sources_paper / "references.bib"
    confirm.write(bib, KEY, file_sha256(two[1]), "the original", cited=True)
    sources = sources_for(sources_paper)
    assert sources.page(KEY, 1).read_status is ReadStatus.OK
    assert "original.pdf (" in sources.copy(KEY)
    assert "the cited edition, named by a person: the original" in sources.copy(KEY)


def test_confirm_names_a_file_of_the_entry_and_says_the_choice(
    shelved, sources_paper: Path, two: tuple[Path, Path], capsys
) -> None:
    assert run(sources_paper, "confirm", KEY, "--why", "x") == 1
    assert f"{KEY} holds 2 files: name the one confirmed with --file" in (
        capsys.readouterr().err
    )
    elsewhere = sources_paper / "elsewhere.pdf"
    assert (
        run(sources_paper, "confirm", KEY, "--file", str(elsewhere), "--why", "x") == 1
    )
    assert f"is not a file of {KEY}'s entry" in capsys.readouterr().err

    why = "the English translation"
    assert run(sources_paper, "confirm", KEY, "--file", str(two[0]), "--why", why) == 0
    out = capsys.readouterr().out
    assert f"({file_sha256(two[0])[:12]}) as another file of {KEY}: {why}" in out
    assert f"{KEY}'s cited edition is {two[1]} (" in out
    held = confirm.load(sources_paper / "references.bib")[KEY]
    assert [(c.why, c.cited) for c in held] == [(why, False)]

    assert (
        run(
            sources_paper,
            "confirm",
            KEY,
            "--file",
            str(two[0]),
            "--cited",
            "--why",
            "y",
        )
        == 0
    )
    out = capsys.readouterr().out
    assert f"as the edition {KEY} cites" in out
    assert "named by a person: y" in out
    assert "reads this one under it" not in out  # the corpus holds this file


def test_status_lists_the_entrys_files_and_what_the_corpus_holds(
    shelved, sources_paper: Path, capsys
) -> None:
    run(sources_paper, "status")
    out = capsys.readouterr().out
    assert "1 source(s) whose entry holds several files:" in out
    assert f"  {KEY}'s cited edition is " in out
    assert "translation.pdf under quist1974, not the cited edition" in out


# --- what a choice must not take for the cited edition ---


def test_a_listed_file_not_here_leaves_one_file_undecided(
    two: tuple[Path, Path],
) -> None:
    """The file not on this machine may be the cited edition: the one here
    is not taken for it by being alone, though the entry's pages still
    tell."""
    alone = BibRecord(KEY, TITLE, "Quist, Tobias", files=(two[0],), unseen=1)
    choice = choose(alone, {})
    assert choice.chosen is Chosen.UNDECIDED and choice.several
    assert "and lists 1 not on this machine" in choice.said()
    printed = BibRecord(KEY, TITLE, pages="30--35", files=(two[1],), unseen=1)
    assert choose(printed, {}).chosen is Chosen.PAGES


def test_a_file_a_person_labelled_is_never_the_cited_edition_by_a_rule(
    tmp_path: Path, two: tuple[Path, Path]
) -> None:
    """Without the entry's pages, the translation alone prints the
    entry's title; a person's label takes it out of the rules."""
    assert choose(record(*two, pages=""), {}).cited.path == two[0]
    bib = tmp_path / "references.bib"
    confirm.write(bib, KEY, file_sha256(two[0]), "the translation", cited=False)
    choice = choose(record(*two, pages=""), confirm.load(bib))
    assert choice.chosen is Chosen.UNDECIDED
    assert {f.label for f in choice.files} == {"the translation", UNDECIDED_LABEL}


def test_a_file_printing_another_editions_pages_is_not_the_cited_one(
    tmp_path: Path,
) -> None:
    """The original prints no page numbers here; the translation prints
    its own, 206 on: it is another edition, so the one title page it
    prints does not make it the cited one."""
    (tmp_path / "a").mkdir()
    translation = write_pdf(tmp_path / "a" / "translation.pdf", TRANSLATION)
    unnumbered = ["Schranken fur gemischte Wege\nT. Quist", "Der zweite Abschnitt."]
    plain = write_pdf(tmp_path / "original.pdf", unnumbered)
    assert choose(record(translation, plain), {}).chosen is Chosen.UNDECIDED


def test_an_equation_number_is_no_page_number() -> None:
    page = "Running head\nfirst\nbody\nsecond line\n(31)"
    later = "Running head\nfirst\nbody\nsecond line\n[32]"
    assert not prints_pages({1: page, 2: later}, 30, 35)


def test_a_label_does_not_clear_the_hold_on_the_entrys_key(tmp_path: Path) -> None:
    from axiomation.sources.match import Check

    bib = tmp_path / "references.bib"
    confirm.write(bib, KEY, "a" * 64, "the supplement", cited=False)
    kept = confirm.load(bib)
    failed = Check(False, "its first 4 pages with text print no title page")
    assert confirm.hold(KEY, failed, kept, "a" * 64)
    confirm.write(bib, KEY, "a" * 64, "the cited scan")
    assert not confirm.hold(KEY, failed, confirm.load(bib), "a" * 64)


def test_a_file_now_the_cited_edition_keeps_its_old_key_until_it_is_read(
    tmp_path: Path, two: tuple[Path, Path]
) -> None:
    bib = tmp_path / "references.bib"
    confirm.write(bib, KEY, file_sha256(two[0]), "the cited translation")
    translation = file_key(KEY, file_sha256(two[0]))
    held = {KEY: record(*two)}
    kept = confirm.load(bib)
    before = {KEY: file_sha256(two[1]), translation: file_sha256(two[0])}
    assert not stale(translation, held, kept, before)
    after = {KEY: file_sha256(two[0]), translation: file_sha256(two[0])}
    assert stale(translation, held, kept, after)


def test_a_key_holding_the_file_mark_is_refused(tmp_path: Path) -> None:
    from axiomation.sources.config import SourcesError

    bib = tmp_path / "references.bib"
    bib.write_text("@article{odd@key,\n  title = {T},\n}\n", encoding="utf-8")
    with pytest.raises(SourcesError, match="odd@key holds '@'"):
        records(bib)


# --- an undecided entry, moves, and the work's files read together ---


def paper_entry(sources_paper: Path, *files: Path, pages: str = "") -> Path:
    bib = sources_paper / "references.bib"
    bib.write_text(
        f"@article{{{KEY},\n  title = {{{TITLE}}},\n  author = {{Quist, Tobias}},\n"
        f"  year = {{1974}},\n  pages = {{{pages}}},\n"
        f"  file = {{{';'.join(map(str, files))}}},\n}}\n",
        encoding="utf-8",
    )
    return bib


@pytest.fixture
def ours(monkeypatch: pytest.MonkeyPatch, fake_qdrant, settings):
    """A corpus axiomation made, behind the commands."""
    from axiomation.sources.library import client as client_module
    from axiomation.sources.library import qdrant as qdrant_module
    from axiomation.sources.library.upsert import ensure_collection

    monkeypatch.setenv("AXIOMATION_VOYAGE_API_KEY", "fixture-key")
    monkeypatch.setattr(qdrant_module, "connect", lambda _: fake_qdrant)
    monkeypatch.setattr(client_module, "connect", lambda _: fake_qdrant)
    fake_qdrant.collections.pop(settings.collection, None)
    ensure_collection(fake_qdrant, settings)
    return fake_qdrant


@pytest.fixture
def copies(tmp_path: Path) -> tuple[Path, Path]:
    """Two files of one paper that both print its title page: undecided."""
    (tmp_path / "c").mkdir()
    return (
        write_pdf(tmp_path / "c" / "short.pdf", PAPER),
        write_pdf(tmp_path / "c" / "long.pdf", [*PAPER, "A last page."]),
    )


def no_embedding(monkeypatch: pytest.MonkeyPatch) -> None:
    from axiomation.sources.ingest import run as run_module

    def refuse(*_: object, **__: object) -> None:
        raise AssertionError("a move embeds nothing")

    monkeypatch.setattr(run_module, "_ingest_entry", refuse)


def test_an_undecided_entry_holds_each_file_and_keeps_its_stored_one(
    ours, sources_paper: Path, settings, copies: tuple[Path, Path]
) -> None:
    from axiomation.sources.ingest import run as run_module

    bib = paper_entry(sources_paper, *copies)
    stock(ours, settings.collection, KEY, PAPER, file_sha256(copies[0]))
    plan = run_module.plan_ingest(ours, settings, bib_path=bib)
    longer = file_key(KEY, file_sha256(copies[1]))
    assert [(i.entry.key, i.reason.value) for i in plan.pending] == [(longer, "new")]
    assert [c.key for c in plan.undecided] == [KEY] and not plan.moves


def test_an_undecided_entry_never_stored_holds_every_file_beside_its_key(
    ours, sources_paper: Path, settings, copies: tuple[Path, Path]
) -> None:
    from axiomation.sources.ingest import run as run_module
    from axiomation.sources.providers import sources_for

    bib = paper_entry(sources_paper, *copies)
    plan = run_module.plan_ingest(ours, settings, bib_path=bib)
    keys = sorted(file_key(KEY, file_sha256(f)) for f in copies)
    assert sorted(i.entry.key for i in plan.pending) == keys
    for path in copies:
        stock(
            ours,
            settings.collection,
            file_key(KEY, file_sha256(path)),
            PAPER,
            file_sha256(path),
        )
    sources = sources_for(sources_paper)
    assert sorted(sources.other_files(KEY)) == keys
    said = sources.copy(KEY)
    assert f"`{KEY}`: nothing, the cited edition not decided" in said
    assert all(f"`{k}`" in said for k in keys)


def test_a_file_named_the_cited_edition_moves_under_the_key_with_no_embedding(
    ours,
    sources_paper: Path,
    settings,
    copies: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The short file was kept under the key while undecided, the long one
    beside it; a person names the long one: the two trade keys, their
    pages and records carried, nothing read or embedded."""
    from axiomation.sources.ingest import run as run_module

    bib = paper_entry(sources_paper, *copies)
    short, long = (file_sha256(f) for f in copies)
    stock(ours, settings.collection, KEY, ["the short file"], short)
    stock(ours, settings.collection, file_key(KEY, long), ["the long file"], long)
    confirm.write(bib, KEY, long, "the paper as published")
    plan = run_module.plan_ingest(ours, settings, bib_path=bib)
    assert not plan.pending
    assert sorted((m.source, m.entry.key) for m in plan.moves) == sorted(
        [(file_key(KEY, long), KEY), (KEY, file_key(KEY, short))]
    )
    no_embedding(monkeypatch)
    report = run_module.apply_ingest(ours, settings, plan, api_key="k")
    assert report.moved == 2 and report.tokens == 0
    by_key = {
        p.payload["bib_key"]: p.payload
        for p in ours.points(settings.collection)
        if "bib_key" in p.payload
    }
    assert by_key[KEY]["text"].startswith("the long file")
    assert by_key[KEY]["file_sha256"] == long
    assert by_key[file_key(KEY, short)]["text"].startswith("the short file")
    assert by_key[KEY]["file_path"] == str(copies[1])
    again = run_module.plan_ingest(ours, settings, bib_path=bib)
    assert not again.pending and not again.moves


def test_one_grep_reads_every_file_of_a_work_and_says_which(
    shelved, sources_paper: Path, settings, two: tuple[Path, Path], capsys
) -> None:
    translation = file_key(KEY, file_sha256(two[0]))
    shelved.collections[settings.collection].clear()
    stock(shelved, settings.collection, KEY, ORIGINAL, file_sha256(two[1]))
    stock(shelved, settings.collection, translation, TRANSLATION, file_sha256(two[0]))
    assert run(sources_paper, "grep", KEY, "section") == 0
    out = capsys.readouterr().out
    first, *_ = out.splitlines()
    assert first.startswith(f"{KEY}: {KEY}'s files in the corpus: `{KEY}`")
    heads = [line for line in out.splitlines() if "/section/" in line]
    assert heads == [
        f"{KEY}  /section/  [ok]",  # the original's words are its own language
        f"{translation}  /section/  [ok]  2 line(s) on 2 page(s)",
    ]
    assert run(sources_paper, "page", translation, "1") == 0
    assert capsys.readouterr().out.startswith(f"{translation}: {KEY}'s files in")


def test_a_move_carries_the_entrys_metadata(
    ours,
    sources_paper: Path,
    settings,
    copies: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A title fixed in the entry since the file was read: the moved pages
    carry the new one, so no later ingest sees a stale payload as current."""
    from axiomation.sources.ingest import run as run_module

    bib = paper_entry(sources_paper, *copies)
    short, long = (file_sha256(f) for f in copies)
    stock(ours, settings.collection, KEY, ["one", "two", "three"], short)
    stock(ours, settings.collection, file_key(KEY, long), ["only"], long)
    for point in ours.points(settings.collection):
        if "bib_key" in point.payload:
            point.payload["title"] = "An Old Title"
    confirm.write(bib, KEY, long, "the paper as published")
    plan = run_module.plan_ingest(ours, settings, bib_path=bib)
    no_embedding(monkeypatch)
    run_module.apply_ingest(ours, settings, plan, api_key="k")
    points = [p for p in ours.points(settings.collection) if "bib_key" in p.payload]
    assert {p.payload["title"] for p in points} == {TITLE}
    counts = {}
    for p in points:
        counts[p.payload["bib_key"]] = counts.get(p.payload["bib_key"], 0) + 1
    assert counts == {KEY: 1, file_key(KEY, short): 3}  # no page left behind
    assert all(p.vector for p in points)
    assert not run_module.plan_ingest(ours, settings, bib_path=bib).metadata_only


def test_a_plain_move_writes_before_it_clears(
    ours, sources_paper: Path, settings, two: tuple[Path, Path], monkeypatch
) -> None:
    """The translation stored under the key moves beside it while the key
    is read anew: a failure writing the move leaves the file where it was."""
    from axiomation.sources.ingest import run as run_module

    bib = paper_entry(sources_paper, *two, pages="30--35")
    stock(ours, settings.collection, KEY, TRANSLATION, file_sha256(two[0]))
    plan = run_module.plan_ingest(ours, settings, bib_path=bib)
    (move,) = plan.moves

    marker = ours.upsert

    def down(collection_name: str, points: list) -> None:
        if any("bib_key" in point.payload for point in points):
            raise ConnectionError("Qdrant refused the write")
        marker(collection_name, points)  # the collection's marker still writes

    monkeypatch.setattr(ours, "upsert", down)
    no_embedding(monkeypatch)
    report = run_module.apply_ingest(ours, settings, replace_pending(plan), api_key="k")
    assert any("moving" in why and "refused" in why for _, why in report.failures)
    kept = {p.payload.get("bib_key") for p in ours.points(settings.collection)}
    assert move.source in kept  # the translation's pages are still under the key


def replace_pending(plan):
    from dataclasses import replace

    return replace(plan, pending=())


def test_an_undecided_key_holding_an_unlisted_file_is_pruned(
    ours, sources_paper: Path, settings, copies: tuple[Path, Path]
) -> None:
    from axiomation.sources.bib import records

    bib = paper_entry(sources_paper, *copies)
    found = records(bib)
    assert stale(KEY, found, {}, {KEY: "e" * 64})  # a file the entry dropped
    assert not stale(KEY, found, {}, {KEY: file_sha256(copies[0])})
    assert not stale(KEY, found, {}, {KEY: ""})  # no hash recorded: kept


def test_force_reads_a_file_again_rather_than_moving_it(
    ours, sources_paper: Path, settings, two: tuple[Path, Path]
) -> None:
    from axiomation.sources.ingest import run as run_module

    bib = paper_entry(sources_paper, *two, pages="30--35")
    stock(ours, settings.collection, KEY, TRANSLATION, file_sha256(two[0]))
    plan = run_module.plan_ingest(ours, settings, bib_path=bib, force=True)
    assert not plan.moves and len(plan.pending) == 2


def test_the_files_line_names_a_cited_edition_not_yet_under_the_key(
    ours, sources_paper: Path, settings, copies: tuple[Path, Path]
) -> None:
    from axiomation.sources.providers import sources_for

    bib = paper_entry(sources_paper, *copies)
    short, long = (file_sha256(f) for f in copies)
    stock(ours, settings.collection, file_key(KEY, short), PAPER, short)
    stock(ours, settings.collection, file_key(KEY, long), PAPER, long)
    confirm.write(bib, KEY, long, "the paper as published")
    said = sources_for(sources_paper).copy(file_key(KEY, short))
    assert f"`{KEY}`: nothing yet, its cited edition long.pdf" in said
    assert f"`{file_key(KEY, long)}`: long.pdf" in said
    assert f"the cited edition, not yet moved under `{KEY}`" in said


def test_an_ingest_refuses_a_key_holding_the_file_mark(tmp_path: Path) -> None:
    from axiomation.sources.config import SourcesError
    from axiomation.sources.ingest.bib import read

    bib = tmp_path / "references.bib"
    bib.write_text("@article{odd@key,\n  title = {T},\n}\n", encoding="utf-8")
    with pytest.raises(SourcesError, match="odd@key holds '@'"):
        read(bib)


# --- the file check: an entry's pages printed ---


def test_a_file_printing_its_entrys_pages_is_its_work_whatever_its_title_page() -> None:
    from axiomation.sources.match import check, expected

    want = expected(record(pages="30--35"))
    original = dict(enumerate(ORIGINAL, start=1))
    assert not check(expected(record(pages="")), original).matches  # title only
    assert check(want, original).matches
    translation = dict(enumerate(TRANSLATION, start=1))
    assert check(want, translation).matches  # its title page, not its pages


def test_a_files_own_page_sequence_is_no_pagination() -> None:
    """A preprint numbers its pages 1, 2, …: that passes no entry whose
    pages start at 1, which only a pagination of its own would show."""
    from axiomation.sources.match import check, expected

    sibling = {
        1: "Running head\nA sibling paper\nbody\n1",
        2: "Running head\nbody\nmore\n2",
        3: "Running head\nbody\nmore\n3",
    }
    assert not check(expected(record(pages="1--12")), sibling).matches
    assert prints_pages({2: "head 31\nx\ny\nz", 3: "32 head\nx\ny\nz"}, 30, 35)
