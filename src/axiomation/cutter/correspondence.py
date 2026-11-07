"""Two cuts of one paper lined up.

It tests the invariant that two spellings of a paper cut the same, and
measures block churn across versions. Deterministic, with difflib.

A block's key is its form, the words of its owned text outside mathematics
with commands and Markdown emphasis stripped, and the labels it holds:
what two spellings of a paper share when a converter rewrote the maths and
the environment names.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import StrEnum

from axiomation.cutter.blocks import BlockIndex
from axiomation.cutter.groups import label_holders
from axiomation.cutter.model import Block, Cut
from axiomation.reader import Document

#: At least this word ratio makes two unmatched blocks one changed block.
CHANGED_RATIO = 0.8

_MATH_ENVS = (
    "equation|align|alignat|gather|multline|flalign|eqnarray|displaymath"
    "|math|split|cases|array|matrix|pmatrix|bmatrix"
)
_MATH_RE = re.compile(
    r"\$\$.*?\$\$|\$(?:\\.|[^$\\])+\$|\\\[.*?\\\]|\\\(.*?\\\)"
    r"|\\begin\{(" + _MATH_ENVS + r")\*?\}.*?\\end\{\1\*?\}",
    re.DOTALL,
)
_ENVIRONMENT_RE = re.compile(r"\\(?:begin|end)\{[^}]*\}(?:\[[^\]]*\])?")
_COMMAND_RE = re.compile(r"\\(?:[a-zA-Z@]+\*?|.)")
#: Commands whose first argument is layout, not text: a converted spelling
#: of a paper may drop the colour, spacing and indentation commands, so a
#: `{\color{black} …}` must not leave "black" behind.
_LAYOUT_RE = re.compile(
    r"\\(?:color|textcolor|colorbox|vspace|hspace)\*?\s*(?:\[[^\]]*\])?\{[^{}]*\}"
)
#: TeX's registers, counters and lengths, set, read or compared: their
#: arguments are a register's name, a number or a dimension, never printed
#: text, so `\ifnum\value{figure}>0` or `\setlength\itemsep{0em}` leaves
#: no word behind.
_REGISTER = r"(?:\\value\{[^{}]*\}|\\[a-zA-Z@]+|[-+]?\d*\.?\d+\s*[a-z]{0,2})"
_REGISTER_RE = re.compile(
    r"\\(?:value|arabic|roman|Roman|alph|Alph|fnsymbol|stepcounter|refstepcounter"
    r"|newcounter)\s*\{[^{}]*\}"
    r"|\\(?:setcounter|addtocounter)\s*\{[^{}]*\}\s*\{[^{}]*\}"
    r"|\\(?:setlength|addtolength|newlength)\s*(?:\{\\[a-zA-Z@]+\}|\\[a-zA-Z@]+)"
    r"(?:\s*\{[^{}]*\})?"
    rf"|\\if(?:num|dim)\s*{_REGISTER}\s*[<=>]\s*{_REGISTER}"
)
#: A character LaTeX escapes, which Markdown writes bare: `sample\_size`.
_ESCAPED_RE = re.compile(r"\\([_&%#])")
_WORD_RE = re.compile(r"\w+")


class PairKind(StrEnum):
    SAME = "same"
    #: Equal keys, different hashes, and the mathematics differs.
    MATH_DIFFERS = "math-differs"
    #: Equal keys, different hashes, the same mathematics: the markup
    #: differs (`# One` against `\\section{One}`, `*p*` against `\\textit{p}`).
    MARKUP_DIFFERS = "markup-differs"
    CHANGED = "changed"
    SPLIT = "split"
    MERGED = "merged"
    ONLY_A = "only-a"
    ONLY_B = "only-b"


@dataclass(frozen=True, slots=True)
class Pair:
    kind: PairKind
    a: tuple[str, ...]
    b: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Correspondence:
    pairs: tuple[Pair, ...]
    blocks_a: int
    blocks_b: int
    #: Blocks of `a` whose hash some block of `b` has: churn's measure.
    hashes_kept: int

    def summary(self) -> dict[str, int]:
        counts = Counter(pair.kind for pair in self.pairs)
        return {
            "blocks_a": self.blocks_a,
            "blocks_b": self.blocks_b,
            **{kind.value: counts.get(kind, 0) for kind in PairKind},
            "hashes_kept": self.hashes_kept,
        }


def words(text: str) -> tuple[str, ...]:
    """The words of some source text outside mathematics, with its
    environment markers, commands and Markdown emphasis dropped."""
    text = _MATH_RE.sub(" ", text)
    text = _ESCAPED_RE.sub(r"\1", text)
    text = _LAYOUT_RE.sub(" ", text)
    text = _REGISTER_RE.sub(" ", text)
    text = _ENVIRONMENT_RE.sub(" ", text)
    text = _COMMAND_RE.sub(" ", text)
    return tuple(word.strip("_") for word in _WORD_RE.findall(text) if word.strip("_"))


def mathematics(text: str) -> tuple[str, ...]:
    """The runs of mathematics in some source text, whitespace dropped, so a
    reflow is no difference."""
    return tuple("".join(match.group(0).split()) for match in _MATH_RE.finditer(text))


def mathematics_spans(text: str) -> tuple[tuple[int, int], ...]:
    """Where `mathematics`' runs stand in `text`, delimiters included."""
    return tuple(match.span() for match in _MATH_RE.finditer(text))


@dataclass(frozen=True, slots=True)
class Keyed:
    """A block with what `correspond` compares it by: its words outside
    mathematics, its key (form, those words, the labels it holds) and its
    runs of mathematics. Identity reuses it."""

    block: Block
    words: tuple[str, ...]
    key: tuple[object, ...]
    #: Its runs of mathematics, whitespace dropped.
    mathematics: tuple[str, ...]


def keyed(document: Document, cut: Cut) -> list[Keyed]:
    """Every block of `cut`, in its order, keyed."""
    index = BlockIndex(cut.blocks)
    held: dict[str, list[str]] = {}
    for label, holder in label_holders(document, index).items():
        if holder is not None:
            held.setdefault(holder, []).append(label)
    items = []
    for block in cut.blocks:
        text = " ".join(document.text(span) for span in block.owned)
        found = words(text)
        labels = tuple(sorted(held.get(block.id, ())))
        items.append(
            Keyed(block, found, (block.form, found, labels), mathematics(text))
        )
    return items


def correspond(a: tuple[Document, Cut], b: tuple[Document, Cut]) -> Correspondence:
    left, right = keyed(*a), keyed(*b)
    matcher = SequenceMatcher(
        None, [item.key for item in left], [item.key for item in right], autojunk=False
    )
    pairs: list[Pair] = []
    for tag, a_start, a_end, b_start, b_end in matcher.get_opcodes():
        if tag == "equal":
            for one, other in zip(
                left[a_start:a_end], right[b_start:b_end], strict=True
            ):
                if one.block.hash == other.block.hash:
                    kind = PairKind.SAME
                elif one.mathematics != other.mathematics:
                    kind = PairKind.MATH_DIFFERS
                else:
                    kind = PairKind.MARKUP_DIFFERS
                pairs.append(Pair(kind, (one.block.id,), (other.block.id,)))
        else:
            pairs.extend(_gap(left[a_start:a_end], right[b_start:b_end]))
    kept_hashes = {item.block.hash for item in right}
    return Correspondence(
        pairs=tuple(pairs),
        blocks_a=len(left),
        blocks_b=len(right),
        hashes_kept=sum(1 for item in left if item.block.hash in kept_hashes),
    )


def _gap(left: Sequence[Keyed], right: Sequence[Keyed]) -> list[Pair]:
    """The pairs of one unmatched stretch: splits and merges by their words,
    then changed blocks by their word ratio, then what is only on one side.
    Pairs keep the order of `left`, a block only in `right` standing where
    it stands among the matched ones."""
    pairs: list[tuple[int, int, Pair]] = []
    used_left: set[int] = set()
    used_right: set[int] = set()

    def run_matching(
        one: Sequence[Keyed], many: Sequence[Keyed], used_many: set[int]
    ) -> list[tuple[int, int, int]]:
        found = []
        for position, item in enumerate(one):
            if not item.words:
                continue
            for start in range(len(many)):
                if start in used_many:
                    continue
                joined: list[str] = []
                for end in range(start, len(many)):
                    if end in used_many:
                        break
                    joined.extend(many[end].words)
                    if len(joined) >= len(item.words):
                        break
                if (
                    end > start
                    and tuple(joined) == item.words
                    and not used_many.intersection(range(start, end + 1))
                ):
                    found.append((position, start, end + 1))
                    used_many.update(range(start, end + 1))
                    break
        return found

    for position, start, end in run_matching(left, right, used_right):
        used_left.add(position)
        ids = tuple(item.block.id for item in right[start:end])
        pairs.append(
            (position, start, Pair(PairKind.SPLIT, (left[position].block.id,), ids))
        )
    for position, start, end in run_matching(right, left, used_left):
        used_right.add(position)
        ids = tuple(item.block.id for item in left[start:end])
        pairs.append(
            (start, position, Pair(PairKind.MERGED, ids, (right[position].block.id,)))
        )
    last_right = -1
    for position, item in enumerate(left):
        if position in used_left:
            continue
        best: tuple[float, int] | None = None
        for other in range(last_right + 1, len(right)):
            if other in used_right:
                continue
            ratio = SequenceMatcher(
                None, item.words, right[other].words, autojunk=False
            ).ratio()
            if ratio >= CHANGED_RATIO and (best is None or ratio > best[0]):
                best = (ratio, other)
        if best is not None:
            used_left.add(position)
            used_right.add(best[1])
            last_right = best[1]
            pairs.append(
                (
                    position,
                    best[1],
                    Pair(
                        PairKind.CHANGED, (item.block.id,), (right[best[1]].block.id,)
                    ),
                )
            )
    for position, item in enumerate(left):
        if position not in used_left:
            pairs.append((position, -1, Pair(PairKind.ONLY_A, (item.block.id,), ())))
    for position, item in enumerate(right):
        if position not in used_right:
            pairs.append(
                (len(left), position, Pair(PairKind.ONLY_B, (), (item.block.id,)))
            )
    pairs.sort(key=lambda entry: (entry[0], entry[1]))
    return [pair for _, _, pair in pairs]
