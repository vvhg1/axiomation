"""Planning and running an ingest against a fake Qdrant.

Nothing here embeds anything: the planner is where every decision is made,
and `apply_ingest` is exercised through a stubbed entry so the writes, the
prune and the guard can be checked without an API key.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("qdrant_client")

from axiomation.sources.config import (  # noqa: E402
    OcrBackend,
    ResolvedSources,
    SettingFrom,
    SourcesError,
)
from axiomation.sources.contract import MARKER_FIELD, MARKER_ID, TextSource, point_id  # noqa: E402
from axiomation.sources.files import file_sha256  # noqa: E402
from axiomation.sources.ingest import run as run_module  # noqa: E402
from axiomation.sources.ingest.bib import BibEntry, parse  # noqa: E402
from axiomation.sources.ingest.chunk import Chunk  # noqa: E402
from axiomation.sources.ingest.run import (  # noqa: E402
    PRUNE_FLOOR,
    Reason,
    apply_ingest,
    plan_ingest,
)
from axiomation.sources.library.embed import CONTEXT_WINDOW  # noqa: E402
from axiomation.sources.library.qdrant import read_marker  # noqa: E402
from axiomation.sources.library.upsert import (  # noqa: E402
    CREATOR,
    IndexedPaper,
    delete_paper,
    ensure_collection,
    guard,
    indexed_papers,
    stale_backends,
    update_payload,
    upsert_chunks,
)
from sources.fakes import FakeQdrant, marker_payload  # noqa: E402

BIB = """\
@book{{hardy1949,
  title = {{Divergent Series}},
  author = {{Hardy, G. H.}},
  file = {{{hardy}}}
}}

@article{{erdos1950,
  title = {{On a problem of additive number theory}},
  author = {{Erd{{\\H{{o}}}}s, P.}},
  doi = {{10.1000/erdos}},
  file = {{{erdos}}}
}}

@book{{noattachment1900,
  title = {{A Book Nobody Has}},
  author = {{Nobody}}
}}
"""


@pytest.fixture
def bib_file(tmp_path: Path, fixture_pdf: Path) -> Path:
    """Two entries with a local PDF, one without, as a real `.bib`."""
    second = tmp_path / "erdos1950.pdf"
    second.write_bytes(fixture_pdf.read_bytes() + b"%comment\n")
    path = tmp_path / "references.bib"
    path.write_text(BIB.format(hardy=fixture_pdf, erdos=second), encoding="utf-8")
    return path


@pytest.fixture
def owned(settings: ResolvedSources, fake_qdrant: FakeQdrant) -> FakeQdrant:
    """A collection axiomation created, marker and all."""
    ensure_collection(fake_qdrant, settings)
    return fake_qdrant


def entry(key: str = "hardy1949", **overrides) -> BibEntry:
    fields = {
        "key": key,
        "title": "Divergent Series",
        "author": "Hardy, G. H.",
        "file_path": Path(f"/tmp/{key}.pdf"),
        "doi": "",
        **overrides,
    }
    return BibEntry(**fields)


def stub_ingest(monkeypatch: pytest.MonkeyPatch, chunks: int = 3) -> None:
    """Replace extraction and embedding; the planner is what is under test."""

    def fake_entry(client, settings, item, *, api_key, ocr, tesseract, kept):
        written = upsert_chunks(
            client,
            settings.collection,
            item.entry,
            [
                Chunk(
                    bib_key=item.entry.key,
                    title=item.entry.title,
                    page_index=index + 1,
                    chunk_index=index,
                    text="x" * 400,
                    source=TextSource.NONE,
                )
                for index in range(chunks)
            ],
            [[0.0] * settings.voyage_dims] * chunks,
            [{"indices": [1], "values": [1.0]}] * chunks,
            item.file_sha256,
        )
        return written, 100 * chunks, "", None

    monkeypatch.setattr(run_module, "_ingest_entry", fake_entry)


# --- Reading the bibliography ---


def test_only_entries_with_a_local_pdf_are_ingestable(bib_file: Path) -> None:
    assert [item.key for item in parse(bib_file)] == ["hardy1949", "erdos1950"]


def test_prune_reads_every_key_including_the_ones_with_no_pdf(bib_file: Path) -> None:
    from axiomation.sources.bib import records
    from axiomation.sources.files import stale

    found = records(bib_file)
    assert not any(stale(k, found, {}) for k in ("hardy1949", "noattachment1900"))
    assert stale("renamed1970", found, {})


def test_bibtex_braces_are_stripped_from_a_title(bib_file: Path) -> None:
    erdos = next(item for item in parse(bib_file) if item.key == "erdos1950")

    assert erdos.title == "On a problem of additive number theory"
    assert erdos.doi == "10.1000/erdos"


def test_the_payload_hash_moves_with_the_payload_fields() -> None:
    assert entry().payload_hash() != replace(entry(), title="Other").payload_hash()
    assert entry().payload_hash() != replace(entry(), doi="10.1/x").payload_hash()
    assert entry().payload_hash() == entry().payload_hash()


def test_the_payload_hash_ignores_where_the_pdf_lives() -> None:
    """The file path is not payload content, so moving one is not an edit."""
    moved = replace(entry(), file_path=Path("/elsewhere/hardy1949.pdf"))

    assert entry().payload_hash() == moved.payload_hash()


def test_the_file_hash_is_the_pdf_content(fixture_pdf: Path) -> None:
    assert file_sha256(fixture_pdf) == file_sha256(fixture_pdf)
    assert len(file_sha256(fixture_pdf)) == 64


# --- The creator guard ---


def test_a_collection_that_does_not_exist_yet_is_ours_to_create(
    settings: ResolvedSources,
) -> None:
    assert guard(FakeQdrant(), settings) == ""


def test_a_collection_with_no_marker_is_refused(
    settings: ResolvedSources, fake_qdrant: FakeQdrant
) -> None:
    refusal = guard(fake_qdrant, settings)

    assert settings.collection in refusal
    assert "did not create it" in refusal


def test_a_collection_created_by_someone_else_is_refused(
    settings: ResolvedSources, fake_qdrant: FakeQdrant
) -> None:
    fake_qdrant.collections[settings.collection][MARKER_ID] = type(
        "P", (), {"id": MARKER_ID, "payload": marker_payload(creator="othertool")}
    )()

    assert "othertool" in guard(fake_qdrant, settings)


def test_our_own_collection_is_ours_to_update(
    settings: ResolvedSources, owned: FakeQdrant
) -> None:
    assert guard(owned, settings) == ""


def test_a_changed_vector_space_is_a_new_collection_not_an_update(
    settings: ResolvedSources, owned: FakeQdrant
) -> None:
    refusal = guard(owned, replace(settings, voyage_dims=2048))

    assert "2048" in refusal
    assert "new collection" in refusal


def test_the_marker_names_the_creator_and_the_vector_space(
    settings: ResolvedSources, owned: FakeQdrant
) -> None:
    marker = read_marker(owned, settings.collection)

    assert marker is not None
    assert marker.creator == CREATOR
    assert marker.voyage_model == settings.voyage_model
    assert marker.voyage_dims == settings.voyage_dims
    assert marker.matches(settings)


def test_the_marker_names_no_ocr_backend(
    settings: ResolvedSources, owned: FakeQdrant
) -> None:
    """The backend of the day the collection was made says nothing of what
    read its pages, each of which records its own (`sources status`)."""
    (stamp,) = [
        p for p in owned.points(settings.collection) if MARKER_FIELD in p.payload
    ]

    assert "ocr_backend" not in stamp.payload


def test_the_marker_carries_the_window_for_a_contextualized_model(
    settings: ResolvedSources, fake_qdrant: FakeQdrant
) -> None:
    """How the vectors were made and the name does not say."""
    contextualized = replace(
        settings, voyage_model="voyage-context-4", collection="ctx_voyage-context-4_8"
    )

    ensure_collection(fake_qdrant, contextualized)

    marker = read_marker(fake_qdrant, contextualized.collection)
    assert marker is not None
    assert marker.context_window == CONTEXT_WINDOW == 32
    stamp = fake_qdrant.collections[contextualized.collection][MARKER_ID].payload
    assert stamp["context_window"] == 32


def test_the_marker_carries_no_window_for_a_plain_model(
    settings: ResolvedSources, owned: FakeQdrant
) -> None:
    marker = read_marker(owned, settings.collection)

    assert marker is not None
    assert marker.context_window is None
    assert (
        "context_window"
        not in owned.collections[settings.collection][MARKER_ID].payload
    )


def test_creating_a_collection_uses_the_configured_dimensions(
    settings: ResolvedSources,
) -> None:
    client = FakeQdrant()

    assert ensure_collection(client, settings) is True
    assert client.sizes[settings.collection] == settings.voyage_dims
    assert ensure_collection(client, settings) is False


# --- Change detection ---


def test_an_unindexed_paper_is_new(
    settings: ResolvedSources, owned: FakeQdrant, bib_file: Path
) -> None:
    plan = plan_ingest(owned, settings, bib_path=bib_file)

    assert [(item.entry.key, item.reason) for item in plan.pending] == [
        ("hardy1949", Reason.NEW),
        ("erdos1950", Reason.NEW),
    ]


def test_an_unchanged_paper_is_left_alone(
    settings: ResolvedSources,
    owned: FakeQdrant,
    bib_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_ingest(monkeypatch)
    first = plan_ingest(owned, settings, bib_path=bib_file)
    apply_ingest(owned, settings, first, api_key="k")

    again = plan_ingest(owned, settings, bib_path=bib_file)

    assert again.empty
    assert again.pending == ()


def test_a_changed_pdf_is_re_ingested(
    settings: ResolvedSources,
    owned: FakeQdrant,
    bib_file: Path,
    fixture_pdf: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_ingest(monkeypatch)
    apply_ingest(
        owned, settings, plan_ingest(owned, settings, bib_path=bib_file), api_key="k"
    )
    for point in owned.points(settings.collection):
        if point.payload.get("bib_key") == "hardy1949":
            point.payload["file_sha256"] = "0" * 64

    plan = plan_ingest(owned, settings, bib_path=bib_file)

    assert [(item.entry.key, item.reason) for item in plan.pending] == [
        ("hardy1949", Reason.CHANGED)
    ]


def test_force_re_ingests_everything(
    settings: ResolvedSources,
    owned: FakeQdrant,
    bib_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_ingest(monkeypatch)
    apply_ingest(
        owned, settings, plan_ingest(owned, settings, bib_path=bib_file), api_key="k"
    )

    plan = plan_ingest(owned, settings, bib_path=bib_file, force=True)

    assert {item.reason for item in plan.pending} == {Reason.FORCED}
    assert len(plan.pending) == 2


def test_a_paper_ocred_by_another_backend_is_re_ingested(
    settings: ResolvedSources,
    owned: FakeQdrant,
    bib_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_ingest(monkeypatch)
    apply_ingest(
        owned, settings, plan_ingest(owned, settings, bib_path=bib_file), api_key="k"
    )
    for point in owned.points(settings.collection):
        if point.payload.get("bib_key") == "hardy1949":
            point.payload["ocr_backend"] = "paddle"

    plan = plan_ingest(owned, settings, bib_path=bib_file)

    assert [(item.entry.key, item.reason) for item in plan.pending] == [
        ("hardy1949", Reason.OCR_BACKEND)
    ]


def test_the_default_backend_reads_no_scan_again(
    settings: ResolvedSources,
    owned: FakeQdrant,
    bib_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A paper that declares no backend asks for no re-read: a
    corpus read by `paddle`, under the `tesseract` default. A
    re-read would change the pages' hashes and lapse what was judged on
    them."""
    stub_ingest(monkeypatch)
    apply_ingest(
        owned, settings, plan_ingest(owned, settings, bib_path=bib_file), api_key="k"
    )
    for point in owned.points(settings.collection):
        if point.payload.get("bib_key") == "hardy1949":
            point.payload["ocr_backend"] = "paddle"
    undeclared = replace(
        settings,
        origins={**settings.origins, "ocr_backend": SettingFrom.DEFAULT},
    )

    assert undeclared.ocr_backend is OcrBackend.TESSERACT
    assert plan_ingest(owned, undeclared, bib_path=bib_file).pending == ()


def test_a_page_the_backend_already_failed_on_is_not_retried_every_run() -> None:
    failed = IndexedPaper("hardy1949", text_sources=frozenset({"none", "failed"}))

    assert stale_backends(failed, OcrBackend.TESSERACT.value) == frozenset()


def test_paddle_and_http_are_two_backends_to_the_planner() -> None:
    """One transport, two names on the wire: moving a corpus between
    them re-ingests every page that was read by OCR."""
    stored = IndexedPaper("hardy1949", text_sources=frozenset({"none", "paddle"}))

    assert stale_backends(stored, OcrBackend.HTTP.value) == {"paddle"}
    assert stale_backends(stored, OcrBackend.PADDLE.value) == frozenset()


def test_a_moved_title_is_a_metadata_only_update(
    settings: ResolvedSources,
    owned: FakeQdrant,
    bib_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_ingest(monkeypatch)
    apply_ingest(
        owned, settings, plan_ingest(owned, settings, bib_path=bib_file), api_key="k"
    )
    for point in owned.points(settings.collection):
        if point.payload.get("bib_key") == "hardy1949":
            point.payload["bib_sha256"] = "0" * 64

    plan = plan_ingest(owned, settings, bib_path=bib_file)

    assert plan.pending == ()
    assert [item.key for item in plan.metadata_only] == ["hardy1949"]


def test_a_metadata_update_rewrites_the_payload_without_touching_the_vectors(
    settings: ResolvedSources, owned: FakeQdrant
) -> None:
    upsert_chunks(
        owned,
        settings.collection,
        entry(),
        [Chunk("hardy1949", "Old", 1, 0, "text", TextSource.NONE)],
        [[1.0] * settings.voyage_dims],
        [{"indices": [1], "values": [1.0]}],
        "f" * 64,
    )
    before = owned.points(settings.collection)[-1].vector

    update_payload(owned, settings.collection, entry(title="New Title"))

    point = owned.collections[settings.collection][point_id("hardy1949", 0)]
    assert point.payload["title"] == "New Title"
    assert point.vector == before


def test_a_legacy_point_without_hashes_counts_as_unchanged(
    settings: ResolvedSources, owned: FakeQdrant, bib_file: Path
) -> None:
    """Points predating the hashes have unknown provenance; --force gets
    them. Predating the page class too, their records are settled from
    their PDF, their text untouched."""
    for key in ("hardy1949", "erdos1950"):
        owned.add_chunk(
            settings.collection, bib_key=key, file_sha256=None, bib_sha256=None
        )

    plan = plan_ingest(owned, settings, bib_path=bib_file)

    assert not plan.pending and not plan.metadata_only
    assert [found.bib_key for found in plan.readers] == ["hardy1949", "erdos1950"]


def test_one_key_narrows_the_plan(
    settings: ResolvedSources, owned: FakeQdrant, bib_file: Path
) -> None:
    plan = plan_ingest(owned, settings, bib_path=bib_file, key="erdos1950")

    assert [item.entry.key for item in plan.pending] == ["erdos1950"]


def test_an_unknown_key_says_so(
    settings: ResolvedSources, owned: FakeQdrant, bib_file: Path
) -> None:
    with pytest.raises(SourcesError, match="nosuch"):
        plan_ingest(owned, settings, bib_path=bib_file, key="nosuch")


# --- Prune ---


def test_prune_finds_keys_the_bibliography_no_longer_has(
    settings: ResolvedSources, owned: FakeQdrant, bib_file: Path
) -> None:
    owned.add_chunk(settings.collection, bib_key="renamed1970")

    plan = plan_ingest(owned, settings, bib_path=bib_file, prune=True)

    assert plan.stale == ("renamed1970",)
    assert plan.prune_refusal == ""


def test_a_key_without_a_pdf_is_not_stale(
    settings: ResolvedSources, owned: FakeQdrant, bib_file: Path
) -> None:
    owned.add_chunk(settings.collection, bib_key="noattachment1900")

    assert plan_ingest(owned, settings, bib_path=bib_file, prune=True).stale == ()


def test_prune_refuses_to_delete_most_of_the_index(
    settings: ResolvedSources, owned: FakeQdrant, bib_file: Path
) -> None:
    for index in range(PRUNE_FLOOR + 2):
        owned.add_chunk(settings.collection, bib_key=f"gone{index}")

    plan = plan_ingest(owned, settings, bib_path=bib_file, prune=True)

    assert plan.prune_refusal
    assert "--force" in plan.prune_refusal
    with pytest.raises(SourcesError, match="Refusing to prune"):
        apply_ingest(owned, settings, plan, api_key="k")


def test_force_gets_past_the_prune_guard(
    settings: ResolvedSources,
    owned: FakeQdrant,
    bib_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_ingest(monkeypatch)
    for index in range(PRUNE_FLOOR + 2):
        owned.add_chunk(settings.collection, bib_key=f"gone{index}")

    plan = plan_ingest(owned, settings, bib_path=bib_file, prune=True, force=True)
    report = apply_ingest(owned, settings, plan, api_key="k")

    assert len(report.pruned) == PRUNE_FLOOR + 2
    assert not {
        point.payload.get("bib_key") for point in owned.points(settings.collection)
    } & {f"gone{index}" for index in range(PRUNE_FLOOR + 2)}


def test_deleting_a_paper_leaves_the_marker_alone(
    settings: ResolvedSources, owned: FakeQdrant
) -> None:
    owned.add_chunk(settings.collection, bib_key="hardy1949")

    delete_paper(owned, settings.collection, "hardy1949")

    assert read_marker(owned, settings.collection) is not None


# --- Applying ---


def test_apply_refuses_a_collection_axiomation_did_not_create(
    settings: ResolvedSources, fake_qdrant: FakeQdrant, bib_file: Path
) -> None:
    plan = plan_ingest(fake_qdrant, settings, bib_path=bib_file)

    assert plan.refusal
    with pytest.raises(SourcesError, match="did not create it"):
        apply_ingest(fake_qdrant, settings, plan, api_key="k")
    assert fake_qdrant.points(settings.collection) == []


def test_a_completed_ingest_writes_the_whole_payload_contract(
    settings: ResolvedSources,
    owned: FakeQdrant,
    bib_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from axiomation.sources.contract import missing_payload_fields

    stub_ingest(monkeypatch)
    report = apply_ingest(
        owned, settings, plan_ingest(owned, settings, bib_path=bib_file), api_key="k"
    )

    assert report.ingested == 2
    assert report.chunks == 6
    chunks = [
        point.payload
        for point in owned.points(settings.collection)
        if MARKER_FIELD not in point.payload
    ]
    assert len(chunks) == 6
    assert all(missing_payload_fields(payload) == () for payload in chunks)


def test_one_bad_pdf_does_not_stop_the_run(
    settings: ResolvedSources,
    owned: FakeQdrant,
    bib_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_ingest(monkeypatch)
    good = run_module._ingest_entry

    def sometimes(client, config, item, *, api_key, ocr, tesseract, kept):
        if item.entry.key == "hardy1949":
            raise SourcesError("no text extracted")
        return good(
            client,
            config,
            item,
            api_key=api_key,
            ocr=ocr,
            tesseract=tesseract,
            kept=kept,
        )

    monkeypatch.setattr(run_module, "_ingest_entry", sometimes)
    report = apply_ingest(
        owned, settings, plan_ingest(owned, settings, bib_path=bib_file), api_key="k"
    )

    assert report.failures == (("hardy1949", "no text extracted"),)
    assert report.ingested == 1


def test_indexed_papers_counts_chunks_and_collects_text_sources(
    settings: ResolvedSources, owned: FakeQdrant
) -> None:
    owned.add_chunk(settings.collection, chunk_index=0, ocr_backend="none")
    owned.add_chunk(settings.collection, chunk_index=1, ocr_backend="failed")

    papers = indexed_papers(owned, settings.collection)

    assert papers["hardy1949"].chunks == 2
    assert papers["hardy1949"].text_sources == frozenset({"none", "failed"})
    assert MARKER_FIELD not in papers
