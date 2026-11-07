"""An OCR reading held against readers independent of its model.

A vision-language OCR model writes fluent text, so its faults read like
text: a paragraph made up, a sentence written twice, a formula looped, a
digit added to a label. The witnesses are Tesseract's reading of the same
image and the page's old text layer where it has one. Neither is taken as
true: a witness's text never enters the page, it only says which of the
model's readings stands and whether the page is marked (`docs/ocr.md`).

This module finds what a reading's witnesses do not hold. A reading is
its blocks, each with its box on the image where the service gives one
(`Seen`); a stored reading's blocks are its paragraphs, with none. A block
is held against what its own box shows, Tesseract's reading of the box
cropped alone and Tesseract's words of the page within it, beside the
page's witnesses. Its units:

- a paragraph, the text between blank lines, as the services write their
  blocks;
- a label line (a theorem's or a section's word and number);
- a repeat, a stretch written twice that no witness holds twice;
- a loop, a short sequence written many times in a row;
- text left out, a witness's words the reading does not hold;
- a block its own box shows none of: Tesseract reads no word in the box
  (a margin's mark boxed as a figure, and a model's reply about it, "The
  image is too blurry to recognize any text content.").

A block whose text its box has no room for was not read from the box (a
scanner's black band boxed as a header, and a page of text written for
it), and is dropped; where its box holds some of its words, the model
read print there and wrote on past it, and the drop is a finding, so the
page is read again. A block its box shows no word of is kept, only
found: Tesseract reads real print as nothing too, a cover's display type
and a publisher's seal. A page with a finding is read again at another
scale; the first reading with none settles it, and where none does the
likeliest reading stands. Its blocks with a finding of their own are then
each read alone, its box cropped from the page, and a block's reading
replaces it where its witnesses hold it: a block read in its own box
pairs with itself, where the paragraphs of two readings of the page do
not. What is left marks the page disputed (`settle`).

Leader runs (a contents page's dots) carry no words; they are collapsed
before anything is checked, and in the stored text. A witness writes no
TeX, so its formulas are not cut: a `$` in Tesseract's text is a misread
mark (a subscript, an `S`), and two of them would cut the prose between.
"""

from __future__ import annotations

import difflib
import hashlib
import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from axiomation.sources.ingest import layer

#: A paragraph shorter than this, in words of three letters or more, is
#: not judged: a heading or a line of a formula holds too little to tell.
PROSE_WORDS = 12
#: The share of a paragraph's words that stand in a witness, in order, for
#: it to be held. On 168 pages of 11 cited works (paddle's 78 paragraphs
#: of 16 of them, MinerU's 781) the faults held 0.00 to 0.69 and the
#: correct paragraphs 0.79 or more, but one address block set in two
#: columns, which Tesseract reads across (0.50).
HELD = 0.7
#: A paragraph is a formula, and not judged, when more than this share of
#: its characters stand inside formula delimiters.
FORMULA_SHARE = 0.5
#: A stretch of this many words and numbers written twice is a repeat.
REPEAT_RUN = 10
#: A dropped block whose box holds a word of its own this long read print
#: there: a margin strip's junk shares a `the` at most with a model's loop.
LOST_WORD = 4
#: A sequence of up to this many tokens, written this many times in a row,
#: is a loop: `0 ,` 8 times and more on a scanned paper's page, `CHAPTER`
#: on a scanned book's.
LOOP_TOKENS, LOOP_TIMES = 12, 8
#: The share of a witness's dictionary words (four letters or more, in the
#: word list) a reading must hold, in any order, not to have left text out.
#: On 168 pages of 11 cited works the readings that dropped entries held
#: 0.45, 0.55 and 0.78, every other page of two OCR models 0.91 or more. In
#: order it cannot tell: a two-column page read in another block order
#: holds 0.50 with every word there.
KEPT = 0.85
#: A witness with fewer dictionary words than this does not judge what was
#: left out: a page of formulas, a title page.
KEPT_WORDS = 20
#: A block of this many words or more, short of `PROSE_WORDS`, is held
#: when a witness holds any of it. On 198 pages of MinerU's blocks, each
#: held against its own box, 14 of 1,236 such blocks held under `HELD`, none
#: a fault (an arXiv stamp set sideways, a blackletter title, a line of
#: formulas, one word of three misread); one was held by none, the text
#: made up for a scanner's black band.
SHORT_WORDS = 3
#: A block's room in its box: a character 6 pixels wide and a line 25 high
#: at 300 dpi, more than any print needs.
CHARACTERS_PER_INCH, LINES_PER_INCH = 50, 12
#: A block whose text fills more than this of its box's room was not read
#: from it. On those 198 pages every block of print filled 0.36 or less (a
#: contents page's leaders collapsed, a formula counted at a third of its
#: TeX), the block made up for the black band 60.
OVERFULL = 2.0
#: The version of the witness's reading of a page, recorded on each page a
#: declared service read (`contract.OCR_READING_FIELD`). Raised when the
#: witness judges or repairs a page otherwise, so a page an older one left
#: disputed is read afresh at the next `--reread`; a settled one keeps its
#: text. 1, or none recorded: the page held whole against Tesseract and
#: its layer. 2: each block held against its own box too, a block its box
#: has no room for dropped, a flagged block read alone; a witness's text
#: taken whole. 3: the words a reading sets inside its formulas count as
#: its own, a repeat is held on its words, a label is unheld only where a
#: witness numbers it otherwise, and the last pass reads at 80 %. 4: a
#: table's rows end a loop's run of cells, which 3 read across. 5: a
#: stretch of formulas alone is no repeat, an operator's name a reading
#: sets as a command (`\\cosh`) is its word, and a block its box shows no
#: word of is a finding. 6: a block dropped for its box's room whose box
#: holds some of its words is a finding.
OCR_READING = 6

#: The scales a page with a finding is read again at, in turn, of the
#: ingest's resolution: another resolution is all the model needs to read
#: the page otherwise. Of 9 fault pages one OCR model read at 300 dpi, 99 %
#: read every one clean; 95 % read one with a new fault. Another reads an
#: image near its own alike: it wrote one book page's stretch twice
#: at every scale from 100 to 85 %, once at 80 %, and read 17 other pages
#: alike from 95 to 70 %.
PASS_SCALES = (0.99, 0.8)

#: The formula delimiters the services write: paddle `$…$` and `$$…$$`,
#: MinerU `\(…\)` and `\[…\]`, both environments.
_FORMULA_RE = re.compile(
    r"\$\$.*?\$\$|\\\[.*?\\\]|\\\(.*?\\\)|\$[^$]*\$|\\begin\{(\w+\*?)\}.*?\\end\{\1\}",
    re.DOTALL,
)
#: An HTML tag, as a service writes a table; never `<` and `>` of a formula
#: written in plain text.
_TAG_RE = re.compile(r"</?[A-Za-z][A-Za-z0-9]*(?:\s[^<>\n]*)?/?>")
_COMMAND_RE = re.compile(r"\\[a-zA-Z]+")
_HYPHEN_RE = re.compile(r"-\s*\n\s*")
_PROSE_WORD_RE = re.compile(r"[^\W\d_]{3,}")
_TOKEN_WORD_RE = re.compile(r"[^\W_]+")
#: A loop's tokens: a command or a word, a number (a figure written out,
#: `1000000000000`, is no loop of `0`), one mark, or a table's tag, which is
#: layout: a row of empty cells is no loop of `td`, and a row ends a run of
#: cells (a 3 by 3 matrix of zeros is no loop of nine).
_LOOP_TOKEN_RE = re.compile(r"</?[A-Za-z][A-Za-z0-9]*>|\\?[A-Za-z]+|\d+|\S")
_PARAGRAPH_RE = re.compile(r"\n\s*\n")
#: Four leader marks or more in a run: dots, ellipses, centred dots and
#: the spaces a formula sets between them.
_LEADER_RE = re.compile(
    r"(?:[ \t]*(?:\.|·|…|\\c?dots|\\ldots|\\cdot|\\q?quad|\\,|\\;)[ \t]*){4,}"
)
#: Commands that draw a dot or a space: a run of them is a leader, not a
#: loop (MinerU writes a row of dots as an array of `\bullet &`).
_MARKS = frozenset(
    {
        r"\cdot",
        r"\cdots",
        r"\bullet",
        r"\dots",
        r"\ldots",
        r"\vdots",
        r"\ddots",
        r"\quad",
        r"\qquad",
    }
)
#: An array's column spec (`{c c c c}`) is layout, not a loop.
_COLUMNS_RE = re.compile(r"(\\begin\{(?:array|tabular)\})\{[^{}]*\}")
_LABEL_WORDS = (
    "theorem|lemma|proposition|corollary|definition|remark|example|section|"
    "chapter|equation"
)
#: A label line's word and its number: numerals or a roman numeral, parts
#: after a dot or a dash, and a space after a dot before a numeral, which
#: Tesseract sets ("LEMMA 2. 2."); never the word after a dash
#: ("CHAPTER 1-RIEMANNIAN METRICS").
_LABEL_RE = re.compile(
    rf"\b({_LABEL_WORDS})\.?\s+"
    r"((?:\d+|[IVXLC]+)(?:\.\s?\d+|[.\-](?:\d+|[IVXLC]+))*)\b",
    re.IGNORECASE,
)
#: The token `_formula_tokens` writes for a formula.
_FORMULA_TOKEN_RE = re.compile(r"formula[0-9a-f]{12}")


@dataclass(frozen=True, slots=True)
class Findings:
    """What a reading's witnesses do not hold, each by its first words."""

    unheld: tuple[str, ...] = ()
    labels: tuple[str, ...] = ()
    repeats: tuple[str, ...] = ()
    loop: str = ""
    #: the reading holds under `KEPT` of a witness's dictionary words
    left_out: bool = False
    #: the blocks whose own box Tesseract reads no word in
    unseen: tuple[str, ...] = ()
    #: the blocks dropped as having no room in their boxes whose box holds
    #: some of their words: print the model read, then wrote on past
    lost: tuple[str, ...] = ()
    #: the blocks with a finding of their own (a paragraph or a label no
    #: witness holds, a loop), by their place in the reading
    blocks: tuple[int, ...] = ()

    @property
    def count(self) -> int:
        return (
            len(self.unheld)
            + len(self.labels)
            + len(self.repeats)
            + bool(self.loop)
            + self.left_out
            + len(self.unseen)
            + len(self.lost)
        )

    @property
    def settleable(self) -> int:
        """The findings another reading of the page can settle: all but a
        block its box shows no word of, which a pass at another scale reads
        again (the mark is printed) or leaves out, a cover's title with it."""
        return self.count - len(self.unseen)

    @property
    def broken(self) -> bool:
        """A repeat or a loop: a fault whatever the witnesses hold."""
        return bool(self.repeats or self.loop)


def collapse_leaders(text: str) -> str:
    """A text with each run of leader marks written as one ellipsis."""
    return _LEADER_RE.sub(" … ", text)


@dataclass(frozen=True, slots=True)
class Seen:
    """A block of a reading and what its own box shows."""

    text: str
    #: its box on the image the service read, left, top, right, bottom in
    #: pixels; None where the reading came without one
    box: tuple[int, int, int, int] | None = None
    #: the resolution of that image
    dpi: int = 300
    #: Tesseract's reading of the box cropped alone, and Tesseract's words
    #: of the page that stand in the box
    witnesses: tuple[str, ...] = ()

    @property
    def overfull(self) -> bool:
        """Whether its text fills more than `OVERFULL` of its box's room."""
        if self.box is None:
            return False
        left, top, right, bottom = self.box
        room = max(1.0, (right - left) / self.dpi * CHARACTERS_PER_INCH) * max(
            1.0, (bottom - top) / self.dpi * LINES_PER_INCH
        )
        return _printed(self.text) / room > OVERFULL


Reading = Sequence[Seen]


def paragraphs(text: str) -> tuple[Seen, ...]:
    """A reading that came without boxes: its paragraphs, each a block."""
    return tuple(
        Seen(part.strip()) for part in _PARAGRAPH_RE.split(text) if part.strip()
    )


def joined(reading: Reading) -> str:
    """A reading's text: its blocks, joined by a blank line."""
    return "\n\n".join(block.text for block in reading)


def find(reading: str, witnesses: list[str]) -> Findings:
    """What the witnesses' texts do not hold of a reading that came
    without boxes (`find_blocks`)."""
    return find_blocks(paragraphs(reading), witnesses)


def find_blocks(reading: Reading, witnesses: list[str]) -> Findings:
    """What the page's witnesses, and each block's own, do not hold of a
    reading. With no witness text, only a repeat or a loop is found."""
    theirs = [w for w in witnesses if _prose_words(w)]
    unheld: list[str] = []
    labels: list[str] = []
    unseen: list[str] = []
    flagged: list[int] = []
    written = _labels(joined(reading))
    for place, block in enumerate(reading):
        text = collapse_leaders(block.text)
        own = theirs + [w for w in block.witnesses if _prose_words(w)]
        found = _unheld(text, own) if own else []
        named = _unheld_labels(text, own, written) if own else []
        if found or named or _loop(_COLUMNS_RE.sub(r"\1", text)):
            flagged.append(place)
        unheld += found
        labels += named
        unseen += _unseen(text, block)
    text = collapse_leaders(joined(reading))
    return Findings(
        unheld=tuple(unheld),
        labels=tuple(labels),
        repeats=tuple(_repeats(text, theirs)),
        loop=_loop(_COLUMNS_RE.sub(r"\1", text)),
        left_out=_left_out(text, theirs),
        unseen=tuple(unseen),
        blocks=tuple(flagged),
    )


def _unseen(text: str, block: Seen) -> list[str]:
    """The block, by its first words, where its box was read and shows no
    word: a formula and a line of fewer than `SHORT_WORDS` words (a roman
    page number) are not judged, as Tesseract reads both as nothing. Not
    read alone: its box would show nothing again."""
    if not block.witnesses or any(_prose_words(w) for w in block.witnesses):
        return []
    if _formula_share(text) > FORMULA_SHARE:
        return []
    said = _prose_words(_FORMULA_RE.sub(" ", text))
    return [" ".join(said[:PROSE_WORDS])] if len(said) >= SHORT_WORDS else []


@dataclass(frozen=True, slots=True)
class Witnessed:
    """A page's text after its witnesses: the reading kept, its leaders
    collapsed; how many findings it still carries (0: settled); how many
    readings the OCR gave, the first included; the blocks dropped as having
    no room in their boxes; and the blocks a reading alone settled."""

    text: str
    disputed: int
    readings: int
    dropped: int = 0
    alone: int = 0


@dataclass(frozen=True, slots=True)
class _Judged:
    blocks: tuple[Seen, ...]
    found: Findings
    dropped: int


def settle(
    first: Reading,
    witnesses: list[str],
    read_at: Callable[[float], Reading],
    read_alone: Callable[[Seen], Seen | None] | None = None,
) -> Witnessed:
    """The page's reading once its witnesses have spoken. `read_at` reads
    the page again at a share of the ingest's resolution; `read_alone`
    reads one block's box cropped alone, None where it cannot. A reading
    the OCR gave nothing for is no candidate."""
    judged = [_judge(first, witnesses)]
    for scale in PASS_SCALES:
        if judged[-1].found.settleable == 0:
            break
        reading = read_at(scale)
        if joined(reading).strip():
            judged.append(_judge(reading, witnesses))
    kept = judged[-1]
    if kept.found.settleable:
        kept = _likeliest(judged, witnesses)
    blocks, found, alone = kept.blocks, kept.found, 0
    if found.blocks and read_alone is not None:
        blocks, alone = _read_alone(blocks, found.blocks, read_alone, witnesses)
        if alone:
            found = replace(find_blocks(blocks, witnesses), lost=found.lost)
    return Witnessed(
        collapse_leaders(joined(blocks)), found.count, len(judged), kept.dropped, alone
    )


def _judge(reading: Reading, witnesses: list[str]) -> _Judged:
    """A reading's blocks, those its boxes have no room for dropped, and
    what its witnesses find in the rest, a dropped block whose box holds a
    word of its own (`LOST_WORD` letters or more) among them. A block is
    dropped only beside one that stands: a reading all of whose blocks
    overflow is judged as it came."""
    kept = tuple(block for block in reading if not block.overfull)
    if not kept:
        kept = tuple(reading)
    lost = tuple(
        " ".join(said[:PROSE_WORDS])
        for block in reading
        if block not in kept
        and (said := _prose_words(_FORMULA_RE.sub(" ", block.text)))
        and any(
            len(word) >= LOST_WORD
            for w in block.witnesses
            for word in set(said) & set(_prose_words(w))
        )
    )
    found = replace(find_blocks(kept, witnesses), lost=lost)
    return _Judged(kept, found, len(reading) - len(kept))


def _read_alone(
    blocks: tuple[Seen, ...],
    flagged: tuple[int, ...],
    read_alone: Callable[[Seen], Seen | None],
    witnesses: list[str],
) -> tuple[tuple[Seen, ...], int]:
    """The blocks with each flagged one that has a box read alone, its
    reading kept where its witnesses hold it; and how many were kept."""
    settled = 0
    for place in flagged:
        block = blocks[place]
        if block.box is None:
            continue
        again = read_alone(block)
        if again is None or not again.text.strip() or again.overfull:
            continue
        trial = (*blocks[:place], again, *blocks[place + 1 :])
        if place not in find_blocks(trial, witnesses).blocks:
            blocks, settled = trial, settled + 1
    return blocks, settled


def _likeliest(judged: list[_Judged], witnesses: list[str]) -> _Judged:
    """No repeat or loop, then the most its witnesses hold (made-up text is
    fluent but stands in no witness), then the one most passes share."""
    shared = Counter(joined(one.blocks) for one in judged)
    whole = [one for one in judged if not one.found.broken] or judged
    return max(
        whole,
        key=lambda one: (
            held_share(joined(one.blocks), witnesses),
            shared[joined(one.blocks)],
        ),
    )


def held_share(reading: str, witnesses: list[str]) -> float:
    """The share of a reading's prose words its best witness holds in
    order: how a reading no pass settled is ranked."""
    said = _prose_words(_cut_formulas(collapse_leaders(reading)))
    if not said:
        return 0.0
    return max((_held(said, _prose_words(w)) for w in witnesses), default=0.0)


def _printed(text: str) -> float:
    """How many characters a block's text sets in print: its prose with
    leaders collapsed, and each formula at a third of its TeX."""
    text = collapse_leaders(text)
    formulas = sum(len(m.group(0)) for m in _FORMULA_RE.finditer(text))
    return len(" ".join(_cut_formulas(text).split())) + formulas / 3


def _cut_formulas(text: str) -> str:
    """The text with its formulas cut, paragraph by paragraph: a `$` the
    service left unpaired would otherwise swap formula and prose for the
    rest of the page."""
    return "\n\n".join(
        _FORMULA_RE.sub(" ", paragraph) for paragraph in _PARAGRAPH_RE.split(text)
    )


def _prose_words(text: str) -> list[str]:
    text = _COMMAND_RE.sub(" ", _TAG_RE.sub(" ", text))
    return [w.casefold() for w in _PROSE_WORD_RE.findall(_HYPHEN_RE.sub("", text))]


def _tokens(text: str) -> list[str]:
    """Every word and number, and each formula as one token standing for
    its content: a reference written twice is initials, a journal and its
    numbers more than prose words; and two sentences that differ only in
    their formulas ("This ψ, however, is the eigenfunction of P", then of
    Q) are not one stretch written twice, where a passage written twice
    writes its formulas alike. A witness holds no such token, and a
    stretch is held with room for it (`_occurrences`)."""
    return _witness_tokens(_formula_tokens(text))


def _witness_tokens(text: str) -> list[str]:
    """Every word and number of a witness's text, which holds no formula."""
    text = _COMMAND_RE.sub(" ", _TAG_RE.sub(" ", text))
    return [w.casefold() for w in _TOKEN_WORD_RE.findall(_HYPHEN_RE.sub("", text))]


def _formula_tokens(text: str) -> str:
    """The text with each formula one token, alike for formulas written
    alike, paragraph by paragraph as `_cut_formulas` cuts them."""

    def token(match: re.Match[str]) -> str:
        written = " ".join(match.group(0).split())
        return f" formula{hashlib.sha256(written.encode()).hexdigest()[:12]} "

    return "\n\n".join(
        _FORMULA_RE.sub(token, paragraph) for paragraph in _PARAGRAPH_RE.split(text)
    )


def _held(said: list[str], witness: list[str]) -> float:
    matcher = difflib.SequenceMatcher(a=said, b=witness, autojunk=False)
    return sum(m.size for m in matcher.get_matching_blocks()) / len(said)


def _formula_share(paragraph: str) -> float:
    inside = sum(len(m.group(0)) for m in _FORMULA_RE.finditer(paragraph))
    return inside / max(1, len(paragraph))


def _unheld(text: str, witnesses: list[str]) -> list[str]:
    theirs = [_prose_words(w) for w in witnesses]
    found = []
    for paragraph in _PARAGRAPH_RE.split(text):
        if _formula_share(paragraph) > FORMULA_SHARE:
            continue
        said = _prose_words(_FORMULA_RE.sub(" ", paragraph))
        if len(said) < SHORT_WORDS:
            continue
        best = max(_held(said, w) for w in theirs)
        if best < HELD if len(said) >= PROSE_WORDS else best == 0:
            found.append(" ".join(said[:PROSE_WORDS]))
    return found


def _left_out(text: str, witnesses: list[str]) -> bool:
    """Whether the reading holds under `KEPT` of a witness's dictionary
    words. Its formulas are not cut: a model sets prose inside them
    (`\\text{whenever}`, `\\operatorname{grad}`), and an operator's name as
    a command (`\\cosh`), which Tesseract reads as words."""
    known = layer.word_list()
    if not known:
        return False
    said = Counter(_prose_words(text.replace("\\", " ")))
    shares = []
    for witness in witnesses:
        theirs = Counter(
            word for word in _prose_words(witness) if len(word) >= 4 and word in known
        )
        total = sum(theirs.values())
        if total >= KEPT_WORDS:
            shares.append(sum((theirs & said).values()) / total)
    return bool(shares) and max(shares) < KEPT


def _label(match: re.Match[str]) -> tuple[str, str]:
    return match.group(1).casefold(), "".join(match.group(2).split()).casefold()


def _labels(text: str) -> set[tuple[str, str]]:
    """Every label a text writes, by its word and number."""
    return {_label(m) for m in _LABEL_RE.finditer(text)}


def _unheld_labels(
    text: str, witnesses: list[str], written: set[tuple[str, str]]
) -> list[str]:
    """Label lines a witness numbers otherwise: no witness writes the line's
    word and number, and one writes that word with a number the page's
    reading (`written`) writes nowhere. One changed digit never falls under
    the paragraph's floor ("LEMMA 2.2.2" for "LEMMA 2.2."); a witness that
    misreads a label's word or number ("Coro.iary 6", "Chapter |") writes
    no label against it."""
    theirs = set().union(*(_labels(w) for w in witnesses))
    otherwise = {word for word, _ in theirs - written}
    found = []
    for line in text.splitlines():
        match = _LABEL_RE.match(line.strip().lstrip("#*_> "))
        if match is None:
            continue
        label = _label(match)
        if label not in theirs and label[0] in otherwise:
            found.append(" ".join(match.group(0).split()))
    return found


def _occurrences(stretch: list[str], witness: list[str]) -> int:
    """How often a witness holds the stretch, `HELD` of its words and
    numbers in order, in windows of its length that do not overlap. A
    witness holds no formula token: it writes in the formula's place what it
    garbles it to, so a formula takes its room in the window and counts for
    nothing in the share."""
    said = _unformulated(stretch)
    found, start, size = 0, 0, len(stretch)
    slack = size // 5
    while start + size <= len(witness) + slack:
        if _held(said, witness[start : start + size + slack]) >= HELD:
            found += 1
            start += size
        else:
            start += 1
    return found


def _unformulated(stretch: list[str]) -> list[str]:
    """A stretch's words and numbers, its formula tokens left out."""
    return [token for token in stretch if not _FORMULA_TOKEN_RE.fullmatch(token)]


def _repeats(text: str, witnesses: list[str]) -> list[str]:
    """Each stretch written twice that no witness holds twice. A page may
    repeat itself (exercises opening alike), and a witness misreads a word
    here and there, so a stretch is held as a paragraph is. A stretch of
    fewer than `SHORT_WORDS` words outside its formulas is not judged: a
    page's tables of letters and digits print one row twice, and a witness
    reads them across their columns; a row of formulas holds nothing a
    witness writes."""
    said = _tokens(text)
    runs = [tuple(said[i : i + REPEAT_RUN]) for i in range(len(said) - REPEAT_RUN + 1)]
    counts = Counter(runs)
    stretches: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    last = -2
    for index, run in enumerate(runs):
        if counts[run] < 2 or run in seen:
            continue
        seen.add(run)
        if index == last + 1:
            stretches[-1].append(run[-1])
        else:
            stretches.append(list(run))
        last = index
    theirs = [_witness_tokens(w) for w in witnesses]
    return [
        " ".join(stretch[:REPEAT_RUN])
        for stretch in stretches
        if len(_PROSE_WORD_RE.findall(" ".join(_unformulated(stretch)))) >= SHORT_WORDS
        and not any(_occurrences(stretch, w) >= 2 for w in theirs)
    ]


def _loop(text: str) -> str:
    tokens = _LOOP_TOKEN_RE.findall(text)
    for size in range(1, LOOP_TOKENS + 1):
        for start in range(len(tokens) - size * LOOP_TIMES + 1):
            unit = tokens[start : start + size]
            if not any(
                t[0].isalnum() or (t[0] == "\\" and t not in _MARKS) for t in unit
            ):
                continue
            if all(
                tokens[start + k * size : start + (k + 1) * size] == unit
                for k in range(1, LOOP_TIMES)
            ):
                return " ".join(unit)
    return ""
