"""The optional Zotero adapter, against a real socket.

The adapter's first question is whether anything answers on the port, so the
fake is an HTTP server rather than a patch. Nothing here writes: there is no
write call in the adapter to test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from axiomation.sources.read import zotero
from sources.fakes import FakeZotero

CLOSED_PORT = "http://127.0.0.1:1"


@pytest.fixture
def library(fixture_pdf: Path):
    """A Zotero holding one item with one PDF, and one with none."""
    with FakeZotero(
        {
            "hardy1949": {"item_key": "L27CBAD8", "paths": [fixture_pdf]},
            "erdos1950": {"item_key": "ZZ11AA22", "paths": []},
        }
    ) as fake:
        yield fake


def test_a_running_zotero_answers_the_ping(library: FakeZotero) -> None:
    assert zotero.available(library.url)


def test_an_unavailable_port_degrades_rather_than_raising() -> None:
    assert not zotero.available(CLOSED_PORT, timeout=0.5)


def test_attachments_resolve_a_bib_key_to_a_path(
    library: FakeZotero, fixture_pdf: Path
) -> None:
    found = zotero.attachments("hardy1949", base_url=library.url)

    assert [item.path for item in found] == [fixture_pdf]
    assert found[0].open_link.startswith("zotero://open-pdf/")
    assert library.calls[-1] == ("item.attachments", ["hardy1949"])


def test_the_pdf_path_is_the_first_one_that_exists(
    library: FakeZotero, fixture_pdf: Path
) -> None:
    assert zotero.pdf_path("hardy1949", base_url=library.url) == fixture_pdf


def test_an_item_with_no_attachment_has_no_pdf(library: FakeZotero) -> None:
    assert zotero.pdf_path("erdos1950", base_url=library.url) is None


def test_an_attachment_that_is_not_on_this_machine_is_not_offered(
    tmp_path: Path,
) -> None:
    with FakeZotero(
        {"gone1900": {"item_key": "AAAA", "paths": [tmp_path / "gone.pdf"]}}
    ) as fake:
        assert zotero.pdf_path("gone1900", base_url=fake.url) is None


def test_a_select_link_is_built_from_the_item_key(library: FakeZotero) -> None:
    link = zotero.select_link("hardy1949", base_url=library.url)

    assert link == "zotero://select/library/items/L27CBAD8"
    assert library.calls[-1] == ("item.search", ["hardy1949"])


def test_a_key_zotero_does_not_have_has_no_link(library: FakeZotero) -> None:
    assert zotero.select_link("nosuch1999", base_url=library.url) is None


def test_an_unavailable_zotero_answers_nothing_rather_than_raising() -> None:
    assert zotero.attachments("hardy1949", base_url=CLOSED_PORT, timeout=0.5) == ()
    assert zotero.pdf_path("hardy1949", base_url=CLOSED_PORT, timeout=0.5) is None
    assert zotero.select_link("hardy1949", base_url=CLOSED_PORT, timeout=0.5) is None


def test_the_adapter_offers_no_way_to_write(library: FakeZotero) -> None:
    """Agents never write to Zotero or to the bibliography."""
    public = {name for name in dir(zotero) if not name.startswith("_")}

    assert not {
        name
        for name in public
        if any(verb in name for verb in ("write", "create", "update", "delete", "add"))
    }
