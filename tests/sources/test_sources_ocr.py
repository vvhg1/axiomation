"""OCR: when it runs, what it records, and how it fails.

The per-page text source and the refusal to fall back quietly are here
because both are easy to lose in a refactor. The provider split:
`extract` decides which pages need OCR, `ocr` decides how one image is read,
and a second person answers the same contract with a service of their own.
"""

from __future__ import annotations

import base64
import io
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from hashlib import sha256 as _sha256
from pathlib import Path
from typing import Any

import pytest

from axiomation.sources import pdf as pdf_module
from axiomation.sources import tesseract as tesseract_module
from axiomation.sources.config import (
    OcrBackend,
    ResolvedSources,
    SourcesError,
)
from axiomation.sources.contract import TextSource
from axiomation.sources.ingest import extract, witness
from axiomation.sources.ingest import layer as layer_module
from axiomation.sources.ingest.ocr import (
    CHAT_PROMPT,
    Block,
    BlockReader,
    ChatProvider,
    HttpProvider,
    MineruProvider,
    OcrError,
    PaddleProvider,
    TesseractProvider,
    provider_for,
    tesseract_provider,
)
from axiomation.sources.ingest.witness import OCR_READING
from axiomation.sources.library import vectors
from axiomation.sources.library.embed import embed_documents
from axiomation.sources.pdf import Box, PageKind
from sources.fakes import (
    Answer,
    Exchange,
    FakeOcr,
    FakeServer,
    FakeVoyage,
    StubOcr,
)

CLOSED_PORT = "http://127.0.0.1:1/parse"


@dataclass
class FakeDocument:
    """Just enough of a `pdf.Pdf` reader for the extraction decision."""

    layer: str = ""
    images: bool = False
    rendered: list[int] = field(default_factory=list)

    def text(self, index: int) -> str:
        return self.layer

    def has_images(self, index: int) -> bool:
        return self.images

    def image_boxes(self, index: int, *, dpi: int) -> tuple[Box, ...]:
        return ()

    def png(self, index: int, *, dpi: int) -> bytes:
        self.rendered.append(dpi)
        return b"PNG:png"


@dataclass(frozen=True)
class NoOcr:
    """A provider whose page must never be read."""

    name: TextSource = TextSource.TESSERACT

    def read(self, png: bytes) -> str:
        pytest.fail("no OCR")


def settings_for(backend: OcrBackend, url: str = "") -> ResolvedSources:
    return ResolvedSources(
        qdrant_url="http://localhost:6333",
        collection="c",
        voyage_model="m",
        voyage_dims=4,
        bib=None,
        ocr_backend=backend,
        ocr_url=url,
    )


@pytest.fixture(autouse=True)
def tesseract_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """The binary is a system install; a test says where it is missing."""
    monkeypatch.setattr(tesseract_module.shutil, "which", lambda name: f"/bin/{name}")


def page_text(
    document: FakeDocument,
    *,
    scanned: bool,
    ocr: Any = None,
    tesseract: Any = None,
) -> tuple[str, Any]:
    kind = PageKind.SCAN if scanned else PageKind.DIGITAL
    found = extract._page_text(
        document, 1, kind, ocr=ocr or StubOcr(), tesseract=tesseract
    )
    return found.text, found.source


# --- Which pages are read by OCR ---


def test_a_text_layer_is_read_without_ocr() -> None:
    text, source = page_text(
        FakeDocument(layer="real text  "), scanned=False, ocr=NoOcr()
    )

    assert text == "real text"
    assert source is TextSource.NONE


def test_a_page_of_a_scan_is_ocred_even_when_it_has_a_text_layer() -> None:
    """A scan with a bad text layer is what OCR is for."""
    text, source = page_text(FakeDocument(layer="rn1sread", images=True), scanned=True)

    assert text == "o" * 200
    assert source is TextSource.TESSERACT


def test_an_image_page_with_no_text_is_ocred_in_a_digital_paper() -> None:
    _, source = page_text(
        FakeDocument(images=True),
        scanned=False,
        ocr=StubOcr(name=TextSource.PADDLE),
    )

    assert source is TextSource.PADDLE


def test_the_page_is_recorded_as_the_provider_names_itself() -> None:
    """A remote service writes `http`, whatever is behind it."""
    _, source = page_text(
        FakeDocument(images=True), scanned=True, ocr=StubOcr(name=TextSource.HTTP)
    )

    assert source is TextSource.HTTP


def test_a_page_ocr_gave_nothing_for_is_recorded_as_failed() -> None:
    """Per page, not per document: one bad plate is not a bad paper."""
    text, source = page_text(
        FakeDocument(images=True), scanned=True, ocr=StubOcr(text="")
    )

    assert source is TextSource.FAILED
    assert text == ""


def test_a_short_page_that_read_is_recorded_as_read() -> None:
    """A dedication or a part title is a page that read, not a failure."""
    text, source = page_text(
        FakeDocument(images=True), scanned=True, ocr=StubOcr(text="To Daphne")
    )

    assert (text, source) == ("To Daphne", TextSource.TESSERACT)


# --- A layer that may spell no words ---


@pytest.fixture
def spells_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(layer_module, "may_spell_nothing", lambda layer: True)


LOST = "ACBD GFBH FBKJLBH QXRZ"
SOUND = "Über die Elektrodynamik bewegter Körper"


@pytest.mark.usefixtures("spells_nothing")
def test_a_layer_tesseract_reads_alike_is_kept() -> None:
    """German, an index, a symbol list: the word list does not know them,
    and Tesseract reads the page as its layer says."""
    text, source = page_text(
        FakeDocument(layer=SOUND),
        scanned=False,
        ocr=NoOcr(name=TextSource.PADDLE),
        tesseract=StubOcr(text=SOUND),
    )

    assert (text, source) == (SOUND, TextSource.NONE)


@pytest.mark.usefixtures("spells_nothing")
def test_a_layer_of_lost_encoding_is_read_by_the_declared_backend() -> None:
    paddle = StubOcr(text="the words the page shows", name=TextSource.PADDLE)

    text, source = page_text(
        FakeDocument(layer=LOST),
        scanned=False,
        ocr=paddle,
        tesseract=StubOcr(text="the words the page shows"),
    )

    assert (text, source) == ("the words the page shows", TextSource.PADDLE)
    assert len(paddle.seen) == 1


@pytest.mark.usefixtures("spells_nothing")
def test_under_tesseract_a_lost_layer_is_read_once() -> None:
    tesseract = StubOcr(text="the words the page shows")

    text, source = page_text(
        FakeDocument(layer=LOST), scanned=False, ocr=tesseract, tesseract=tesseract
    )

    assert (text, source) == ("the words the page shows", TextSource.TESSERACT)
    assert len(tesseract.seen) == 1


@pytest.mark.usefixtures("spells_nothing")
def test_without_tesseract_a_doubtful_layer_is_kept() -> None:
    text, source = page_text(FakeDocument(layer=LOST), scanned=False, ocr=NoOcr())

    assert (text, source) == (LOST, TextSource.NONE)


def test_the_word_list_screens_a_lost_encoding_and_passes_prose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    known = frozenset({"the", "series", "diverges", "for", "every", "value"})
    monkeypatch.setattr(layer_module, "word_list", lambda: known)
    prose = "the series diverges for every value " * 4
    lost = "acbd gfbh fbkjlbh qxrz " * 6

    assert not layer_module.may_spell_nothing(prose)
    assert layer_module.may_spell_nothing(lost)
    assert not layer_module.may_spell_nothing("acbd gfbh")  # too short to judge


def test_a_layer_of_marks_with_too_few_words_to_judge_may_spell_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An encoding lost whole spells almost no word; Tesseract then has
    the last word, as on any doubted page."""
    monkeypatch.setattr(layer_module, "word_list", lambda: frozenset({"the"}))
    marks = "\ufffd!\ufffd#\"$\ufffd%&'()+*-,./ " * 20

    assert layer_module.may_spell_nothing(marks)
    assert not layer_module.may_spell_nothing(marks[:100])


def test_with_no_word_list_no_layer_is_screened(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(layer_module, "word_list", lambda: frozenset())

    assert not layer_module.may_spell_nothing("acbd gfbh fbkjlbh qxrz " * 6)


def test_two_extractors_of_one_layer_match_whatever_their_spacing() -> None:
    """A copied corpus's extractor spaces a formula otherwise than PDFium."""
    assert (
        layer_module.similarity("∥β− ̃β∥∞≤δ, it holds", "k β − β ̃k∞ ≤ δ, it holds") > 0.8
    )
    assert layer_module.similarity("a page", "") == 0.0


def test_a_blank_page_with_no_images_is_not_ocred() -> None:
    """The class calls no page without an image a scan."""
    text, source = page_text(FakeDocument(), scanned=False, ocr=NoOcr())

    assert text == ""
    assert source is TextSource.NONE


def test_the_provider_is_handed_the_page_as_a_png() -> None:
    """The render stays in `extract`, so no provider ever sees the library."""
    provider = StubOcr()
    document = FakeDocument(images=True)

    page_text(document, scanned=True, ocr=provider)

    assert provider.seen == [b"PNG:png"]
    assert document.rendered == [extract.OCR_DPI]


# --- The witness: a declared service's reading held against Tesseract ---

PAGE = (
    "The series converges for every value of the parameter below one, and "
    "its partial sums stay bounded as the second lemma shows."
)
LOOPED = PAGE + "\n\n$(" + "0 , " * 12 + ")$"


class Readings:
    """A service that reads the page differently at each request."""

    name = TextSource.PADDLE

    def __init__(self, *texts: str) -> None:
        self.texts = list(texts)
        self.seen = 0

    def read(self, png: bytes) -> str:
        self.seen += 1
        return self.texts[self.seen - 1]


def witnessed(
    document: FakeDocument,
    ocr: Any,
    *,
    stored: extract.StoredReading | None = None,
    tesseract: StubOcr | None = None,
) -> extract.PageText:
    return extract._page_text(
        document,
        1,
        PageKind.SCAN,
        ocr=ocr,
        tesseract=tesseract or StubOcr(text=PAGE),
        stored=stored,
    )


def test_a_clean_reading_is_read_once_and_recorded_witnessed() -> None:
    service = Readings(PAGE)

    page = witnessed(FakeDocument(images=True), service)

    assert (page.text, page.source, page.disputed) == (PAGE, TextSource.PADDLE, 0)
    assert service.seen == 1


def test_a_fault_is_read_again_just_off_the_ingest_resolution() -> None:
    document = FakeDocument(images=True)
    service = Readings(LOOPED, PAGE)

    page = witnessed(document, service)

    assert (page.text, page.disputed) == (PAGE, 0)
    assert document.rendered == [extract.OCR_DPI, round(extract.OCR_DPI * 0.99)]


def test_a_stored_reading_is_the_first_pass_and_the_service_is_not_asked() -> None:
    service = Readings()

    page = witnessed(
        FakeDocument(images=True), service, stored=extract.StoredReading(PAGE)
    )

    assert (page.text, page.disputed, service.seen) == (PAGE, 0, 0)


def test_a_stored_reading_with_a_fault_is_read_again() -> None:
    service = Readings(PAGE)

    page = witnessed(
        FakeDocument(images=True), service, stored=extract.StoredReading(LOOPED)
    )

    assert (page.text, page.disputed, service.seen) == (PAGE, 0, 1)


@pytest.mark.parametrize(("reading", "disputed"), [(1, 0), (OCR_READING, 2)])
def test_a_stored_reading_a_witness_judged_stands_unwitnessed(
    reading: int, disputed: int
) -> None:
    """Settled by any witness, or disputed by today's: holding it again
    would find what was found. It keeps the version that judged it."""
    document, service, tesseract = FakeDocument(images=True), Readings(), StubOcr()
    stored = extract.StoredReading(LOOPED, reading, disputed)

    page = witnessed(document, service, stored=stored, tesseract=tesseract)

    assert (page.text, page.disputed, page.kept) == (LOOPED, disputed, reading)
    assert (service.seen, tesseract.seen, document.rendered) == (0, [], [])


#: The page at 300 dpi, in pixels; a crop of one block is far smaller.
PAGE_SIZE = (1000, 1400)
OTHER = (
    "A bound on the remainder follows from comparing each term with the "
    "geometric series whose ratio is the parameter itself."
)
MADE = (
    "Maximum likelihood methods are the best methods for estimating the "
    "general model, as is established in the literature."
)


@dataclass
class ImagedDocument(FakeDocument):
    """A document whose page renders as a blank image of its size."""

    def png(self, index: int, *, dpi: int) -> bytes:
        from PIL import Image

        self.rendered.append(dpi)
        width, height = (round(side * dpi / extract.OCR_DPI) for side in PAGE_SIZE)
        out = io.BytesIO()
        Image.new("L", (width, height), 255).save(out, "PNG")
        return out.getvalue()


class BoxedService:
    """A service that sends its blocks with their boxes: the page's on a
    page's image, one block's reading on a block's box cropped alone."""

    name = TextSource.MINERU

    def __init__(self, page: tuple[Block, ...], alone: str = "") -> None:
        self.page = page
        self.alone = alone

    def read(self, png: bytes) -> str:
        return "\n\n".join(block.text for block in self.read_blocks(png))

    def read_blocks(self, png: bytes) -> tuple[Block, ...]:
        from PIL import Image

        with Image.open(io.BytesIO(png)) as image:
            if image.height < PAGE_SIZE[1] // 2:
                return (Block(self.alone),)
        return self.page


@dataclass
class StripBlankTesseract(StubOcr):
    """Tesseract reading `text` on any image, and nothing on one shorter
    than a line of print where `strips_blank`: a band at the page's edge
    holds no print."""

    strips_blank: bool = False

    def read(self, png: bytes) -> str:
        from PIL import Image

        self.seen.append(png)
        with Image.open(io.BytesIO(png)) as image:
            line = extract.OCR_DPI / witness.LINES_PER_INCH
            return "" if self.strips_blank and image.height < line else self.text


def boxed(
    service: BoxedService, tesseract: str, strips_blank: bool = False
) -> extract.PageText:
    assert isinstance(service, BlockReader)
    return extract._page_text(
        ImagedDocument(images=True),
        1,
        PageKind.SCAN,
        ocr=service,
        tesseract=StripBlankTesseract(text=tesseract, strips_blank=strips_blank),
    )


def test_a_block_whose_box_has_no_room_for_it_is_dropped() -> None:
    """A scanner's band at the page's top boxed as a header, and a page of
    text written for it."""
    service = BoxedService(
        (
            Block("of the property " * 60, (0, 0, 120, 17)),
            Block(PAGE, (50, 100, 950, 400)),
        )
    )

    page = boxed(service, tesseract=PAGE, strips_blank=True)

    assert (page.text, page.disputed, page.dropped) == (PAGE, 0, 1)


def test_a_block_no_witness_holds_is_read_alone_and_replaced() -> None:
    service = BoxedService(
        (Block(PAGE, (50, 100, 950, 400)), Block(MADE, (50, 450, 950, 700))),
        alone=OTHER,
    )

    page = boxed(service, tesseract=f"{PAGE}\n{OTHER}")

    assert (page.text, page.disputed, page.alone) == (f"{PAGE}\n\n{OTHER}", 0, 1)
    assert page.readings == 3


class ScanPages:
    """Two scanned pages, each its own image."""

    def page_kinds(self) -> list[PageKind]:
        return [PageKind.SCAN, PageKind.SCAN]

    def text(self, index: int) -> str:
        return ""

    def has_images(self, index: int) -> bool:
        return True

    def png(self, index: int, *, dpi: int) -> bytes:
        return f"PAGE {index}".encode()


def test_the_service_reads_the_next_page_while_this_one_is_witnessed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tesseract holds the first page until the service has the second."""
    second = threading.Event()

    class Service:
        name = TextSource.MINERU

        def read(self, png: bytes) -> str:
            if png == b"PAGE 2":
                second.set()
            return PAGE

    class Witness(StubOcr):
        def read(self, png: bytes) -> str:
            if png == b"PAGE 1":
                assert second.wait(timeout=5), "the second page was not sent"
            return PAGE

    @contextmanager
    def opened(path: Path) -> Iterator[ScanPages]:
        yield ScanPages()

    monkeypatch.setattr(pdf_module, "open_pdf", opened)

    pages = extract.extract_pdf(Path("two.pdf"), ocr=Service(), tesseract=Witness())

    assert [(page.page_index, page.text, page.disputed) for page in pages] == [
        (1, PAGE, 0),
        (2, PAGE, 0),
    ]


def test_a_scans_old_layer_witnesses_beside_tesseract() -> None:
    """Tesseract read nothing useful; the old layer holds the reading."""
    page = extract._page_text(
        FakeDocument(layer=PAGE, images=True),
        1,
        PageKind.SCAN,
        ocr=Readings(PAGE),
        tesseract=StubOcr(text="~ ~ ~"),
    )

    assert page.disputed == 0


def test_under_the_tesseract_backend_no_witness_runs() -> None:
    """Tesseract held against itself would witness nothing."""
    tesseract = StubOcr(text=PAGE)

    page = extract._page_text(
        FakeDocument(images=True),
        1,
        PageKind.SCAN,
        ocr=tesseract,
        tesseract=tesseract,
    )

    assert (page.text, page.disputed, len(tesseract.seen)) == (PAGE, None, 1)


# --- Which provider reads it ---


def test_a_missing_tesseract_binary_fails_before_the_first_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The package brings the wrapper; the engine is a system
    install, named with its command."""
    monkeypatch.setattr(tesseract_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(tesseract_module.sys, "platform", "darwin")

    for build in (
        lambda: provider_for(settings_for(OcrBackend.TESSERACT)),
        tesseract_provider,
    ):
        with pytest.raises(OcrError) as error:
            build()
        assert "brew install tesseract" in str(error.value)


def test_a_remote_service_is_not_asked_before_a_page_needs_it() -> None:
    """A paper of digital pages needs no service; one that is down is met
    at the first page that needs it, and stops the run there."""
    with FakeOcr() as service:
        provider_for(settings_for(OcrBackend.PADDLE, service.url))

    assert service.calls == []
    with pytest.raises(OcrError):
        provider_for(settings_for(OcrBackend.MINERU, CLOSED_PORT)).read(b"PNG")


def test_each_service_backend_has_its_own_client_and_name() -> None:
    """The name is on the wire in a corpus nobody may rewrite."""
    paddle = provider_for(
        settings_for(OcrBackend.PADDLE, "http://h:8080/layout-parsing")
    )
    mineru = provider_for(settings_for(OcrBackend.MINERU, "http://h:8000/v1/tasks"))
    remote = provider_for(settings_for(OcrBackend.HTTP, "https://ocr.example/v1"))

    assert isinstance(paddle, PaddleProvider) and paddle.name is TextSource.PADDLE
    assert isinstance(mineru, MineruProvider) and mineru.name is TextSource.MINERU
    assert isinstance(remote, HttpProvider) and remote.name is TextSource.HTTP
    assert all(isinstance(p, BlockReader) for p in (paddle, mineru, remote))
    assert paddle.url == "http://h:8080/layout-parsing"


def test_tesseract_is_read_in_this_process() -> None:
    provider = provider_for(settings_for(OcrBackend.TESSERACT))

    assert isinstance(provider, TesseractProvider)
    assert provider.name is TextSource.TESSERACT


@pytest.mark.parametrize(
    "backend", [OcrBackend.HTTP, OcrBackend.PADDLE, OcrBackend.MINERU]
)
def test_a_remote_backend_with_no_address_is_refused(backend: OcrBackend) -> None:
    with pytest.raises(SourcesError) as error:
        provider_for(settings_for(backend))

    assert "ocr_url" in str(error.value)
    assert "tesseract" in str(error.value)


def test_the_tesseract_provider_reads_the_rendered_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("pytesseract")
    png = pytest.importorskip("PIL.Image").new("RGB", (2, 2))
    import io

    import pytesseract

    buffer = io.BytesIO()
    png.save(buffer, format="PNG")
    monkeypatch.setattr(pytesseract, "image_to_string", lambda image: "  read  ")

    assert TesseractProvider().read(buffer.getvalue()) == "read"


# --- The HTTP contract (docs/ocr.md) ---


def test_the_service_receives_the_page_and_its_content_type() -> None:
    with FakeOcr() as service:
        text = HttpProvider(url=service.url).read(b"PNG BYTES")

    assert text == "read by the service"
    assert len(service.calls) == 1
    assert service.calls[0].path == "/parse"
    assert service.calls[0].body == b"PNG BYTES"
    assert service.calls[0].content_type == "application/octet-stream"


def test_a_key_is_sent_as_a_bearer_token_and_nothing_is_sent_without_one() -> None:
    with FakeOcr() as service:
        HttpProvider(url=service.url, api_key="s3cret").read(b"PNG")
        HttpProvider(url=service.url).read(b"PNG")

    assert service.calls[0].authorization == "Bearer s3cret"
    assert service.calls[1].authorization == ""


def test_a_refusal_fails_loudly_rather_than_falling_back() -> None:
    """A silent fallback would poison a corpus build unnoticed."""
    with FakeOcr(status=503) as service, pytest.raises(OcrError) as error:
        address = service.url
        HttpProvider(url=service.url).read(b"PNG")

    assert address in str(error.value)
    assert "ocr_url" in str(error.value)
    assert "tesseract" in str(error.value)


def test_an_answer_that_is_not_the_contract_names_the_contract() -> None:
    with (
        FakeOcr(body=b"<html>not json</html>") as service,
        pytest.raises(SourcesError) as error,
    ):
        HttpProvider(url=service.url).read(b"PNG")

    assert "docs/ocr.md" in str(error.value)
    assert "text" in str(error.value)


def test_a_services_blocks_come_with_their_boxes() -> None:
    answer = {
        "text": "the page as the service joined it",
        "blocks": [
            {"text": " Heading ", "box": [10, 20, 300, 60.4]},
            {"text": "", "box": [0, 0, 1, 1]},
            {"text": "A paragraph.", "box": [10, 80, 300, 400], "type": "text"},
        ],
    }
    with FakeOcr(body=json.dumps(answer).encode()) as service:
        provider = HttpProvider(url=service.url)
        blocks = provider.read_blocks(b"PNG")
        text = provider.read(b"PNG")

    assert blocks == (
        Block("Heading", (10, 20, 300, 60)),
        Block("A paragraph.", (10, 80, 300, 400)),
    )
    assert text == "Heading\n\nA paragraph."


def test_an_image_a_service_sets_in_its_text_is_cut() -> None:
    """MinerU writes a picture in a table's cell as a data URI: no text the
    page prints, and enough of it to pass the embedding model's context."""
    picture = '<img src="data:image/jpeg;base64,/9j/4AAQSkZJRg=="/>'
    answer = {
        "text": "unread",
        "blocks": [
            {
                "text": f"<table><tr><td>k</td><td>{picture}</td></tr></table>",
                "box": [10, 20, 300, 60],
            },
            {"text": picture, "box": [10, 80, 300, 400]},
        ],
    }
    with FakeOcr(body=json.dumps(answer).encode()) as service:
        blocks = HttpProvider(url=service.url).read_blocks(b"PNG")
    with FakeOcr(text=f"One line.\n\n{picture}") as service:
        text = HttpProvider(url=service.url).read(b"PNG")

    assert blocks == (
        Block("<table><tr><td>k</td><td></td></tr></table>", (10, 20, 300, 60)),
    )
    assert text == "One line."


def test_an_answer_without_blocks_is_its_paragraphs_with_no_box() -> None:
    with FakeOcr(text="One line.\n\n\n  Two lines\nof it.  ") as service:
        blocks = HttpProvider(url=service.url).read_blocks(b"PNG")

    assert blocks == (Block("One line."), Block("Two lines\nof it."))


@pytest.mark.parametrize("box", [[1, 2, 3], [10, 10, 5, 20], "the top"])
def test_a_block_whose_box_is_not_four_edges_breaks_the_contract(box: Any) -> None:
    answer = {"text": "x", "blocks": [{"text": "x", "box": box}]}
    with (
        FakeOcr(body=json.dumps(answer).encode()) as service,
        pytest.raises(OcrError) as error,
    ):
        HttpProvider(url=service.url).read_blocks(b"PNG")

    assert '"box"' in str(error.value)


def test_a_closed_port_fails_and_names_no_machine() -> None:
    with pytest.raises(SourcesError) as error:
        HttpProvider(url=CLOSED_PORT).read(b"PNG")

    message = str(error.value)
    assert CLOSED_PORT in message
    assert "ocr_url" in message
    assert "~/repos" not in message
    assert str(Path.home()) not in message


# --- The engines' own servers ---


def sha256(data: bytes) -> str:
    return _sha256(data).hexdigest()


def png_of(width: int, height: int) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("L", (width, height), 255).save(buffer, format="PNG")
    return buffer.getvalue()


def paddle_block(label: str, content: str, box: list[int]) -> dict[str, Any]:
    return {"block_label": label, "block_content": content, "block_bbox": box}


def paddle_answer(*blocks: dict[str, Any], error_code: int = 0) -> bytes:
    """PaddleX's `/layout-parsing` answer, as its server writes it."""
    return json.dumps(
        {
            "logId": "l",
            "errorCode": error_code,
            "errorMsg": "Success" if error_code == 0 else "Internal Server Error",
            "result": {
                "layoutParsingResults": [
                    {
                        "prunedResult": {"parsing_res_list": list(blocks)},
                        "markdown": {"text": "the pipeline's own text"},
                    }
                ]
            },
        }
    ).encode()


def paddle_server(*answers: Answer) -> FakeServer:
    """A PaddleX server answering each request with the next answer, the
    last one from then on."""
    queue = list(answers)

    def route(exchange: Exchange) -> Answer:
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return FakeServer(route)


def test_paddle_posts_the_page_as_paddlex_serving_takes_it() -> None:
    page = png_of(10, 20)
    with paddle_server((200, paddle_answer())) as server:
        PaddleProvider(url=f"{server.base}/layout-parsing").read_blocks(page)

    (sent,) = server.exchanges
    assert (sent.method, sent.path) == ("POST", "/layout-parsing")
    assert sent.headers["Content-Type"] == "application/json"
    request = json.loads(sent.body)
    assert request == {"file": base64.b64encode(page).decode(), "fileType": 1}


def test_paddles_blocks_come_with_their_boxes_and_a_formula_with_its_number() -> None:
    """The pipeline's own text drops a formula's number; a reader quotes
    "(2.2)" with its formula."""
    answer = paddle_answer(
        paddle_block("header", "A. Author: A theory of graphs", [600, 180, 1900, 230]),
        paddle_block("text", "", [1300, 300, 2300, 600]),
        paddle_block("display_formula", " $$ B+X=C. $$ ", [270, 530, 520, 580]),
        paddle_block("formula_number", "(2.2)", [1140, 530, 1220, 580]),
        paddle_block("image", "", [1360, 2700, 2240, 2900]),
        paddle_block("figure_title", "FIG. 2. A graph", [1290, 2930, 2290, 3000]),
    )
    with paddle_server((200, answer)) as server:
        provider = PaddleProvider(url=f"{server.base}/layout-parsing")
        blocks = provider.read_blocks(b"PNG")
        text = provider.read(b"PNG")

    assert blocks == (
        Block("A. Author: A theory of graphs", (600, 180, 1900, 230)),
        Block("$$ B+X=C. $$ (2.2)", (270, 530, 1220, 580)),
        Block("FIG. 2. A graph", (1290, 2930, 2290, 3000)),
    )
    assert text.split("\n\n")[1] == "$$ B+X=C. $$ (2.2)"


@pytest.mark.parametrize(
    "body", [paddle_answer(error_code=500), b'{"text": "the contract"}', b"<html>"]
)
def test_an_answer_not_paddles_names_the_endpoint_paddle_needs(body: bytes) -> None:
    """Not repeated: the same page would get the same answer."""
    with paddle_server((200, body)) as server, pytest.raises(OcrError) as error:
        PaddleProvider(url=f"{server.base}/layout-parsing").read(b"PNG")

    assert len(server.exchanges) == 1
    assert "/layout-parsing" in str(error.value)


def test_an_error_status_is_repeated_once_then_fails_loudly() -> None:
    with paddle_server((503, b"")) as server, pytest.raises(OcrError) as error:
        url = f"{server.base}/layout-parsing"
        PaddleProvider(url=url).read(b"PNG")

    assert len(server.exchanges) == 2
    assert url in str(error.value)
    assert "ocr_url" in str(error.value) and "tesseract" in str(error.value)


def test_a_second_try_that_reads_is_the_page() -> None:
    answer = paddle_answer(paddle_block("text", "Read.", [1, 2, 3, 4]))
    with paddle_server((503, b""), (200, answer)) as server:
        text = PaddleProvider(url=f"{server.base}/layout-parsing").read(b"PNG")

    assert text == "Read."


def test_a_key_goes_to_paddle_as_a_bearer_token() -> None:
    with paddle_server((200, paddle_answer())) as server:
        PaddleProvider(url=f"{server.base}/x", api_key="s3cret").read(b"PNG")
        PaddleProvider(url=f"{server.base}/x").read(b"PNG")

    assert server.exchanges[0].headers["Authorization"] == "Bearer s3cret"
    assert "Authorization" not in server.exchanges[1].headers


def mineru_zip(*blocks: dict[str, Any], name: str = "structured_content.json") -> bytes:
    """A MinerU task's result: the zip its server writes."""
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("markdown.md", "the page as Markdown")
        archive.writestr(
            name,
            json.dumps(
                {
                    "pages": [{"page_idx": 0, "blocks": list(blocks)}],
                    "metadata": {"producer": {"name": "mineru", "version": "4.1.3"}},
                }
            ),
        )
    return buffer.getvalue()


@dataclass
class Mineru:
    """MinerU's API server, as `mineru-kit api-server` answers: a task, its
    upload, its state polled, its result."""

    result: bytes = field(default_factory=mineru_zip)
    #: the task's state at each poll in turn, the last from then on
    states: list[str] = field(
        default_factory=lambda: ["queued", "running", "completed"]
    )
    #: where the page is uploaded: a path on the server, an address
    #: elsewhere, or None where the server holds the page already
    upload: str | None = "/v1/uploads/t1"
    #: how many task creations are refused before one is taken
    refused: int = 0
    #: where the result's address redirects, if anywhere
    redirect_result_to: str = ""
    polls: int = 0

    def __call__(self, exchange: Exchange) -> Answer:
        if exchange.method == "POST" and exchange.path == "/v1/tasks":
            if self.refused:
                self.refused -= 1
                return 503, b""
            self.polls = 0
            file: dict[str, Any] = {"file_name": "page.png"}
            if self.upload is not None:
                file["upload"] = {
                    "url": self.upload,
                    "headers": {"Content-Type": "image/png"},
                }
            return 200, json.dumps({"task_id": "t1", "files": [file]}).encode()
        if exchange.method == "GET" and exchange.path == "/v1/tasks/t1":
            state = self.states[min(self.polls, len(self.states) - 1)]
            self.polls += 1
            task = {
                "task_id": "t1",
                "status": state,
                "files": [
                    {"result_url": "/v1/results/t1.zip", "error": "out of memory"}
                ],
            }
            return 200, json.dumps(task).encode()
        if exchange.method == "GET" and exchange.path == "/v1/results/t1.zip":
            if self.redirect_result_to:
                return 302, self.redirect_result_to.encode()
            return 200, self.result
        if exchange.method == "PUT":
            return 200, b""
        return 404, b""


@pytest.fixture
def quick_polls(monkeypatch: pytest.MonkeyPatch) -> None:
    from axiomation.sources.ingest import ocr

    monkeypatch.setattr(ocr, "MINERU_POLL_SECONDS", 0)


def mineru_block(
    kind: str, content: str, box: list[float], **more: Any
) -> dict[str, Any]:
    return {"type": kind, "bbox": box, "content": content, **more}


def test_mineru_reads_a_page_in_a_task_of_its_own(quick_polls: None) -> None:
    page = png_of(1000, 2000)
    with FakeServer(Mineru()) as server:
        MineruProvider(url=f"{server.base}/v1/tasks").read_blocks(page)

    steps = [(e.method, e.path) for e in server.exchanges]
    assert steps == [
        ("POST", "/v1/tasks"),
        ("PUT", "/v1/uploads/t1"),
        ("GET", "/v1/tasks/t1"),
        ("GET", "/v1/tasks/t1"),
        ("GET", "/v1/tasks/t1"),
        ("GET", "/v1/results/t1.zip"),
    ]
    created = json.loads(server.exchanges[0].body)
    assert created["files"] == [
        {"file_name": "page.png", "bytes": len(page), "sha256sum": sha256(page)}
    ]
    assert server.exchanges[1].body == page
    assert server.exchanges[1].headers["Content-Type"] == "image/png"


def test_a_page_the_server_holds_is_not_uploaded_again(quick_polls: None) -> None:
    with FakeServer(Mineru(upload=None)) as server:
        MineruProvider(url=f"{server.base}/v1/tasks").read_blocks(png_of(10, 10))

    assert "PUT" not in {e.method for e in server.exchanges}


def test_minerus_boxes_are_the_images_pixels_and_a_display_equation_is_delimited(
    quick_polls: None,
) -> None:
    """MinerU gives a box in shares of the image, and a display equation
    bare; its inline math comes in `$…$` already."""
    result = mineru_zip(
        mineru_block("header", "A. Author", [0.2, 0.05, 0.75, 0.07]),
        mineru_block(
            "text", "(1) $A + 2X \\rightleftharpoons 3X$,", [0.1, 0.1, 0.2, 0.1]
        ),
        mineru_block("equation", "B + X = C \\tag{2.2}", [0.1, 0.2, 0.5, 0.3]),
        mineru_block("equation", "\\[ x \\]", [0.1, 0.3, 0.5, 0.4]),
        mineru_block("image", "", [0.1, 0.5, 0.9, 0.9]),
    )
    with FakeServer(Mineru(result=result)) as server:
        blocks = MineruProvider(url=f"{server.base}/v1/tasks").read_blocks(
            png_of(1000, 2000)
        )

    assert blocks == (
        Block("A. Author", (200, 100, 750, 140)),
        Block("(1) $A + 2X \\rightleftharpoons 3X$,", (100, 200, 200, 201)),
        Block("\\[ B + X = C \\tag{2.2} \\]", (100, 400, 500, 600)),
        Block("\\[ x \\]", (100, 600, 500, 800)),
    )


def test_a_tables_caption_and_footnote_are_blocks_of_their_own_in_page_order(
    quick_polls: None,
) -> None:
    caption = {"bbox": [0.1, 0.05, 0.6, 0.09], "content": "TABLE 1 Two inputs"}
    footnote = {"bbox": [0.1, 0.62, 0.6, 0.65], "content": "a. After the first table."}
    result = mineru_zip(
        mineru_block(
            "table",
            "| 0 | 1 |\n| --- | --- |\n| 1 | 0 |",
            [0.1, 0.1, 0.9, 0.6],
            captions=[caption],
            footnotes=[footnote],
        ),
        mineru_block(
            "image",
            "",
            [0.1, 0.7, 0.9, 0.9],
            captions=[{"bbox": [0.1, 0.91, 0.9, 0.95], "content": "FIG. 2. A graph"}],
            footnotes=[],
        ),
    )
    with FakeServer(Mineru(result=result)) as server:
        provider = MineruProvider(url=f"{server.base}/v1/tasks")
        texts = [block.text for block in provider.read_blocks(png_of(100, 100))]

    assert texts == [
        "TABLE 1 Two inputs",
        "| 0 | 1 |\n| --- | --- |\n| 1 | 0 |",
        "a. After the first table.",
        "FIG. 2. A graph",
    ]


def test_a_failed_task_is_repeated_once_then_fails_loudly(quick_polls: None) -> None:
    with FakeServer(Mineru(states=["running", "failed"])) as server:
        url = f"{server.base}/v1/tasks"
        with pytest.raises(OcrError) as error:
            MineruProvider(url=url).read(png_of(10, 10))

    creations = [e for e in server.exchanges if e.method == "POST"]
    assert len(creations) == 2
    assert "failed" in str(error.value) and "out of memory" in str(error.value)
    assert url in str(error.value)


def test_a_refused_creation_is_repeated_once(quick_polls: None) -> None:
    with FakeServer(Mineru(refused=1)) as server:
        MineruProvider(url=f"{server.base}/v1/tasks").read(png_of(10, 10))

    assert len([e for e in server.exchanges if e.method == "POST"]) == 2


def test_a_task_never_done_fails_in_the_pages_time(
    quick_polls: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from axiomation.sources.ingest import ocr

    monkeypatch.setattr(ocr, "OCR_TIMEOUT", 0.05)
    with (
        FakeServer(Mineru(states=["running"])) as server,
        pytest.raises(OcrError) as error,
    ):
        MineruProvider(url=f"{server.base}/v1/tasks").read(png_of(10, 10))

    assert ("GET", "/v1/tasks/t1") in {(e.method, e.path) for e in server.exchanges}
    assert "not done" in str(error.value)


def test_a_task_that_succeeded_is_read(quick_polls: None) -> None:
    with FakeServer(Mineru(states=["running", "succeeded"])) as server:
        MineruProvider(url=f"{server.base}/v1/tasks").read(png_of(10, 10))

    assert server.exchanges[-1].path == "/v1/results/t1.zip"


def test_a_task_of_another_shape_names_the_endpoint_mineru_needs(
    quick_polls: None,
) -> None:
    """A server of another version, answering 200 with JSON that is not a
    task: the ingest's own failure, not a traceback."""
    with (
        FakeServer(lambda exchange: (200, b'{"id": "t1"}')) as server,
        pytest.raises(OcrError) as error,
    ):
        MineruProvider(url=f"{server.base}/v1/tasks").read(png_of(10, 10))

    assert len(server.exchanges) == 1
    assert "/v1/tasks" in str(error.value)


def test_a_redirect_elsewhere_carries_no_key(quick_polls: None) -> None:
    """The key goes to the server's own addresses only, a redirect
    included."""
    with (
        FakeServer(lambda exchange: (200, mineru_zip())) as store,
        FakeServer(Mineru(redirect_result_to=f"{store.base}/bucket/t1.zip")) as server,
    ):
        MineruProvider(url=f"{server.base}/v1/tasks", api_key="s3cret").read(
            png_of(10, 10)
        )

    (fetched,) = store.exchanges
    assert fetched.path == "/bucket/t1.zip"
    assert "Authorization" not in fetched.headers


def test_a_refused_key_is_named() -> None:
    refusal = b'{"error": "invalid_api_key"}'
    with (
        chat_server(refusal, status=401) as server,
        pytest.raises(OcrError) as error,
    ):
        ChatProvider(url=f"{server.base}/c", model="m", api_key="x").read(b"PNG")

    assert "ocr_api_key" in str(error.value)
    assert "invalid_api_key" in str(error.value)


def test_a_result_not_minerus_names_the_endpoint_mineru_needs(
    quick_polls: None,
) -> None:
    """Not repeated: the same page would get the same answer."""
    result = mineru_zip(name="something_else.json")
    with FakeServer(Mineru(result=result)) as server, pytest.raises(OcrError) as error:
        MineruProvider(url=f"{server.base}/v1/tasks").read(png_of(10, 10))

    assert len([e for e in server.exchanges if e.method == "POST"]) == 1
    assert "/v1/tasks" in str(error.value)


def test_the_key_goes_to_minerus_server_and_never_to_an_upload_elsewhere(
    quick_polls: None,
) -> None:
    """An upload address may be a store's, signed already: a second key
    breaks it, and the key is not the store's to see."""
    with (
        FakeServer(lambda exchange: (200, b"")) as store,
        FakeServer(Mineru(upload=f"{store.base}/bucket/page")) as server,
    ):
        MineruProvider(url=f"{server.base}/v1/tasks", api_key="s3cret").read(
            png_of(10, 10)
        )

    assert {e.headers.get("Authorization") for e in server.exchanges} == {
        "Bearer s3cret"
    }
    (upload,) = store.exchanges
    assert upload.method == "PUT" and "Authorization" not in upload.headers


def chat_answer(content: Any) -> bytes:
    """An OpenAI-compatible chat completion, as vLLM or Ollama writes it."""
    return json.dumps(
        {
            "id": "c",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
        }
    ).encode()


def chat_server(body: bytes, status: int = 200) -> FakeServer:
    return FakeServer(lambda exchange: (status, body))


def test_the_chat_backend_sends_the_page_as_an_image_with_the_prompt() -> None:
    page = png_of(10, 20)
    with chat_server(chat_answer("Read.")) as server:
        ChatProvider(
            url=f"{server.base}/v1/chat/completions", model="qwen2.5vl:7b"
        ).read(page)

    (sent,) = server.exchanges
    assert (sent.method, sent.path) == ("POST", "/v1/chat/completions")
    request = json.loads(sent.body)
    assert request["model"] == "qwen2.5vl:7b"
    assert request["temperature"] == 0
    (message,) = request["messages"]
    image, prompt = message["content"]
    assert image == {
        "type": "image_url",
        "image_url": {
            "url": "data:image/png;base64," + base64.b64encode(page).decode()
        },
    }
    assert prompt == {"type": "text", "text": CHAT_PROMPT}


def test_a_chat_reading_is_its_paragraphs_with_no_box() -> None:
    """A chat model draws no boxes: the witness holds its paragraphs
    against the page as a whole."""
    with chat_server(
        chat_answer("# A title\n\nOne line.\n\n\n  Two\nlines.  ")
    ) as server:
        blocks = ChatProvider(url=f"{server.base}/c", model="m").read_blocks(b"PNG")

    assert blocks == (Block("# A title"), Block("One line."), Block("Two\nlines."))


def test_a_page_a_chat_model_fences_is_unfenced() -> None:
    answer = chat_answer("```markdown\n# A title\n\nOne line.\n```")
    with chat_server(answer) as server:
        text = ChatProvider(url=f"{server.base}/c", model="m").read(b"PNG")

    assert text == "# A title\n\nOne line."


@pytest.mark.parametrize(
    "body", [chat_answer(None), b'{"choices": []}', paddle_answer(), b"<html>"]
)
def test_an_answer_not_a_chat_completion_names_the_endpoint_chat_needs(
    body: bytes,
) -> None:
    with chat_server(body) as server, pytest.raises(OcrError) as error:
        ChatProvider(url=f"{server.base}/c", model="m").read(b"PNG")

    assert len(server.exchanges) == 1
    assert "/chat/completions" in str(error.value)
    assert "ocr_model" in str(error.value)


def test_an_error_status_carries_the_services_reason() -> None:
    """OpenRouter refusing a model its account's providers do not serve:
    the reason is what the person acts on."""
    reason = json.dumps(
        {"error": {"message": "No allowed providers are available for the model."}}
    ).encode()
    with chat_server(reason, status=404) as server, pytest.raises(OcrError) as error:
        ChatProvider(url=f"{server.base}/c", model="m").read(b"PNG")

    assert len(server.exchanges) == 2
    assert "HTTP 404" in str(error.value)
    assert "No allowed providers" in str(error.value)


def test_a_key_goes_to_the_chat_service_as_a_bearer_token() -> None:
    with chat_server(chat_answer("x")) as server:
        ChatProvider(url=f"{server.base}/c", model="m", api_key="sk-or").read(b"PNG")

    assert server.exchanges[0].headers["Authorization"] == "Bearer sk-or"


def test_the_chat_backend_needs_its_model() -> None:
    settings = replace(
        settings_for(OcrBackend.CHAT, "http://h/v1/chat/completions"), ocr_model=""
    )
    with pytest.raises(SourcesError) as error:
        provider_for(settings)

    assert "ocr_model" in str(error.value)
    provider = provider_for(replace(settings, ocr_model="qwen2.5vl:7b"))
    assert isinstance(provider, ChatProvider)
    assert (provider.name, provider.model) == (TextSource.CHAT, "qwen2.5vl:7b")


# --- Embedding ---


def test_embed_documents_returns_a_vector_pair_and_a_token_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeVoyage()
    monkeypatch.setattr(vectors, "voyage", lambda key: client)
    monkeypatch.setattr(
        vectors,
        "sparse_vectors",
        lambda texts: [{"indices": [1], "values": [1.0]} for _ in texts],
    )

    dense, sparse, tokens = embed_documents(
        ["one two", "three"], voyage_model="voyage-4", voyage_dims=4, api_key="k"
    )

    assert [len(vector) for vector in dense] == [4, 4]
    assert len(sparse) == 2
    assert tokens == 3


def test_embed_documents_ships_every_batch_the_packer_makes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeVoyage()
    monkeypatch.setattr(vectors, "voyage", lambda key: client)
    monkeypatch.setattr(vectors, "sparse_vectors", lambda texts: [{}] * len(texts))
    monkeypatch.setattr("axiomation.sources.library.embed.BATCH_SIZE", 2)

    embed_documents(
        ["a", "b", "c"], voyage_model="voyage-4", voyage_dims=2, api_key="k"
    )

    assert client.batches == [["a", "b"], ["c"]]


def test_sparse_vectors_speak_qdrants_index_value_pairs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Vector:
        def __init__(self) -> None:
            self.indices = _Array([3, 7])
            self.values = _Array([0.5, 0.25])

    monkeypatch.setattr(
        vectors, "bm25", lambda: type("M", (), {"embed": lambda self, t: [Vector()]})()
    )

    assert vectors.sparse_vectors(["anything"]) == [
        {"indices": [3, 7], "values": [0.5, 0.25]}
    ]


class _Array(list):
    """Stands in for the numpy arrays fastembed returns."""

    def tolist(self) -> list:
        return list(self)


def test_the_voyage_client_is_built_once_per_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("voyageai")
    import voyageai

    built: list[str] = []
    monkeypatch.setattr(
        voyageai, "Client", lambda api_key: built.append(api_key) or object()
    )
    monkeypatch.setattr(vectors, "_voyage", None)
    monkeypatch.setattr(vectors, "_voyage_key", None)

    first = vectors.voyage("one")
    assert vectors.voyage("one") is first
    assert vectors.voyage("two") is not first
    assert built == ["one", "two"]
