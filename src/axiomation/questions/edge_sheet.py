"""The edge sheet: the file the edges stage writes, and the
format of a hand-written key (a person's edges of a paper, a writer's
reference), so that a key and a writer's answer pass one check.

`render` writes a cut paper as a sheet with no edge yet: each block under a
line `⟦<handle> claim+definition⟧ <id> (<form>)` with its owned text, the
handle its first label or a slug of its first words (`handle_for`: stable
when the cut changes elsewhere, and reading as what it names), a `⟦+
<role>⟧` where a later segment starts, `⟦…⟧` between a block's runs, and
each group between `⟦group <handle> <kind>⟧` and `⟦end group <handle>⟧`.
The writer writes under each block, and after each group's end line, its
outgoing edges, the support of each claim the block makes, then a done
mark:

    → <kind> <target> [<part>, …] <mark> <flag> · "<evidence>" — <note>
    → self <partial> · "<its reason: the citation or the step>" — <note>
    ⊢ <status> <declaration> · "<the claim's words>" — <note>
    ⟦done b12⟧

The kinds are `uses`, `about`, `justifies` and `qualifies`:
the last an assumption's edge to the definition it constrains, from a block
with an assumption role to a block or a part of one, never a group, a scope
or a file. A target is a block or group handle, `scope:paper` (a claim
about the text over the whole paper), or `file:<path>`. A part names which
part of a target of several parts the edge relies on, by a legend line in
the header, `  b32 [C_n²]: "<the target's own words>"`; the line may end
in a role, `(definition)`, where the part's content plays another role than
its segment. The mark is `ref`, `name` or `position`, or absent. The flag
`where-used`, on an about edge only, says the source claims where the
target is used. The flag `generalizes`, on an about edge from a block
with an assumption role onto an assumption or a definition's content,
never beside `where-used`, says the source is a wider premise written
after the target, the target a case of it. The flag `assumed`, on a
uses edge onto `conjectured` claims from a claim's statement, says the
statement takes them as hypotheses ("assuming", "if", "under"): it
asserts the claim on that condition, never the conjecture (`→ uses
[<claim>, …] T assumed` on a block of several claims names those it is
a hypothesis for). A justification's own
words may say how completely it argues: `sketch` (the argument as written stops short of
the target: an outline, a heuristic, a special case with the rest declared
to follow) or `analogy` (another argument carried over, none of it
written, the one it names a uses edge from its block, a citation in it or
the group it sits in), one at most, on a justifies edge or a self mark
only. The flag `optional` and the legend role `(standard)` are a
hand key's notation: an edge the scorer counts neither a
miss nor an extra, and a gloss of standard notation.

A support line gives the standing of a claim the block makes:
`justified` by a justifies edge coming in, `self` by the block's own
reasons (a self mark quoting the reason, the citation or the step, never
the claim), `restated` through an about edge of its block naming it
(where the block holds several claims) onto a block holding the claim
it restates, `conjectured` so needing none, or `unsupported`. An about
line of a block of several claims that restates none of them says so,
`→ about [-] T`, so none of them restates through it. An
`unsupported` claim may carry what the paper declares of the argument it
does not give: `omitted` ("omitted", "left to the reader",
"standard", no argument named) or `promised` (said to be given elsewhere
in the paper, and nowhere given), its note quoting the declaring words.
Every block whose roles hold a claim has one or more. A support line on a block whose
roles hold none is taken as well, and adds the claim role to the block
(`standings_without_claim`). The ambient frame
is a header line `Pervasive: b24, b25`. The header lists the paper's
files an edge may target on its `Files:` line, and each code file on a
line of its own, `Code: <key> — <its opening line>`.

A cut line proposes a correction to the block it stands under, with the block's own
words that show it and a reason:

    ✂ split "<the words where the new block starts>" · "<evidence>" — <reason>
    ✂ merge <the next block> · "<evidence>" — <reason>
    ✂ move <the next block> "<the words where it starts>" · "<evidence>" — <reason>

A block takes one or more splits, or one merge or move; a merge of three
is a merge on each of the first two. The next block is the one after it
in the sheet, in the same file.

A flag line is a position on the cut or an earlier step,
under a block among its lines: the signal, `role`, `part`, `cut` or none,
the block's own words and what the writer read there. It changes nothing;
a later step reads it.

    ? role "<the block's words>" — <what it read>

An update shows a changed block's old text under its line, one
frame line per block of the old version it came from, `⟦was⟧ <text>`, and
under the edge that may carry a change to a block, the condition on which
that block is asked again:

    ⟦asked if <handle> [<part>]'s Δ is not unchanged or reworded⟧
    ⟦meaning moved by rule: <kind> [<part>] of <handle>, if its Δ is not …⟧

the second ending as the first does, `… unchanged or reworded⟧`.

The writer says how the edit changed what a block says, part by part, by a
Δ line among the block's lines, `<class>` one of `IMPACT_CLASSES`:

    Δ [<label>] <class> — <note>
    Δ <class> — <note>              for the block as a whole

A sheet without them reads as one with no Δ line; a hand-written key
never holds them.

`check` is the sheet's gate: every block and group done, every excerpt
verbatim in its source (whitespace aside), every target and part known,
one edge per source, kind and target, every claim block's support given
and consistent with its edges, every cut line placed, every flag line
under a block with its words and a note, and the sheet's blocks the
cut's, in order.

Plain text and data only: nothing here reads a store or talks to a model.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from axiomation import computations
from axiomation.records import RESTATES_NONE

KINDS = ("uses", "about", "justifies", "qualifies")
MARKS = ("ref", "name", "position")
FLAGS = ("where-used", "generalizes", "assumed", "optional")
#: How completely a justification's own words say it argues,
#: on a justifies edge or a self mark: `records.Partial`'s values as text.
PARTIALS = ("sketch", "analogy")
#: What the paper declares of an argument it does not give, on an
#: `unsupported` standing: `records.Declared`'s values.
DECLARATIONS = ("omitted", "promised")
STANDARD = "standard"  # a legend role: a gloss of standard notation
STATUSES = ("justified", "self", "restated", "conjectured", "unsupported")
ROLES = ("definition", "assumption", "claim", "justification", "motivation")
NONE = "none"  # the block line's roles of a block that holds none
SELF = "self"  # a self mark's kind on an edge line
CITATION = re.compile(r"\\\w*cite\w*")  # \cite, \citep, \textcite, \parencite, …
SCOPE_PAPER = "scope:paper"
FILE = "file:"
#: The line between the sheet's header and its blocks.
RULE = "---"

BLOCK = re.compile(r"^⟦(\S+) ([^⟧]*)⟧ (\S+) \((\w+)\)$")
GROUP = re.compile(r"^⟦group (\S+) (\w+)⟧$")
END = re.compile(r"^⟦end group (\S+)⟧$")
DONE = re.compile(r"^⟦done (\S+)⟧$")
SEGMENT = re.compile(r"⟦\+ \w+⟧ ?")
RUN_BREAK = "⟦…⟧"
#: An update's lines: how an edit changed what a block says,
#: `ImpactClass`'s values as text (this module reads no records); a
#: changed block's old text; the conditions under a reaching edge.
IMPACT_CLASSES = (
    "unchanged",
    "reworded",
    "narrowed",
    "widened",
    "strengthened",
    "weakened",
    "removed",
    "moved",
)
DELTA_MARK = "Δ"
DELTA = re.compile(
    r"^Δ(?: \[(?P<label>[^\]]+)\])? (?P<cls>[^\s—]+)(?: —(?: (?P<note>.*))?)?$"
)
DELTA_FORM = "Δ [<part>] <class> — <note>, or Δ <class> — <note> for the block"
WAS = "⟦was⟧ "
#: An open stale mark, shown under its owner on a kept
#: state's sheet: the store's, never written by an answer.
MARK = re.compile(r"^⟦stale since (?P<state>[^\s:]+): (?P<why>[^⟧]*)⟧$")
CONDITION = re.compile(
    r"^⟦(?:asked if (?P<asked>[^\s\[']+)(?: \[(?P<asked_parts>[^\]]+)\])?'s Δ"
    r"|meaning moved by rule: (?P<kind>\w+)(?: \[(?P<moved_parts>[^\]]+)\])? of "
    r"(?P<moved>\S+), if its Δ) is not unchanged or reworded⟧$"
)
LEGEND = re.compile(r'^  (\S+) \[([^\]]+)\]: "(.*?)"(?: \((\w+)\))?$')
#: a header line meant as a legend line that LEGEND does not read
LEGEND_LIKE = re.compile(r"^\s*`?(\S+?)`? ?\[([^\]]+)\]\s*:")
LEGEND_FORM = "two spaces, the target, [label], a colon and the words in double quotes"
#: A self mark's line takes a partial where an edge's takes its target, so
#: the target group never reads `sketch` or `analogy` there as one.
EDGE = re.compile(
    r"^→ (?P<kind>\w+)"
    r"(?: \[(?P<own>[^\]]+)\])?"
    r"(?: (?P<target>(?!·)(?!\[)(?!(?<=\] )(?:sketch|analogy)(?: |$))"
    r"(?!(?<=→ self )(?:sketch|analogy)(?: |$))\S+))?"
    r"(?: \[(?P<parts>[^\]]+)\])?"
    r"(?: (?P<mark>ref|name|position))?"
    r"(?P<flag>(?: (?:where-used|generalizes|assumed|optional|sketch|analogy))+)?"
    r" · \"(?P<evidence>.*?)\"(?: — (?P<note>.*))?$"
)
SUPPORT = re.compile(
    r"^⊢ (?P<status>\w+)(?: (?P<declared>[\w-]+))?(?: \[(?P<handle>[^\]\s]+)\])?"
    r" · \"(?P<claim>.*?)\""
    r"(?: — (?P<note>.*))?$"
)
#: A cut line's operations: split, merge and rebound (`move`).
CUT_OPS = ("split", "merge", "move")
CUT_MARK = "✂ "
CUT = re.compile(
    r"^✂ (?P<op>split|merge|move)(?: (?P<target>(?!\")[^\s·]\S*))?"
    r"(?: \"(?P<at>.*?)\")? · \"(?P<evidence>.*?)\"(?: — (?P<why>.*))?$"
)
CUT_FORM = '✂ split "<words>" · "<evidence>" — <reason>'
#: A flag line and the signals it may give, or none.
FLAG_MARK = "?"
FLAG_LINE = re.compile(
    r'^\?(?: (?P<signal>[^\s"]+))? "(?P<evidence>.*?)"(?: — (?P<note>.*))?$'
)
FLAG_FORM = '? <signal> "<the block\'s words>" — <what you read there>'
FLAG_SIGNALS = ("role", "part", "cut", "other")
#: A person's keep mark before an edge or standing line: the
#: line holds as it stands, by the person. Only a person's file holds it.
KEEP_MARK = "= "
SCOPE = re.compile(r"^scope:paper$")
#: a header line naming the ambient frame's blocks
PERVASIVE = re.compile(r"^Pervasive: (.*)$")
FILES = re.compile(r"^Files: (.*)$")
#: a header line naming a code file and its opening line
CODE = re.compile(r"^Code: (\S+)(?: — (.*))?$")


class CodeFile(str):
    """A code file's key, as a `file:` target names it, carrying its opening
    line, which the sheet's `Code:` line shows; equal to its
    key wherever a file is looked up."""

    opening: str

    def __new__(cls, key: str, opening: str = "") -> CodeFile:
        made = super().__new__(cls, key)
        made.opening = opening
        return made


def unkept(line: str) -> tuple[str, bool]:
    """A line without a person's keep mark, and whether it held one: an
    edge or standing line read as it stands."""
    rest = line.removeprefix(KEEP_MARK)
    if rest != line and rest.startswith(("→ ", "⊢ ")):
        return rest, True
    return line, False


def norm(text: str) -> str:
    """`text` with every run of whitespace one space, and none at the ends."""
    return " ".join(text.split())


# --- handles --------------------------------------------------------------------


def handle_for(body: str, labels: Sequence[str] = ()) -> str:
    """A block's handle from its own words, stable under cut changes: its
    first label (from the paper's text within its extent, since a label sits
    in a gap the block does not own), else a slug of its first four words
    with LaTeX stripped (a proof's bracket title first); `handles` makes
    them unique in order."""
    if labels:
        return label_handle(labels[0])
    text = SEGMENT.sub("", body).replace(RUN_BREAK, " ")
    text = re.sub(r"\\label\{[^}]*\}", " ", text)
    text = re.sub(
        r"\\begin\{\w+\*?\}(?:\[([^\]]*)\])?", lambda m: m.group(1) or " ", text
    )
    text = re.sub(r"\\end\{\w+\*?\}", " ", text)
    text = re.sub(
        r"\\(?:ref|eqref|autocite|textcite|cite|citep|citet)\{([^}]+)\}", r"\1", text
    )
    text = re.sub(r"\\[A-Za-z]+", " ", text)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    words = re.findall(r"[A-Za-z0-9]+(?:[-:_.][A-Za-z0-9]+)*", text)
    slug = "-".join(w.lower() for w in words[:4])
    return slug or "block"


def handles(
    bodies: Sequence[str], labels: Sequence[Sequence[str]] | None = None
) -> list[str]:
    """`handle_for` over blocks in order, a numeric suffix on a repeat."""
    seen: dict[str, int] = {}
    out = []
    for body, found in zip(bodies, labels or [[] for _ in bodies], strict=True):
        h = handle_for(body, found)
        seen[h] = seen.get(h, 0) + 1
        out.append(h if seen[h] == 1 else f"{h}-{seen[h]}")
    return out


def free_handle(name: str, taken: Callable[[str], bool]) -> str:
    """`name`, else it with the first suffix `-2`, `-3`, … not `taken`."""
    if not taken(name):
        return name
    n = 2
    while taken(f"{name}-{n}"):
        n += 1
    return f"{name}-{n}"


def label_handle(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9:_.-]+", "-", label)


def group_handle(first_block_handle: str, label: str | None = None) -> str:
    """A group's handle: its own label (`group_label`), else `group:` and
    its first block's handle, so a group handle never reads as a block's."""
    return label_handle(label) if label else f"group:{first_block_handle}"


#: a label right after the `\\begin` (and bracket title) opening an extent
ENV_LABEL = re.compile(r"\s*\\begin\{\w+\*?\}(?:\[[^\]]*\])?\s*\\label\{([^}]+)\}")
LABEL = re.compile(r"\\label\{([^}]+)\}")


def group_label(first_extent: str) -> str | None:
    """A group's label, from its first block's extent text: the `\\label`
    right after the `\\begin` that opens the group's environment, which the
    author uses for the environment as a whole (`\\pageref{proof:X}` for a
    proof), though it lies in the first block's extent."""
    m = ENV_LABEL.match(first_extent)
    return m.group(1) if m else None


def labels_in(extent: str, group_owned: str | None = None) -> list[str]:
    """A block's `\\label`s, read from the paper's text over its extent
    (gaps included); `group_owned`, the label of the group it opens, is the
    group's, not the block's."""
    return [label for label in LABEL.findall(extent) if label != group_owned]


def paper_files(directory: Path, never_included: Sequence[str] = ()) -> list[str]:
    """The paper's files by their names on disk, as a `file:` target names
    them; what the reader never includes (a licence note) left out."""
    never = set(never_included)
    return sorted(
        p.name
        for p in Path(directory).iterdir()
        if p.is_file() and not p.name.startswith(".") and p.name not in never
    )


def sheet_files(directory: Path, never_included: Sequence[str] = ()) -> list[str]:
    """The files a sheet shows: `paper_files` but code, then each
    of the paper's code files (`computations.code_files`), a `CodeFile`
    with its opening line."""
    directory = Path(directory)
    code = computations.code_files(directory)
    plain = [
        name
        for name in paper_files(directory, never_included)
        if not computations.is_code(name)
    ]
    return plain + [
        CodeFile(key, computations.opening_line(path.read_text(errors="replace")))
        for key, path in sorted(code.items())
    ]


# --- the render -------------------------------------------------------------------


@dataclass(frozen=True)
class SheetBlock:
    """A block as the sheet shows it: its id and form, its owned runs'
    text, its segments as `(starts, role)` in order (the first `starts`
    empty; a role `none` for a part that plays none), and its labels.
    `roles` names the block line's roles only where it has no segments."""

    id: str
    form: str
    runs: tuple[str, ...]
    segments: tuple[tuple[str, str], ...] = ()
    labels: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    #: its handle where it is given, not minted from its words
    handle: str | None = None

    @property
    def role_names(self) -> str:
        found = dict.fromkeys(role for _, role in self.segments) or dict.fromkeys(
            self.roles
        )
        return "+".join(found) or NONE


@dataclass(frozen=True)
class SheetGroup:
    """A group: its kind, its blocks' ids in order, and its own label."""

    kind: str
    blocks: tuple[str, ...]
    label: str | None = None
    #: its handle where it is given, not minted from its first block's
    handle: str | None = None


class RenderError(ValueError):
    """A cut the sheet cannot show: a segment's start that does not place
    once in its block's text."""


def block_handles(blocks: Sequence[SheetBlock]) -> dict[str, str]:
    """Each block's handle, by id: its given one, else minted from its
    words."""
    if all(b.handle for b in blocks):
        return {b.id: b.handle for b in blocks if b.handle}
    bodies = [f"\n{RUN_BREAK}\n".join(b.runs) for b in blocks]
    found = handles(bodies, [list(b.labels) for b in blocks])
    return {b.id: b.handle or h for b, h in zip(blocks, found, strict=True)}


def group_handles(groups: Sequence[SheetGroup], by_block: dict[str, str]) -> list[str]:
    """Each group's handle, in order."""
    return [g.handle or group_handle(by_block[g.blocks[0]], g.label) for g in groups]


def render(
    blocks: Sequence[SheetBlock],
    groups: Sequence[SheetGroup],
    files: Sequence[str],
    header: str,
) -> str:
    """The sheet, no edge written yet: `header` (its lines up to the
    `Files:` line, ending in a blank line), the paper's files, the rule,
    then every block and group."""
    by_block = block_handles(blocks)
    named = list(zip(groups, group_handles(groups, by_block), strict=True))
    starts = {g.blocks[0]: (g, name) for g, name in named}
    ends = {g.blocks[-1]: name for g, name in named}
    plain = [f for f in files if not computations.is_code(f)]
    code = [
        f"Code: {f}" + (f" — {f.opening}" if getattr(f, "opening", "") else "")
        for f in files
        if computations.is_code(f)
    ]
    lines = [*header.split("\n"), f"Files: {', '.join(plain)}", *code, "", RULE, ""]
    for b in blocks:
        if b.id in starts:
            g, name = starts[b.id]
            lines += [f"⟦group {name} {g.kind}⟧", ""]
        body = f"\n{RUN_BREAK}\n".join(b.runs)
        # every start placed on the unmarked text: one part's excerpt may run
        # past the next part's start, and a marker inside it would break it
        places = []
        for excerpt, role in b.segments[1:]:
            pattern = re.compile(r"\s+".join(re.escape(w) for w in excerpt.split()))
            matches = list(pattern.finditer(body))
            if len(matches) != 1:
                raise RenderError(
                    f"{b.id}: the segment start {excerpt!r} places "
                    f"{len(matches)} times, not once"
                )
            places.append((matches[0].start(), role))
        for at, role in reversed(sorted(places, key=lambda p: p[0])):
            body = body[:at] + f"⟦+ {role}⟧ " + body[at:]
        lines += [
            f"⟦{by_block[b.id]} {b.role_names}⟧ {b.id} ({b.form})",
            body.strip("\n"),
            "",
        ]
        if b.id in ends:
            lines += [f"⟦end group {ends[b.id]}⟧", ""]
    return "\n".join(lines)


# --- reading ----------------------------------------------------------------------


@dataclass(frozen=True)
class Edge:
    source: str
    kind: str  # uses | about | justifies | qualifies | self
    target: str
    parts: tuple[str, ...]
    mark: str
    evidence: str
    note: str
    flag: str = ""  # FLAGS and PARTIALS, space-separated
    #: where it stands in the sheet, for a problem's message
    line: int = field(default=0, compare=False)
    #: the source block's own claims it names, by handle: a self mark's
    #: that it supports, an about edge's that restate, an assumed uses
    #: edge's that take its target as a hypothesis
    own: tuple[str, ...] = ()

    @property
    def where_used(self) -> bool:
        return "where-used" in self.flag.split()

    @property
    def generalizes(self) -> bool:
        return "generalizes" in self.flag.split()

    @property
    def assumed(self) -> bool:
        return "assumed" in self.flag.split()

    @property
    def optional(self) -> bool:
        return "optional" in self.flag.split()

    @property
    def partials(self) -> list[str]:
        """The `PARTIALS` it carries: one at most passes the check."""
        return [f for f in self.flag.split() if f in PARTIALS]

    @property
    def partial(self) -> str:
        """Its `sketch` or `analogy`, else empty."""
        return next(iter(self.partials), "")


@dataclass(frozen=True)
class Support:
    source: str
    status: str
    claim: str
    note: str
    line: int = field(default=0, compare=False)
    #: what the paper declares of the argument it does not give, else empty
    declared: str = ""
    #: the handle its writer gave the claim, else empty (`claim_handles`)
    handle: str = ""


@dataclass(frozen=True)
class CutLine:
    """A proposed correction to the cut: `source` the block it
    stands under, `target` the next block for a merge or a move, `at` the
    words where a split's new block or a move's next block starts."""

    source: str
    op: str  # CUT_OPS
    target: str
    at: str
    evidence: str
    why: str
    line: int = field(default=0, compare=False)


@dataclass(frozen=True)
class FlagLine:
    """A flag line: `source` the block it stands under, its
    signal word (empty for none), the block's words and the note."""

    source: str
    signal: str
    evidence: str
    note: str
    line: int = field(default=0, compare=False)


@dataclass(frozen=True)
class DeltaLine:
    """A Δ line: `source` the block it stands under, `label` the
    legend part it is for (empty for the block as a whole), its class and
    note."""

    source: str
    label: str
    cls: str
    note: str
    line: int = field(default=0, compare=False)


@dataclass(frozen=True)
class Condition:
    """A condition line of an update, under the edge that may
    carry a change to the block it is owned by: whose Δ (`handle`, `parts`)
    decides it, and whether the rule moves the block's meaning (`moved`)
    or asks it. `text` is the line."""

    owner: str
    text: str
    handle: str
    parts: tuple[str, ...]
    moved: bool
    line: int = field(default=0, compare=False)


def delta_line(label: str, cls: str, note: str) -> str:
    """A Δ line, as a writer writes it."""
    named = f" [{label}]" if label else ""
    return f"{DELTA_MARK}{named} {cls} — {note}"


def mark_line(state: str, why: str) -> str:
    """An open stale mark's line under its owner, `why` without
    the brackets a line ends at."""
    return f"⟦stale since {state}: {why.replace('⟧', ')')}⟧"


def asked_if(handle: str, parts: Sequence[str] = ()) -> str:
    """The condition line of a block the rule asks."""
    named = f" [{', '.join(parts)}]" if parts else ""
    return f"⟦asked if {handle}{named}'s Δ is not unchanged or reworded⟧"


def moved_by_rule(kind: str, handle: str, parts: Sequence[str] = ()) -> str:
    """The condition line of a block whose meaning the rule moves."""
    named = f" [{', '.join(parts)}]" if parts else ""
    return (
        f"⟦meaning moved by rule: {kind}{named} of {handle}, if its Δ is not "
        "unchanged or reworded⟧"
    )


@dataclass
class Sheet:
    block_ids: list[str] = field(default_factory=list)
    roles: dict[str, tuple[str, ...]] = field(default_factory=dict)
    texts: dict[str, str] = field(default_factory=dict)
    groups: dict[str, list[str]] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    supports: list[Support] = field(default_factory=list)
    cuts: list[CutLine] = field(default_factory=list)
    flag_lines: list[FlagLine] = field(default_factory=list)
    deltas: list[DeltaLine] = field(default_factory=list)
    #: A changed block's old texts, by handle.
    was: dict[str, list[str]] = field(default_factory=dict)
    conditions: list[Condition] = field(default_factory=list)
    done: list[str] = field(default_factory=list)
    #: The lines a person's keep mark holds, `(owner, line)`, the line
    #: without its mark.
    kept: list[tuple[str, str]] = field(default_factory=list)
    #: The stale mark lines, `(owner, line)`.
    marks: list[tuple[str, str]] = field(default_factory=list)
    legend: dict[tuple[str, str], str] = field(default_factory=dict)
    legend_roles: dict[tuple[str, str], str] = field(default_factory=dict)
    bodies: dict[str, str] = field(default_factory=dict)  # with ⟦+ role⟧ markers
    pervasive: tuple[str, ...] = ()  # the ambient frame's blocks, by the header
    files: tuple[str, ...] | None = None  # the header's `Files:` and `Code:` lines
    problems: list[str] = field(default_factory=list)

    @property
    def block_handles(self) -> list[str]:
        """The blocks' handles in order, groups left out."""
        return [h for h in self.texts if h not in self.groups]


def parse(text: str) -> Sheet:
    sheet = Sheet()
    body: dict[str, list[str]] = {}
    current: str | None = None  # the block whose text is being read
    owner: str | None = None  # the block or group whose edges follow
    open_groups: list[str] = []
    in_header = True  # before the first block or group line
    for number, line in enumerate(text.split("\n"), 1):
        if in_header and (BLOCK.match(line) or GROUP.match(line)):
            in_header = False
        line, kept = unkept(line)
        if kept and owner is not None:
            sheet.kept.append((owner, line))
        if m := PERVASIVE.match(line):
            sheet.pervasive = tuple(m.group(1).replace(",", " ").split())
        elif in_header and (m := FILES.match(line)):
            named = tuple(f.strip() for f in m.group(1).split(",") if f.strip())
            sheet.files = named + (sheet.files or ())
        elif in_header and (m := CODE.match(line)):
            sheet.files = (*(sheet.files or ()), CodeFile(m.group(1), m.group(2) or ""))
        elif m := LEGEND.match(line):
            sheet.legend[(m.group(1), m.group(2))] = m.group(3)
            if m.group(4):
                sheet.legend_roles[(m.group(1), m.group(2))] = m.group(4)
        elif in_header and (m := LEGEND_LIKE.match(line)):
            sheet.problems.append(
                f"line {number}: not read as a legend line, whose form is "
                f"{LEGEND_FORM}: '  {m.group(1)} [{m.group(2)}]: \"<words>\"'"
            )
        elif m := BLOCK.match(line):
            handle = m.group(1)
            current = owner = handle
            sheet.block_ids.append(m.group(3))
            sheet.roles[handle] = tuple(m.group(2).split("+"))
            body[handle] = []
            for g in open_groups:
                sheet.groups[g].append(handle)
        elif m := GROUP.match(line):
            current = owner = None
            open_groups.append(m.group(1))
            sheet.groups[m.group(1)] = []
        elif m := END.match(line):
            current = None
            owner = m.group(1)
            if m.group(1) in open_groups:
                open_groups.remove(m.group(1))
        elif line.startswith("→ "):
            current = None
            m = EDGE.match(line)
            if not m or owner is None:
                sheet.problems.append(f"line {number}: not an edge line: {line!r}")
                continue
            kind, target = m.group("kind"), m.group("target") or ""
            sheet.edges.append(
                Edge(
                    owner,
                    kind,
                    target,
                    _tokens(m.group("parts")),
                    m.group("mark") or "",
                    m.group("evidence"),
                    m.group("note") or "",
                    (m.group("flag") or "").strip(),
                    number,
                    _tokens(m.group("own")),
                )
            )
        elif line.startswith("⊢ "):
            current = None
            m = SUPPORT.match(line)
            if not m or owner is None:
                sheet.problems.append(f"line {number}: not a support line: {line!r}")
                continue
            sheet.supports.append(
                Support(
                    owner,
                    m.group("status"),
                    m.group("claim"),
                    m.group("note") or "",
                    number,
                    m.group("declared") or "",
                    m.group("handle") or "",
                )
            )
        elif line.startswith(CUT_MARK):
            current = None
            m = CUT.match(line)
            if not m or owner is None:
                sheet.problems.append(
                    f"line {number}: not a cut line, whose form is {CUT_FORM}, "
                    f'or merge <next block>, or move <next block> "<words>": '
                    f"{line!r}"
                )
                continue
            sheet.cuts.append(
                CutLine(
                    owner,
                    m.group("op"),
                    m.group("target") or "",
                    m.group("at") or "",
                    m.group("evidence"),
                    (m.group("why") or "").strip(),
                    number,
                )
            )
        elif line == FLAG_MARK or line.startswith(f"{FLAG_MARK} "):
            current = None
            m = FLAG_LINE.match(line)
            if not m or owner is None:
                sheet.problems.append(
                    f"line {number}: not a flag line, whose form is {FLAG_FORM}: "
                    f"{line!r}"
                )
                continue
            sheet.flag_lines.append(
                FlagLine(
                    owner,
                    m.group("signal") or "",
                    m.group("evidence"),
                    (m.group("note") or "").strip(),
                    number,
                )
            )
        elif line.startswith(WAS):
            # under a block's line, before its text: never the text itself
            if owner is None or owner in sheet.groups or owner not in body:
                sheet.problems.append(
                    f"line {number}: a ⟦was⟧ line stands under a block's line: {line!r}"
                )
                continue
            sheet.was.setdefault(owner, []).append(line.removeprefix(WAS))
        elif MARK.match(line):
            current = None
            if owner is None:
                sheet.problems.append(
                    f"line {number}: a stale mark under nothing: {line!r}"
                )
                continue
            sheet.marks.append((owner, line))
        elif m := CONDITION.match(line):
            if owner is None:
                sheet.problems.append(
                    f"line {number}: a condition line under nothing: {line!r}"
                )
                continue
            moved = m.group("moved") is not None
            parts = m.group("moved_parts" if moved else "asked_parts") or ""
            sheet.conditions.append(
                Condition(
                    owner,
                    line,
                    m.group("moved") if moved else m.group("asked"),
                    tuple(p.strip() for p in parts.split(",") if p.strip()),
                    moved,
                    number,
                )
            )
        elif line == DELTA_MARK or line.startswith(f"{DELTA_MARK} "):
            current = None
            m = DELTA.match(line)
            if not m or owner is None:
                sheet.problems.append(
                    f"line {number}: not a Δ line, whose form is {DELTA_FORM}: {line!r}"
                )
                continue
            sheet.deltas.append(
                DeltaLine(
                    owner,
                    (m.group("label") or "").strip(),
                    m.group("cls"),
                    (m.group("note") or "").strip(),
                    number,
                )
            )
        elif m := DONE.match(line):
            current = None
            sheet.done.append(m.group(1))
        elif current is not None:
            body[current].append(line)
    sheet.bodies = {h: "\n".join(lines) for h, lines in body.items()}
    sheet.texts = {
        h: norm(SEGMENT.sub("", "\n".join(lines))) for h, lines in body.items()
    }
    for g, members in sheet.groups.items():
        sheet.texts[g] = norm(" ".join(sheet.texts[b] for b in members))
    return sheet


def renamed(text: str, onto: Mapping[str, str]) -> str:
    """`text`, a sheet, with each handle `onto` names renamed where a line
    names a block or a group by it: its block, group, end and done lines,
    an edge's and a cut line's target, a legend line's, a condition's and
    the `Pervasive:` line; quoted words, texts and a part's label as they
    are. A handle `onto` does not name stays."""

    def named(line: str, m: re.Match[str], *groups: str | int) -> str:
        spans = sorted(
            (m.span(g) for g in groups if m.group(g) is not None), reverse=True
        )
        for start, end in spans:
            line = (
                line[:start] + onto.get(line[start:end], line[start:end]) + line[end:]
            )
        return line

    lines = []
    for line in text.split("\n"):
        bare, kept = unkept(line)
        if m := PERVASIVE.match(bare):
            names = re.sub(
                r"[^\s,]+", lambda n: onto.get(n.group(), n.group()), m.group(1)
            )
            bare = f"Pervasive: {names}"
        elif m := (
            LEGEND.match(bare)
            or BLOCK.match(bare)
            or GROUP.match(bare)
            or END.match(bare)
            or DONE.match(bare)
        ):
            bare = named(bare, m, 1)
        elif m := EDGE.match(bare) or CUT.match(bare):
            bare = named(bare, m, "target")
        elif m := CONDITION.match(bare):
            bare = named(bare, m, "asked", "moved")
        lines.append(f"{KEEP_MARK}{bare}" if kept else bare)
    return "\n".join(lines)


def _tokens(bracket: str | None) -> tuple[str, ...]:
    return tuple(p.strip() for p in (bracket or "").split(",") if p.strip())


def claim_handles(sheet: Sheet, block: str) -> dict[str, str]:
    """Each claim's handle on `block`, to its words (`standings_by_handle`)."""
    return {h: s.claim for h, s in standings_by_handle(sheet, block).items()}


def standings_by_handle(sheet: Sheet, block: str) -> dict[str, Support]:
    """Each claim's handle on `block`, to its standing, in the sheet's
    order: the handle its ⊢ line defines, this sheet's name
    for it, else one minted from its words (`handle_for`), a numeric suffix
    where it would repeat a defined handle or one minted before it. A
    handle a sheet once showed stays on its ⊢ line as text, so a carried
    sheet keeps it and only a ⊢ line without one is minted; a reference
    is resolved to the claim's words, so a handle is never an identity. A
    legend label never moves one: the gate refuses a label equal to a
    handle instead. A defined handle two claims share resolves to the
    first; the gate refuses it."""
    held = [s for s in sheet.supports if s.source == block]
    taken = {s.handle for s in held if s.handle}
    found: dict[str, Support] = {}
    for s in held:
        handle = s.handle
        if not handle:
            minted = handle = handle_for(s.claim)
            n = 1
            while handle in taken:
                n += 1
                handle = f"{minted}-{n}"
            taken.add(handle)
        found.setdefault(handle, s)
    return found


def shown_handles(sheet: Sheet) -> dict[int, str]:
    """The handle each ⊢ line shows, by its line: the one it defines, and
    on a block of two or more claims the minted one of each other, as a prepared sheet
    shows them (`with_handles`)."""
    found: dict[int, str] = {}
    for block in dict.fromkeys(s.source for s in sheet.supports):
        held = standings_by_handle(sheet, block)
        found |= {s.line: h for h, s in held.items() if s.handle or len(held) > 1}
    return found


def with_handles(text: str) -> str:
    """`text` with each ⊢ line that defines no handle showing its minted
    one, on the blocks holding two or more claims, where a line may need
    to name one: the product writes them on every sheet
    it prepares for a later pass, so that pass names a claim without
    defining a handle. Deterministic, as a question's key hashes its
    sheet; a sheet with nothing to mint is returned as it is."""
    sheet = parse(text)
    defined = {s.line for s in sheet.supports if s.handle}
    minted = {n: h for n, h in shown_handles(sheet).items() if n not in defined}
    if not minted:
        return text
    lines = text.split("\n")
    for number, h in minted.items():
        line = lines[number - 1]
        # a person's kept line is held as it stands
        m = None if line.startswith(KEEP_MARK) else SUPPORT.match(line)
        if m is None:
            continue
        at = m.end("declared") if m.group("declared") else m.end("status")
        lines[number - 1] = f"{line[:at]} [{h}]{line[at:]}"
    return "\n".join(lines)


def named_claims(sheet: Sheet, block: str, tokens: Sequence[str]) -> tuple[str, ...]:
    """The words of `block`'s claims that `tokens` name by handle, in the
    tokens' order; a legend label or an unknown token names none."""
    held = claim_handles(sheet, block)
    return tuple(held[t] for t in tokens if t in held)


def legend_labels(sheet: Sheet, tokens: Sequence[str], target: str) -> tuple[str, ...]:
    """The tokens that are not a claim handle of `target`: its legend
    labels (or, where the gate refused one, an unknown token)."""
    held = claim_handles(sheet, target)
    return tuple(t for t in tokens if t not in held)


def check(
    text: str,
    cut_ids: list[str] | None = None,
    within: dict[str, str] | None = None,
    gone: Collection[tuple[str, str]] = (),
    claims_fixed: Collection[str] = (),
) -> list[str]:
    """What is wrong with a sheet; empty when it passes. `within` names, by
    handle, the block each nested block (a footnote) lies in, which a cut
    line's next block passes over. `gone` names, by handle and label, the
    parts an update's edit removed: a Δ line, or an edge the update's gate
    holds fixed, may name one the legend no longer holds.
    `claims_fixed` names the blocks whose roles a person gave: a support line adds no
    claim role there."""
    sheet = parse(text)
    problems = list(sheet.problems)
    handles = set(sheet.texts)
    if cut_ids is not None and sheet.block_ids != cut_ids:
        problems.append(
            "the sheet's blocks are not the cut's, in order: render it again"
        )
    for h in sheet.pervasive:
        if h not in handles or h in sheet.groups:
            problems.append(f"Pervasive: {h} is not a block of the sheet")
    missing = [h for h in sheet.texts if h not in sheet.done]
    if missing:
        problems += [f"not done: {h}" for h in missing]
    for (target, label), words in sheet.legend.items():
        if target not in handles or not quoted(sheet, target, words):
            problems.append(
                f"part {target} [{label}]: not verbatim in {target}"
                + _spans(sheet, target, words)
            )
    for (target, label), role in sheet.legend_roles.items():
        if role not in ROLES and role != STANDARD:
            problems.append(f"part {target} [{label}]: no such role {role!r}")
    problems += _handle_problems(sheet)
    seen: dict[tuple[str, str, str], list[frozenset[str]]] = {}
    for e in sheet.edges:
        where = f"{e.source} → {e.kind} {e.target}".rstrip()
        problems += _named_problems(sheet, e, where)
        if e.kind == SELF:
            if e.target or e.parts or e.mark or set(e.flag.split()) - set(PARTIALS):
                problems.append(f"{where}: a self mark has no target, part or mark")
        elif e.kind not in KINDS:
            problems.append(f"{where}: no such kind")
        elif e.target in handles:
            if e.target == e.source:
                problems.append(f"{where}: an edge inside its own block")
        elif not SCOPE.match(e.target) and not e.target.startswith(FILE):
            problems.append(f"{where}: no such target")
        if e.kind in ("uses", "about") and e.source in sheet.groups:
            problems.append(f"{where}: a group is a source for justifies only")
        if e.kind == "qualifies":
            if e.source in sheet.groups or e.target in sheet.groups:
                problems.append(
                    f"{where}: qualifies runs between blocks, never a group"
                )
            elif e.target not in handles:
                problems.append(f"{where}: qualifies targets a block or a part of one")
            if "assumption" not in sheet.roles.get(e.source, ()):
                problems.append(
                    f"{where}: qualifies runs from a block with an assumption role"
                )
        if e.where_used and e.kind != "about":
            problems.append(f"{where}: where-used marks an about edge only")
        if e.generalizes:
            problems += _generalizes_problems(sheet, e, where)
        if e.assumed:
            problems += _assumed_problems(sheet, e, where)
        problems += _partial_problems(sheet, e, where)
        handles_there = claim_handles(sheet, e.target)
        for label in e.parts:
            # a gone part a fixed line may still name: its Δ decides the line
            if label in handles_there:
                continue
            if (e.target, label) not in sheet.legend and (e.target, label) not in gone:
                problems.append(
                    f"{where}: no part [{label}] in the legend (a header line, "
                    f"{LEGEND_FORM}: '  {e.target} [{label}]: \"<words>\"')"
                    + _handles_named(handles_there)
                )
        if not norm(e.evidence):
            problems.append(f"{where}: the evidence is empty: quote the source")
        elif not quoted(sheet, e.source, e.evidence):
            problems.append(
                f"{where}: evidence not in {e.source}: {e.evidence!r}"
                + _spans(sheet, e.source, e.evidence)
            )
        key = (e.source, e.kind, e.target)
        named = frozenset(e.parts)
        if e.kind != SELF and key in seen and not _apart(e.kind, named, seen[key]):
            problems.append(
                f"{where}: a second edge for one source, kind and target"
                + (
                    " (two justifies lines name disjoint claims of the target)"
                    if e.kind == "justifies"
                    else ""
                )
            )
        seen.setdefault(key, []).append(named)
    problems += _support_problems(sheet, claims_fixed)
    problems += _cut_problems(sheet, within or {})
    problems += _flag_problems(sheet)
    problems += _delta_problems(sheet, gone)
    return problems


def _handles_named(held: Mapping[str, str]) -> str:
    """Where the claims' handles stand, never which they are: a problem
    says nothing a line elsewhere on its block changes (`edges_file.
    problem_key`)."""
    return ", nor a claim's handle (its ⊢ lines show them)" if held else ""


#: what each line's own bracket, and a justifies line's target bracket,
#: names: the standing it may name there
_OWN_STATUS = {SELF: "self", "about": "restated"}


def _handle_problems(sheet: Sheet) -> list[str]:
    """The claims' handles: two claims of a block never
    share their words, a claim's identity being its block and its words;
    a handle a ⊢ line defines names one claim of its block, and no handle
    is a legend label there, so a bracket resolves one way; no legend part
    has the role `claim`, a claim being named by its handle."""
    problems = []
    for block in dict.fromkeys(s.source for s in sheet.supports):
        quotes = [norm(s.claim) for s in sheet.supports if s.source == block]
        for words in dict.fromkeys(w for w in quotes if w and quotes.count(w) > 1):
            problems.append(
                f"{block} ⊢ {words!r}: two claims of a block quote the same words, so "
                "no reader tells them apart: a standing's words are the quote that "
                "finds this claim in its sentence, not the sentence: quote what "
                "singles each reading out"
            )
        defined = [s.handle for s in sheet.supports if s.source == block and s.handle]
        for h in dict.fromkeys(h for h in defined if defined.count(h) > 1):
            problems.append(f"{block} ⊢ [{h}]: a handle names one claim of its block")
        for h in claim_handles(sheet, block):
            if (block, h) in sheet.legend:
                problems.append(
                    f"{block} [{h}]: a legend label and a claim's handle: rename the "
                    "label"
                )
    for target, label in sheet.legend:
        if (
            sheet.legend_roles.get((target, label)) == STANDARD
            or part_role(sheet, target, label) != "claim"
        ):
            continue
        problems.append(
            f"part {target} [{label}]: a claim is named by its handle on its ⊢ "
            "line, never by a part; a fragment relied "
            "on as content takes (definition), (assumption), (justification) or "
            "(motivation)"
        )
    return problems


def _named_problems(sheet: Sheet, e: Edge, where: str) -> list[str]:
    """The claims a line names by handle: its own
    bracket, on a self mark, an about edge or an `assumed` uses edge only,
    names claims of its block of the standing it may name (a self mark's
    `self`, an about edge's `restated`, an assumed edge's any); a justifies
    line's handles name `justified` claims of its target, and a justifies
    line naming parts names a claim."""
    problems = []
    if RESTATES_NONE in e.own:
        if e.kind != "about":
            problems.append(
                f"{where}: [{RESTATES_NONE}] says an about line restates none of its "
                "block's claims; no other line takes it"
            )
        if len(e.own) > 1:
            problems.append(
                f"{where}: [{RESTATES_NONE}] stands alone in its bracket: the line "
                "restates none of its block's claims, or names those it restates"
            )
    elif e.own:
        assumed = e.kind == "uses" and e.assumed
        status = _OWN_STATUS.get(e.kind)
        held = standings_by_handle(sheet, e.source)
        if status is None and not assumed:
            problems.append(
                f"{where}: only a self mark, an about edge or an assumed uses edge "
                "names claims of its own block, in the bracket after its kind"
            )
        for h in e.own if status or assumed else ():
            s = held.get(h)
            if s is None:
                problems.append(
                    f"{where}: [{h}] is no claim's handle on {e.source}"
                    + _handles_named({k: v.claim for k, v in held.items()})
                )
            elif status is not None and s.status != status:
                problems.append(
                    f"{where}: [{h}] is {s.status}; it names a {status} claim here"
                )
    if e.kind == "justifies" and e.parts:
        held = standings_by_handle(sheet, e.target)
        named = [held[t] for t in e.parts if t in held]
        for t in e.parts:
            if t in held and held[t].status != "justified":
                problems.append(
                    f"{where}: [{t}] is {held[t].status}; a justifies line names a "
                    "justified claim"
                )
        if not named:
            problems.append(
                f"{where}: names no claim: name the claim it argues by its handle"
                + (" (its ⊢ lines show them)" if held else "")
                + ", or name none"
            )
    return problems


def _delta_problems(sheet: Sheet, gone: Collection[tuple[str, str]] = ()) -> list[str]:
    """The Δ lines against the blocks: each under a block, its
    class one of `IMPACT_CLASSES`, its part one the legend gives the block
    (or one the edit removed, `gone`), a note given, one per part."""
    problems = []
    seen: set[tuple[str, str]] = set()
    for d in sheet.deltas:
        named = f" [{d.label}]" if d.label else ""
        where = f"line {d.line}: {d.source} Δ{named} {d.cls}"
        if d.source in sheet.groups:
            problems.append(f"{where}: a Δ line stands under a block, not a group")
            continue
        if d.cls not in IMPACT_CLASSES:
            problems.append(
                f"{where}: no such class: write one of {', '.join(IMPACT_CLASSES)}"
            )
        known = (d.source, d.label) in sheet.legend or (d.source, d.label) in gone
        if d.label and not known:
            problems.append(
                f"{where}: {d.source} holds no part [{d.label}] in the legend"
            )
        if not d.note:
            problems.append(f"{where}: say how its meaning changed, after ' — '")
        if (d.source, d.label) in seen:
            problems.append(f"{where}: a second Δ line for one part")
        seen.add((d.source, d.label))
    return problems


def _flag_problems(sheet: Sheet) -> list[str]:
    """The flag lines against the blocks: each under a block,
    its signal known, its words the block's and a note given."""
    problems = []
    for f in sheet.flag_lines:
        where = f"line {f.line}: {f.source} ? {f.signal}".rstrip()
        if f.source in sheet.groups:
            problems.append(f"{where}: a flag line stands under a block, not a group")
            continue
        if f.signal and f.signal not in FLAG_SIGNALS:
            problems.append(
                f"{where}: no such signal: write one of {', '.join(FLAG_SIGNALS)}, "
                "or no word"
            )
        if not norm(f.evidence) or not quoted(sheet, f.source, f.evidence):
            problems.append(
                f"{where}: the words are not in {f.source}: {f.evidence!r}"
                + _spans(sheet, f.source, f.evidence)
            )
        if not f.note:
            problems.append(f"{where}: say what you read there, after ' — '")
    return problems


def _cut_problems(sheet: Sheet, within: dict[str, str]) -> list[str]:
    """The cut lines against the blocks: each placed where its operation
    needs it, with its evidence and a reason. The next block
    passes over the blocks nested in the source (`within`), as the
    assembler does."""
    problems = []
    order = sheet.block_handles
    files = {
        h: block_id.rsplit(":", 2)[0]
        for h, block_id in zip(order, sheet.block_ids, strict=False)
    }
    ops: dict[str, list[str]] = {}
    for c in sheet.cuts:
        where = f"line {c.line}: {c.source} ✂ {c.op} {c.target}".rstrip()
        if c.source in sheet.groups:
            problems.append(f"{where}: a cut line stands under a block, not a group")
            continue
        ops.setdefault(c.source, []).append(c.op)
        changed = [c.source]
        if c.op == "split":
            if c.target:
                problems.append(f"{where}: a split names no block, only its words")
            problems += _cut_point(sheet, c.source, c.at, where, "split")
        else:
            index = order.index(c.source) if c.source in order else len(order)
            following = next(
                (h for h in order[index + 1 :] if within.get(h) != c.source), None
            )
            if following is None or files.get(following) != files.get(c.source):
                problems.append(
                    f"{where}: a {c.op} names the next block in the same file, "
                    f"and {c.source} is the last of its file"
                )
                continue
            if c.target != following:
                problems.append(
                    f"{where}: a {c.op} names the next block, here {following}"
                )
                continue
            across = next(
                (
                    g
                    for g, members in sheet.groups.items()
                    if (c.source in members) != (c.target in members)
                ),
                None,
            )
            if across is not None:
                problems.append(
                    f"{where}: {c.source} and {c.target} lie on either side of "
                    f"group {across}'s edge; a {c.op} cannot cross it"
                )
            changed.append(c.target)
            if c.op == "merge" and c.at:
                problems.append(f"{where}: a merge takes no words, only the block")
            if c.op == "move":
                problems += _move_point(sheet, c, where)
        placed = any(quoted(sheet, h, c.evidence) for h in changed)
        if not norm(c.evidence) or not placed:
            problems.append(
                f"{where}: evidence not in {' or '.join(changed)}: {c.evidence!r}"
            )
        if not c.why:
            problems.append(f"{where}: give the reason after ' — '")
    for h, found in ops.items():
        if len(found) > 1 and set(found) != {"split"}:
            problems.append(
                f"{h}: one or more splits, or one merge or move, on a block; "
                f"not {', '.join(found)}"
            )
    return problems


def _cut_point(sheet: Sheet, handle: str, words: str, where: str, op: str) -> list[str]:
    """Why `words` cannot place a cut point in `handle`: they must stand
    once in its text, and not at its start."""
    text, found = sheet.texts.get(handle, ""), norm(words)
    if not found:
        return [f'{where}: a {op} takes the words where the new block starts, "…"']
    count = text.count(found)
    if count != 1:
        return [
            f"{where}: the words stand {count} times in {handle}, not once: "
            "quote more of them"
        ]
    if text.startswith(found):
        return [f"{where}: the words are the start of {handle}: nothing to {op}"]
    if not quoted(sheet, handle, words):
        return [f"{where}: the words span a ⟦…⟧: quote within one run"]
    return []


def _move_point(sheet: Sheet, c: CutLine, where: str) -> list[str]:
    """Why a move's words cannot place the next block's new start: once in
    the two blocks, and neither block's start."""
    found = norm(c.at)
    if not found:
        return [f'{where}: a move takes the words where {c.target} would start, "…"']
    texts = [sheet.texts.get(c.source, ""), sheet.texts.get(c.target, "")]
    count = sum(t.count(found) for t in texts)
    if count != 1:
        return [
            f"{where}: the words stand {count} times in {c.source} and {c.target}, "
            "not once: quote more of them"
        ]
    if texts[1].startswith(found):
        return [f"{where}: {c.target} starts there already: nothing to move"]
    if texts[0].startswith(found):
        return [
            f"{where}: the words are the start of {c.source}, which would own nothing"
        ]
    if not (quoted(sheet, c.source, c.at) or quoted(sheet, c.target, c.at)):
        return [f"{where}: the words span a ⟦…⟧: quote within one run"]
    return []


def _pieces(sheet: Sheet, handle: str) -> list[str]:
    """The texts an excerpt from `handle` must lie within one of: each run
    of the block, or of each of the group's blocks. An excerpt is placed in
    one owned run (`exchange`'s export), so it never spans
    two blocks or a `⟦…⟧`."""
    members = sheet.groups.get(handle, [handle])
    return [
        norm(piece)
        for member in members
        for piece in sheet.texts.get(member, "").split(RUN_BREAK)
    ]


def quoted(sheet: Sheet, handle: str, words: str) -> bool:
    """Whether `words` stand, whitespace aside, within one piece of
    `handle`'s text (`_pieces`)."""
    found = norm(words)
    return any(found in piece for piece in _pieces(sheet, handle))


def _spans(sheet: Sheet, handle: str, words: str) -> str:
    """Why an excerpt found in the whole text is still refused."""
    if norm(words) and norm(words) in sheet.texts.get(handle, ""):
        return " (it spans two blocks or a ⟦…⟧: quote within one run of one block)"
    return ""


def _gives_reason(evidence: str, claim: str) -> bool:
    """Whether a self mark's evidence can be its claim's reason: it holds a
    citation command (`\\citep`, `\\textcite`, …), or it does not hold the
    whole claim. This tests the gross form only, a mark that gives the claim
    as its own reason, against each mark that supports the claim: one
    naming its claims is read against those alone. The
    rule is the brief's."""
    words = norm(claim).rstrip(".,;: ")
    if CITATION.search(evidence) or not words:
        return True
    return words not in norm(evidence)


def _apart(kind: str, named: frozenset[str], before: list[frozenset[str]]) -> bool:
    """Whether a justifies line may stand beside the lines from its source
    onto its target: each names claims, none of them shared, so each claim's
    completeness is read from its own line (`docs/model.md` § 3)."""
    return (
        kind == "justifies"
        and bool(named)
        and all(other and not (other & named) for other in before)
    )


def _generalizes_problems(sheet: Sheet, e: Edge, where: str) -> list[str]:
    """A `generalizes` flag's place: an about edge, never beside
    `where-used`, between premises: from a block with an assumption role
    and no claim, onto an assumption or a definition's content (a block
    of either role holding no claim, or the parts it names, each of
    either). An about edge from or onto a block holding a claim is about
    the claim (`docs/model.md` § 3)."""
    if e.kind != "about":
        return [f"{where}: generalizes marks an about edge only"]
    problems = []
    if e.where_used:
        problems.append(f"{where}: generalizes and where-used exclude each other")
    roles = sheet.roles.get(e.source, ())
    if "assumption" not in roles or "claim" in roles:
        problems.append(
            f"{where}: generalizes runs from a block with an assumption role "
            "and no claim"
        )
    premise = ("assumption", "definition")
    if e.target not in sheet.roles:
        held = False
    elif e.parts:
        claims = claim_handles(sheet, e.target)
        held = all(
            p not in claims and part_role(sheet, e.target, p) in premise
            for p in e.parts
        )
    else:
        roles = sheet.roles[e.target]
        held = bool(set(premise) & set(roles)) and "claim" not in roles
    if not held:
        problems.append(
            f"{where}: generalizes targets an assumption or a definition's content"
        )
    return problems


def _assumed_problems(sheet: Sheet, e: Edge, where: str) -> list[str]:
    """An `assumed` flag's place: a uses edge from a claim's statement,
    never a step of an argument (a member of a group a justifies edge runs
    from, or a block with a justification role one runs from: a block of
    claims alone that justifies another is a result, `docs/model.md` § 9),
    onto `conjectured` claims only, named where the target holds several,
    never content: where the paper asserts the target, "assuming T, y"
    relies on T as "by T, y" does. On a block of several claims it names
    those it is a hypothesis for."""
    if e.kind != "uses":
        return [f"{where}: assumed marks a uses edge only"]
    problems = []
    claims = standings_by_handle(sheet, e.source)
    if not claims:
        problems.append(
            f"{where}: assumed runs from a block holding a claim: the statement "
            "that takes the conjecture as a hypothesis"
        )
    elif len(claims) > 1 and not e.own:
        problems.append(
            f"{where}: assumed on a block of several claims names those it is a "
            "hypothesis for, in the bracket after its kind "
            "(`→ uses [<claim>] T assumed`)"
        )
    justifying = {x.source for x in sheet.edges if x.kind == "justifies"}
    steps = [g for g in justifying if g in sheet.groups]
    for g in steps:
        steps += [m for m in sheet.groups[g] if m in sheet.groups and m not in steps]
    writes = "justification" in sheet.roles.get(e.source, ())
    if (e.source in justifying and writes) or any(
        e.source in sheet.groups[g] for g in steps
    ):
        problems.append(
            f"{where}: assumed runs from a claim's statement, never a step of an "
            "argument: a hypothesis the statement leaves out is no hypothesis of "
            "the claim"
        )
    held = standings_by_handle(sheet, e.target) if e.target in sheet.roles else {}
    named = [held[p] for p in e.parts if p in held]
    relied = named if named else [] if e.parts else list(held.values())
    if (
        not relied
        or len(named) != len(e.parts)
        or any(s.status != "conjectured" for s in relied)
    ):
        problems.append(
            f"{where}: assumed targets `conjectured` claims only, never content: "
            "a claim the paper asserts is relied on, as written"
        )
    elif len(held) > 1 and not named:
        problems.append(
            f"{where}: assumed onto a block of several claims names the "
            "conjectures it takes (`→ uses T [<claim>] assumed`): naming none, "
            "it takes none"
        )
    return problems


def _partial_problems(sheet: Sheet, e: Edge, where: str) -> list[str]:
    """A `sketch` or `analogy`: on a justifies edge or a self
    mark only, one at most, and an `analogy` naming the argument it carries
    over (`docs/model.md` § 3): a uses edge from its block (or a group's
    member) onto an argument outside it (`_arguments`), a citation in its
    own quote, or its source's unflagged line beside it, onto the same
    target for another claim (a self mark: its block's), the argument the
    source writes out. A uses edge onto a result's statement, or a citation
    elsewhere in the block, names no argument carried over."""
    found = e.partials
    if not found:
        return []
    if e.kind not in ("justifies", SELF):
        return [f"{where}: {found[0]} marks a justifies edge or a self mark only"]
    if len(found) > 1:
        return [f"{where}: one of {' or '.join(PARTIALS)}, not {' and '.join(found)}"]
    if found[0] != "analogy":
        return []
    members = set(sheet.groups.get(e.source, [e.source]))
    # a group carries over no argument of its own: the uses edges among its
    # steps name none
    inside = members | {e.source} if e.source in sheet.groups else set()
    arguments = _arguments(sheet) - inside
    if any(
        x.kind == "uses" and x.source in members and _onto_argument(sheet, x, arguments)
        for x in sheet.edges
    ):
        return []
    if CITATION.search(e.evidence):
        return []
    if any(
        x.source == e.source
        and x.kind == e.kind
        and x.target == e.target
        and "analogy" not in x.partials
        for x in sheet.edges
    ):
        return []
    return [
        f"{where}: an analogy names the argument it carries over: a uses edge "
        "onto it (the proof it carries, never the result's statement; the "
        "group it closes, `→ uses <group>`), the citation in its own quote, "
        "or, where its source writes another claim's case out, that case's "
        "unflagged line beside it; else `omitted` on the claim's standing"
    ]


def _onto_argument(sheet: Sheet, x: Edge, arguments: set[str]) -> bool:
    """Whether a uses edge rests on an argument: its target holds one, and
    the parts it names, if any, include a claim or a justification; one
    naming only a definition's or an assumption's words relies on what
    they say, not on an argument."""
    if x.target not in arguments:
        return False
    claims = claim_handles(sheet, x.target)
    return not x.parts or any(
        label in claims
        or part_role(sheet, x.target, label) in ("claim", "justification")
        for label in x.parts
    )


def _arguments(sheet: Sheet) -> set[str]:
    """What holds an argument an analogy can carry over: a group, a block
    with a justification role, a justifies edge's source, a block carrying
    a self mark."""
    found = set(sheet.groups)
    found |= {b for b, roles in sheet.roles.items() if "justification" in roles}
    found |= {x.source for x in sheet.edges if x.kind in ("justifies", SELF)}
    return found


#: the line each standing needs, and what the gate says where none is
_SUPPORTED_BY = {
    "justified": ("justifies", "no justifies edge comes in"),
    "self": (SELF, "no self mark on the block"),
}


def _restates_a_claim(sheet: Sheet, e: Edge) -> bool:
    """Whether an about line relies on a claim of its target, as the
    export's reader reads it (`established._Graph._relied`): the claims it
    names; none where it names only parts; else every claim the target
    holds. A group, a file and `scope:paper` hold none."""
    held = claim_handles(sheet, e.target)
    return bool(held) and (not e.parts or any(p in held for p in e.parts))


def _support_problems(sheet: Sheet, claims_fixed: Collection[str] = ()) -> list[str]:
    """The standings against the edges. A sheet without any ⊢ line records
    no standings and is let through; once a sheet records one, it records every claim
    block's. A support line on a block with no claim role adds the role
    (`standings_without_claim`), but on a block in `claims_fixed`, whose
    roles a person gave."""
    if not sheet.supports:
        return []
    problems = []
    handles = {
        id(s): h
        for block in dict.fromkeys(s.source for s in sheet.supports)
        for h, s in standings_by_handle(sheet, block).items()
    }

    def lines(s: Support, kind: str) -> list[Edge]:
        if kind == "justifies":
            return [e for e in sheet.edges if e.kind == kind and e.target == s.source]
        return [e for e in sheet.edges if e.kind == kind and e.source == s.source]

    def missing(s: Support, kind: str, none: str) -> str | None:
        """Why no line of `kind` covers `s`: `none` where none is there,
        else that each names another claim."""
        if covering(s, kind):
            return None
        if not lines(s, kind):
            return none
        return f"{none} for it: each names another claim"

    def covering(s: Support, kind: str) -> list[Edge]:
        """The lines of `kind` that cover `s`: those
        naming its handle, and those naming no claim of its block."""
        if kind == "justifies":
            lines = [e for e in sheet.edges if e.kind == kind and e.target == s.source]
            held = claim_handles(sheet, s.source)
            named = [[t for t in e.parts if t in held] for e in lines]
        else:
            lines = [e for e in sheet.edges if e.kind == kind and e.source == s.source]
            named = [list(e.own) for e in lines]
        h = handles.get(id(s))
        return [e for e, n in zip(lines, named, strict=True) if not n or h in n]

    def restates_nothing(s: Support) -> str | None:
        """Why `s`, `restated`, borrows its support from no claim: an
        about line of its block must name it at its source end, or name
        none where the block holds one claim, and point at a claim
        (`docs/model.md` § 4). Its block's other about lines stand as
        descriptions."""
        about = [e for e in sheet.edges if e.kind == "about" and e.source == s.source]
        if not about:
            return "no about edge goes out"
        several = len(standings_by_handle(sheet, s.source)) > 1
        h = handles.get(id(s))
        own = [e for e in about if h in e.own or not (e.own or several)]
        if not own:
            if any(not e.own for e in about):
                return (
                    "its block holds several claims, and no about edge names this "
                    "one at its source end (`→ about [<handle>] T`)"
                )
            if any(RESTATES_NONE in e.own for e in about):
                return (
                    "no about edge goes out for it: each names another claim or "
                    f"restates none ([{RESTATES_NONE}])"
                )
            return "no about edge goes out for it: each names another claim"
        if not any(_restates_a_claim(sheet, e) for e in own):
            return (
                "no about edge of it points at a claim: a definition, an "
                "assumption, a heading or a proof holds none: point it at the "
                "claim it restates, or give the claim the standing its own words "
                "have (justified, self, unsupported)"
            )
        return None

    def unsupported_by_its_lines(s: Support) -> str | None:
        """Why the lines its standing needs do not support `s`, if they do
        not."""
        if s.status == "restated":
            return restates_nothing(s)
        if s.status in _SUPPORTED_BY:
            return missing(s, *_SUPPORTED_BY[s.status])
        return None

    given = {s.source for s in sheet.supports}
    claims = [h for h, roles in sheet.roles.items() if "claim" in roles]
    without = [h for h in claims if h not in given]
    if without:
        problems += [f"no support line: {h}" for h in without]
    for s in sheet.supports:
        where = f"{s.source} ⊢ {s.status}"
        if s.source not in sheet.roles:
            problems.append(
                f"{where}: a support line stands under a block, not a group"
            )
            continue
        if s.source in claims_fixed and "claim" not in sheet.roles[s.source]:
            problems.append(
                f"{where}: a support line on a block with no claim: a person gave "
                "its roles"
            )
            continue
        if s.declared and s.declared not in DECLARATIONS:
            problems.append(
                f"{where}: no such declaration {s.declared!r}: write "
                f"{' or '.join(DECLARATIONS)}, or none"
            )
        elif s.declared and s.status != "unsupported":
            problems.append(
                f"{where}: {s.declared} is declared on an unsupported standing only"
            )
        if s.status not in STATUSES:
            problems.append(f"{where}: no such status")
        elif why := unsupported_by_its_lines(s):
            problems.append(f"{where}: {why}")
        elif s.status == "self" and not any(
            _gives_reason(e.evidence, s.claim) for e in covering(s, SELF)
        ):
            problems.append(
                f"{where}: its self mark quotes the claim, not its reason: quote "
                "the citation or the step that yields it, else it is unsupported"
            )
        if not norm(s.claim):
            problems.append(f"{where}: the claim's words are empty: quote them")
        elif not quoted(sheet, s.source, s.claim):
            problems.append(
                f"{where}: claim not in {s.source}: {s.claim!r}"
                + _spans(sheet, s.source, s.claim)
            )
    return problems


def standings_without_claim(sheet: Sheet) -> dict[str, list[Support]]:
    """The blocks whose roles hold no claim and which take a support line
    anyway, each with its support lines, in the sheet's order: each line
    adds the claim role to its block (`docs/model.md` § 4). A block that
    holds a claim takes none."""
    found: dict[str, list[Support]] = {}
    for s in sheet.supports:
        roles = sheet.roles.get(s.source)
        if roles is not None and "claim" not in roles:
            found.setdefault(s.source, []).append(s)
    return found


#: The start of a problem that says only that the sheet is unfinished, which
#: the check on every write leaves out.
UNFINISHED = ("not done: ", "no support line: ")


def counts(text: str) -> dict[str, int]:
    sheet = parse(text)
    found = {k: 0 for k in (*KINDS, SELF)}
    for e in sheet.edges:
        found[e.kind] = found.get(e.kind, 0) + 1
    for status in STATUSES:
        found[f"claims {status}"] = sum(1 for s in sheet.supports if s.status == status)
    found["blocks"] = len(sheet.block_ids)
    found["done"] = len(sheet.done)
    return found


# --- segments: the role of a part --------------------------------------------------


def segments(sheet: Sheet, handle: str) -> list[tuple[str, str]]:
    """A block's segments as (role, normalised text): the block line's
    first role, then each ⟦+ role⟧ marker's."""
    roles = list(sheet.roles.get(handle, ()))
    pieces = re.split(r"⟦\+ (\w+)⟧ ?", sheet.bodies.get(handle, ""))
    out = [(roles[0] if roles else NONE, norm(pieces[0]))]
    for i in range(1, len(pieces) - 1, 2):
        out.append((pieces[i], norm(pieces[i + 1])))
    return out


def segment_role_at(found: list[tuple[str, str]], words: str) -> str | None:
    """The role of the segment in which `words` start."""
    body = " ".join(text for _, text in found)
    at = body.find(words)
    if not words or at < 0:
        return None
    pos = 0
    for role, text in found:
        if at < pos + len(text):
            return role
        pos += len(text) + 1
    return found[-1][0]


def part_role(sheet: Sheet, target: str, label: str) -> str:
    """A legend part's role: its legend role when it is one of the five,
    else the role of the segment its words start in."""
    legend_role = sheet.legend_roles.get((target, label))
    if legend_role in ROLES:
        return legend_role
    found = segments(sheet, target)
    words = norm(sheet.legend.get((target, label), ""))
    return segment_role_at(found, words) or found[0][0]
