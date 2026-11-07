"""A review answered by block (docs/architecture.md § 7). The argument
session reads `paper.md`, the state's edges sheet made lean, its subjects
and what more they ask under their lines; a session that judges nothing
(the findings, contribution, fieldresults and relatedwork sessions) reads the paper
plain, what its question uses laid under each block (`plain`,
`laid_under`). Each writes `review.md`,
each block's lines under a heading `@@ <handle>` (a block or a group, as
`paper.md` names it), the session judging nothing ending with its done
mark:

- `true [<claim>] <verdict> <depth> [pages KEY:N, …] — <evidence>`: the
  claim of a `⊢` line of the heading's block;
- `valid <T> [<claim>] <verdict> <depth> [pages …] — <evidence>`: the
  heading's block or group as J, for T's claim its justifies line covers;
- `rule [<claim>] …` and `keep [<claim>]`, on the heading's claim;
- `find <aspect> <severity> "<words>" — …`: the heading's block the first
  anchor where none is written;
- `topic "<words>" — …` and `suggest work:KEY … "<words>" — …` (the
  relatedwork session): the heading's block their anchor, or first
  anchor, where none is written;
- `edge …`, `group …`, `stands …`, `fixed …` and `reading …`, as the sheet
  writes them: they name their owner.

`[<claim>]` is the claim's handle as its `⊢` line shows it, left out where
one subject fits. `translated` writes these as the sheet's lines, each
subject named by its id, after the sheet the spec carries; the sheet's
check reads that (`review_file.checked`), so the gate, the session's own
check, the runner's check and the store read one sheet, and each problem
is told by the session's line and block (`told`).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from axiomation.identity import norm

#: The spec's key for the subjects by block: `[id, aspect, block, target,
#: claim]` each.
SUBJECTS = "by_block"
HEADING = "@@"
#: Each kind of line the session writes, by its first word.
KINDS = (
    "checked",
    "topic",
    "suggest",
    "true",
    "valid",
    "rule",
    "keep",
    "find",
    "edge",
    "group",
    "stands",
    "fixed",
    "reading",
)
#: The kinds that name their owner, and go through as the session wrote them.
OWNED = ("edge", "group", "stands", "fixed", "reading")
#: The mark a session that judges nothing ends its answer with.
DONE_MARK = "⟦done⟧"
#: The mark on a subject's line where the session asks fewer than every one.
ASKED = " · asked"

_CLAIM = r"(?: \[(?P<claim>[^\]]*)\])?"
_TRUE = re.compile(rf"^true{_CLAIM} (?P<rest>\S.*)$")
_VALID = re.compile(rf"^valid (?P<target>[^\s\[]+){_CLAIM} (?P<rest>\S.*)$")
_RULE = re.compile(rf"^rule{_CLAIM} (?P<rest>\S.*)$")
_KEEP = re.compile(rf"^keep{_CLAIM}$")
_FIND = re.compile(r"^find (?P<aspect>\S+) (?P<rest>\S.*)$")
_SEVERITY_FIRST = re.compile(r'^(?:work:\S+ )*[123] (?:pages [^"]+ )?"')
#: A checked line under a heading, its anchor left out: its quoted words
#: first.
_CHECKED_WORDS = re.compile(r'^checked (?P<rest>".*)$')
#: A topic line under a heading, its anchor left out; a suggest line, its
#: anchors left out: the work it offers first.
_TOPIC_WORDS = re.compile(r'^topic (?P<rest>".*)$')
_SUGGEST_WORK = re.compile(r"^suggest (?P<rest>work:.*)$")
#: the forms show the pages read as `[pages KEY:N, …]`, its brackets the
#: optional part's, beside the claim's literal `[<claim>]`: a line that
#: keeps them means the same
_BRACKETED_PAGES = re.compile(r"\[(pages [^\]\"]+)\]")
_LINE = re.compile(r"^line (\d+): ")
_ID = re.compile(r"\bs(\d+)\b")


@dataclass(frozen=True, slots=True)
class Subject:
    """A subject as the session answers it: its id, its aspect, the block
    or group its line goes under (a `true`'s claim's block, a `valid`'s
    J), a `valid`'s T, and its claim's handle where its block holds
    several."""

    id: str
    aspect: str
    block: str
    target: str | None
    claim: str | None

    def data(self) -> list[str | None]:
        return [self.id, self.aspect, self.block, self.target, self.claim]

    def named(self) -> str:
        """The subject as the session names it: its heading and its line's
        start."""
        claim = f" [{self.claim}]" if self.claim else ""
        target = f" {self.target}" if self.target else ""
        return f"`@@ {self.block}` {self.aspect}{target}{claim}"


@dataclass(frozen=True, slots=True)
class Translated:
    """The sheet the check reads, each of its added lines' origin (its
    1-based number to the session's heading and line), what could not be
    written as the sheet's lines, and those lines as written."""

    sheet: str
    origin: Mapping[int, tuple[str | None, str]]
    problems: tuple[str, ...]
    left: tuple[str, ...] = ()

    @property
    def kept(self) -> str:
        """The answer as the store keeps it: the sheet, and after it each
        line that did not translate, as written, which a later check
        refuses again."""
        return self.sheet + "".join(f"{line}\n" for line in self.left)


def placed(
    id: str,
    aspect: str,
    line: str,
    words: str | None,
    claims_of: Callable[[str], Mapping[str, str]],
) -> Subject:
    """A sheet subject placed by block: its line (`B · "…"`, or `J → T ·
    "…"`) and its claim's words, its claim's handle read off its block's
    claims (`claims_of`, handle to words) where they match."""
    head = line.split(" · ", 1)[0]
    if aspect == "valid":
        block, target = head.split(" → ", 1)
        held = claims_of(target)
    else:
        block, target = head, None
        held = claims_of(block)
    named = [h for h, w in held.items() if norm(w) == norm(words or "")]
    return Subject(id, aspect, block, target, named[0] if len(named) == 1 else None)


def subjects_of(spec: Mapping[str, Any]) -> list[Subject]:
    return [Subject(*s) for s in spec[SUBJECTS]]


def by_block(answer: str) -> list[tuple[str | None, str]]:
    """Each written line with the heading above it (None before any),
    blank lines left out."""
    found: list[tuple[str | None, str]] = []
    heading = None
    for line in answer.split("\n"):
        if line.startswith(HEADING):
            heading = line[len(HEADING) :].strip()
        elif line.strip():
            found.append((heading, line.strip()))
    return found


def _match(
    subjects: Sequence[Subject],
    aspect: str,
    block: str,
    target: str | None,
    claim: str | None,
) -> Subject | str:
    """The one subject a line names, or why none."""
    fits = [
        s
        for s in subjects
        if s.aspect == aspect
        and s.block == block
        and (aspect != "valid" or s.target == target)
    ]
    if claim is not None:
        named = [s for s in fits if s.claim == claim]
        if len(named) == 1:
            return named[0]
        held = ", ".join(sorted({s.claim for s in fits if s.claim}))
        return f"no {aspect} subject names claim [{claim}] here" + (
            f"; its claims: {held}" if held else ""
        )
    if len(fits) == 1:
        return fits[0]
    if not fits:
        where = f" onto {target}" if target else ""
        return f"no {aspect} subject{where} on this block"
    held = ", ".join(sorted(s.claim or "?" for s in fits))
    return f"several {aspect} subjects here: name the claim, one of {held}"


def translated(spec: Mapping[str, Any], answer: str) -> Translated:
    """The session's lines written as the sheet's lines after the spec's
    sheet, and what does not translate."""
    subjects = subjects_of(spec)
    out = str(spec["reference"]).rstrip("\n").split("\n")
    origin: dict[int, tuple[str | None, str]] = {}
    problems: list[str] = []
    left: list[str] = []

    def refuse(line: str, problem: str) -> None:
        problems.append(problem)
        left.append(line)

    def add(heading: str | None, line: str, written: str) -> None:
        out.append(written)
        origin[len(out)] = (heading, line)

    for heading, written in by_block(answer):
        where = f"`@@ {heading}` '{written}'" if heading else f"'{written}'"
        kind = written.split(" ", 1)[0]
        if written == DONE_MARK:
            add(heading, written, written)
            continue
        if kind in OWNED:
            add(heading, written, written)
            continue
        line = _BRACKETED_PAGES.sub(r"\1", written, count=1)
        if kind == "find":
            m = _FIND.match(line)
            if m is None:
                refuse(written, f"{where}: a find line names its aspect and severity")
            elif _SEVERITY_FIRST.match(m["rest"]):
                if heading is None:
                    refuse(
                        written,
                        f"{where}: a find line before any heading names its anchors",
                    )
                else:
                    add(heading, written, f"find {m['aspect']} {heading} {m['rest']}")
            else:
                add(heading, written, line)
            continue
        if kind == "checked":
            m = _CHECKED_WORDS.match(line)
            if m is None:
                add(heading, written, line)
            elif heading is None:
                refuse(
                    written, f"{where}: a checked line goes under its block's heading"
                )
            else:
                add(heading, written, f"checked {heading} {m['rest']}")
            continue
        if kind in ("topic", "suggest"):
            m = (_TOPIC_WORDS if kind == "topic" else _SUGGEST_WORK).match(line)
            if m is None:
                add(heading, written, line)
            elif heading is None:
                refuse(
                    written, f"{where}: a {kind} line goes under its block's heading"
                )
            else:
                add(heading, written, f"{kind} {heading} {m['rest']}")
            continue
        if kind == "fetch":
            refuse(
                written, f"{where}: a work is fetched by your `fetch` tool, not a line"
            )
            continue
        if kind not in KINDS:
            refuse(written, f"{where}: a line starts with one of {', '.join(KINDS)}")
            continue
        if heading is None:
            refuse(written, f"{where}: a {kind} line goes under its block's heading")
            continue
        pattern = {"true": _TRUE, "valid": _VALID, "rule": _RULE, "keep": _KEEP}[kind]
        m = pattern.match(line)
        if m is None:
            refuse(written, f"{where}: does not read as a {kind} line")
            continue
        aspect = "valid" if kind == "valid" else "true"
        target = m["target"] if kind == "valid" else None
        found = _match(subjects, aspect, heading, target, m["claim"] or None)
        if isinstance(found, str):
            refuse(written, f"{where}: {found}")
            continue
        if kind in ("true", "valid"):
            add(heading, written, f"judge {found.id} {m['rest']}")
        elif kind == "rule":
            add(heading, written, f"rule {found.id} {m['rest']}")
        else:
            add(heading, written, f"keep {found.id}")
    return Translated("\n".join(out) + "\n", origin, tuple(problems), tuple(left))


#: The sheet's words for its lines, each in the session's: a judge line is
#: a verdict line (`true` or `valid`), and a subject is named by its block.
GRAMMAR = (
    (
        "judge <subject> <verdict> <depth> [pages KEY:N, …] — <evidence>",
        "true [<claim>] <verdict> <depth> [pages KEY:N, …] — <evidence>` or "
        "`valid <T> [<claim>] <verdict> <depth> [pages KEY:N, …] — <evidence>",
    ),
    ("rule <subject>", "rule [<claim>]"),
    ("keep <subject>", "keep [<claim>]"),
    (
        "only a subject marked `⊢ unsupported` does",
        "only a claim with `↳ ⊢ unsupported: …` under its line does",
    ),
    ("judge lines", "verdict lines"),
    ("judge line", "verdict line"),
)


def told(spec: Mapping[str, Any], found: Translated, problem: str) -> str:
    """A problem of the sheet's check told by the session's line and by
    block, in the session's words: the check's words renamed, the
    session's line quoted as it wrote it."""
    by_id = {s.id: s for s in subjects_of(spec)}
    m = _LINE.match(problem)
    place = ""
    if m and int(m.group(1)) in found.origin:
        heading, line = found.origin[int(m.group(1))]
        place = f"`@@ {heading}` '{line}': " if heading else f"'{line}': "
        problem = problem[m.end() :]
    for theirs, ours in GRAMMAR:
        problem = problem.replace(theirs, ours)
    return place + _ID.sub(
        lambda i: (
            by_id[f"s{i.group(1)}"].named() if f"s{i.group(1)}" in by_id else i.group(0)
        ),
        problem,
    )


# --- paper.md --------------------------------------------------------------------

#: The edges sheet's head lines the reader does not use: the edges file's
#: title and its note to the edges writer, and the paper's files and code,
#: of which the session is given none.
_HEAD_NOISE = re.compile(r"^(# edges|Parts' legend lines .*|Files: .*|Code: .*)$")
_DONE = re.compile(r"^⟦done \S+⟧$")
#: A part's marker as the edges sheet shows it inside a block's text: not
#: the paper's words, so dropped from a quote before it is found.
PART_MARKER = re.compile(r"⟦\+ [^⟧]*⟧\s*")
#: A uses edge onto a file of the paper's: `→ uses file:scripts/compute.py`.
_USES_FILE = re.compile(r"^→ uses (file:\S+)")
#: The mark under a block of what is laid in for the session's question
#: (`laid_under`): never a block's opening, never the paper's words.
LAID = "↳"
#: A block's opening's place in the author's files (`paper.md:9:1`): a
#: file the session is not given, under the name of the one it is.
_PLACE = re.compile(r"^(⟦(?!\+|group |end group )[^⟧]*⟧) \S+:\d+:\d+ (\(.*\))$")
#: The quote on a uses, about or qualifies line: the owner's words, which
#: the block above holds (or a member of its group). A justifies or self
#: line keeps its quote: it says which claim J argues.
_QUOTED = re.compile(r'^(→ (?:uses|about|qualifies)\b.*?) · ".*?"(?= — |$)')
_OPENING = re.compile(r"^⟦(?!\+|group |end group )(\S+) [^⟧]*⟧")
_GROUP_END = re.compile(r"^⟦end group (\S+)⟧$")
#: A flag in the hints' open report: its kind, its block and the rest.
_FLAG = re.compile(r"^  \? (?P<kind>\S+) (?P<handle>\S+) · (?P<rest>.*)$")
#: The open report's heading over its flags, which leave it.
_FLAGS_HEADING = re.compile(r"^flag \(\d+\):$")
_FLAGGED_LINE = re.compile(r"^→ (justifies|self)\b.*\b(analogy|sketch)\b", re.M)
_GENERALIZES = re.compile(r"^→ about .*\bgeneralizes\b", re.M)
#: A hint at the margin, under a block or at the head (a subject's own
#: `  ↳` lines, indented, are the key's).
_HINTED = re.compile(rf"^{LAID} ", re.M)
#: A hint that a person argued a reading asked again: a dismissal, a proposal.
_ARGUED = re.compile(rf"^{LAID} (dismissed|proposed): ", re.M)


def lean(sheet: str) -> str:
    """The edges sheet without what its reader does not use: the head's
    noise, each block's place in the author's files, its done mark (the
    next block's opening ends it), and the quotes of its uses, about and
    qualifies lines."""
    head, sep, body = sheet.partition("\n---\n")
    if not sep:
        head, body = "", sheet
    kept = [line for line in head.split("\n") if not _HEAD_NOISE.match(line)]
    head = "\n".join(kept).strip("\n")
    if head:
        head = f"The parts edge lines name (`[<part>]`):\n\n{head}"
    body = _lean_body(body)
    return f"{head}\n\n---\n{body}" if head else body.lstrip("\n")


def _lean_body(body: str) -> str:
    """The sheet's blocks without their places, done marks and the quotes
    of their uses, about and qualifies lines."""
    body = "\n".join(
        _QUOTED.sub(r"\1", _PLACE.sub(r"\1 \2", line))
        for line in body.split("\n")
        if not _DONE.match(line)
    )
    return re.sub(r"\n{3,}", "\n\n", body)


def plain(sheet: str) -> str:
    """The paper plain: each block its handle and text, without the edges
    sheet's head, edges, standings, done marks and part markers."""
    _, sep, body = sheet.partition("\n---\n")
    lines = _lean_body(body if sep else sheet).split("\n")
    kept = [
        PART_MARKER.sub("", line) for line in lines if not line.startswith(("→ ", "⊢ "))
    ]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip("\n") + "\n"


def files_used(sheet: str) -> dict[str, list[str]]:
    """The files each block or group uses on the edges sheet (`file:KEY`),
    by handle, in its order."""
    found: dict[str, list[str]] = {}
    handle = None
    for line in sheet.split("\n"):
        if _DONE.match(line):
            continue
        if m := (_OPENING.match(line) or _GROUP_END.match(line)):
            handle = m.group(1)
        elif handle and (m := _USES_FILE.match(line)):
            found.setdefault(handle, []).append(m.group(1))
    return found


def laid_under(
    text: str,
    under: Mapping[str | None, Sequence[str]],
    head: Sequence[str] = (),
    heading: str = "",
) -> str:
    """`text` (`plain`, or `paper`'s) with each handle's lines under its
    block's or group's last line; those of no handle, or of one it does
    not show, at its head under `heading` (`UNPLACED`), never lost; `head`
    above all."""
    heading = heading or UNPLACED
    lines = text.rstrip("\n").split("\n")
    sections = _sections(lines)
    after: dict[int, list[str]] = {}
    unplaced: list[str] = []
    for handle, notes in under.items():
        if not notes:
            continue
        span = sections.get(handle) if handle else None
        if span is None:
            named = [f"on {handle}, a block not shown:"] if handle else []
            unplaced += [*named, *notes, ""]
        else:
            after.setdefault(_last_line(lines, span), []).extend(notes)
    out: list[str] = []
    for i, line in enumerate(lines):
        out += [line, *after.get(i, [])]
    top = [*head, *([heading, "", *unplaced] if unplaced else [])]
    return "\n".join([*top, *out]) + "\n"


def flags(hints: str) -> tuple[dict[str, list[str]], str]:
    """The hints' flags by the block they point at, each as a line under
    it, and the hints without them."""
    found: dict[str, list[str]] = {}
    rest: list[str] = []
    for line in hints.split("\n"):
        if m := _FLAG.match(line):
            found.setdefault(m["handle"], []).append(f"⚑ {m['kind']} · {m['rest']}")
        elif not _FLAGS_HEADING.match(line):
            rest.append(line)
    return found, "\n".join(rest)


#: A line of the hints that says nothing others read: a heading, the
#: note, `(none)`, the open report's head and its counts.
_EMPTY_HINT = re.compile(
    r"^(#.*|What others read.*|\(none\)|(?:nothing|\d+) open at state .*|"
    r"[\w-]+ \(\d+\):)$"
)


def bare(hints: str) -> bool:
    """Whether the hints hold nothing others read at the state: only
    headings, their note, `(none)` and the open report's head and counts."""
    return all(_EMPTY_HINT.match(line) for line in hints.split("\n") if line.strip())


@dataclass(frozen=True, slots=True)
class Shown:
    """A subject as `paper.md` shows it: where its line is, and what more
    it asks under it (`↳ …`)."""

    subject: Subject
    notes: tuple[str, ...] = ()


def _sections(lines: Sequence[str]) -> dict[str, tuple[int, int]]:
    """Each block's and group's lines in `paper.md`, by handle: a block's
    from its opening to the next opening, a group's from its end to the
    next opening."""
    starts: list[tuple[str, int]] = []
    for i, line in enumerate(lines):
        if m := (_OPENING.match(line) or _GROUP_END.match(line)):
            starts.append((m.group(1), i))
        elif line.startswith("⟦group "):
            starts.append(("", i))
    found: dict[str, tuple[int, int]] = {}
    for n, (handle, i) in enumerate(starts):
        end = starts[n + 1][1] if n + 1 < len(starts) else len(lines)
        if handle:
            found[handle] = (i, end)
    return found


def _subject_line(
    lines: Sequence[str], span: tuple[int, int], s: Subject, words: str | None
) -> int | None:
    """The line of `paper.md` a subject's answer is on: a `true`'s `⊢`
    line in its block, a `valid`'s J's justifies line onto T."""
    rows = range(*span)
    if s.aspect == "valid":
        hits = [
            i
            for i in rows
            if re.match(rf"^→ justifies {re.escape(s.target or '')}(?: |$)", lines[i])
        ]
    else:
        # a block holding one claim shows no handle on its line
        claims = [i for i in rows if lines[i].startswith("⊢ ")]
        named = [i for i in claims if s.claim and f" [{s.claim}] · " in lines[i]]
        hits = named or claims
        if len(hits) > 1 and words:
            hits = [i for i in hits if norm(words) in norm(lines[i])] or hits
    return hits[0] if len(hits) == 1 else None


#: The head of `paper.md` over what stands under no block of it.
UNPLACED = "Under no block below:"
#: The heading over the hints that lie on no block shown.
HINTS_UNPLACED = "Hints on no block below:"


def paper(
    sheet: str,
    hints: str,
    shown: Sequence[tuple[Shown, str | None]],
    questions: Sequence[tuple[str | None, Sequence[str]]],
) -> tuple[str, bool]:
    """`paper.md` for the session, and whether it asks every line: the
    edges sheet made lean (`lean`), each flag of the hints under its block,
    each asked subject's notes under its line, marked `ASKED` unless every
    `⊢` and justifies line is asked, and each finding to settle under its
    first block (by handle). What has no block or line here (a finding on
    the paper as a whole, a flag on a block the state no longer holds, a
    subject whose line this sheet lacks) stands at the head
    (`UNPLACED`), never lost."""
    flagged, _ = flags(hints)
    lines = lean(sheet).split("\n")
    sections = _sections(lines)
    after: dict[int, list[str]] = {}
    marked: set[int] = set()
    head: list[str] = []
    for s, words in shown:
        span = sections.get(s.subject.block)
        at = None if span is None else _subject_line(lines, span, s.subject, words)
        if at is None:
            head += [f"{s.subject.named()}: asked; its line is not shown", *s.notes, ""]
            continue
        marked.add(at)
        after.setdefault(at, []).extend(s.notes)
    for handle, notes in flagged.items():
        span = sections.get(handle)
        if span is None:
            head += [f"on {handle}, a block not shown:", *notes, ""]
        else:
            after.setdefault(_last_line(lines, span), []).extend(notes)
    for handle, question in questions:
        span = sections.get(handle) if handle else None
        if span is None:
            head += [*question, ""]
        else:
            after.setdefault(_last_line(lines, span), []).extend(question)
    every = not head and marked == {
        i for i, line in enumerate(lines) if line.startswith(("⊢ ", "→ justifies "))
    }
    out: list[str] = []
    for i, line in enumerate(lines):
        out.append(line + (ASKED if i in marked and not every else ""))
        out += after.get(i, [])
    text = "\n".join(out)
    if head:
        text = "\n".join([UNPLACED, "", *head, text])
    return text, every


def _last_line(lines: Sequence[str], span: tuple[int, int]) -> int:
    """A section's last line that is not blank."""
    last = span[0]
    for i in range(*span):
        if lines[i].strip():
            last = i
    return last


# --- the brief -----------------------------------------------------------------

_PART = re.compile(r"<!-- if (\w+) -->\n?(.*?)<!-- end \1 -->\n?", re.DOTALL)


def instructions(template: str, holds: set[str]) -> str:
    """The brief's text with each part whose name `holds` misses cut:
    `<!-- if NAME -->…<!-- end NAME -->`, on lines of their own or inside
    one."""
    text = _PART.sub(lambda m: m.group(2) if m.group(1) in holds else "", template)
    return re.sub(r"\n{3,}", "\n\n", text)


#: The brief's last section, before which another goes.
LAST_SECTION = "## How to work"


def with_section(brief: str, section: str) -> str:
    """The brief with `section` (a `# Title` and its text) as a section of
    its own, before its last (`LAST_SECTION`), or at its end; as it was,
    where `section` is empty."""
    if not section:
        return brief
    text = "#" + section.strip("\n") + "\n\n"
    at = brief.find(LAST_SECTION)
    return brief[:at] + text + brief[at:] if at >= 0 else f"{brief.rstrip()}\n\n{text}"


def holds(
    keyed: str,
    rules: bool,
    settle: bool,
    later: bool,
    every: bool,
    cited: bool,
    read_as: str = "",
) -> set[str]:
    """The parts of the brief a session needs: rulings where a claim takes
    a rule line, settling where a finding is asked to settle, the later
    paragraphs where a claim shows a candidate, a proposal or a settled
    finding, `analogy`/`sketch` and `generalizes` where `paper.md` holds
    such a line, the subjects as every line or as the asked ones, the
    cited works where any is read. Each follows what the question's key
    takes (`paper.md` without its hints, its subjects, its works), so a
    round replays while only hints change; but the parts that tell the
    hints, which follow `read_as`, `paper.md` as the session reads it: how
    hints read where any is laid in (`hinted`), and how to weigh a
    person's argument where a dismissal or a proposal is (`argued`): a
    part on what is not there reads as a rule of its own (the argument's
    bar, held to every negative reading)."""
    found = {"every" if every else "some"}
    if _HINTED.search(read_as):
        found.add("hinted")
    if _ARGUED.search(read_as):
        found.add("argued")
    for name, present in (
        ("rules", rules),
        ("settle", settle),
        ("later", later),
        ("flagged", bool(_FLAGGED_LINE.search(keyed))),
        ("generalizes", bool(_GENERALIZES.search(keyed))),
        ("cited", cited),
    ):
        if present:
            found.add(name)
    return found
