"""Fixtures: the paper, its settings,
a fake Qdrant and a reader on it. A test module that needs the `library`
extra skips without it (`pytest.importorskip("qdrant_client")` at its top);
the rest run in the core (`make test-core`)."""

from __future__ import annotations

from pathlib import Path

import pytest

from axiomation.sources.config import (
    ResolvedSources,
    Settings,
    load_settings,
)
from axiomation.sources.config import resolve as resolve_sources
from user_files import write_user_file


@pytest.fixture
def sources_paper(tmp_path: Path, user_file: Path) -> Path:
    """A paper that names its own collection, its person's Qdrant a port
    nothing answers on."""
    write_user_file(
        user_file, qdrant_url="http://localhost:65535", ocr_backend="tesseract"
    )
    root = tmp_path / "sources-paper"
    root.mkdir()
    (root / "paper.md").write_text("# A paper\n", encoding="utf-8")
    (root / "axiomation.toml").write_text(
        "[paper]\n"
        'manuscripts = ["paper.md"]\n'
        'bib = "references.bib"\n'
        "\n"
        "[sources]\n"
        'collection = "testpaper_voyage-3-lite_8"\n'
        'voyage_model = "voyage-3-lite"\n'
        "voyage_dims = 8\n",
        encoding="utf-8",
    )
    (root / "references.bib").write_text("", encoding="utf-8")
    return root


@pytest.fixture
def sources_config(sources_paper: Path) -> Settings:
    return load_settings(sources_paper)


@pytest.fixture
def settings(sources_config: Settings) -> ResolvedSources:
    return resolve_sources(sources_config)


@pytest.fixture
def fake_qdrant(settings: ResolvedSources):
    """An empty Qdrant holding the paper's own collection."""
    pytest.importorskip("qdrant_client")
    from sources.fakes import FakeQdrant

    client = FakeQdrant()
    client.make(settings.collection, size=settings.voyage_dims)
    return client


@pytest.fixture
def reader(settings: ResolvedSources, fake_qdrant):
    """A `SourceReader` wired to the fake, never to a socket."""
    from axiomation.sources.library.client import SourceReader

    built = SourceReader(settings, api_key="fixture-key")
    built._client = fake_qdrant
    return built


@pytest.fixture(scope="session")
def fixture_pdf(tmp_path_factory) -> Path:
    """A three-page PDF with a text layer, written once for the session."""
    from sources.pdfs import write_pdf

    path = tmp_path_factory.mktemp("pdf") / "hardy1949.pdf"
    return write_pdf(
        path, [f"Page {number} of the divergent series. " * 6 for number in (1, 2, 3)]
    )
