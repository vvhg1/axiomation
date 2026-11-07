"""Whether a file is its entry's work: the entry's title, authors and year
against the file's first pages.

A review reads a cited work from the pages of the file held under its key,
and a wrong file there reads silently: a book with the same title by
another author, a sibling paper by the same authors. So the file must
print its entry's title on a title page, and that page, or the page after
it, its entry's authors. Both, because each alone passes a wrong
file: authors named anywhere in the first pages pass a book whose preface
cites the right one ("the book with the same title as this one"), and a
title's common phrases pass a sibling paper ("bounds for sums of
random variables").

A file that prints its entry's DOI, or its entry's pages as a pagination
of its own (its pages 30 to 35 numbered 30 to 35, at least two of them),
is its entry's work without a title page: the edition the entry names,
whatever language its title page is in.

The year is a note beside the check, never a reason to hold: reprints,
arXiv versions and later printings print other years than their entry's
as often as a wrong edition does.

The text alone is read, never the PDF's metadata, which is often empty or
another document's. A scan's text is its OCR, and OCR glues words and
splits them, so the match ignores spaces, and a name matches within one
letter.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field

from axiomation.sources.bib import BibRecord

#: How far into a paper its title page may stand: a publisher's cover
#: sheet comes first. Short, so a sibling's reference list, which prints
#: the work's title and authors, stays out. Every window counts only the
#: pages that print text: a scan's blank leaves are no pages of its own.
PAPER_PAGES = 4
#: A book's title page comes after a half-title, a series page, a library
#: stamp.
BOOK_PAGES = 10
#: The first pages of the PDF a check reads, its windows among them: a
#: library scan's binding and blank leaves can stand before them (one
#: book's title page is its PDF's eleventh, six of the ten before it
#: blank).
FIRST_PAGES = 3 * BOOK_PAGES
#: How far after the first page printing the title a later one may still
#: be a title page (a cover or half-title first, the title page after it).
#: The authors stand on one of those pages, or on the page right after the
#: first: never on a page after a later one, which in a book is often a
#: copyright page printing the title, the preface after it.
AUTHOR_PAGES = 3
#: The share of the title's words that must stand on one page in a row.
#: On one corpus of 482 works, 4 fell below a half; a sibling paper's
#: shared phrase (6 of 14 words) falls below it too.
TITLE_RUN = 0.5
#: The same for a file found on the web, a stranger: a sibling's shared
#: phrase can reach half a title, and an open copy's text layer prints its
#: title whole. On that corpus, 15 of the 477 files that pass the first
#: bar fall below this one, most of them scans.
FOUND_TITLE_RUN = 0.75
#: A name this long matches with one letter misread (OCR's): shorter, a
#: common word passes for it (Hall for Hill, well for Bell).
NAME_LETTERS = 5
#: The share of a page's tokens that must be words for its text to be read.
READABLE = 0.25

#: How many lines at a page's top and at its foot may print its number.
EDGE_LINES = 2
#: How many pages must print their numbers in one run (each page's number
#: its index plus one offset) for a file to print a range: one number alone
#: may be a year, a volume or an equation.
FOLIOS = 2
#: A page's number standing alone: never part of a word, a decimal, a
#: range, or an equation's or a citation's number in brackets.
_FOLIO = re.compile(r"(?<![\w.,/()\[\]-])\d{1,5}(?![\w.,/()\[\]-])")
_RANGE = re.compile(r"^\s*(\d+)\s*(?:-{1,3}|–|—)?\s*(\d+)?\s*$")

#: The entry types whose work is a book: its title page further in.
BOOK_KINDS = frozenset(
    {
        "book",
        "mvbook",
        "inbook",
        "bookinbook",
        "collection",
        "mvcollection",
        "incollection",
        "proceedings",
        "mvproceedings",
        "reference",
        "manual",
        "phdthesis",
        "mastersthesis",
        "thesis",
        "techreport",
        "report",
    }
)

_TEXT_COMMAND = re.compile(r"\\(?:emph|text[a-z]*|math[a-z]*|mbox|url)")
_SYMBOL_ACCENT = re.compile(r"""\\['"^`~=.]""")
_LETTER = re.compile(r"\\(o|O|l|L|ss|ae|AE|oe|OE|i|j|aa|AA)(?![a-zA-Z])")
_LETTER_ACCENT = re.compile(r"\\[uvHtcdbkr](?:\s+|(?=[a-zA-Z]))")
_COMMAND = re.compile(r"\\[a-zA-Z]+")
#: Accents a text layer prints as a character of their own beside the
#: letter (`Gr¨unwald`, `´Angel`, `˙Zyczkowski`).
_SPACING_ACCENTS = re.compile("[¨´˙˘ˇ˜¸˛˝¯˚ˆ`]")
_FOLD = str.maketrans(
    {
        "ø": "o",
        "Ø": "o",
        "ß": "ss",
        "æ": "ae",
        "Æ": "ae",
        "ł": "l",
        "Ł": "l",
        "đ": "d",
        "ı": "i",
        "œ": "oe",
        "Œ": "oe",
        "&": " and ",
    }
)
_YEAR = re.compile(r"(?<!\d)(1[5-9]\d\d|20\d\d)(?!\d)")


@dataclass(slots=True)
class Opening:
    """A work's first pages, by index, and its file's SHA-256 (empty where
    the corpus never recorded it)."""

    file_sha256: str
    pages: dict[int, str] = field(default_factory=dict)


def _delatex(value: str) -> str:
    """A bib field's text: what its LaTeX commands spell, its braces gone."""
    value = _TEXT_COMMAND.sub("", value)
    value = _SYMBOL_ACCENT.sub("", value)
    value = _LETTER.sub(lambda m: m.group(1).lower(), value)
    value = _LETTER_ACCENT.sub("", value)
    value = _COMMAND.sub(" ", value)
    return value.replace("{", "").replace("}", "").replace("$", "").replace("\\", "")


def words(text: str) -> list[str]:
    """Lowercase words, accents and punctuation gone, a word and a number
    apart (`Kifer1` is `kifer 1`), a line-break hyphen joined."""
    text = _SPACING_ACCENTS.sub("", text).translate(_FOLD)
    text = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", text))
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    text = re.sub(r"-\s*\n\s*", "", text)
    text = re.sub(r"(?<=[^\W\d_])(?=\d)|(?<=\d)(?=[^\W\d_])", " ", text)
    return re.findall(r"[^\W_]+", text)


def surnames(people: str) -> tuple[str, ...]:
    """Each person's surname, as words: `Squeakinger, Ottoline M. and Crumble,
    Bartholomew` is `squeakinger`, `crumble`; `others` is nobody."""
    found = []
    for name in re.split(r"\s+and\s+", _delatex(people)):
        name = name.strip()
        if not name or name.casefold() == "others":
            continue
        last = name.split(",")[0] if "," in name else name.split()[-1]
        spelled = " ".join(words(last))
        if spelled:
            found.append(spelled)
    return tuple(found)


@dataclass(frozen=True, slots=True)
class Expected:
    """What an entry says its work is: its title's words, its people's
    surnames, its year, its DOI, and whether it is a book."""

    title: tuple[str, ...]
    surnames: tuple[str, ...] = ()
    year: str = ""
    doi: str = ""
    book: bool = False
    #: the title and people as the entry writes them, to quote back
    shown_title: str = ""
    shown_people: str = ""
    #: the entry's pages, first and last, where it gives them as numbers
    page_span: tuple[int, int] | None = None

    @property
    def pages(self) -> int:
        """How far into the file its title page may stand."""
        return BOOK_PAGES if self.book else PAPER_PAGES


def expected(record: BibRecord) -> Expected:
    """An entry's expectation of its file."""
    title = _delatex(record.title)
    return Expected(
        title=tuple(words(title)),
        surnames=surnames(record.author),
        year=record.year[:4],
        doi=record.doi.strip().casefold(),
        book=record.kind in BOOK_KINDS,
        shown_title=" ".join(title.split()),
        shown_people=", ".join(name.title() for name in surnames(record.author)),
        page_span=page_range(record.pages),
    )


def expected_found(
    record: BibRecord, title: str, creators: Iterable[str], issued: str, doi: str
) -> Expected:
    """A resolved work's expectation: its title, creators (`Given Family`),
    year and DOI where the resolver found them, the entry's elsewhere."""
    people = " and ".join(creators) or record.author
    return expected(
        BibRecord(
            record.key,
            title=title or record.title,
            author=people,
            doi=doi or record.doi,
            year=issued[:4] or record.year,
            kind=record.kind,
        )
    )


@dataclass(frozen=True, slots=True)
class Check:
    """A file against its entry: whether it matches, and if not why; the
    title page found; what the file's title page, or its first readable
    page where none was found, says (`says`, from page `says_page`: page
    text, fenced wherever it is shown); and the year note."""

    matches: bool
    why: str = ""
    title_page: int | None = None
    says: str = ""
    says_page: int | None = None
    year_note: str = ""


def _run(flat: str, title: tuple[str, ...]) -> float:
    """The longest run of the title's words printed in a row, as a share
    of the title: spaces ignored, so glued and split words still count."""
    best = 0
    for start in range(len(title)):
        for end in range(len(title), start + best, -1):
            if "".join(title[start:end]) in flat:
                best = end - start
                break
    return best / len(title)


def _close(token: str, name: str) -> bool:
    """The same word, or a long enough name with one letter misread: never a
    letter added or dropped, which turns a common word into a name
    (`hedge`, `Hedges`)."""
    if token == name:
        return True
    if len(name) < NAME_LETTERS or len(token) != len(name):
        return False
    return sum(a != b for a, b in zip(token, name, strict=True)) <= 1


def _named(page: list[str], surname: str) -> bool:
    """Whether a page names a surname: its last word with one letter
    misread, or
    spelled whole by a few words in a row that OCR split (`Em brec h ts`),
    never by the end of one word and the start of the next."""
    last = surname.split()[-1]
    if any(_close(token, last) for token in page):
        return True
    for start in range(len(page)):
        joined = ""
        for token in page[start:]:
            joined += token
            if len(joined) >= len(last):
                break
        if joined == last:
            return True
    return False


def readable(text: str) -> bool:
    """Whether a page's text is words enough to be read."""
    return _readable(words(text))


def _readable(page: list[str]) -> bool:
    return bool(page) and (
        sum(len(w) >= 3 and w.isalpha() for w in page) / len(page) >= READABLE
    )


def _opening(text: str, limit: int = 160) -> str:
    said = " ".join(text.split())
    return said if len(said) <= limit else said[: limit - 1] + "…"


def page_range(pages: str) -> tuple[int, int] | None:
    """The entry's pages as numbers, first and last: `30--35`, or one page;
    None for anything else (`e1234`, `xi--xx`)."""
    found = _RANGE.match(pages)
    if found is None:
        return None
    first = int(found.group(1))
    last = int(found.group(2)) if found.group(2) else first
    return (first, last) if first <= last else None


def prints_pages(pages: Mapping[int, str], first: int, last: int) -> bool:
    """Whether a file prints numbers in `first` to `last` at its pages' top
    or foot, at least `FOLIOS` of them in one run, as a pagination of its
    own: never its pages' own sequence (1, 2, … on pages 1, 2, …), which any
    preprint prints."""
    return _printed(pages, first, last, inside=True)


def prints_other_pages(pages: Mapping[int, str], first: int, last: int) -> bool:
    """Whether a file prints, in one run of at least `FOLIOS` pages, page
    numbers none of which is in `first` to `last`: another edition's."""
    return _printed(pages, first, last, inside=False)


def _printed(pages: Mapping[int, str], first: int, last: int, inside: bool) -> bool:
    runs: Counter[int] = Counter()
    hits: Counter[int] = Counter()
    for index, text in pages.items():
        lines = [line for line in text.splitlines() if line.strip()]
        edge = lines[:EDGE_LINES] + lines[-EDGE_LINES:]
        for n in {int(n) for line in edge for n in _FOLIO.findall(line)}:
            runs[n - index] += 1
            hits[n - index] += first <= n <= last
    if inside:
        return any(hits[o] >= FOLIOS for o in hits if o)
    return any(c >= FOLIOS and not hits[o] for o, c in runs.items())


def check(
    want: Expected, pages: Mapping[int, str], title_run: float = TITLE_RUN
) -> Check:
    """A file's first pages (by index, from 1) against its entry, the
    windows counting only the pages that print text; a page is its title
    page where `title_run` of the title stands on it in a row."""
    printed = [i for i in sorted(pages) if pages[i].strip()][: want.pages]
    window = {i: pages[i] for i in printed}
    read = {i: words(text) for i, text in window.items()}
    note = _year_note(want.year, window.values())
    # a DOI printed whole, not the start of a longer one
    doi = re.compile(re.escape(want.doi) + r"(?![0-9a-z])") if want.doi else None
    if doi and any(doi.search(text.casefold()) for text in window.values()):
        return Check(True, year_note=note)
    # the entry's own pagination printed: its work, in the edition it names,
    # whatever language its title page is in
    if want.page_span and prints_pages(pages, *want.page_span):
        return Check(True, year_note=note)
    if not want.title:  # an entry with no title: its authors anywhere in the window
        named = not want.surnames or any(
            _named(page, name) for page in read.values() for name in want.surnames
        )
        why = f"its first pages name none of its entry's authors ({want.shown_people})"
        return Check(named, "" if named else why, year_note=note)
    titled = [
        i for i, page in read.items() if _run("".join(page), want.title) >= title_run
    ]
    if not titled:
        readable = [i for i, page in read.items() if _readable(page)]
        if not readable:
            return Check(
                False,
                f"its first {want.pages} pages with text hold no readable words: "
                "its text layer is broken or missing",
                year_note=note,
            )
        return Check(
            False,
            f"its first {want.pages} pages with text print no title page: the "
            f"entry's title, “{want.shown_title}”, stands on none of them",
            says=_opening(window[readable[0]]),
            says_page=readable[0],
            year_note=note,
        )
    first = titled[0]
    said = Check(
        True,
        title_page=first,
        says=_opening(window[first]),
        says_page=first,
        year_note=note,
    )
    if not want.surnames:
        return said
    at = printed.index(first)
    later = printed[at : at + AUTHOR_PAGES + 1]
    near = [read[i] for i in titled if i in later]
    if len(later) > 1:
        near.append(read[later[1]])
    if any(_named(page, name) for page in near for name in want.surnames):
        return said
    return Check(
        False,
        f"its title page (p. {first}) names none of its entry's authors "
        f"({want.shown_people})",
        title_page=first,
        says=said.says,
        says_page=first,
        year_note=note,
    )


def scanned_window(
    want: Expected, pages: Mapping[int, str], scans: Collection[int]
) -> bool:
    """Whether a file's title page may stand on a scan (`scans`, by
    index): among its first pages that print text or are scans, as many as
    `check` reads, one is a scan. There a failed check on the text layer
    (none, or an old OCR's) may be the layer missing the title, not
    another work: an archive's cover sheet, one digital line, before a
    scan with no layer, fails it so."""
    window = [i for i in sorted(pages) if pages[i].strip() or i in scans]
    return any(i in scans for i in window[: want.pages])


def _year_note(year: str, texts: Iterable[str]) -> str:
    """The years the first pages print, where none is within one of the
    entry's: another edition, a reprint, a preprint's year."""
    if not year.isdigit():
        return ""
    printed = sorted({int(y) for text in texts for y in _YEAR.findall(text)})
    if not printed or any(abs(y - int(year)) <= 1 for y in printed):
        return ""
    shown = ", ".join(map(str, printed[:5])) + ("…" if len(printed) > 5 else "")
    return f"its first pages print {shown}, not {year}"
