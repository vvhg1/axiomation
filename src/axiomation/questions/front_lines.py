"""The front stages answered as lines: the cut, the roles and the groups
(docs/architecture.md § 2).

The session reads the stage's file, as the stage renders it, as
`paper.md`, and leaves it as it is. It writes its answer in a file of
lines (`ANSWER_FILES`), each naming a block by the handle `paper.md`
shows and quoting the paper's own words, white space aside:

- the roles, one line a block: its first part's role, then each later
  part's role and the words it starts at, after a `|`:
  `b13 justification | claim "Hence the bound holds"`;
- the cut, one line a correction: `split b12 "<words>"` (a block starts
  there, inside b12), `join b13` (b13 joins the block before it),
  `move b13 "<words>"` (b13 starts there, in it or in the block before);
- the groups, every group the paper has: `group proof bound_proof b13 b14`;
- in the roles and the groups, a flag on a line of its own:
  `b12 ? split "<the block's words>" — <note>`.

`laid` reads the lines against the blocks the file shows and lays them
into it as the session would have edited it: a role in its block's
marker and a `⟦+ <role>⟧` line where a later part starts; a `⟦new⟧` line
where a block starts and a joined or moved block's marker gone; the
groups' lines in place of the ones shown; a flag under its block's
marker. Each stage's check reads the laid file as it reads an edit of
its file (`checked`), and the store keeps the laid file, so every reader
of a stage's answer reads it as before. A block's text is all its runs
the file shows, a run resumed after a nested block (`⟦b12 continued⟧`)
included; words that do not stand once where a line places them are a
problem, never dropped.

`laid` needs only the file shown and the lines, so the host runs it as the
session's gate (`main`), outside the session's sandbox, when the session
tries to end.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from axiomation.questions import (
    FILE_TOOLS,
    Gate,
    Question,
    Stage,
    Validation,
    Workspace,
    gates,
)
from axiomation.questions.cut_file import (
    CONTEXT,
    CONTINUED,
    HEADER,
    MARKER,
    RenderError,
    read_markers,
)
from axiomation.records import STAGE_SIGNALS

#: The file the session reads: the stage's file, as the stage renders it.
PAPER_FILE = "paper.md"
#: The file the session writes its lines in, by stage.
ANSWER_FILES = {
    Stage.BOUNDARIES: "cuts.md",
    Stage.ROLES: "roles.md",
    Stage.GROUPS: "groups.md",
}
#: The gate's spec: the stage, the file shown, and the handles of the
#: blocks nested in another (a footnote), which no block joins.
STAGE = "front_stage"
SHOWN = "shown"
NESTED = "nested"
#: The one line of an answer with nothing to write: a cut with nothing to
#: correct, a paper with no group. An empty file is no answer.
NOTHING = "none"


#: The roles a line names, as the briefs name them; `none` for a span that
#: plays none.
ROLES = ("definition", "assumption", "claim", "justification", "motivation", "none")
#: The kinds a groups line names (`groups_file.KINDS`).
GROUP_KINDS = ("proof", "proof-section", "derivation")
#: The cut's three corrections.
SPLIT, JOIN, MOVE = "split", "join", "move"
#: The line a laid split or move puts where a block starts.
NEW = "⟦new⟧"

_HANDLE = re.compile(r"b\d+")
_LINE = re.compile(r"^(b\d+)(?:\s+(.*))?$")
_PART = re.compile(r'^(\S+)\s+"(.+)"$')
_FLAG = re.compile(r'^\?\s*(?:([^\s"]+)\s+)?"(.+?)"\s*(?:—\s*(.*))?$')
_CUT = re.compile(r'^(split|join|move)\s+(\S+)(?:\s+"(.+)")?\s*$')
_GROUP = re.compile(r"^group\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)$")
_GROUP_MARK = re.compile(r"^⟦(?:group\s|end group\s)[^⟧]*⟧\s*$")


# --- the file shown -----------------------------------------------------------------


@dataclass(slots=True)
class _Block:
    """A block as the file shows it: its marker's line, and the lines of
    its text, every run of it in order."""

    marker: int
    lines: list[int] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _Shown:
    """The stage's file: its lines, and its blocks by handle, in the order
    their markers stand."""

    lines: tuple[str, ...]
    blocks: Mapping[str, _Block]
    nested: frozenset[str] = frozenset()

    def text(self, handles: Sequence[str]) -> tuple[str, list[tuple[int, int]]]:
        """The text of `handles`' blocks, in file order, and for each of
        its characters the line and column it stands at."""
        rows = sorted(n for h in handles for n in self.blocks[h].lines)
        text: list[str] = []
        where: list[tuple[int, int]] = []
        for n in rows:
            if text:
                text.append("\n")
                where.append((n, 0))
            text.append(self.lines[n])
            where += [(n, column) for column in range(len(self.lines[n]))]
        return "".join(text), where

    def before(self, handle: str) -> str | None:
        """The block whose text stands last before `handle`'s marker, the
        one `handle` joins, a nested block's passed over (the cut joins a
        block to the one it is not nested in); None where there is none,
        or the context or a file's start lies between."""
        owner = {n: h for h, block in self.blocks.items() for n in block.lines}
        for n in range(self.blocks[handle].marker - 1, -1, -1):
            if n in owner and owner[n] not in self.nested and self.lines[n].strip():
                return owner[n]
            if HEADER.match(self.lines[n]) or _head(self.lines[n]) == CONTEXT:
                return None
        return None


def _head(line: str) -> str | None:
    """A marker line's first word, `""` for an empty one; None for a line
    that is not a marker."""
    m = MARKER.match(line)
    if m is None:
        return None
    tokens = m.group(1).split()
    return tokens[0] if tokens else ""


def shown_blocks(text: str, nested: Collection[str] = ()) -> _Shown:
    """The blocks of the stage's file, `nested` those nested in another. A
    `##### <file>` line and a `⟦context⟧` marker end a block's text; any
    other marker that is not a block's (a part's, a group's, a ledger
    line) leaves it running."""
    lines = tuple(text.split("\n"))
    found: dict[str, _Block] = {}
    owner: str | None = None
    for n, line in enumerate(lines):
        if HEADER.match(line):
            owner = None
            continue
        m = MARKER.match(line)
        if m is None:
            if owner is not None:
                found[owner].lines.append(n)
            continue
        tokens = m.group(1).split()
        head = tokens[0] if tokens else ""
        if _HANDLE.fullmatch(head) and tokens[1:] == [CONTINUED]:
            owner = head if head in found else None
        elif _HANDLE.fullmatch(head):
            found[head] = _Block(n)
            owner = head
        elif head == CONTEXT:
            owner = None
    return _Shown(lines, found, frozenset(nested))


def _starts(text: str, words: str) -> list[int]:
    """Where `words` start in `text`, white space between them read as any;
    words that start with a letter or a digit start a word there too."""
    parts = words.split()
    if not parts:
        return []
    joined = r"\s+".join(re.escape(w) for w in parts)
    head = r"(?<!\w)" if re.match(r"\w", parts[0]) else ""
    pattern = re.compile(f"(?=({head}{joined}))")
    return [m.start(1) for m in pattern.finditer(text)]


def _first(text: str) -> int:
    """Where `text`'s first character that is not white space stands."""
    return len(text) - len(text.lstrip())


# --- the lines read -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Mark:
    """A line the laying puts into the file: `text` before column `column`
    of line `line`; at column 0, on a line of its own before it. `order`
    sorts the marks that stand at one place."""

    line: int
    column: int
    text: str
    order: tuple[int, int] = (0, 0)


@dataclass(frozen=True, slots=True)
class Laid:
    """Lines laid into the stage's file: the file, and what does not read."""

    text: str
    problems: tuple[str, ...]


def _place(
    shown: _Shown, handles: Sequence[str], words: str, where: str, line: str
) -> tuple[tuple[int, int] | None, str | None]:
    """Where `words` start in `handles`' text, or why they cannot be
    placed there: they stand nowhere, more than once, or where the text
    starts."""
    text, at = shown.text(handles)
    found = _starts(text, words)
    if len(found) != 1:
        said = "nowhere" if not found else f"{len(found)} times"
        return None, (
            f"'{line}': the words \"{words}\" stand {said} in {where}: quote "
            "words that stand there once"
        )
    if found[0] == _first(text):
        return None, None
    return at[found[0]], None


def _flag(
    shown: _Shown, stage: Stage, handle: str, rest: str, line: str
) -> tuple[_Mark | None, str | None]:
    """A flag line's mark under its block's marker, or what is wrong."""
    signals = STAGE_SIGNALS[stage.value]
    f = _FLAG.match(rest)
    if f is None or not (f.group(3) or "").strip():
        return None, (
            f"'{line}': a flag is `{handle} ? <signal> \"<the block's own words>\" "
            "— <what you read there>`"
        )
    signal, words, note = f.group(1), f.group(2), f.group(3).strip()
    if signal is not None and signal not in signals:
        return None, (
            f"'{line}': a flag's signal is one of {', '.join(sorted(signals))}, or none"
        )
    if "⟧" in note or "⟧" in words:
        return None, f"'{line}': a flag holds no `⟧`"
    text, _ = shown.text([handle])
    if not _starts(text, words):
        return None, f"'{line}': {handle} does not hold the words \"{words}\""
    said = f"{signal} " if signal else ""
    # under its block's marker, before any group line that stands there
    return _Mark(
        shown.blocks[handle].marker + 1, 0, f'⟦? {said}"{words}" — {note}⟧', (-2, 0)
    ), None


def _said(answer: str) -> list[str]:
    """The answer's lines that say something: not blank, no `#` heading."""
    return [
        line.strip()
        for line in answer.split("\n")
        if line.strip() and not line.strip().startswith("#")
    ]


def _lines(answer: str) -> list[str]:
    """The answer's lines to read: none where the one line is `NOTHING`."""
    found = _said(answer)
    return [] if found == [NOTHING] else found


def _parts(rest: str) -> list[str]:
    """A roles line's parts, split at each `|` that stands outside double
    quotes."""
    parts, current, quoted = [], [], False
    for c in rest:
        if c == '"':
            quoted = not quoted
        if c == "|" and not quoted:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(c)
    return [*parts, "".join(current).strip()]


def _roles(shown: _Shown, answer: str) -> tuple[dict[int, str], list[_Mark], list[str]]:
    """The roles' lines: each marker's line as laid, the parts' and flags'
    marks, and the problems."""
    markers: dict[int, str] = {}
    marks: list[_Mark] = []
    problems: list[str] = []
    answered: set[str] = set()
    for line in _lines(answer):
        m = _LINE.match(line)
        if m is None or m.group(1) not in shown.blocks:
            problems.append(
                f"'{line}': a line starts with a block's handle as `{PAPER_FILE}` "
                "shows it"
            )
            continue
        handle, rest = m.group(1), (m.group(2) or "").strip()
        if rest.startswith("?"):
            mark, problem = _flag(shown, Stage.ROLES, handle, rest, line)
            marks += [mark] if mark else []
            problems += [problem] if problem else []
            continue
        if handle in answered:
            problems.append(f"'{line}': {handle} has a roles line already: one a block")
            continue
        answered.add(handle)
        parts = _parts(rest)
        if parts[0] not in ROLES:
            problems.append(f"'{line}': a role is one of {', '.join(ROLES)}")
            continue
        block = shown.blocks[handle]
        markers[block.marker] = f"⟦{handle} {parts[0]}⟧"
        previous, last = parts[0], (-1, -1)
        for part in parts[1:]:
            p = _PART.match(part)
            if p is None or p.group(1) not in ROLES:
                problems.append(
                    f"'{line}': a later part is `| <role> \"<the words it starts at>\"`"
                )
                break
            role, words = p.group(1), p.group(2)
            at, problem = _place(shown, [handle], words, handle, line)
            if problem is not None:
                problems.append(problem)
                break
            if at is None:
                problems.append(
                    f"'{line}': \"{words}\" start {handle}: give its first part's "
                    "role first"
                )
                break
            if at <= last:
                problems.append(f"'{line}': give the parts in the order they stand")
                break
            if role == previous:
                problems.append(
                    f"'{line}': a part's role is the one before it: a later part "
                    "starts where the role changes"
                )
                break
            marks.append(_Mark(at[0], at[1], f"⟦+ {role}⟧"))
            previous, last = role, at
    unanswered = [h for h in shown.blocks if h not in answered]
    if unanswered:
        problems.append(f"no roles line for {', '.join(unanswered)}")
    return markers, marks, problems


def _cut(shown: _Shown, answer: str) -> tuple[set[int], list[_Mark], list[str]]:
    """The cut's lines: the marker lines a join or a move takes out, the
    `⟦new⟧` marks, and the problems."""
    gone: set[int] = set()
    marks: list[_Mark] = []
    problems: list[str] = []
    ends: dict[str, str] = {}
    for line in _lines(answer):
        m = _CUT.match(line)
        if m is None:
            problems.append(
                f"'{line}': a line is `split <block> \"<words>\"`, `join <block>` "
                'or `move <block> "<words>"`'
            )
            continue
        kind, handle, words = m.group(1), m.group(2), m.group(3)
        if handle not in shown.blocks:
            problems.append(f"'{line}': no block {handle} in `{PAPER_FILE}`")
            continue
        if kind == SPLIT:
            if words is None:
                problems.append(f"'{line}': a split quotes the words a block starts at")
                continue
            at, problem = _place(shown, [handle], words, handle, line)
            if problem is None and at is None:
                problem = f"'{line}': \"{words}\" start {handle}: no split there"
            if problem is not None:
                problems.append(problem)
                continue
            assert at is not None
            marks.append(_Mark(at[0], at[1], NEW))
            continue
        if handle in ends:
            problems.append(f"'{line}': {handle} has a {ends[handle]} line already")
            continue
        before = shown.before(handle)
        if before is None:
            problems.append(f"'{line}': no block of yours stands before {handle}")
            continue
        if kind == JOIN:
            if words is not None:
                problems.append(
                    f"'{line}': `join <block>` joins a block to the one before it, "
                    "and names no words"
                )
                continue
        else:
            if words is None:
                problems.append(f"'{line}': a move quotes the words {handle} starts at")
                continue
            at, problem = _place(
                shown, [before, handle], words, f"{before} and {handle}", line
            )
            if problem is None and at is None:
                problem = f"'{line}': \"{words}\" start {before}: no block starts there"
            if problem is not None:
                problems.append(problem)
                continue
            assert at is not None
            marks.append(_Mark(at[0], at[1], NEW))
        ends[handle] = kind
        gone.add(shown.blocks[handle].marker)
    return gone, marks, problems


@dataclass(frozen=True, slots=True)
class GroupLine:
    """A group: its kind and name, its first and last block."""

    kind: str
    name: str
    first: str
    last: str


def _groups(shown: _Shown, answer: str) -> tuple[list[_Mark], list[str]]:
    """The groups' lines: their marks and the flags', and the problems."""
    order = {h: n for n, h in enumerate(shown.blocks)}
    found: list[GroupLine] = []
    marks: list[_Mark] = []
    problems: list[str] = []
    for line in _lines(answer):
        h = _LINE.match(line)
        if h and (h.group(2) or "").startswith("?"):
            if h.group(1) not in shown.blocks:
                problems.append(f"'{line}': no block {h.group(1)} in `{PAPER_FILE}`")
                continue
            mark, problem = _flag(shown, Stage.GROUPS, h.group(1), h.group(2), line)
            marks += [mark] if mark else []
            problems += [problem] if problem else []
            continue
        m = _GROUP.match(line)
        if m is None:
            problems.append(
                f"'{line}': a line is `group <kind> <name> <first block> <last block>`"
            )
            continue
        g = GroupLine(*m.groups())
        missing = [b for b in (g.first, g.last) if b not in shown.blocks]
        if g.kind not in GROUP_KINDS:
            problems.append(
                f"'{line}': a group's kind is one of {', '.join(GROUP_KINDS)}"
            )
        elif missing:
            problems.append(f"'{line}': no block {missing[0]} in `{PAPER_FILE}`")
        elif order[g.last] <= order[g.first]:
            problems.append(
                f"'{line}': a group holds two or more blocks, {g.first} first"
            )
        elif "⟧" in g.name:
            problems.append(f"'{line}': a group's name holds no `⟧`")
        elif any(_key(x.name) == _key(g.name) for x in found):
            problems.append(f"'{line}': another group is named {g.name}")
        elif any((x.first, x.last) == (g.first, g.last) for x in found):
            problems.append(
                f"'{line}': another group holds {g.first} to {g.last}: keep one"
            )
        else:
            found.append(g)
    span = {g.name: (order[g.first], order[g.last]) for g in found}
    for a in found:
        for b in found:
            (a0, a1), (b0, b1) = span[a.name], span[b.name]
            if a0 < b0 <= a1 < b1:
                problems.append(
                    f"groups {a.name} and {b.name} partly overlap: nest one inside "
                    "the other, or make them disjoint"
                )
    handles = list(shown.blocks)
    for g in found:
        a, b = span[g.name]
        size = b - a
        # outer groups start first and end last; a group ends after the
        # last text of its blocks, an outer block's resumed text included
        start = shown.blocks[g.first].marker
        marks.append(_Mark(start, 0, f"⟦group {g.kind} {g.name}⟧", (0, -size)))
        held = [shown.blocks[h] for h in handles[a : b + 1]]
        end = max(n for block in held for n in [block.marker, *block.lines]) + 1
        marks.append(_Mark(end, 0, f"⟦end group {g.name}⟧", (-1, size)))
    return marks, problems


def _key(name: str) -> str:
    """A name as the groups' file pairs it: case aside, `-` as `_`."""
    return name.casefold().replace("-", "_")


# --- laying them in -----------------------------------------------------------------


def _written(
    shown: _Shown,
    marks: Sequence[_Mark],
    markers: Mapping[int, str] | None = None,
    gone: Collection[int] = (),
    dropped: Callable[[str], bool] = lambda line: False,
) -> str:
    """The file shown with `markers` in place of their lines, the lines
    `gone` and those `dropped` taken out, and each mark laid in: on a line
    of its own, the line broken where it stands inside one."""
    at: dict[int, list[_Mark]] = {}
    for mark in marks:
        at.setdefault(mark.line, []).append(mark)
    out: list[str] = []
    for n in range(len(shown.lines) + 1):
        here = sorted(at.get(n, []), key=lambda m: (m.column, m.order))
        if n == len(shown.lines):
            out += [m.text for m in here]
            break
        if n in gone or dropped(shown.lines[n]):
            out += [m.text for m in here]
            continue
        line = (markers or {}).get(n, shown.lines[n])
        cut = 0
        for mark in here:
            head = line[cut : mark.column].rstrip()
            if head.strip():
                out.append(head)
            out.append(mark.text)
            cut = max(cut, mark.column)
        out.append(line[cut:])
    return "\n".join(out)


def laid(stage: Stage, shown: str, answer: str, nested: Collection[str] = ()) -> Laid:
    """`answer`'s lines read against the blocks of `shown`, the stage's
    file, `nested` the blocks nested in another, and laid into it; what
    does not read is named, and nothing of it is laid."""
    held = shown_blocks(shown, nested)
    if not _said(answer):
        # an empty file is no answer (docs/architecture.md § 2): said here,
        # the session writes its lines or `none` in the same session
        said = f"{ANSWER_FILES[stage]} says nothing: write your lines, or the one line"
        return Laid(shown, (f"{said} `{NOTHING}`",))
    if stage is Stage.ROLES:
        markers, marks, problems = _roles(held, answer)
        text = _written(held, marks, markers)
    elif stage is Stage.BOUNDARIES:
        gone, marks, problems = _cut(held, answer)
        text = _written(held, marks, gone=gone)
    elif stage is Stage.GROUPS:
        marks, problems = _groups(held, answer)
        text = _written(held, marks, dropped=lambda line: bool(_GROUP_MARK.match(line)))
    else:
        raise ValueError(f"the {stage.value} stage is not answered as lines")
    return Laid(shown if problems else text, tuple(problems))


def is_file(answer: str, shown: str) -> bool:
    """Whether `answer` is the stage's file `shown`, not lines: a kept
    answer, its lines laid, read again (its text outside the markers is
    `shown`'s), or the file edited, which holds every block's marker (the
    file's check names what changed). Lines that copy a block's marker or
    two are still lines, read as such."""
    if read_markers(answer).stream == read_markers(shown).stream:
        return True
    blocks = {
        h for line in shown.split("\n") if _HANDLE.fullmatch(h := _head(line) or "")
    }
    return bool(blocks) and blocks <= {_head(line) for line in answer.split("\n")}


# --- a stage's question and its check -----------------------------------------------


def workspace(
    stage: Stage,
    shown: str,
    instructions: str,
    beside: Mapping[str, str] | None = None,
    nested: Collection[str] = (),
) -> Workspace:
    """The session's files, brief and gate: the stage's file `shown` as
    `PAPER_FILE`, to read, and the lines file it writes, empty; `beside`,
    other files it reads (the cut's outline); `nested`, the handles of the
    blocks nested in another, which a join or a move passes over. A brief
    that names no `PAPER_FILE` is a file's brief (a profile's own
    template), which would ask for the markers edited: refused."""
    answer = ANSWER_FILES[stage]
    if f"`{PAPER_FILE}`" not in instructions:
        raise RenderError(
            f"the {stage.value} brief names no `{PAPER_FILE}`: its session "
            f"answers as lines in `{answer}`, so its brief is a lines brief"
        )
    spec = {STAGE: stage.value, SHOWN: shown, NESTED: sorted(nested)}
    return Workspace(
        files={PAPER_FILE: shown, answer: "", **(beside or {})},
        edit=answer,
        instructions=instructions,
        reask_from_edit=True,
        gate=Gate(module=__name__, spec=json.dumps(spec, ensure_ascii=False)),
        tools=FILE_TOOLS,
        brief_alone=True,
    )


def shown_file(question: Question) -> str:
    """The stage's file as `question` shows it: `PAPER_FILE` where it is
    answered as lines, else the file it edits."""
    assert question.workspace is not None
    files = question.workspace.files
    return files[PAPER_FILE] if _asked(question) else files[question.workspace.edit]


def _asked(question: Question) -> bool:
    """Whether `question` is answered as lines."""
    gate = None if question.workspace is None else question.workspace.gate
    return gate is not None and gate.module == __name__


#: Where a problem the stage's check finds in the laid file names a line
#: of it, which the session never sees.
_LINE_NUMBER = re.compile(r"^lines? \d+(?: (?:and|to) \d+)?: ")
_LINE_SAID = re.compile(r" \((?:lines?|at line) \d+(?: (?:and|to) \d+)?\)")


def checked(
    question: Question, answer: str, check: Callable[[str], Validation]
) -> Validation:
    """`check`, the stage's check of its file, on `answer`: where the
    question is answered as lines, on the lines laid into the file, the
    laid file kept; a problem with the lines is named as such, nothing
    laid, and the lines kept as written, so a re-ask starts from them. An
    answer that is the file already (`is_file`) is read as it is."""
    if not _asked(question):
        return check(answer)
    assert question.workspace is not None and question.workspace.gate is not None
    spec = json.loads(question.workspace.gate.spec)
    shown = shown_file(question)
    if is_file(answer, shown):
        return check(answer)
    found = laid(question.stage, shown, answer, spec.get(NESTED, ()))
    if found.problems:
        return Validation(problems=found.problems)
    validation = check(found.text)
    if validation.valid:
        return replace(validation, kept=found.text)
    return replace(
        validation,
        problems=tuple(
            f"with the lines laid into {PAPER_FILE}'s blocks: "
            + _LINE_SAID.sub("", _LINE_NUMBER.sub("", problem))
            for problem in validation.problems
        ),
    )


# --- the gate -----------------------------------------------------------------------


def problems(spec: Mapping[str, Any], answer: str) -> list[str]:
    """What the gate finds wrong with `answer` under `spec`: nothing for the
    file itself, which the runner's check reads."""
    if is_file(answer, spec[SHOWN]):
        return []
    found = laid(Stage(spec[STAGE]), spec[SHOWN], answer, spec.get(NESTED, ()))
    return list(found.problems)


def main(argv: Sequence[str] | None = None) -> int:
    """The session's gate, as Claude Code's Stop hook, `<spec> <edited>`:
    nothing when the lines read, else Claude Code's `block` decision with
    their problems, at most `gates.ROUNDS` times. It never fails the
    session: a gate that cannot run lets it end, and the runner's check
    decides."""
    gates.hook_input()
    args = list(argv if argv is not None else sys.argv[1:])
    try:
        spec_path, edited_path = Path(args[0]), Path(args[1])
        found = problems(gates.spec(spec_path), gates.read_regular(edited_path))
    except Exception as error:  # noqa: BLE001 - the gate must never trap a session
        print(f"the lines gate could not run: {error}", file=sys.stderr)
        return 0
    blocked = bool(found) and gates.round_left(spec_path)
    gates.log(spec_path, {"mode": "stop", "problems": found, "blocked": blocked})
    if blocked:
        gates.block(gates.reason(edited_path.name, found))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
