"""A stored page's recorded reader, checked once against its PDF.

A page the ingest writes records its kind, so its reader is the one the
page class chose. A page written before (a copied collection, an ingest
that decided per document) is checked at the next ingest: a scan's old
layer stored as `none` is read again under a declared backend, a digital
layer recorded as OCR is recorded `none`, and every other page is settled
as it is; no text or vector moves.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("qdrant_client")

from axiomation.sources.config import OcrBackend, ResolvedSources, SettingFrom  # noqa: E402
from axiomation.sources.contract import (  # noqa: E402
    LAYER_READER_FIELD,
    OCR_DISPUTED_FIELD,
    OCR_FIGURES_FIELD,
    OCR_READING_FIELD,
    PAGE_KIND_FIELD,
    TextSource,
)
from axiomation.sources.ingest import run as run_module  # noqa: E402
from axiomation.sources.ingest.ocr import OcrError  # noqa: E402
from axiomation.sources.ingest.readers import (  # noqa: E402
    LayerAudit,
    PageRecord,
    StoredPage,
    audit,
)
from axiomation.sources.ingest.run import Reason, apply_ingest, plan_ingest  # noqa: E402
from axiomation.sources.ingest.witness import OCR_READING  # noqa: E402
from axiomation.sources.library.store import unsettled_pages  # noqa: E402
from axiomation.sources.pdf import LAYER_READER, PageKind  # noqa: E402
from sources.fakes import FakeQdrant  # noqa: E402
from sources.pdfs import full_page, prose, write_drawn_pdf  # noqa: E402

LAYER = [
    "The series diverges for every value of the parameter.",
    "Its partial sums grow without bound, as the second lemma shows.",
]
OLD_OCR = ["Tbe senes diverges fory every va1ue of tbe pararneter."]
KEY = "hardy1949"


@pytest.fixture
def mixed_pdf(tmp_path: Path) -> Path:
    """A digital page, a scan carrying an old OCR layer, a scan with none."""
    pytest.importorskip("pypdfium2")
    return write_drawn_pdf(
        tmp_path / f"{KEY}.pdf",
        [
            prose(LAYER),
            [full_page(), *prose(OLD_OCR, invisible=True)],
            [full_page()],
        ],
    )


def legacy(owned: FakeQdrant, collection: str, pdf_path: Path) -> None:
    """The work as a copied collection stored it: no kind on any page, the
    digital layer recorded `paddle`, the old OCR layer recorded `none`."""
    for index, (reader, text) in enumerate(
        [
            ("paddle", "\n".join(LAYER)),
            ("none", OLD_OCR[0]),
            ("paddle", "The series diverges, read by an OCR model."),
        ]
    ):
        owned.add_chunk(
            collection,
            bib_key=KEY,
            page_number=index + 1,
            chunk_index=index,
            ocr_backend=reader,
            text=text,
            file_path=str(pdf_path),
        )


@pytest.fixture
def stored(
    settings: ResolvedSources, fake_qdrant: FakeQdrant, mixed_pdf: Path
) -> FakeQdrant:
    from axiomation.sources.library.upsert import ensure_collection

    ensure_collection(fake_qdrant, settings)
    legacy(fake_qdrant, settings.collection, mixed_pdf)
    return fake_qdrant


@pytest.fixture
def bib_file(tmp_path: Path, mixed_pdf: Path) -> Path:
    path = tmp_path / "references.bib"
    path.write_text(
        f"@book{{{KEY},\n  title = {{Divergent Series}},\n"
        f"  author = {{Hardy, G. H.}},\n  file = {{{mixed_pdf}}}\n}}\n",
        encoding="utf-8",
    )
    return path


def matching_hashes(owned: FakeQdrant, settings: ResolvedSources, bib: Path) -> None:
    """The stored work is the bib's file and entry: nothing else moves."""
    from axiomation.sources.files import file_sha256
    from axiomation.sources.ingest.bib import parse

    (entry,) = parse(bib)
    for point in owned.points(settings.collection):
        if point.payload.get("bib_key") == KEY:
            point.payload["file_sha256"] = file_sha256(entry.file_path)
            point.payload["bib_sha256"] = entry.payload_hash()


def work(owned: FakeQdrant, settings: ResolvedSources) -> list:
    return [
        p for p in owned.points(settings.collection) if p.payload.get("bib_key") == KEY
    ]


def paddle(settings: ResolvedSources) -> ResolvedSources:
    """A corpus read by `paddle`, declared so."""
    return replace(settings, ocr_backend=OcrBackend.PADDLE)


def undeclared(settings: ResolvedSources) -> ResolvedSources:
    return replace(
        settings, origins={**settings.origins, "ocr_backend": SettingFrom.DEFAULT}
    )


def test_each_page_is_checked_against_its_pdf(mixed_pdf: Path) -> None:
    pages = [
        StoredPage(1, 0, "paddle", "\n".join(LAYER)),
        StoredPage(2, 1, "none", OLD_OCR[0]),
        StoredPage(3, 2, "paddle", "The series diverges, read by an OCR model."),
    ]

    found = audit(KEY, mixed_pdf, pages, tesseract=None)

    assert found.reread == (2,)
    assert found.records == (
        PageRecord(1, 0, PageKind.DIGITAL, TextSource.NONE),
        PageRecord(3, 2, PageKind.SCAN, None),
    )


def test_an_ocr_reading_of_a_digital_page_with_no_layer_keeps_its_reader(
    tmp_path: Path,
) -> None:
    pytest.importorskip("pypdfium2")
    plate = write_drawn_pdf(tmp_path / "plate.pdf", [[*prose(LAYER)], []])
    pages = [StoredPage(2, 1, "paddle", "words read off a figure")]

    found = audit(KEY, plate, pages, tesseract=None)

    assert found.records == (PageRecord(2, 1, PageKind.DIGITAL, None),)


def test_only_pages_without_a_kind_are_checked(
    settings: ResolvedSources, stored: FakeQdrant
) -> None:
    point = work(stored, settings)[-1]
    point.payload[PAGE_KIND_FIELD] = "scan"

    pages = unsettled_pages(stored, settings.collection, KEY)

    assert [page.page_index for page in pages] == [1, 2]


def test_the_default_backend_settles_records_and_reads_nothing_again(
    settings: ResolvedSources, stored: FakeQdrant, bib_file: Path
) -> None:
    """Under the default, nothing is read again and no judgment lapses; the
    records are made true where the text allows, the old layer left for a
    declared backend."""
    matching_hashes(stored, settings, bib_file)
    texts = {p.id: p.payload["text"] for p in work(stored, settings)}
    plan = plan_ingest(stored, undeclared(settings), bib_path=bib_file)

    assert plan.pending == ()
    (found,) = plan.readers
    assert found.reread == (2,)

    report = apply_ingest(stored, undeclared(settings), plan, api_key="k")

    by_page = {p.payload["page_number"]: p.payload for p in work(stored, settings)}
    assert (by_page[1]["ocr_backend"], by_page[1][PAGE_KIND_FIELD]) == (
        "none",
        "digital",
    )
    assert (by_page[3]["ocr_backend"], by_page[3][PAGE_KIND_FIELD]) == (
        "paddle",
        "scan",
    )
    assert PAGE_KIND_FIELD not in by_page[2]
    assert {p.id: p.payload["text"] for p in work(stored, settings)} == texts
    assert (report.settled, report.relabelled) == (2, 1)


def test_a_declared_backend_reads_an_old_layer_again_when_asked(
    settings: ResolvedSources, stored: FakeQdrant, bib_file: Path
) -> None:
    matching_hashes(stored, settings, bib_file)

    asked = plan_ingest(stored, paddle(settings), bib_path=bib_file, reread=True)
    unasked = plan_ingest(stored, paddle(settings), bib_path=bib_file)

    assert [(item.entry.key, item.reason) for item in asked.pending] == [
        (KEY, Reason.READER)
    ]
    assert asked.readers == ()
    assert unasked.pending == ()
    assert [found.reread for found in unasked.readers] == [(2,)]


def test_a_settled_corpus_is_not_checked_again(
    settings: ResolvedSources, stored: FakeQdrant, bib_file: Path
) -> None:
    matching_hashes(stored, settings, bib_file)
    for point in work(stored, settings):
        point.payload[PAGE_KIND_FIELD] = "digital"
        point.payload[LAYER_READER_FIELD] = LAYER_READER

    assert plan_ingest(stored, paddle(settings), bib_path=bib_file).empty


# --- A failing engine stops the run; a work read again keeps its pages ---


def test_an_engine_failure_stops_the_run_and_keeps_the_stored_pages(
    settings: ResolvedSources,
    stored: FakeQdrant,
    bib_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A service that fails on the first page, and on a blank page after,
    would fail on every page after it; a work read again is replaced only
    once read."""

    class Down:
        name = TextSource.PADDLE

        def read(self, png: bytes) -> str:
            raise OcrError("the OCR service answered 503.")

    monkeypatch.setattr(run_module, "provider_for", lambda *a, **k: Down())
    monkeypatch.setattr(run_module, "tesseract_provider", lambda: Down())

    def failing(path: Path, **_: object) -> list:
        raise OcrError("the OCR service answered 503.")

    monkeypatch.setattr(run_module, "extract_pdf", failing)
    before = {p.id: dict(p.payload) for p in work(stored, settings)}
    plan = plan_ingest(stored, settings, bib_path=bib_file, force=True)

    report = apply_ingest(stored, settings, plan, api_key="k")

    assert report.stopped.startswith("the OCR service answered 503.")
    assert "0 of 1 works read" in report.stopped
    assert report.read == () and report.failures == ()
    after = {p.id: p.payload for p in work(stored, settings)}
    assert after == before


def test_a_page_an_engine_that_reads_fails_on_fails_its_work_alone(
    settings: ResolvedSources,
    stored: FakeQdrant,
    bib_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One page that times out, while the engine reads a blank page, is
    that work's fault: the work keeps its stored pages and the run goes
    on, so a run of hundreds of works does not stop at one page."""
    from sources.fakes import StubOcr

    engine = StubOcr()
    monkeypatch.setattr(run_module, "provider_for", lambda *a, **k: engine)
    monkeypatch.setattr(run_module, "tesseract_provider", lambda: engine)

    def failing(path: Path, **_: object) -> list:
        raise OcrError("the OCR service did not answer in 300 seconds.")

    monkeypatch.setattr(run_module, "extract_pdf", failing)
    before = {p.id: dict(p.payload) for p in work(stored, settings)}
    plan = plan_ingest(stored, settings, bib_path=bib_file, force=True)

    report = apply_ingest(stored, settings, plan, api_key="k")

    assert report.stopped == ""
    ((key, why),) = report.failures
    assert key == KEY and why.startswith("the OCR service did not answer")
    assert "the fault is this work's" in why
    assert len(engine.seen) == 1  # the blank page, asked once
    assert {p.id: p.payload for p in work(stored, settings)} == before


# --- A digital layer the word list doubts: Tesseract has the last word ---


@pytest.fixture
def doubted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    from axiomation.sources.ingest import layer as layer_module

    pytest.importorskip("pypdfium2")
    monkeypatch.setattr(layer_module, "may_spell_nothing", lambda layer: True)
    return write_drawn_pdf(tmp_path / "doubted.pdf", [prose(LAYER)])


STORED_LAYER = [StoredPage(1, 0, "none", "\n".join(LAYER))]


def test_a_layer_tesseract_cannot_read_on_the_page_is_read_again(
    doubted: Path,
) -> None:
    from sources.fakes import StubOcr

    lost = StubOcr(text="words the font's lost encoding hid from the layer")

    found = audit(KEY, doubted, STORED_LAYER, tesseract=lost)

    assert (found.reread, found.records) == ((1,), ())


def test_a_layer_tesseract_reads_alike_is_settled(doubted: Path) -> None:
    """German, an index: the word list does not know them."""
    from sources.fakes import StubOcr

    found = audit(KEY, doubted, STORED_LAYER, tesseract=StubOcr(text=" ".join(LAYER)))

    assert found.reread == ()
    assert found.records == (PageRecord(1, 0, PageKind.DIGITAL, None),)


def test_without_tesseract_a_doubted_layer_is_left_unsettled(doubted: Path) -> None:
    found = audit(KEY, doubted, STORED_LAYER, tesseract=None)

    assert (found.reread, found.records) == ((), ())


# --- Pages the declared service read before its witness: held at `--reread` ---

SCAN_TEXT = (
    "The series diverges for every value of the parameter, and its partial "
    "sums grow without bound as the second lemma shows."
)
LOOPED_TEXT = SCAN_TEXT + "\n\n$(" + "0 , " * 12 + ")$"


@pytest.fixture
def scans(
    settings: ResolvedSources, fake_qdrant: FakeQdrant, tmp_path: Path
) -> tuple[FakeQdrant, Path]:
    """Two image-only scan pages the service read, each settled as a scan,
    neither witnessed; the second reading looped."""
    from axiomation.sources.library.upsert import ensure_collection

    pytest.importorskip("pypdfium2")
    pdf_path = write_drawn_pdf(tmp_path / f"{KEY}.pdf", [[full_page()], [full_page()]])
    ensure_collection(fake_qdrant, settings)
    for index, text in enumerate([SCAN_TEXT, LOOPED_TEXT]):
        fake_qdrant.add_chunk(
            settings.collection,
            bib_key=KEY,
            page_number=index + 1,
            chunk_index=index,
            ocr_backend="paddle",
            text=text,
            file_path=str(pdf_path),
        )
        fake_qdrant.points(settings.collection)[-1].payload[PAGE_KIND_FIELD] = "scan"
    bib = tmp_path / "references.bib"
    bib.write_text(
        f"@book{{{KEY},\n  title = {{Divergent Series}},\n"
        f"  author = {{Hardy, G. H.}},\n  file = {{{pdf_path}}}\n}}\n",
        encoding="utf-8",
    )
    matching_hashes(fake_qdrant, settings, bib)
    return fake_qdrant, bib


def test_unwitnessed_pages_are_listed_and_read_only_when_asked(
    settings: ResolvedSources, scans: tuple[FakeQdrant, Path]
) -> None:
    owned, bib = scans

    unasked = plan_ingest(owned, paddle(settings), bib_path=bib)
    asked = plan_ingest(owned, paddle(settings), bib_path=bib, reread=True)

    assert (unasked.pending, unasked.unwitnessed) == ((), ((KEY, 2),))
    assert [(p.entry.key, p.reason) for p in asked.pending] == [(KEY, Reason.WITNESS)]


def test_no_witness_is_owed_under_the_default_backend(
    settings: ResolvedSources, scans: tuple[FakeQdrant, Path]
) -> None:
    """The default is Tesseract, the witness itself, and no request to read
    a stored page again."""
    owned, bib = scans

    found = plan_ingest(owned, undeclared(settings), bib_path=bib, reread=True)

    assert (found.unwitnessed, found.pending) == ((), ())


def test_a_witnessed_reread_reads_only_the_faulty_page_again(
    settings: ResolvedSources,
    scans: tuple[FakeQdrant, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stored readings are each page's first: the clean page keeps its
    text and the service reads only the looped one again."""
    from sources.fakes import StubOcr

    owned, bib = scans
    service = StubOcr(text=SCAN_TEXT, name=TextSource.PADDLE)
    monkeypatch.setattr(run_module, "provider_for", lambda *a, **k: service)
    monkeypatch.setattr(
        run_module, "tesseract_provider", lambda: StubOcr(text=SCAN_TEXT)
    )

    def embed(texts: list[str], **_: object) -> tuple[list, list, int]:
        vectors = [[0.0] * settings.voyage_dims] * len(texts)
        return vectors, [{"indices": [1], "values": [1.0]}] * len(texts), 10

    monkeypatch.setattr(run_module, "embed_documents", embed)
    plan = plan_ingest(owned, paddle(settings), bib_path=bib, reread=True)

    report = apply_ingest(owned, paddle(settings), plan, api_key="k")

    by_page = {p.payload["page_number"]: p.payload for p in work(owned, settings)}
    assert [by_page[n]["text"] for n in (1, 2)] == [SCAN_TEXT, SCAN_TEXT]
    assert [by_page[n][OCR_DISPUTED_FIELD] for n in (1, 2)] == [0, 0]
    assert len(service.seen) == 1
    (witnessed,) = report.witnessed
    assert (witnessed.witnessed, witnessed.reread, witnessed.disputed) == (2, (2,), ())
    assert plan_ingest(owned, paddle(settings), bib_path=bib).empty


def test_a_page_an_older_witness_left_disputed_is_read_afresh(
    settings: ResolvedSources,
    scans: tuple[FakeQdrant, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Its stored reading is no first pass: the service reads it again,
    and a page the older witness settled keeps its text and that witness's
    version, not held again."""
    from sources.fakes import StubOcr

    owned, bib = scans
    pages = {p.payload["page_number"]: p.payload for p in work(owned, settings)}
    pages[1][OCR_DISPUTED_FIELD] = 0
    pages[2][OCR_DISPUTED_FIELD] = 1
    pages[2]["text"] = SCAN_TEXT  # clean today: only its old dispute sends it
    service = StubOcr(text=SCAN_TEXT, name=TextSource.PADDLE)
    monkeypatch.setattr(run_module, "provider_for", lambda *a, **k: service)
    monkeypatch.setattr(
        run_module, "tesseract_provider", lambda: StubOcr(text=SCAN_TEXT)
    )

    def embed(texts: list[str], **_: object) -> tuple[list, list, int]:
        vectors = [[0.0] * settings.voyage_dims] * len(texts)
        return vectors, [{"indices": [1], "values": [1.0]}] * len(texts), 10

    monkeypatch.setattr(run_module, "embed_documents", embed)

    unasked = plan_ingest(owned, paddle(settings), bib_path=bib)
    plan = plan_ingest(owned, paddle(settings), bib_path=bib, reread=True)
    apply_ingest(owned, paddle(settings), plan, api_key="k")

    assert unasked.unwitnessed == ((KEY, 1),)
    assert [(p.entry.key, p.reason) for p in plan.pending] == [(KEY, Reason.WITNESS)]
    assert len(service.seen) == 1
    by_page = {p.payload["page_number"]: p.payload for p in work(owned, settings)}
    assert [by_page[n]["text"] for n in (1, 2)] == [SCAN_TEXT, SCAN_TEXT]
    assert [by_page[n][OCR_READING_FIELD] for n in (1, 2)] == [1, OCR_READING]
    assert plan_ingest(owned, paddle(settings), bib_path=bib).empty


def test_a_page_todays_witness_left_disputed_is_not_read_again(
    settings: ResolvedSources, scans: tuple[FakeQdrant, Path]
) -> None:
    owned, bib = scans
    for point in work(owned, settings):
        point.payload[OCR_DISPUTED_FIELD] = 1
        point.payload[OCR_READING_FIELD] = OCR_READING

    found = plan_ingest(owned, paddle(settings), bib_path=bib, reread=True)

    assert (found.unwitnessed, found.pending) == ((), ())


# --- A layer an older reader wrote, read by today's ---

#: Today's reading of the named-glyph page, and an older reader's of it,
#: which gave a TeX symbol font's raw codes.
TODAY = "plain ⟨x⟩‖"
OLDER = "plain hxik"
SYMBOLS = "whittaker1915"


@pytest.fixture
def symbols(
    settings: ResolvedSources, fake_qdrant: FakeQdrant, tmp_path: Path
) -> tuple[FakeQdrant, Path]:
    """A digital work whose one page draws in a symbol font named by its
    glyphs, stored as its layer and tested for figures, with no layer
    reader's version; and its bib."""
    pytest.importorskip("pypdfium2")
    from axiomation.sources.files import file_sha256
    from axiomation.sources.ingest.bib import parse
    from axiomation.sources.library.upsert import ensure_collection
    from sources.pdfs import write_named_glyphs_pdf

    path = write_named_glyphs_pdf(
        tmp_path / f"{SYMBOLS}.pdf",
        "hxik",
        differences={104: "angbracketleft", 105: "angbracketright", 107: "bardbl"},
    )
    bib = tmp_path / "symbols.bib"
    bib.write_text(
        f"@book{{{SYMBOLS},\n  title = {{Modern Analysis}},\n"
        f"  author = {{Whittaker, E. T.}},\n  file = {{{path}}}\n}}\n",
        encoding="utf-8",
    )
    (entry,) = parse(bib)
    ensure_collection(fake_qdrant, settings)
    fake_qdrant.add_chunk(
        settings.collection,
        bib_key=SYMBOLS,
        title="Modern Analysis",
        text=TODAY,
        file_path=str(path),
        file_sha256=file_sha256(path),
        bib_sha256=entry.payload_hash(),
        ocr_backend="none",
        **{PAGE_KIND_FIELD: "digital", OCR_FIGURES_FIELD: 0},
    )
    return fake_qdrant, bib


def stored_page(owned: FakeQdrant, settings: ResolvedSources) -> dict:
    (point,) = [
        p
        for p in owned.points(settings.collection)
        if p.payload.get("bib_key") == SYMBOLS
    ]
    return point.payload


def test_a_layer_today_reads_alike_takes_the_version_and_keeps_its_text(
    settings: ResolvedSources, symbols: tuple[FakeQdrant, Path]
) -> None:
    owned, bib = symbols

    plan = plan_ingest(owned, undeclared(settings), bib_path=bib)
    report = apply_ingest(owned, undeclared(settings), plan, api_key="k")

    assert plan.layers == (LayerAudit(SYMBOLS, outdated=(), alike=(0,)),)
    assert (report.stamped, report.ingested) == (1, 0)
    payload = stored_page(owned, settings)
    assert (payload["text"], payload[LAYER_READER_FIELD]) == (TODAY, LAYER_READER)
    assert plan_ingest(owned, undeclared(settings), bib_path=bib).empty


def test_a_layer_today_reads_otherwise_is_listed_and_read_again_asked(
    settings: ResolvedSources,
    symbols: tuple[FakeQdrant, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The older reader's raw codes are no text of today's: the work is
    read again, whatever the judgments on it lapse, and the page holds
    today's reading."""
    from sources.fakes import StubOcr

    owned, bib = symbols
    stored_page(owned, settings)["text"] = OLDER
    monkeypatch.setattr(run_module, "provider_for", lambda *a, **k: StubOcr())
    monkeypatch.setattr(run_module, "tesseract_provider", lambda: StubOcr())

    def embed(texts: list[str], **_: object) -> tuple[list, list, int]:
        vectors = [[0.0] * settings.voyage_dims] * len(texts)
        return vectors, [{"indices": [1], "values": [1.0]}] * len(texts), 10

    monkeypatch.setattr(run_module, "embed_documents", embed)

    listed = plan_ingest(owned, undeclared(settings), bib_path=bib)
    asked = plan_ingest(owned, undeclared(settings), bib_path=bib, reread=True)
    report = apply_ingest(owned, undeclared(settings), asked, api_key="k")

    assert (listed.layers, listed.pending) == (
        (LayerAudit(SYMBOLS, outdated=(1,), alike=()),),
        (),
    )
    assert [(item.reason, item.pages) for item in asked.pending] == [
        (Reason.LAYER, (1,))
    ]
    assert report.ingested == 1
    payload = stored_page(owned, settings)
    assert (payload["text"], payload[LAYER_READER_FIELD]) == (TODAY, LAYER_READER)
    assert plan_ingest(owned, undeclared(settings), bib_path=bib).empty


def test_a_pages_figures_text_is_no_part_of_its_layer(
    settings: ResolvedSources, symbols: tuple[FakeQdrant, Path]
) -> None:
    from axiomation.sources.ingest.figures import FIGURE_LINE

    owned, bib = symbols
    stored_page(owned, settings)["text"] = f"{TODAY}\n\n{FIGURE_LINE}\nloss curve"

    (found,) = plan_ingest(owned, undeclared(settings), bib_path=bib).layers

    assert found == LayerAudit(SYMBOLS, outdated=(), alike=(0,))


def test_a_page_kept_as_its_layer_records_the_reader_an_ocr_page_does_not(
    settings: ResolvedSources, symbols: tuple[FakeQdrant, Path]
) -> None:
    from axiomation.sources.ingest.bib import parse
    from axiomation.sources.ingest.chunk import Chunk
    from axiomation.sources.library.upsert import upsert_chunks

    owned, bib = symbols
    (entry,) = parse(bib)
    chunks = [
        Chunk(SYMBOLS, "Modern Analysis", 1, 0, TODAY, TextSource.NONE),
        Chunk(SYMBOLS, "Modern Analysis", 2, 1, "read", TextSource.PADDLE),
    ]

    upsert_chunks(
        owned,
        settings.collection,
        entry,
        chunks,
        [[0.0] * settings.voyage_dims] * 2,
        [{"indices": [1], "values": [1.0]}] * 2,
        "f" * 64,
    )

    by_page = {
        p.payload["page_number"]: p.payload
        for p in owned.points(settings.collection)
        if p.payload.get("bib_key") == SYMBOLS
    }
    assert by_page[1][LAYER_READER_FIELD] == LAYER_READER
    assert LAYER_READER_FIELD not in by_page[2]


def test_a_layer_read_otherwise_beside_ocr_pages_waits_for_a_declared_backend(
    settings: ResolvedSources, symbols: tuple[FakeQdrant, Path]
) -> None:
    """Reading the work again would read its scan by OCR too: under the
    default backend nothing is read again, and the work is listed."""
    owned, bib = symbols
    stored_page(owned, settings)["text"] = OLDER
    (layer,) = [
        p
        for p in owned.points(settings.collection)
        if p.payload.get("bib_key") == SYMBOLS
    ]
    owned.add_chunk(
        settings.collection,
        **{
            **layer.payload,
            "page_number": 2,
            "chunk_index": 1,
            "ocr_backend": "paddle",
            "text": "a scan's page read by a service",
            PAGE_KIND_FIELD: "scan",
            OCR_DISPUTED_FIELD: 0,
        },
    )

    default = plan_ingest(owned, undeclared(settings), bib_path=bib, reread=True)
    declared = plan_ingest(owned, paddle(settings), bib_path=bib, reread=True)

    assert default.pending == ()
    assert default.layers == (
        LayerAudit(SYMBOLS, outdated=(1,), alike=(), needs_backend=True),
    )
    assert [(item.reason, item.pages) for item in declared.pending] == [
        (Reason.LAYER, (1,))
    ]


def test_a_scans_page_stored_as_its_layer_is_no_layer_to_refresh(
    settings: ResolvedSources, symbols: tuple[FakeQdrant, Path]
) -> None:
    """A scan's stored layer is old OCR: the old-layer audit reads it again
    by OCR, never the layer reader."""
    owned, bib = symbols
    payload = stored_page(owned, settings)
    payload["text"], payload[PAGE_KIND_FIELD] = OLDER, "scan"

    found = plan_ingest(owned, undeclared(settings), bib_path=bib, reread=True)

    assert (found.layers, found.pending) == ((), ())


def test_a_work_read_again_for_its_witness_names_the_layer_it_refreshes(
    settings: ResolvedSources, symbols: tuple[FakeQdrant, Path]
) -> None:
    """Read again for another cause, a work's layer pages are read again
    too: the dry run names those today's reader reads otherwise."""
    owned, bib = symbols
    stored_page(owned, settings)["text"] = OLDER
    (layer,) = [
        p
        for p in owned.points(settings.collection)
        if p.payload.get("bib_key") == SYMBOLS
    ]
    owned.add_chunk(
        settings.collection,
        **{
            **layer.payload,
            "page_number": 2,
            "chunk_index": 1,
            "ocr_backend": "paddle",
            "text": "a scan's page read by a service",
            PAGE_KIND_FIELD: "scan",
        },
    )

    (item,) = plan_ingest(owned, paddle(settings), bib_path=bib, reread=True).pending

    assert (item.reason, item.refreshed) == (Reason.WITNESS, (1,))
