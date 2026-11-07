"""Block hashes.

The hash changes when the argument changes and holds still when the
typography does. Normalization is `strip_line` and nothing more:
whitespace collapses, structural LaTeX and heading markers go, and the
spaces inside a run of mathematics go with them. No macro rewriting.

A block hashes its owned text, which already leaves out comments, labels
and directives: an edit to any of them moves no hash.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from axiomation.reader.formats.md_latex import MdLatexFormat

HASH_LENGTH = 16


def normalize(lines: Iterable[str]) -> str:
    """The comparable text of some lines: one line, one space between words."""
    words = (word for line in lines for word in MdLatexFormat.strip_line(line).split())
    return " ".join(words)


def hash_text(text: str) -> str:
    """The short sha256 that identifies a piece of content."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:HASH_LENGTH]


def block_hash(runs: Iterable[str]) -> str:
    """The hash of a block's owned runs, each read as its own lines."""
    return hash_text(normalize(line for run in runs for line in run.split("\n")))
