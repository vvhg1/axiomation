"""A person's edit.

A person's edit is an answer, by `person`, to its stage's own file: the
boundary author's `cut.md` (pass 1), the roles review's `roles.md` and the
groups session's `groups.md` (pass 2), each on the whole paper, or the
edges sheet, the last edges answer carried onto the final cut without its
cut, flag and update lines. The file is checked by that stage's own check;
a person's file holds no `?` flag (a flag asks a later step, which a
person's answer does not), and a person's `edges.md` no cut line either.
The question is the stage's, its key `person_key`: the stage, the fixed
`person` template and the hashes of the file shown and the file edited, so
no brief's version moves it.

**Pins** are computed, never stored: from an applied answer's state before
and after (the block starts it added or removed, the blocks whose roles it
changed, the groups it made, changed or dissolved), and on the edges sheet
from its shown file and its raw (the lines it wrote, the lines it deleted,
as `edges_file.written_lines` reads them). An edges pin is found on a later
sheet by carrying both files the way the edge half was carried
(`EdgePins.on`); across versions `axiomation diff --store` carries them and
holds them in the identity answer's raw, `carried.pins`
(`carried_pins`), so an update's replay needs no OLD. `Pinned` is what a
later model answer may not change: `driver.weigh`, the touched roles, an
update's groups step and every later edges gate read it.

The person round itself is the driver's (`driver.run_person_rounds`).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from axiomation.changes import Layer
from axiomation.cutter.model import Block, Cut, Group
from axiomation.identity import BlockClass, Lineage
from axiomation.questions import (
    Question,
    Stage,
    Template,
    Validation,
    edges_file,
    groups_file,
    load_template,
    roles_file,
    vocabulary_file,
)
from axiomation.questions import edge_sheet as es
from axiomation.questions.cut_file import file_questions
from axiomation.questions.edges_file import Beside
from axiomation.reader import Document, Vocabulary, document_hash
from axiomation.records import PERSON
from axiomation.runner import Check

if TYPE_CHECKING:
    from axiomation.driver import ReviewSettings

#: The person host's template: the person's question's, recorded on its
#: answer. Its version is not in the key, so a prose fix never makes a
#: person's answer stale.
TEMPLATE = PERSON
#: The layers a person edits, each in its stage's own file, and the stage
#: whose question shows it.
STAGES: dict[Layer, Stage] = {
    Layer.CUT: Stage.BOUNDARIES,
    Layer.ROLES: Stage.ROLES,
    Layer.GROUPS: Stage.GROUPS,
    Layer.EDGES: Stage.EDGES,
}
#: A person's answer's layer, by the stage its record names.
LAYER_OF: dict[str, Layer] = {stage.value: layer for layer, stage in STAGES.items()}
#: Why a person's file is refused for a flag line.
NO_FLAG = "a `?` flag asks a later step, which a person's answer does not: remove it"


def template() -> Template:
    return load_template(TEMPLATE)


def person_key(
    stage: Stage, kind: str, shown: str, edited: str, version: str, by: str = PERSON
) -> str:
    """A given answer's key: its kind, its stage, its author's host (`by`:
    `person`, or `agent`), the version it was given on (its
    document hash, so one edit on two versions whose files read the same
    is recorded on each), and the hashes of the file shown and the file
    edited. No template version: its author reads no brief."""
    canonical = json.dumps(
        {
            "kind": kind,
            "stage": stage.value,
            "host": by,
            "inputs": {
                "version": version,
                "shown": _sha(shown),
                "edited": _sha(edited),
            },
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return _sha(canonical)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def stage_question(
    document: Document,
    cut: Cut,
    layer: Layer,
    settings: ReviewSettings,
    sheet: str | None = None,
    notes: Mapping[str, str] | None = None,
    by: str = PERSON,
    pins: Sequence[tuple[str, str, str]] = (),
    beside: Beside | None = None,
    kept: Collection[str] = (),
) -> Question:
    """The stage question whose file a person edits, on the whole paper
    (no chunk limit), as the stage would show it on `cut`: the boundary
    author's pass 1, the roles review's and the groups session's pass 2,
    the edges sheet `sheet` (`edges_file.person_sheet`) every owner open,
    with `beside` its lines a kept state's open stale marks and an update's
    Δ lines. `notes`, by block id, follow a block's
    marker in the roles file: where a role came from (`driver.claim_notes`).
    An agent's edges (`by`) take no keep mark, and their
    gate holds `pins`; it refuses no problem the kept state's sheet holds
    (`kept`, `edges_file.kept_problems`). Its key is the stage's;
    `person_question` replaces it."""
    templates = settings.templates
    if layer is Layer.CUT:
        (found,) = file_questions(
            document,
            cut,
            None,
            templates.boundaries,
            settings.overlap,
            1,
            settings.outline,
        )
        return found
    if layer is Layer.ROLES:
        (found,) = roles_file.session_questions(
            document, cut, None, templates.roles_review, settings.overlap, 2, notes
        )
        return found
    if layer is Layer.GROUPS:
        # a person edits the file, under its own brief
        (found,) = groups_file.session_questions(
            document,
            cut,
            None,
            load_template(groups_file.FILE_TEMPLATE),
            settings.overlap,
            2,
        )
        return found
    if sheet is None:
        raise ValueError("the edges have no answer to show a person")
    return edges_file.person_question(
        document,
        cut,
        template(),
        settings.files,
        sheet,
        keeps=by == PERSON,
        pins=pins,
        beside=beside,
        kept=kept,
    )


def shown_file(question: Question) -> str:
    """The file a question shows for editing."""
    assert question.workspace is not None
    return question.workspace.files[question.workspace.edit]


def shown_hash(question: Question) -> str:
    """The hash of the file a question shows (`AnswerRecord.shown`)."""
    return _sha(shown_file(question))


def person_question(
    document: Document,
    cut: Cut,
    layer: Layer,
    settings: ReviewSettings,
    sheet: str | None,
    edited: str,
    notes: Mapping[str, str] | None = None,
    by: str = PERSON,
    pins: Sequence[tuple[str, str, str]] = (),
    beside: Beside | None = None,
    kept: Collection[str] = (),
) -> Question:
    """The question for the edit `edited` given by `by` (a person's, or an
    agent's): the stage question, its template the person's and
    its key `person_key`, of the file shown without what stands `beside`
    its lines: the marks and Δs a state's record shows, which a
    re-run's person rounds do not, so the re-run finds the same key.
    `kept`, the problems the kept state's sheet holds, are in no key."""
    found = stage_question(
        document, cut, layer, settings, sheet, notes, by, pins, beside, kept
    )
    keyed = (
        stage_question(document, cut, layer, settings, sheet, notes, by, pins)
        if beside
        else found
    )
    return dataclasses.replace(
        found,
        key=person_key(
            found.stage,
            found.kind,
            shown_file(keyed),
            edited,
            document_hash(document),
            by,
        ),
        template=template(),
    )


def vocabulary_question(
    document: Document,
    vocabulary: Vocabulary | None,
    edited: str,
    by: str = PERSON,
) -> Question | None:
    """The question for the vocabulary map `edited`, given by `by` on the
    version of `document`, read without a map: the whole map shown with
    `vocabulary`'s answers (`vocabulary_file.given_question`), its template
    the person's and its key `person_key`; `None` where the paper has no
    name to map."""
    found = vocabulary_file.given_question(
        document, vocabulary, load_template(vocabulary_file.SESSION_TEMPLATE)
    )
    if found is None:
        return None
    return dataclasses.replace(
        found,
        key=person_key(
            found.stage,
            found.kind,
            shown_file(found),
            edited,
            document_hash(document),
            by,
        ),
        template=template(),
    )


def checked(question: Question, text: str, check: Check) -> Validation:
    """A person's file under its stage's own check, and refused where it
    holds a `?` flag (the edges gate refuses a cut line and a flag itself)."""
    found = check(question, text)
    if found.valid and found.flags:
        return Validation(problems=(NO_FLAG,))
    return found


# --- pins ------------------------------------------------------------------------


class PinKind(StrEnum):
    """What a pin holds: a block start a person added or removed (cut),
    a block's roles a person changed, a group a person made, changed or
    dissolved, a line a person wrote, deleted or kept (edges; a kept line
    holds as a written one does)."""

    ADDED = "added"
    REMOVED = "removed"
    CHANGED = "changed"
    MADE = "made"
    DISSOLVED = "dissolved"
    WRITTEN = edges_file.WRITTEN_PIN
    DELETED = edges_file.DELETED_PIN
    KEPT = edges_file.KEPT_PIN


#: A pin's state in the export: held, held with its block's meaning moved
#: (`moved`), or lapsed (`lapsed`).
HELD = "held"


def moved(why: str) -> str:
    """A held pin on a block whose meaning moved: by its Δ class, or by
    the rule. Reported for the person to confirm again; the pin stays."""
    return f"held, meaning moved ({why})"


def lapsed(why: str) -> str:
    return f"lapsed ({why})"


@dataclass(frozen=True, slots=True)
class Pin:
    """A person's pin: its layer, its owner (a block's or a group's id; on
    the edges sheet, the owner's handle), its kind, its line (on the edges
    sheet the line itself, else what it holds, in words) and its state."""

    layer: Layer
    owner: str
    kind: PinKind
    line: str
    state: str = HELD
    #: A removed start's place, its block id before the person's edit: a
    #: person's later cut edit that starts a block there lapses the pin.
    place: str | None = None
    #: Its answer's place in store order (carried pins before NEW's own):
    #: where a check names two pins against each other, the later holds.
    rank: int = 0
    #: On the edges sheet, the legend words of each part its line names,
    #: in order, as its own sheet holds them: a part renamed on another
    #: sheet (carry's `p-2`) is found by its words.
    words: tuple[str, ...] = ()

    @property
    def held(self) -> bool:
        return not self.state.startswith("lapsed")

    @property
    def stands(self) -> bool:
        """Whether the pin holds its line under its owner: written or kept."""
        return self.kind.value in edges_file.STANDS


def cut_pins(layer: Layer, before: Cut, after: Cut) -> tuple[Pin, ...]:
    """A cut, roles or groups answer's pins, from the state before it and
    after: the starts it added (the block there) or removed (the block
    that now holds the place), the blocks whose roles or segments it
    changed, the groups it made, changed or dissolved."""
    if layer is Layer.CUT:
        old = {b.id for b in before.blocks}
        new = {b.id for b in after.blocks}
        found = [
            Pin(layer, b.id, PinKind.ADDED, f"a block starts at {b.id}")
            for b in after.blocks
            if b.id not in old
        ]
        for b in before.blocks:
            if b.id in new:
                continue
            start = b.owned[0] if b.owned else b.extent
            holder = next(
                (
                    a.id
                    for a in after.blocks
                    if any(
                        r.file == start.file and r.start <= start.start < r.end
                        for r in a.owned
                    )
                ),
                None,
            )
            line = f"no block starts at {b.id}"
            found.append(
                Pin(layer, holder, PinKind.REMOVED, line, place=b.id)
                if holder is not None
                else Pin(
                    layer,
                    b.id,
                    PinKind.REMOVED,
                    line,
                    lapsed("no block holds its place"),
                )
            )
        return tuple(found)
    if layer is Layer.ROLES:
        was = {b.id: b for b in before.blocks}
        return tuple(
            Pin(layer, b.id, PinKind.CHANGED, f"{_roles(was[b.id])} → {_roles(b)}")
            for b in after.blocks
            if b.id in was
            and (was[b.id].roles, was[b.id].segments) != (b.roles, b.segments)
        )
    old_groups = {g.id: g for g in before.groups}
    new_groups = {g.id: g for g in after.groups}
    found = []
    for g in after.groups:
        held = old_groups.get(g.id)
        if held is None:
            found.append(Pin(layer, g.id, PinKind.MADE, _group(g)))
        elif (held.kind, held.blocks) != (g.kind, g.blocks):
            found.append(Pin(layer, g.id, PinKind.CHANGED, _group(g)))
    found += [
        Pin(layer, g.id, PinKind.DISSOLVED, _group(g))
        for g in before.groups
        if g.id not in new_groups
    ]
    return tuple(found)


def _roles(block: Block) -> str:
    if block.segments:
        return " + ".join(s.role.value if s.role else "none" for s in block.segments)
    return "+".join(r.value for r in block.roles) or "none"


def _group(group: Group) -> str:
    return f"{group.kind.value} of {', '.join(group.blocks)}"


def edge_pins(shown: str, raw: str) -> tuple[Pin, ...]:
    """An edges answer's pins: per owner, the lines it wrote (in the raw,
    not shown) and the lines it deleted (shown, not in the raw), done
    marks aside, and the lines it kept by the keep mark that it did not
    also write. A Δ line it corrected is the update's record, not
    a line of the graph: it pins nothing."""

    def lines(sheet: str) -> dict[str, list[str]]:
        return {
            owner: [
                line
                for line in owned
                if not es.DONE.match(line) and not line.startswith(es.DELTA_MARK)
            ]
            for owner, owned in edges_file.written_lines(sheet).items()
        }

    before, after = lines(shown), lines(raw)
    found: list[Pin] = []
    for owner in dict.fromkeys([*before, *after]):
        # counted, so a line a carry made twice is a line written
        was, now = Counter(before.get(owner, [])), Counter(after.get(owner, []))
        found += [
            Pin(Layer.EDGES, owner, PinKind.WRITTEN, line)
            for line in now
            if now[line] > was[line]
        ]
        found += [
            Pin(Layer.EDGES, owner, PinKind.DELETED, line)
            for line in was
            if was[line] > now[line]
        ]
    written = {(p.owner, p.line) for p in found if p.kind is PinKind.WRITTEN}
    found += [
        Pin(Layer.EDGES, owner, PinKind.KEPT, line)
        for owner, line in dict.fromkeys(es.parse(raw).kept)
        if (owner, line) not in written
    ]
    return tuple(found)


def _as_diffed(kind: PinKind) -> PinKind:
    """The kind a pin's line shows as between a sheet without it and the
    sheet with it (`unpinned`): a kept line as a written one."""
    return PinKind.WRITTEN if kind is PinKind.KEPT else kind


def merged(pins: Sequence[Pin]) -> tuple[Pin, ...]:
    """Pins of several answers in order, a later one taking the place of
    an earlier one on the same owner (on the edges sheet, the same owner
    and line): only a person's later answer changes a pin."""
    found: dict[tuple[Layer, str, str], Pin] = {}
    for pin in pins:
        found.pop(pin_place(pin), None)
        found[pin_place(pin)] = pin
    return tuple(found.values())


def pin_place(pin: Pin) -> tuple[Layer, str, str]:
    """Where a pin holds: its layer and owner, and on the edges sheet its line."""
    return (pin.layer, pin.owner, pin.line if pin.layer is Layer.EDGES else "")


def pin_lines(pins: Sequence[Pin]) -> tuple[tuple[str, str, str], ...]:
    """The held edges pins as a gate reads them: `(owner, line, kind)`."""
    return tuple(
        (p.owner, p.line, p.kind.value)
        for p in pins
        if p.layer is Layer.EDGES and p.held
    )


def unpinned(sheet: str, pins: Sequence[Pin]) -> str:
    """`sheet` as it stood before a person's edges: each written pin line
    taken from under its owner, each deleted one put back before the
    owner's done mark. With `sheet`, it is a pair whose `edge_pins` are
    `pins`, to carry onto another cut (`EdgePins`)."""
    taken = {(p.owner, p.line) for p in pins if p.stands}
    back: dict[str, list[str]] = defaultdict(list)
    for p in pins:
        if p.kind is PinKind.DELETED:
            back[p.owner].append(p.line)
    out: list[str] = []
    owner: str | None = None

    def close() -> None:
        if owner is None or owner not in back:
            return
        at = len(out)
        while at and (out[at - 1] == "" or es.DONE.match(out[at - 1])):
            at -= 1
        out[at:at] = back[owner]

    for line in sheet.split("\n"):
        if m := (es.BLOCK.match(line) or es.GROUP.match(line) or es.END.match(line)):
            close()
            owner = None if es.GROUP.match(line) else m.group(1)
        elif owner is not None and (owner, es.unkept(line)[0]) in taken:
            continue
        out.append(line)
    close()
    return "\n".join(out)


def marked(sheet: str, pins: Sequence[Pin]) -> str:
    """`sheet` with the keep mark before each kept pin's line: with the
    sheet, a pair whose `edge_pins` are those pins."""
    kept = {(p.owner, p.line) for p in pins if p.kind is PinKind.KEPT}
    out: list[str] = []
    owner: str | None = None
    for line in sheet.split("\n"):
        if m := (es.BLOCK.match(line) or es.GROUP.match(line) or es.END.match(line)):
            owner = None if es.GROUP.match(line) else m.group(1)
        elif owner is not None and (owner, line) in kept:
            line = es.KEEP_MARK + line
        out.append(line)
    return "\n".join(out)


@dataclass(frozen=True, slots=True, eq=False)
class EdgePins:
    """A person's edges pins as two sheets written on one cut: the file
    shown and the file edited (or a sheet and itself `unpinned`), and the
    answer's place in store order (`rank`). Their pins on another cut of
    the version are found by carrying both onto it, the way the edge half
    was carried; whether they hold there is `held_against`'s, on the sheet
    a gate shows."""

    shown: str
    raw: str
    cut: Cut
    rank: int = 0

    def on(self, document: Document, cut: Cut, files: Sequence[str]) -> tuple[Pin, ...]:
        """The pins on the sheet of `cut`, each with its part words: on
        this cut, as the two sheets differ; on another, each pin carried
        alone (the sheet without its edit beside the sheet), so a pin a
        carry makes one line with another is told apart and reported
        lapsed, never dropped."""
        own = [
            dataclasses.replace(p, rank=self.rank)
            for p in with_words(edge_pins(self.shown, self.raw), es.parse(self.raw))
        ]
        if cut == self.cut:
            return tuple(own)
        raw = edges_file.carry(document, self.raw, self.cut, cut, files).sheet
        carried = es.parse(raw)
        found: list[Pin] = []
        taken: set[tuple[str, str, PinKind]] = set()
        for pin in own:
            alone = edges_file.carry(
                document, unpinned(self.raw, [pin]), self.cut, cut, files
            ).sheet
            image = [
                dataclasses.replace(p, kind=pin.kind)
                for p in edge_pins(alone, raw)
                if p.kind is _as_diffed(pin.kind)
            ]
            place = (image[0].owner, image[0].line, pin.kind) if image else None
            if place is None or place in taken:
                found.append(
                    dataclasses.replace(
                        pin, state=structural("a carry made it one line with another")
                    )
                )
                continue
            taken.add(place)
            (now,) = with_words([image[0]], carried)
            found.append(dataclasses.replace(now, rank=self.rank))
        return tuple(found)


def with_words(pins: Sequence[Pin], sheet: es.Sheet) -> tuple[Pin, ...]:
    """Each edges pin with the legend words, on `sheet`, of the parts its
    line names."""
    found = []
    for pin in pins:
        m = es.EDGE.match(pin.line)
        labels = [x.strip() for x in ((m and m.group("parts")) or "").split(",")]
        target = (m and m.group("target")) or ""
        found.append(
            dataclasses.replace(
                pin,
                words=tuple(sheet.legend.get((target, x), "") for x in labels if x),
            )
        )
    return tuple(found)


def structural(why: str) -> str:
    """A pin a failing check names: `lapsed (structural: <check>)`."""
    return lapsed(f"structural: {why}")


#: Why a pin under an owner the gate fixes lapses: the sheet shown, which
#: the gate holds there, has it otherwise.
AS_SHOWN = "the sheet shows it otherwise"


def held_against(
    pins: Sequence[Pin],
    shown: str,
    reopened: Collection[str] | None,
    spec: Mapping[str, Any],
) -> tuple[Pin, ...]:
    """The edges pins as the sheet `shown` can hold them before its
    session: a pin holds while the
    sheet shown, with the held pins in place, passes the gate's structural
    checks; the pins a failing check names lapse, `lapsed (structural:
    <check>)`. The writer's answer never lapses one: the gate refuses an
    answer that breaks a held pin, naming it (`edges_file.pin_problems`).

    1. Under an owner the gate fixes (one not in `reopened`; None opens
       every owner), a pin holds as the sheet shows it: re-rendered to the
       shown line where they differ only by a part's collision suffix
       (`[p]` and `[p-2]`, carry's rename), else a written line the sheet
       lacks, or a deleted line it holds, lapses. A pin whose owner the
       sheet does not hold lapses.
    2. The candidate is `shown` with the held written pins in place and
       the held deleted ones taken off, checked by the edges gate's own
       check (`edges_file.problems`, on `spec`'s reference, blocks, files
       and nesting); `edges_file.implicated` names the pins a problem
       takes part in. Where it names pins against each other (a second
       edge for one source, kind and target), the later answer's pin holds
       and the earlier lapse; pins of one answer lapse together, but a
       line a merge made twice is one pin, which holds, the copy the
       writer's to drop. Every other named pin lapses. Repeated until
       no problem names a held pin; a problem no pin takes part in (a
       writer's work still owed) stays the writer's."""
    sheet = es.parse(shown)
    lines = edges_file.written_lines(shown)
    found = list(pins)
    for n, pin in enumerate(found):
        if not pin.held or pin.layer is not Layer.EDGES:
            continue
        if pin.owner not in sheet.texts:
            found[n] = dataclasses.replace(pin, state=lapsed("its owner is gone"))
            continue
        if pin.line not in lines.get(pin.owner, []):
            pin = found[n] = dataclasses.replace(pin, line=_renamed(pin, sheet))
        if reopened is None or pin.owner in reopened:
            continue
        if (pin.line in lines.get(pin.owner, [])) != pin.stands:
            found[n] = dataclasses.replace(pin, state=lapsed(AS_SHOWN))
    while True:
        held = [n for n, p in enumerate(found) if p.held and p.layer is Layer.EDGES]
        candidate = unpinned(
            shown,
            [
                *(
                    dataclasses.replace(found[n], kind=PinKind.DELETED)
                    for n in held
                    if found[n].stands
                    and found[n].line not in lines.get(found[n].owner, [])
                ),
                *(
                    dataclasses.replace(found[n], kind=PinKind.WRITTEN)
                    for n in held
                    if found[n].kind is PinKind.DELETED
                    and found[n].line in lines.get(found[n].owner, [])
                ),
            ],
        )
        named = [(found[n].owner, found[n].line, found[n].kind.value) for n in held]
        lapsing: dict[int, str] = {}
        for problem in edges_file.problems(
            spec["reference"],
            candidate,
            spec["block_ids"],
            spec["files"],
            within=spec.get("within"),
            claims_fixed=spec.get(edges_file.CLAIMS_FIXED, ()),
        ):
            hits = [(held[i], why) for i, why in edges_file.implicated(problem, named)]
            if not hits:
                continue
            if hits[0][1].startswith(edges_file.DUPLICATE):
                hits = _against(hits, found)
            for n, why in hits:
                lapsing.setdefault(n, why)
        if not lapsing:
            lapsing = _removal_breaks(found, held, shown, candidate, spec)
        if not lapsing:
            return tuple(found)
        for n, why in lapsing.items():
            found[n] = dataclasses.replace(found[n], state=structural(why))


def _checked(sheet: str, spec: Mapping[str, Any]) -> list[str]:
    """The edges gate's check of `sheet`, line numbers aside."""
    return [
        re.sub(r"^line \d+: ", "", problem)
        for problem in edges_file.problems(
            spec["reference"],
            sheet,
            spec["block_ids"],
            spec["files"],
            within=spec.get("within"),
            claims_fixed=spec.get(edges_file.CLAIMS_FIXED, ()),
        )
    ]


def _removal_breaks(
    pins: Sequence[Pin],
    held: Sequence[int],
    shown: str,
    candidate: str,
    spec: Mapping[str, Any],
) -> dict[int, str]:
    """The held deleted pins whose line, taken off the candidate, adds a
    problem it does not have with the line in place (the edge a standing
    rests on): such a pin lapses, with the problem its removal makes."""
    lines = edges_file.written_lines(shown)
    removed = [
        n
        for n in held
        if pins[n].kind is PinKind.DELETED
        and pins[n].line in lines.get(pins[n].owner, [])
    ]
    if not removed:
        return {}
    now = set(_checked(candidate, spec))
    found: dict[int, str] = {}
    for n in removed:
        back = unpinned(candidate, [dataclasses.replace(pins[n], kind=PinKind.DELETED)])
        added = sorted(now - set(_checked(back, spec)))
        if added:
            found[n] = added[0].split(": ")[-1] if ": " in added[0] else added[0]
    return found


def _against(hits: list[tuple[int, str]], pins: Sequence[Pin]) -> list[tuple[int, str]]:
    """Of the pins a check names against each other (a second edge for one
    source, kind and target), those that lapse: the earlier answers', the
    later holding as the person's latest word; of the latest answer's, all
    where it gave more than one (a line it gave twice is one pin). One
    pin alone holds, a copy beside it the writer's to drop."""
    later = max(pins[n].rank for n, _ in hits)
    last = [(n, why) for n, why in hits if pins[n].rank == later]
    lapsing = [(n, why) for n, why in hits if pins[n].rank < later]
    return lapsing if len(last) == 1 else lapsing + last


def _renamed(pin: Pin, sheet: es.Sheet) -> str:
    """A pin's line with each part it names under the label `sheet` gives
    the same legend words (carry's collision rename, `p` to `p-2`), where
    one label alone does; else as it is."""
    m = es.EDGE.match(pin.line)
    if m is None or not m.group("parts") or not pin.words:
        return pin.line
    target = m.group("target") or ""
    labels = [x.strip() for x in m.group("parts").split(",") if x.strip()]
    if len(labels) != len(pin.words):
        return pin.line
    now = []
    for label, words in zip(labels, pin.words, strict=True):
        same = [
            x
            for (t, x), w in sheet.legend.items()
            if t == target and es.norm(w) == es.norm(words)
        ]
        if (target, label) in sheet.legend and es.norm(
            sheet.legend[(target, label)]
        ) == es.norm(words):
            now.append(label)
        elif len(same) == 1:
            now.append(same[0])
        else:
            return pin.line
    return pin.line[: m.start("parts")] + ", ".join(now) + pin.line[m.end("parts") :]


def as_left(
    pins: Sequence[Pin], sheet: str, spec: Mapping[str, Any]
) -> tuple[Pin, ...]:
    """The edges pins as a run's final edge half holds them, for its
    export: decided on the sheet as it is, every owner fixed (a pin the
    sheet holds as pinned is held unless the structure names it; one it
    does not lapses); for a pin the sheet does not hold, the reason is the
    check that let it go, where every owner open finds one."""
    fixed = held_against(pins, sheet, (), spec)
    open_ = held_against(pins, sheet, None, spec)
    return tuple(
        o if f.state == lapsed(AS_SHOWN) and not o.held else f
        for f, o in zip(fixed, open_, strict=True)
    )


def gate_pins(
    pins: Sequence[Pin], question: Question
) -> tuple[tuple[str, str, str], ...]:
    """The pins an edges question's gate holds: `pins`, carried onto its
    sheet, as `held_against` holds them on the sheet it shows, with the
    owners it reopens and its spec."""
    assert question.workspace is not None and question.workspace.gate is not None
    spec = json.loads(question.workspace.gate.spec)
    shown = spec.get("shown", spec["reference"])
    return pin_lines(held_against(pins, shown, spec.get("reopened"), spec))


def pinned_question(
    build: Callable[[Sequence[tuple[str, str, str]]], Question],
    pins: Sequence[Pin],
) -> Question:
    """An edges question with the pins its gate holds: built without them,
    its sheet and reopened owners read, and built again with those that
    hold (a question with none keeps the key it has without pins)."""
    question = build(())
    if not pins:
        return question
    held = gate_pins(pins, question)
    return build(held) if held else question


def held_on(pins: Sequence[Pin], cut: Cut) -> tuple[Pin, ...]:
    """The cut, roles and groups pins as `cut` holds them: a pin whose
    block or group the cut no longer holds as pinned lapses, with why (a
    person's own later cut edit, or a correction no pin stopped); edges
    pins pass as they are."""
    blocks = {b.id for b in cut.blocks}
    groups = {g.id for g in cut.groups}
    found = []
    for pin in pins:
        why = None
        if not pin.held or pin.layer is Layer.EDGES:
            pass
        elif pin.layer in (Layer.CUT, Layer.ROLES):
            if pin.owner not in blocks:
                why = "its block changed"
            elif pin.place is not None and pin.place in blocks:
                why = "a block starts there again"
        elif pin.kind is PinKind.DISSOLVED:
            if pin.owner in groups:
                why = "the group was made again"
        elif pin.owner not in groups:
            why = "its group changed"
        found.append(
            pin if why is None else dataclasses.replace(pin, state=lapsed(why))
        )
    return tuple(found)


@dataclass(frozen=True, slots=True, eq=False)
class Pinned:
    """What a later model answer may not change: the
    block starts no correction may move (`starts`), the blocks no
    correction may change (`whole`: split, merge or move a boundary of),
    the blocks whose roles no question asks (`roles`), the groups no
    correction may change or dissolve (`groups`) and the blocks no
    correction may change while their group stands (`members`), and the
    edges pins (`edges`), each found on the sheet a gate shows."""

    starts: frozenset[str] = frozenset()
    whole: frozenset[str] = frozenset()
    roles: frozenset[str] = frozenset()
    groups: frozenset[str] = frozenset()
    members: frozenset[str] = frozenset()
    edges: tuple[EdgePins, ...] = field(default=())

    def __bool__(self) -> bool:
        return bool(
            self.starts
            or self.whole
            or self.roles
            or self.groups
            or self.members
            or self.edges
        )

    def edge_pins(
        self, document: Document, cut: Cut, files: Sequence[str]
    ) -> tuple[Pin, ...]:
        """The edges pins on the sheet of `cut`, held or lapsed."""
        return merged([p for e in self.edges for p in e.on(document, cut, files)])

    def with_edges(self, more: Sequence[EdgePins]) -> Pinned:
        return dataclasses.replace(self, edges=(*self.edges, *more))


def pinned_of(
    pins: Sequence[Pin], edges: Sequence[EdgePins] = (), cut: Cut | None = None
) -> Pinned:
    """The held cut, roles and groups pins as `Pinned` reads them, with
    the edges pins `edges`; a pinned group's members as `cut` holds them."""
    held = [p for p in pins if p.held]
    roles = {p.owner for p in held if p.layer is Layer.ROLES}
    groups = {p.owner for p in held if p.layer is Layer.GROUPS}
    return Pinned(
        starts=frozenset(
            p.owner for p in held if p.layer is Layer.CUT and p.kind is PinKind.ADDED
        ),
        whole=frozenset(
            {
                p.owner
                for p in held
                if p.layer is Layer.CUT and p.kind is PinKind.REMOVED
            }
            | roles
        ),
        roles=frozenset(roles),
        groups=frozenset(groups),
        members=frozenset(
            b for g in (cut.groups if cut else ()) if g.id in groups for b in g.blocks
        ),
        edges=tuple(edges),
    )


# --- across versions ------------------------------------------------------------

#: The classes whose block keeps OLD's roles, one to one: a pin follows it.
#: Another class lapses it.
FOLLOWS = (
    BlockClass.SAME,
    BlockClass.MOVED,
    BlockClass.MATH,
    BlockClass.MARKUP,
    BlockClass.MACRO,
)
#: The classes whose block keeps its text: its lines are carried verbatim.
KEEPS_TEXT = (BlockClass.SAME, BlockClass.MOVED)
#: Why a pin lapses, by its block's class.
_LAPSES = {
    BlockClass.CHANGED: "its block changed",
    BlockClass.SPLIT: "its block was split",
    BlockClass.MERGED: "its block was merged",
    BlockClass.DELETED: "its block was deleted",
}


def pin_payload(pin: Pin) -> dict[str, Any]:
    """A pin as an identity answer's raw carries it (`carried.pins`), with
    its answer's place in store order."""
    return {
        "layer": pin.layer.value,
        "owner": pin.owner,
        "kind": pin.kind.value,
        "line": pin.line,
        "state": pin.state,
        "rank": pin.rank,
    }


def pin_from(data: Mapping[str, Any]) -> Pin:
    return Pin(
        Layer(data["layer"]),
        data["owner"],
        PinKind(data["kind"]),
        data["line"],
        data["state"],
        rank=data.get("rank", 0),
    )


def carried_pins(
    old: Document,
    new: Document,
    sheet: str,
    old_cut: Cut,
    new_cut: Cut,
    files: Sequence[str],
    lineage: Lineage,
    exported: Sequence[Mapping[str, Any]],
) -> tuple[Pin, ...]:
    """OLD's held pins (`exported`, its export's `review.person.pins`, by
    block or group id, each with its answer's place in store order)
    carried onto NEW's rebased cut: a cut, roles or
    groups pin follows identity's one-to-one links that keep OLD's roles,
    an edges pin is carried with `sheet`, OLD's edge half, as its lines
    are (one pin at a time, so each is told apart). A pin lapses where its
    block changed, was split, merged or deleted, or where a carry finds no
    line for it; the edges pins left are then held against the carried
    sheet by the one rule (`held_against`, every owner open, NEW's
    structure), the later answer holding where a check names two. A
    lapsed pin keeps OLD's names."""
    old_handles = edges_file.handles(old, old_cut)
    handle_of = {ident: h for h, ident in old_handles.items()}
    groups = {g.id: g for g in old_cut.groups}
    new_handle_of = (
        {ident: h for h, ident in edges_file.handles(new, new_cut).items()}
        if any(data["layer"] == Layer.EDGES.value for data in exported)
        else {}
    )

    def onto(block: str) -> tuple[str | None, str | None]:
        """A block's one-to-one heir on NEW, else why the pin lapses."""
        link = lineage.link_of_old(block)
        if link is None:
            return None, "its block was deleted"
        if link.cls not in FOLLOWS or len(link.new) != 1:
            return None, _LAPSES.get(link.cls, f"its block is {link.cls.value}")
        return link.new[0], None

    found: list[Pin] = []
    for data in exported:
        pin = Pin(
            Layer(data["layer"]),
            data["block"],
            PinKind(data["kind"]),
            data["line"],
            HELD,
            rank=data.get("rank", 0),
        )
        if not str(data.get("state", HELD)).startswith(HELD):
            continue  # lapsed on OLD already
        if pin.layer in (Layer.CUT, Layer.ROLES):
            heir, why = onto(pin.owner)
            found.append(
                dataclasses.replace(pin, owner=heir)
                if heir is not None
                else dataclasses.replace(pin, state=lapsed(why or ""))
            )
        elif pin.layer is Layer.GROUPS:
            found.append(_group_onto(pin, groups, onto, new_cut))
        else:
            found.append(
                _edge_onto(
                    pin,
                    old,
                    new,
                    sheet,
                    old_cut,
                    new_cut,
                    files,
                    lineage,
                    handle_of,
                    new_handle_of,
                )
            )
    edges = [n for n, p in enumerate(found) if p.layer is Layer.EDGES and p.held]
    if edges:
        carried = edges_file.carry(
            old,
            sheet,
            old_cut,
            new_cut,
            files,
            new=new,
            lineage=lineage,
            cuts_weighed=False,
        ).sheet
        checked = held_against(
            with_words([found[n] for n in edges], es.parse(carried)),
            carried,
            None,
            edges_file.structure(
                new,
                new_cut,
                files,
                {p.owner for p in found if p.layer is Layer.ROLES and p.held},
            ),
        )
        for n, pin in zip(edges, checked, strict=True):
            found[n] = pin
    return tuple(found)


def _as_it_stands(
    pin: Pin,
    members: Sequence[str],
    lineage: Lineage,
    old_ids: Mapping[str, str],
    new_handle_of: Mapping[str, str],
) -> str | None:
    """The handle on NEW of an edges pin's block whose line stands there as
    it was written: the block kept its text, and so did the block its line
    targets, under the same handle (a carry keeps such a block's lines
    verbatim); else None, and the pin is carried. A group's pin, or a line
    onto a group, is carried. Carried, a deletion pin whose line shares
    its claim with a written one would differ by the handles the carry
    mints for both lines, and lapse though nothing changed."""
    if len(members) != 1:
        return None

    def kept(ident: str) -> str | None:
        link = lineage.link_of_old(ident)
        if link is None or link.cls not in KEEPS_TEXT or len(link.new) != 1:
            return None
        return new_handle_of.get(link.new[0])

    owner = kept(members[0])
    edge = es.EDGE.match(pin.line)
    target = (edge and edge.group("target")) or ""
    if owner is None or target.startswith((es.FILE, "scope:")) or not target:
        return owner
    ident = old_ids.get(target)
    if ident is None or "@" in ident:
        return None
    return owner if kept(ident) == target else None


def _group_onto(
    pin: Pin,
    groups: Mapping[str, Group],
    onto: Callable[[str], tuple[str | None, str | None]],
    new_cut: Cut,
) -> Pin:
    """A group pin carried across: its members each to its one-to-one heir
    (`onto`), the group on NEW the one of its kind whose first block is the
    first's heir; lapsed where a member's block did not follow."""
    kind, _, first = pin.owner.partition("@")
    first = first.split("#")[0]
    members = groups[pin.owner].blocks if pin.owner in groups else (first,)
    heirs = []
    for member in members:
        heir, why = onto(member)
        if heir is None:
            return dataclasses.replace(pin, state=lapsed(why or ""))
        heirs.append(heir)
    same = next(
        (
            g.id
            for g in new_cut.groups
            if g.kind.value == kind and g.blocks[0] == heirs[0]
        ),
        f"{kind}@{heirs[0]}",
    )
    return dataclasses.replace(pin, owner=same)


def _edge_onto(
    pin: Pin,
    old: Document,
    new: Document,
    sheet: str,
    old_cut: Cut,
    new_cut: Cut,
    files: Sequence[str],
    lineage: Lineage,
    handle_of: Mapping[str, str],
    new_handle_of: Mapping[str, str],
) -> Pin:
    """One edges pin carried across: held as it stands where its line
    stands on NEW as on OLD (`_as_it_stands`); else `sheet` and the sheet
    without it (`unpinned`), carried as the edge half is, their difference
    the pin on NEW's sheet. `handle_of` and `new_handle_of` name each
    block and group of OLD and of NEW by its handle."""
    owner = handle_of.get(pin.owner, pin.owner)
    at_old = dataclasses.replace(pin, owner=owner)
    members = (
        next(g for g in old_cut.groups if g.id == pin.owner).blocks
        if any(g.id == pin.owner for g in old_cut.groups)
        else (pin.owner,)
    )
    for member in members:
        link = lineage.link_of_old(member)
        if link is None or link.cls not in FOLLOWS:
            why = "its block was deleted" if link is None else _LAPSES.get(link.cls, "")
            return dataclasses.replace(at_old, state=lapsed(why))
    owner_now = _as_it_stands(
        pin, members, lineage, {h: i for i, h in handle_of.items()}, new_handle_of
    )
    if owner_now is not None:
        return dataclasses.replace(at_old, owner=owner_now)
    # the sheet without the pin's edit beside the sheet with it: their
    # difference, carried, is the pin on NEW's sheet
    shown, raw = (
        edges_file.carry(
            old,
            text,
            old_cut,
            new_cut,
            files,
            new=new,
            lineage=lineage,
            cuts_weighed=False,
        ).sheet
        for text in (unpinned(sheet, [at_old]), sheet)
    )
    held = [
        dataclasses.replace(p, kind=pin.kind)
        for p in edge_pins(shown, raw)
        if p.kind is _as_diffed(pin.kind)
    ]
    if len(held) != 1:
        return dataclasses.replace(at_old, state=lapsed("its quote is gone"))
    return dataclasses.replace(held[0], rank=pin.rank)
