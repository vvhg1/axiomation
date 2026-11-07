"""The reader: a paper's files to its document layer.

It knows nothing about blocks, roles or edges, and imports nothing outside
the standard library and this package (`tests/test_spine.py`).
"""

from __future__ import annotations

from pathlib import Path

from axiomation.reader.document import (
    Candidate,
    CandidateShape,
    Citation,
    Comment,
    CommentKind,
    Declaration,
    Directive,
    Document,
    EnvEntry,
    Environment,
    EnvKind,
    FileRole,
    Footnote,
    Format,
    Hidden,
    HiddenKind,
    Include,
    Label,
    Macro,
    MapAnswer,
    Quotation,
    RefEntry,
    Reference,
    Section,
    SourceFile,
    Span,
    Vocabulary,
    VocabularyKind,
    document_hash,
    files_hash,
)
from axiomation.reader.includes import ReaderError, find_root, walk
from axiomation.reader.layer import ReadingOrder, read_layer


def read_paper(
    directory: Path, root: Path | None = None, vocabulary: Vocabulary | None = None
) -> Document:
    """The document layer of the paper in `directory`.

    `root` overrides how the root file is found (`includes.find_root`);
    `vocabulary` is the paper's vocabulary map, read on top of its
    declarations.
    """
    directory = Path(directory)
    return read_layer(walk(directory, find_root(directory, root)), vocabulary)


__all__ = [
    "Candidate",
    "CandidateShape",
    "Citation",
    "Comment",
    "CommentKind",
    "Declaration",
    "Directive",
    "Document",
    "EnvEntry",
    "EnvKind",
    "Environment",
    "FileRole",
    "Footnote",
    "Format",
    "Hidden",
    "HiddenKind",
    "Include",
    "Label",
    "Macro",
    "MapAnswer",
    "Quotation",
    "ReaderError",
    "ReadingOrder",
    "RefEntry",
    "Reference",
    "Section",
    "SourceFile",
    "Span",
    "Vocabulary",
    "VocabularyKind",
    "document_hash",
    "files_hash",
    "find_root",
    "read_paper",
]
