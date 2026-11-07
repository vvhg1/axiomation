"""A PDF's content hash (`sources.content`): what it prints, not how it is
saved. A file annotated, given other metadata or compressed otherwise
prints the same; a word changed, an image redrawn or a page added prints
other content; a file the PDF reader cannot parse is known by its SHA-256.

Synthetic PDFs (`pdfs`), written again with pypdf; no corpus.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from pdfs import write_pdf
from pypdf import PdfWriter
from pypdf.annotations import FreeText, Text

from axiomation.sources.content import CONTENT, IMAGES, content_hash, content_parts

PAGES = ["The first page says the bound holds.", "The second page proves it."]


def saved_again(source: Path, target: Path, *, compress: bool) -> Path:
    """`source` written again as a reader saves it: highlights and notes
    added, its title changed, its streams compressed or not."""
    writer = PdfWriter(clone_from=source)
    writer.add_annotation(0, Text(text="a note", rect=(50, 550, 200, 650)))
    writer.add_annotation(1, FreeText(text="see p. 2", rect=(50, 50, 200, 100)))
    writer.add_metadata({"/Title": "my copy", "/Author": "a reader"})
    if compress:
        for page in writer.pages:
            page.compress_content_streams()
    writer.write(target)
    return target


def test_a_file_annotated_or_saved_otherwise_prints_the_same(tmp_path) -> None:
    first = write_pdf(tmp_path / "a.pdf", PAGES, images=[1])
    for compress in (True, False):
        again = saved_again(first, tmp_path / f"b{compress}.pdf", compress=compress)
        assert again.read_bytes() != first.read_bytes()
        assert content_hash(again) == content_hash(first)


def test_a_word_an_image_or_a_page_changed_prints_other_content(tmp_path) -> None:
    first = write_pdf(tmp_path / "a.pdf", PAGES, images=[1])
    changed = [PAGES[0].replace("holds", "fails"), PAGES[1]]
    word = write_pdf(tmp_path / "w.pdf", changed, images=[1])
    image = write_pdf(tmp_path / "i.pdf", PAGES, images=[2])
    more = write_pdf(tmp_path / "m.pdf", [*PAGES, "A third page."], images=[1])
    hashes = {content_hash(p) for p in (first, word, image, more)}
    assert len(hashes) == 4
    # the parts say what changed: the words are the content streams'
    was, now = content_parts(first).parts, content_parts(word).parts
    assert was[CONTENT] != now[CONTENT] and was[IMAGES] == now[IMAGES]


def test_a_file_the_reader_cannot_parse_is_known_by_its_sha256(tmp_path) -> None:
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.7 not a PDF at all")
    found = content_parts(broken)
    assert found.hash == hashlib.sha256(broken.read_bytes()).hexdigest()
    assert found.parts == {}


def test_an_image_s_pixels_alone_changed_print_other_content(tmp_path) -> None:
    """The same page, its image's pixels other: the content streams stay,
    the images' part tells."""
    first = write_pdf(tmp_path / "a.pdf", PAGES, images=[1])
    writer = PdfWriter(clone_from=first)
    xobjects = writer.pages[0]["/Resources"]["/XObject"]
    (name,) = list(xobjects)
    image = xobjects[name].get_object()
    image.set_data(bytes(255 - b for b in image.get_data()))
    other = tmp_path / "b.pdf"
    writer.write(other)
    was, now = content_parts(first), content_parts(other)
    assert was.hash != now.hash
    assert was.parts[CONTENT] == now.parts[CONTENT]
    assert was.parts[IMAGES] != now.parts[IMAGES]
