"""Where a sentence of prose begins and ends, for the quotation sites,
which bind a citation to the quotation its sentence holds.

Inline and display mathematics are one token, a macro's arguments are
opaque (`\\autocite[Thm. 4.2]{knuth1997}` ends nothing), a short list of
abbreviations keeps "e.g." from ending a sentence, and a citation that
trails the full stop belongs to the sentence it cites for.
"""

from __future__ import annotations

import re

_CITE_TRAILER_RE = re.compile(r"\s*\\[a-zA-Z]*cite[a-zA-Z]*(\[[^\]]*\])*\{[^}]*\}")
_TERMINATOR_RE = re.compile(r"[.?!](?=\s|$)")
_WORD_BEFORE_RE = re.compile(r"(\S+)$")

#: Tokens whose full stop never ends a sentence. Short on purpose: a long
#: list is a second parser.
ABBREVIATIONS: frozenset[str] = frozenset(
    {
        "e.g.",
        "i.e.",
        "cf.",
        "viz.",
        "resp.",
        "etc.",
        "vs.",
        "al.",
        "approx.",
        "fig.",
        "eq.",
        "eqn.",
        "sec.",
        "app.",
        "ch.",
        "thm.",
        "lem.",
        "prop.",
        "cor.",
        "def.",
        "no.",
        "pp.",
        "p.",
        "dr.",
        "prof.",
    }
)


def mask(text: str) -> str:
    """`text` with what a full stop can hide inside blanked, every offset
    kept: mathematics, and the arguments of a LaTeX macro."""
    out = list(text)
    i = 0
    length = len(text)
    while i < length:
        char = text[i]
        if char == "$":
            fence = 2 if text.startswith("$$", i) else 1
            j = text.find("$" * fence, i + fence)
            stop = length if j < 0 else j
            for k in range(i + fence, stop):
                out[k] = " "
            i = stop + fence
            continue
        if char == "\\" and i + 1 < length and text[i + 1].isalpha():
            j = i + 1
            while j < length and text[j].isalpha():
                j += 1
            i = _blank_groups(text, out, j)
            continue
        i += 1
    return "".join(out)


def _blank_groups(text: str, out: list[str], index: int) -> int:
    """Blank the `[...]` and `{...}` groups of a macro, nesting included."""
    length = len(text)
    while index < length and text[index] in "[{":
        opening = text[index]
        closing = "]" if opening == "[" else "}"
        depth = 0
        j = index
        while j < length:
            if text[j] == opening:
                depth += 1
            elif text[j] == closing:
                depth -= 1
                if depth == 0:
                    break
            j += 1
        stop = length if j >= length else j
        for k in range(index + 1, stop):
            out[k] = " "
        index = stop + 1
    return index


def sentence_bounds(text: str) -> list[tuple[int, int]]:
    """The half-open offset ranges of the sentences in one run of prose."""
    masked = mask(text)
    bounds: list[tuple[int, int]] = []
    start = 0
    for match in _TERMINATOR_RE.finditer(masked):
        end = match.end()
        if _is_abbreviation(text[start:end]):
            continue
        trailer = _CITE_TRAILER_RE.match(masked, end)
        if trailer is not None:
            end = trailer.end()
        if text[start:end].strip():
            bounds.append((start, end))
        start = end
    if text[start:].strip():
        bounds.append((start, len(text)))
    return bounds


def _is_abbreviation(chunk: str) -> bool:
    match = _WORD_BEFORE_RE.search(chunk)
    if match is None:
        return False
    word = match.group(1).lstrip("([\"'").lower()
    if word in ABBREVIATIONS:
        return True
    # an initial: "A. Fixture", "J. R. R. Tolkien"
    return len(word) == 2 and word[0].isalpha() and word[1] == "."


def sentence_at(text: str, offset: int) -> tuple[int, int] | None:
    """The offset range of the sentence covering `offset`."""
    for start, end in sentence_bounds(text):
        if start <= offset < end:
            return (start, end)
    return None
