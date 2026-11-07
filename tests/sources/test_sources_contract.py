"""The payload contract and the point ids."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from axiomation.sources.config import OcrBackend
from axiomation.sources.contract import (
    BIB_NAME_LIMIT,
    MARKER_ID,
    OCR_SOURCES,
    PAGE_INDEX_FIELD,
    PROVENANCE_FIELDS,
    READ_FIELDS,
    TextSource,
    bib_slug,
    collection_name,
    missing_payload_fields,
    missing_provenance_fields,
    point_id,
    vector_space_hash,
)

LIVE_PAYLOAD = {
    "bib_key": "lee2000",
    "title": "Methods of Brownie Geometry",
    "author": "Squeakinger, Ottoline and Fudge, Ramona",
    "doi": "",
    "page_number": 198,
    "chunk_index": 194,
    "file_path": "/home/x/Zotero/storage/ABCD1234/Squeakinger and Fudge - 2000.pdf",
    "file_sha256": "abf40c54",
    "bib_sha256": "09213346",
    "ocr_backend": "paddle",
    "text": "...",
}


def test_point_id_is_deterministic() -> None:
    assert point_id("green1902", 0) == point_id("green1902", 0)


def test_point_id_is_unique_across_chunks() -> None:
    assert len({point_id("green1902", index) for index in range(500)}) == 500


def test_point_id_is_unique_across_papers() -> None:
    assert point_id("green1902", 0) != point_id("green2010", 0)


def test_point_id_fits_a_u64() -> None:
    for key, index in [("green1902", 0), ("a", 99999), ("ZZZ", 0)]:
        assert 0 <= point_id(key, index) < 2**64


def test_the_marker_id_cannot_collide_with_a_chunk() -> None:
    """No bib key spells the seed, so no chunk can take the marker's id."""
    assert MARKER_ID not in {
        point_id(key, index)
        for key in ("hardy1949", "lee2000", "", "__axiomation__")
        for index in range(50)
    }


def test_a_live_payload_satisfies_the_contract() -> None:
    """A payload as a collection another tool built stores it."""
    assert missing_payload_fields(LIVE_PAYLOAD) == ()


def test_a_missing_field_is_named_in_contract_order() -> None:
    payload = {key: value for key, value in LIVE_PAYLOAD.items() if key != "file_path"}

    assert missing_payload_fields(payload) == ("file_path",)


def test_every_read_field_is_reported_when_nothing_is_there() -> None:
    assert missing_payload_fields({}) == READ_FIELDS


def test_a_legacy_point_reads_fine_and_is_reported_as_legacy() -> None:
    """A collection may hold points written before the provenance hashes."""
    legacy = {
        key: value
        for key, value in LIVE_PAYLOAD.items()
        if key not in PROVENANCE_FIELDS
    }

    assert missing_payload_fields(legacy) == ()
    assert missing_provenance_fields(legacy) == PROVENANCE_FIELDS


def test_a_modern_point_has_nothing_legacy_about_it() -> None:
    assert missing_provenance_fields(LIVE_PAYLOAD) == ()


def test_the_two_halves_of_the_contract_do_not_overlap() -> None:
    assert not set(READ_FIELDS) & set(PROVENANCE_FIELDS)


def test_the_collection_name_is_axiomations_own() -> None:
    """Not a bare `papers_<model>_<dims>`: that rule would put the
    default on top of a collection another tool built."""
    assert collection_name("voyage-4", 2048) == "axiomation_voyage-4_2048"
    assert not collection_name("voyage-4", 2048).startswith("papers_")


def test_the_collection_name_carries_the_bibliography() -> None:
    """The corpus is keyed by bib key, so two bibliographies are two corpora."""
    assert (
        collection_name("voyage-4", 2048, "/somewhere/references.bib")
        == "axiomation_references_voyage-4_2048"
    )


def test_papers_sharing_a_bibliography_share_a_collection() -> None:
    """The monorepo case, and the reason the key is the bib and not the paper."""
    first = collection_name("voyage-4", 2048, "/repo/references.bib")
    second = collection_name("voyage-4", 2048, "/repo/papers/b/../../references.bib")
    assert first == second


def test_a_different_bibliography_is_a_different_collection() -> None:
    assert collection_name("voyage-4", 2048, "a.bib") != collection_name(
        "voyage-4", 2048, "b.bib"
    )


def test_the_vector_space_stays_in_the_name() -> None:
    """A model or dimension change invalidates every vector, and `guard`
    refuses such a collection; keeping it in the name avoids the refusal."""
    assert collection_name("voyage-4", 2048, "r.bib") != collection_name(
        "voyage-4", 1024, "r.bib"
    )
    assert collection_name("voyage-4", 2048, "r.bib") != collection_name(
        "voyage-3-lite", 2048, "r.bib"
    )


@pytest.mark.parametrize(
    ("filename", "slug"),
    [
        ("references.bib", "references"),
        ("My Refs!.bib", "my-refs"),
        ("../a/b/Zotero Export.bib", "zotero-export"),
        ("__weird__.bib", "weird"),
    ],
)
def test_a_bibliography_name_becomes_something_qdrant_accepts(
    filename: str, slug: str
) -> None:
    assert bib_slug(filename) == slug


def test_a_very_long_bibliography_name_is_cut() -> None:
    assert len(bib_slug("x" * 200 + ".bib")) == BIB_NAME_LIMIT


def test_no_bibliography_leaves_the_name_well_formed() -> None:
    """No empty segment, so the name never reads `axiomation__voyage-4`."""
    assert "__" not in collection_name("voyage-4", 2048, None)


def test_the_vector_space_hash_separates_models_and_dimensions() -> None:
    assert vector_space_hash("voyage-4", 2048) != vector_space_hash("voyage-4", 1024)
    assert vector_space_hash("voyage-4", 2048) != vector_space_hash("voyage-3", 2048)
    assert vector_space_hash("voyage-4", 2048) == vector_space_hash("voyage-4", 2048)


def test_the_text_source_values_are_the_wire_values() -> None:
    assert TextSource.NONE == "none"
    assert TextSource("paddle") is TextSource.PADDLE
    assert TextSource("failed") is TextSource.FAILED


def test_every_ocr_backend_has_a_wire_value() -> None:
    """The backend is written on every chunk it reads, so a backend nobody
    can record is a backend nobody may ship."""
    assert {TextSource(backend.value) for backend in OcrBackend} <= set(TextSource)


def test_every_text_source_is_a_text_layer_a_failure_or_an_ocr_pass() -> None:
    """`text_quality` counts by this split, and an unlisted member would be
    counted as a point of unknown origin."""
    assert OCR_SOURCES | {TextSource.NONE, TextSource.FAILED} == set(TextSource)


# --- The wire name stays put ---


def test_the_page_field_is_the_wire_name_and_the_contract_uses_it() -> None:
    assert PAGE_INDEX_FIELD == "page_number"
    assert PAGE_INDEX_FIELD in READ_FIELDS


def _code_names(path: Path, name: str) -> bool:
    """Whether this module uses `name` in its code, prose aside.

    Comments are invisible to the parser, which is the point: a comment that
    explains the wire name is documentation. A docstring is skipped for the
    same reason. What is left is code, which is what the rule is about.
    """
    tree = ast.parse(path.read_text())
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        )
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    for node in ast.walk(tree):
        literal = isinstance(node, ast.Constant) and isinstance(node.value, str)
        if literal and name in node.value and id(node) not in docstrings:
            return True
        if isinstance(node, ast.Name) and node.id == name:
            return True
        if isinstance(node, ast.Attribute) and node.attr == name:
            return True
        if isinstance(node, ast.arg) and node.arg == name:
            return True
        if isinstance(node, ast.keyword) and node.arg == name:
            return True
    return False


def test_only_the_contract_calls_a_page_position_a_page_number() -> None:
    """The stored field means an index, and one module is allowed to say so.

    Anywhere else the name reads as the number a source prints on the page,
    which the corpus does not know: in a book the two differ by its front
    matter. A reader
    of this code should never have to work out which of the two a name
    means, so the wire name lives in `contract.py` and every other module
    imports it.
    """
    source = Path(__file__).resolve().parent.parent / "src" / "axiomation"
    allowed = {source / "sources" / "contract.py"}
    offenders = {
        path.relative_to(source).as_posix()
        for path in source.rglob("*.py")
        if path not in allowed and _code_names(path, "page_number")
    }

    assert offenders == set()
