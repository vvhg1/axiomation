"""The words in a digital page's images.

Tesseract reads the rendered page; its words inside an image's box that
the layer lacks and that spell a word send the image to the declared OCR,
whose text is added after the layer under a line naming it. A page so
tested records it, and a stored layer page that never was is owed the
test: by a read of its work where it carries an image, by a payload
update where it carries none.
"""

from __future__ import annotations

import io
from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("qdrant_client")

from axiomation.sources.config import OcrBackend, ResolvedSources, SettingFrom  # noqa: E402
from axiomation.sources.contract import (  # noqa: E402
    LAYER_READER_FIELD,
    OCR_FIGURES_FIELD,
    PAGE_KIND_FIELD,
    TextSource,
)
from axiomation.sources.ingest import extract, figures  # noqa: E402
from axiomation.sources.ingest import layer as layer_module  # noqa: E402
from axiomation.sources.ingest import run as run_module  # noqa: E402
from axiomation.sources.ingest.figures import FIGURE_LINE  # noqa: E402
from axiomation.sources.ingest.ocr import Word  # noqa: E402
from axiomation.sources.ingest.run import Reason, apply_ingest, plan_ingest  # noqa: E402
from axiomation.sources.library.store import untested_pages  # noqa: E402
from axiomation.sources.pdf import LAYER_READER  # noqa: E402
from sources.fakes import FakeQdrant, StubOcr  # noqa: E402
from sources.pdfs import PAGE_HEIGHT, PAGE_WIDTH, image, prose, write_drawn_pdf  # noqa: E402

LAYER = "The loss falls as the model trains."
#: a second page's layer: a line drawn alike on most pages is a stamp
OTHER = "Its weights settle on a circuit."
KEY = "nair2023"
#: the figure drawn at 72, 300 (its lower left), 200 by 150 points, in
#: pixels at the OCR's 300 dpi from the page's top left
FIGURE = (300, 1425, 1133, 2050)
INSIDE = (700, 1700)
OUTSIDE = (700, 600)
FIGURE_READING = "Validation accuracy over training epochs"


@pytest.fixture(autouse=True)
def known(monkeypatch: pytest.MonkeyPatch) -> None:
    """The system's word list, as far as these pages need it."""
    words = frozenset(
        {"validation", "accuracy", "training", "epochs", "loss", "model", "trains"}
    )
    monkeypatch.setattr(layer_module, "word_list", lambda: words)


@pytest.fixture
def figure_pdf(tmp_path: Path) -> Path:
    pytest.importorskip("pypdfium2")
    return write_drawn_pdf(
        tmp_path / f"{KEY}.pdf", [[*prose([LAYER]), image(72, 300, 200, 150)]]
    )


def word(
    text: str,
    at: tuple[int, int] = INSIDE,
    *,
    confidence: float = 95,
    line: int = 1,
) -> Word:
    x, y = at
    return Word(text, (x - 20, y - 10, x + 20, y + 10), confidence, (1, 1, line))


def read(
    pdf_path: Path, words: list[Word], *, service: str = FIGURE_READING
) -> tuple[extract.PageText, StubOcr]:
    """The PDF's one page, read under paddle with Tesseract reading
    `words` on it."""
    paddle = StubOcr(text=service, name=TextSource.PADDLE)
    (page,) = extract.extract_pdf(
        pdf_path, ocr=paddle, tesseract=StubOcr(page_words=words)
    )
    return page, paddle


# --- Where the images stand ---


def test_an_image_box_is_in_the_rendered_pages_pixels(figure_pdf: Path) -> None:
    from axiomation.sources.pdf import open_pdf

    with open_pdf(figure_pdf) as document:
        boxes = document.image_boxes(1, dpi=extract.OCR_DPI)

    assert boxes == (FIGURE,)


def test_an_overlay_past_the_page_is_no_figure(tmp_path: Path) -> None:
    from axiomation.sources.pdf import open_pdf

    pytest.importorskip("pypdfium2")
    overlay = image(-PAGE_WIDTH, -PAGE_HEIGHT, 3 * PAGE_WIDTH, 3 * PAGE_HEIGHT)
    path = write_drawn_pdf(tmp_path / "overlay.pdf", [[*prose([LAYER]), overlay]])

    with open_pdf(path) as document:
        assert document.image_boxes(1, dpi=extract.OCR_DPI) == ()


def test_a_crop_is_its_box_of_the_rendered_page(figure_pdf: Path) -> None:
    from PIL import Image

    from axiomation.sources.pdf import open_pdf

    with open_pdf(figure_pdf) as document:
        (crop,) = document.crops(1, dpi=extract.OCR_DPI, boxes=[FIGURE])

    with Image.open(io.BytesIO(crop)) as picture:
        assert picture.size == (FIGURE[2] - FIGURE[0], FIGURE[3] - FIGURE[1])


# --- Which images are read ---


def test_words_a_figure_holds_send_it_to_the_ocr(figure_pdf: Path) -> None:
    page, paddle = read(figure_pdf, [word("Validation"), word("epochs")])

    assert page.text == f"{LAYER}\n\n{FIGURE_LINE}\n{FIGURE_READING}"
    assert (page.source, page.figures, len(paddle.seen)) == (TextSource.NONE, 1, 1)


def test_one_word_in_a_figure_is_not_enough(figure_pdf: Path) -> None:
    page, paddle = read(figure_pdf, [word("Validation")])

    assert (page.text, page.figures, paddle.seen) == (LAYER, 0, [])


@pytest.mark.parametrize(
    "words",
    [
        pytest.param([word("Validation", OUTSIDE), word("epochs")], id="outside"),
        pytest.param([word("model"), word("epochs")], id="in-the-layer"),
        pytest.param([word("Vaiidatlon"), word("epochs")], id="spells-nothing"),
        pytest.param([word("Validation", confidence=40), word("epochs")], id="unsure"),
    ],
)
def test_a_word_counts_only_inside_the_image_and_missing_from_the_layer(
    figure_pdf: Path, words: list[Word]
) -> None:
    """Outside the box, held by the layer, spelling nothing, or read with
    little confidence: none of these is a figure's text."""
    page, paddle = read(figure_pdf, words)

    assert (page.figures, paddle.seen) == (0, [])


def test_without_a_word_list_the_works_own_words_spell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Many Linux systems have no word list: a word of the work's own
    layer, on another page, still spells one."""
    pytest.importorskip("pypdfium2")
    monkeypatch.setattr(layer_module, "word_list", frozenset)
    path = write_drawn_pdf(
        tmp_path / f"{KEY}.pdf",
        [
            [*prose([LAYER]), image(72, 300, 200, 150)],
            prose(["Validation accuracy is measured after training epochs."]),
        ],
    )
    paddle = StubOcr(text=FIGURE_READING, name=TextSource.PADDLE)
    words = [word("Validation"), word("epochs"), word("Brownies")]

    first, _ = extract.extract_pdf(
        path, ocr=paddle, tesseract=StubOcr(page_words=words)
    )

    assert (first.figures, len(paddle.seen)) == (1, 1)


def test_images_drawn_in_one_box_are_read_once(tmp_path: Path) -> None:
    """A figure drawn as layered images holds its text once."""
    pytest.importorskip("pypdfium2")
    drawn = image(72, 300, 200, 150)
    path = write_drawn_pdf(tmp_path / f"{KEY}.pdf", [[*prose([LAYER]), drawn, drawn]])

    page, paddle = read(path, [word("Validation"), word("epochs")])

    assert (page.figures, len(paddle.seen)) == (1, 1)
    assert page.text.count(FIGURE_READING) == 1


def test_a_chart_the_ocr_reads_empty_keeps_tesseracts_lines_with_words(
    figure_pdf: Path,
) -> None:
    """A plot's tick values are no text; its legend and axis titles are,
    a word its page's layer holds too."""
    words = [
        word("Validation", line=1),
        word("accuracy", line=1),
        word("0.012", line=2),
        word("4800", line=2),
        word("epochs", line=3),
        word("model", line=4),
        word("Wlle", line=5),
    ]

    page, _ = read(figure_pdf, words, service="")

    assert page.text == (
        f"{LAYER}\n\n{FIGURE_LINE}\nValidation accuracy\nepochs\nmodel"
    )
    assert page.figures == 1


def test_a_figure_reading_with_no_room_in_its_box_keeps_tesseracts_lines(
    figure_pdf: Path,
) -> None:
    """A model that loops on a figure writes cells past any room the box
    has: the reading is left out, as the witness drops a page's block."""
    looped = "<table><tr>" + "<td></td>" * 2000 + "</tr></table>"
    words = [word("Validation", line=1), word("epochs", line=1)]

    page, _ = read(figure_pdf, words, service=looped)

    assert page.text == f"{LAYER}\n\n{FIGURE_LINE}\nValidation epochs"
    assert (page.figures, page.dropped) == (1, 1)
    witness = run_module.WorkWitness.of(KEY, [page])
    assert witness is not None
    assert (witness.figures, witness.dropped) == ((1,), (1,))


def test_no_figure_is_read_under_the_tesseract_backend(figure_pdf: Path) -> None:
    """Tesseract would read its own words again: the test runs under another
    backend only, as the witness does."""
    tesseract = StubOcr(page_words=[word("Validation"), word("epochs")])

    (page,) = extract.extract_pdf(figure_pdf, ocr=tesseract, tesseract=tesseract)

    assert (page.text, page.figures, tesseract.seen) == (LAYER, None, [])


def test_a_page_without_an_image_is_tested_without_tesseract(tmp_path: Path) -> None:
    pytest.importorskip("pypdfium2")
    path = write_drawn_pdf(tmp_path / f"{KEY}.pdf", [prose([LAYER])])

    page, _ = read(path, [])

    assert (page.text, page.figures) == (LAYER, 0)


# --- A stored layer page never tested: by a read, or by a payload update ---


def paddle(settings: ResolvedSources) -> ResolvedSources:
    return replace(settings, ocr_backend=OcrBackend.PADDLE)


def undeclared(settings: ResolvedSources) -> ResolvedSources:
    return replace(
        settings, origins={**settings.origins, "ocr_backend": SettingFrom.DEFAULT}
    )


@pytest.fixture
def stored(
    settings: ResolvedSources, fake_qdrant: FakeQdrant, tmp_path: Path
) -> tuple[FakeQdrant, Path]:
    """A digital work stored as its layer before figures were read: a page
    with a figure and a page without."""
    from axiomation.sources.files import file_sha256
    from axiomation.sources.ingest.bib import parse
    from axiomation.sources.library.upsert import ensure_collection

    pytest.importorskip("pypdfium2")
    pdf_path = write_drawn_pdf(
        tmp_path / f"{KEY}.pdf",
        [[*prose([LAYER]), image(72, 300, 200, 150)], prose([OTHER])],
    )
    ensure_collection(fake_qdrant, settings)
    for index, text in enumerate([LAYER, OTHER]):
        fake_qdrant.add_chunk(
            settings.collection,
            bib_key=KEY,
            page_number=index + 1,
            chunk_index=index,
            ocr_backend="none",
            text=text,
            file_path=str(pdf_path),
        )
        fake_qdrant.points(settings.collection)[-1].payload[PAGE_KIND_FIELD] = "digital"
    bib = tmp_path / "references.bib"
    bib.write_text(
        f"@article{{{KEY},\n  title = {{Progress Measures for Brownies}},\n"
        f"  author = {{Squeakinger, Ottoline}},\n  file = {{{pdf_path}}}\n}}\n",
        encoding="utf-8",
    )
    (entry,) = parse(bib)
    for point in fake_qdrant.points(settings.collection):
        if point.payload.get("bib_key") == KEY:
            point.payload["file_sha256"] = file_sha256(entry.file_path)
            point.payload["bib_sha256"] = entry.payload_hash()
    return fake_qdrant, bib


def payloads(owned: FakeQdrant, settings: ResolvedSources) -> dict[int, dict]:
    return {
        p.payload["page_number"]: p.payload
        for p in owned.points(settings.collection)
        if p.payload.get("bib_key") == KEY
    }


def test_an_untested_figure_page_is_listed_and_read_only_when_asked(
    settings: ResolvedSources, stored: tuple[FakeQdrant, Path]
) -> None:
    owned, bib = stored

    unasked = plan_ingest(owned, paddle(settings), bib_path=bib)
    asked = plan_ingest(owned, paddle(settings), bib_path=bib, reread=True)

    (found,) = unasked.untested
    assert (unasked.pending, found.images, found.plain) == ((), (1,), (1,))
    assert [(p.entry.key, p.reason) for p in asked.pending] == [(KEY, Reason.FIGURES)]
    assert asked.untested == ()


def test_no_figure_test_is_owed_under_the_default_backend(
    settings: ResolvedSources, stored: tuple[FakeQdrant, Path]
) -> None:
    owned, bib = stored
    for point in owned.points(settings.collection):
        point.payload[LAYER_READER_FIELD] = LAYER_READER

    found = plan_ingest(owned, undeclared(settings), bib_path=bib, reread=True)

    assert (found.untested, found.pending, found.empty) == ((), (), True)


def test_a_page_without_an_image_is_recorded_tested_and_not_reread(
    settings: ResolvedSources, stored: tuple[FakeQdrant, Path]
) -> None:
    owned, bib = stored
    plan = plan_ingest(owned, paddle(settings), bib_path=bib)

    report = apply_ingest(owned, paddle(settings), plan, api_key="k")

    by_page = payloads(owned, settings)
    assert OCR_FIGURES_FIELD not in by_page[1]
    assert (by_page[2][OCR_FIGURES_FIELD], by_page[2]["text"]) == (0, OTHER)
    assert (report.figureless, report.ingested) == (1, 0)
    (left,) = plan_ingest(owned, paddle(settings), bib_path=bib).untested
    assert (left.images, left.plain) == ((1,), ())


def test_a_reread_adds_the_figures_text_and_records_every_page(
    settings: ResolvedSources,
    stored: tuple[FakeQdrant, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owned, bib = stored
    service = StubOcr(text=FIGURE_READING, name=TextSource.PADDLE)
    monkeypatch.setattr(run_module, "provider_for", lambda *a, **k: service)
    monkeypatch.setattr(
        run_module,
        "tesseract_provider",
        lambda: StubOcr(page_words=[word("Validation"), word("epochs")]),
    )

    def embed(texts: list[str], **_: object) -> tuple[list, list, int]:
        vectors = [[0.0] * settings.voyage_dims] * len(texts)
        return vectors, [{"indices": [1], "values": [1.0]}] * len(texts), 10

    monkeypatch.setattr(run_module, "embed_documents", embed)
    plan = plan_ingest(owned, paddle(settings), bib_path=bib, reread=True)

    report = apply_ingest(owned, paddle(settings), plan, api_key="k")

    assert report.failures == ()
    by_page = payloads(owned, settings)
    assert [by_page[n][OCR_FIGURES_FIELD] for n in (1, 2)] == [1, 0]
    assert by_page[1]["text"] == f"{LAYER}\n\n{FIGURE_LINE}\n{FIGURE_READING}"
    assert [by_page[n]["ocr_backend"] for n in (1, 2)] == ["none", "none"]
    (witness,) = report.witnessed
    assert (witness.witnessed, witness.figures) == (0, (1,))
    assert plan_ingest(owned, paddle(settings), bib_path=bib, reread=True).empty


def test_the_audit_tells_image_pages_from_plain_ones(
    stored: tuple[FakeQdrant, Path], settings: ResolvedSources
) -> None:
    owned, bib = stored
    pdf_path = Path(next(iter(payloads(owned, settings).values()))["file_path"])

    found = figures.audit(
        KEY, pdf_path, untested_pages(owned, settings.collection, KEY)
    )

    assert (found.images, found.plain) == ((1,), (1,))


def test_the_plan_names_what_it_records_and_what_waits_for_a_reread(
    settings: ResolvedSources, stored: tuple[FakeQdrant, Path]
) -> None:
    from axiomation.sources.cli import _plan_lines

    owned, bib = stored

    lines = list(_plan_lines(plan_ingest(owned, paddle(settings), bib_path=bib)))

    assert any(
        line.startswith(f"untested   {KEY}: pp. 1 carry images") for line in lines
    )
    assert f"figures    {KEY}: 1 pages without an image recorded as tested, " in (
        "".join(lines)
    )
