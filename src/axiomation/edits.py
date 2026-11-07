"""An edit on a kept state.

A state is what the store's answers give. An edit is an answer given on
the state's version, by a person or an agent, to its stage's own file as
the kept state shows it: the kept files materialized, the kept cut, and
the edges answer the kept export names laid on that cut (`Kept`). Nothing
replays the run, so an edit works after any brief changed.

An edges edit is applied at once by the step a run's person rounds take
(`driver.run_person_rounds`, seeded with the state's applied rounds and
pins, `KeptRounds`), and kept as an `edit` state of the same version. Its
export is the kept export with its graph's halves written again by the
export's own functions from the step's result (the edge half; the node
half where a standing added a claim role; a build's rule flags,
which read the edges), and the step's provenance added to its `review` in
the export's own key order: so a replay of the run gives the same bytes.

A cut, roles or groups edit is applied by the same step, which asks what
it reopens (a cut edit the touched roles, then the edges' re-ask) through
the runner it is given; its export the kept one taken onto the final cut
as the run's own writer takes it. Unanswered, the edit stays
recorded and pending, no state is made, and the same command resumes it.
"""

from __future__ import annotations

import dataclasses
import itertools
import re
import tempfile
from collections.abc import Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from axiomation import person, versions
from axiomation.assembler import corrected_cut
from axiomation.changes import Layer
from axiomation.cutter.model import Cut, CutRules, DisplayRule, GivenHandles
from axiomation.driver import (
    KeptRounds,
    PersonRound,
    PersonRounds,
    Proposal,
    ReviewSettings,
    checker,
    claim_notes,
    cut_corrections_payload,
    decided_by,
    decided_on,
    flags_raised,
    person_payload,
    pinned_at,
    roles_changed,
    run_person_rounds,
)
from axiomation.exchange import (
    Link,
    answer_link,
    cut_from_payload,
    cut_payload,
    dumps,
    edges_payload,
    flag_payload,
    line_authors,
    line_keys,
    vocabulary_of,
)
from axiomation.flags import (
    BY_AGENT,
    BY_RULE,
    Raised,
    rule_flags,
    vocabulary_flags,
)
from axiomation.identity import (
    BlockClass,
    ExcerptClass,
    Lineage,
    Tier,
    Version,
    marks_any,
    pair,
    vocabulary_marks,
)
from axiomation.identity import Link as LineageLink
from axiomation.marks import Mark, by_owner, open_marks
from axiomation.person import Pin, PinKind
from axiomation.questions import (
    Question,
    Stage,
    edges_file,
    roles_file,
    vocabulary_file,
)
from axiomation.questions import edge_sheet as es
from axiomation.questions.edges_file import (  # noqa: F401 - edits' own names
    ADD,
    KEEP,
    RETRACT,
    Beside,
    CarriedExcerpt,
    Fragment,
)
from axiomation.reader import Document, document_hash, read_paper
from axiomation.records import (
    AGENT,
    GIVEN_HOSTS,
    IDENTITY,
    PERSON,
    RESTATES_NONE,
    AnswerRecord,
    CutCorrection,
    EdgeRecords,
    RecordStore,
    Signal,
)
from axiomation.resolver import Attempt, Recorded, Resolver
from axiomation.run_state import RunFile, text_of
from axiomation.runner import Runner, RunReport
from axiomation.update import (
    MarkContext,
    Remark,
    blocks_onto,
    carrying,
    dropped_start_flags,
    excerpts_onto,
    folded,
    handles_onto,
    heir_of,
    marks_entries,
    marks_from,
    moved_ids,
    moved_pins,
    sessions_onto,
)
from axiomation.versions import Command, State

#: The edge half's keys in an export.
EDGE_HALF = ("entries", "edges", "self", "standings", "ambient", "handles")
#: The node half's keys, which a standing's claim role changes.
NODE_HALF = ("blocks", "parts", "groups", "candidates")


class EditError(ValueError):
    """An edit the kept state cannot take, with why."""


@dataclass(frozen=True, slots=True)
class Author:
    """Who gives an edit: a person, or an agent with its model and the
    session it ran in."""

    host: str = PERSON
    model: str = PERSON
    session: str | None = None

    @property
    def is_person(self) -> bool:
        return self.host == PERSON


#: A person, the default author.
A_PERSON = Author()


def agent(model: str, session: str | None) -> Author:
    return Author(AGENT, model, session)


@dataclass(frozen=True, slots=True, eq=False)
class Kept:
    """A kept state opened for an edit: its document read from its kept
    files, its export, its cut, the paper's files a `file:` target names,
    the answer whose sheet is its edge half (None where it has none) and
    that sheet laid on its cut as a person edits it, its pins, the cut
    corrections its export lists, and the settings its stages' files are
    shown with; while open, where its files are and its open stale
    marks."""

    store: RecordStore
    state: State
    document: Document
    export: dict[str, Any]
    #: its cut named by its blocks' kept handles (`docs/model.md` § 7),
    #: the names an edit is shown and writes
    cut: Cut
    files: tuple[str, ...]
    answer: AnswerRecord | None
    raw: str | None
    sheet: str | None
    pins: tuple[Pin, ...]
    proposals: tuple[Proposal, ...]
    settings: ReviewSettings
    #: Where its files are materialized while it is open, and its open
    #: stale marks.
    directory: Path
    marks: tuple[Mark, ...] = ()
    #: What today's edges check refuses in its sheet as shown, each as
    #: `edges_file.problem_key` says it: the state's, never an edit's
    #: (`docs/model.md` § 7).
    kept_edges_problems: tuple[str, ...] = ()
    #: its cut named as its export names it: the names its edges answer
    #: and its own records were written with; `opened` sets it, and where
    #: it is None the export is named as `cut` is
    written: Cut | None = None

    @property
    def review(self) -> dict[str, Any]:
        return self.export["review"]

    @property
    def is_update(self) -> bool:
        """Whether its export is an update's shape, not a build's."""
        return "update" in self.review

    def handles(self) -> dict[str, str]:
        """The sheet's handles on its cut: handle → block or group id."""
        return edges_file.handles(self.document, self.cut)

    @property
    def as_written(self) -> Cut:
        """Its cut named as its export's own records name it."""
        return self.written or self.cut


@contextmanager
def opened(
    store: RecordStore,
    state: State,
    settings: ReviewSettings | None = None,
    marks: Sequence[Mark] | None = None,
) -> Iterator[Kept]:
    """`state` opened for an edit, its kept files materialized for as long
    as it is open; refused where its edges do not read back from their
    answer on its cut, naming the difference. Its stages' files are shown,
    and what an edit reopens asked, under `settings` (the profile's; a
    build's export's own chunk, overlap, outline and stages over them), with
    the state's open marks (`marks`, read here where not given)."""
    with tempfile.TemporaryDirectory(prefix="axiomation-kept-") as directory:
        document = versions.materialize(store, state, Path(directory))
        export = versions.kept_payload(store, state)
        missing = [k for k in ("rules", "review") if k not in export]
        if missing:
            # a key's export carries where its blocks lie and its edges
            # alone: no run's cut, so no stage's file to show or edit
            raise EditError(
                f"state {state.id}: its export names no {' and no '.join(missing)}, "
                "so it is no run's cut to open"
            )
        rules = CutRules(DisplayRule(export["rules"]["displays"]))
        written = edges_file.named(
            document, cut_from_payload(document, export, rules, roles=True)
        )
        names = versions.handles(store, state)
        at = names.at(state)
        cut = dataclasses.replace(
            written, handles=GivenHandles(at, names.borne - set(at.values()))
        )
        files = versions.kept_sheet_files(
            store, state, Path(directory), document.never_included
        )
        answer, raw = _edge_answer(
            store.seen_from(state.id), export, document, written, files
        )
        sheet = (
            None
            if raw is None
            else edges_file.person_sheet(document, raw, written, cut, files)
        )
        review = export["review"]
        found = Kept(
            store=store,
            state=state,
            document=document,
            export=export,
            cut=cut,
            files=files,
            answer=answer,
            raw=raw,
            sheet=sheet,
            pins=tuple(_pin(p) for p in (review.get("person") or {}).get("pins", [])),
            proposals=_proposals(store, review),
            settings=_settings(review, settings, files),
            directory=Path(directory),
            marks=tuple(open_marks(store, state) if marks is None else marks),
            written=written,
        )
        problems = read_back(found)
        if problems:
            raise EditError(
                f"state {state.id}: its edges do not read back from answer "
                f"{answer.key[:12] if answer else '?'} on its cut: "
                + "; ".join(problems)
            )
        if sheet is not None:
            found = dataclasses.replace(
                found,
                kept_edges_problems=edges_file.kept_problems(
                    shown(found, Layer.EDGES), found.settings.files
                ),
            )
        yield found


def _settings(
    review: Mapping[str, Any],
    settings: ReviewSettings | None,
    files: tuple[str, ...],
) -> ReviewSettings:
    """The settings an edit on a state is shown and asked under: those its
    export records (a build's: chunk, overlap, outline, stages), else
    `settings`' (an update's export records none; every stage by default),
    on the state's files, its cut given."""
    base = settings or ReviewSettings(stages=tuple(Stage))
    chunk = review.get("chunk", base.chunk)
    return dataclasses.replace(
        base,
        chunk=None if chunk == "paper" else chunk,
        overlap=review.get("overlap", base.overlap),
        outline=review.get("outline", base.outline),
        stages=tuple(Stage(s) for s in review["stages"])
        if "stages" in review
        else base.stages,
        files=files,
        given_cut=True,
    )


def _edge_answer(
    store: RecordStore,
    export: Mapping[str, Any],
    document: Document,
    cut: Cut,
    files: Sequence[str],
) -> tuple[AnswerRecord | None, str | None]:
    """The answer the export's edges name and their sheet on `cut`
    (`versions.kept_edges`), refused as an edit's error."""
    try:
        return versions.kept_edges(store, export, document, cut, files)
    except versions.VersionError as error:
        raise EditError(str(error)) from None


def read_back(kept: Kept) -> list[str]:
    """Where the kept edge half differs from its sheet read back on the
    kept cut, per key of the edge half; none where they are equal (record
    ids and provenance aside): the sheet, rendered from the export, says
    what the export holds."""
    if kept.sheet is None or kept.answer is None:
        return []
    handles = kept.handles()
    payload = edges_payload(
        kept.document,
        kept.cut,
        edges_file.records(kept.sheet, handles),
        kept.answer,
        handles,
    )
    # the sheet is shown by the kept handles, the export written by its own
    kept_name = {ident: h for h, ident in handles.items()}
    exported = {
        **kept.export,
        "handles": {
            i: kept_name.get(i, h) for i, h in kept.export.get("handles", {}).items()
        },
    }
    written = _written_handles(kept.export)
    return [
        f"{key}: {len(_bare(payload, key, written))} read, "
        f"{len(_bare(exported, key, written))} kept"
        for key in EDGE_HALF
        if _bare(payload, key, written) != _bare(exported, key, written)
    ]


def _written_handles(export: Mapping[str, Any]) -> set[tuple[str, str]]:
    """The claims whose handle the export keeps, `(claim words, handle)`:
    any other handle is minted from the words (the sheet shown writes it
    on the line), so a standing without a kept handle says the same
    lines."""
    return {
        (es.norm(s["claim"]["text"]), s["handle"])
        for s in export.get("standings", [])
        if s.get("handle")
    }


def _bare(
    payload: Mapping[str, Any], key: str, written: Collection[tuple[str, str]] = ()
) -> Any:
    """An edge half's list with each item's record id and author set aside,
    and a standing's handle where its line does not write it (`written`):
    the read-back compares what the lines say."""
    value = payload.get(key)
    if key == "standings":
        return [
            {
                k: v
                for k, v in e.items()
                if k not in ("id", "provenance")
                and (k != "handle" or (es.norm(e["claim"]["text"]), v) in written)
            }
            for e in value or []
        ]
    if key in ("edges", "self"):
        return [
            {k: v for k, v in e.items() if k not in ("id", "provenance")}
            for e in value or []
        ]
    return value


def _pin(data: Mapping[str, Any]) -> Pin:
    """A pin as an export's `review.person.pins` holds it."""
    return Pin(
        Layer(data["layer"]),
        data["block"],
        PinKind(data["kind"]),
        data["line"],
        data["state"],
        rank=data.get("rank", 0),
    )


def _proposals(store: RecordStore, review: Mapping[str, Any]) -> tuple[Proposal, ...]:
    """The cut corrections the export lists, as the driver's proposals:
    each listed correction from the store by its id, with its round,
    stage, whether it applied and who gave it (its blocks before, which no
    later step reads, left out)."""
    by_id = {c.id: c for c in store.corrections()}
    return tuple(
        Proposal(
            by_id[entry["id"]],
            entry["round"],
            entry["stage"],
            entry["applied"],
            entry["problem"],
            (),
            tuple(entry["changed"]["after"]),
            tuple(entry.get("reverses", ())),
            entry.get("by"),
        )
        for entry in review.get("cut_corrections", [])
        if entry["id"] in by_id
    )


# --- the question and the answer ---------------------------------------------


def question(
    kept: Kept, layer: Layer, edited: str | None, author: Author = A_PERSON
) -> Question:
    """The question whose file `author` edits on the kept state, its key
    the given answer's for `edited` (the stage's, to show it, where None).
    An agent's edges gate holds every person's pin the state holds
    (`person.pinned_question`); a person's holds none: a person's later
    word takes the place of an earlier one."""
    if layer is Layer.EDGES and kept.sheet is None:
        raise EditError(f"state {kept.state.id} holds no edges to edit")
    notes = claim_notes(kept.proposals, kept.cut)
    beside = beside_lines(kept) if layer is Layer.EDGES else None

    def build(pins: Sequence[tuple[str, str, str]]) -> Question:
        if edited is None:
            return person.stage_question(
                kept.document,
                kept.cut,
                layer,
                kept.settings,
                kept.sheet,
                notes,
                author.host,
                pins,
                beside,
            )
        return person.person_question(
            kept.document,
            kept.cut,
            layer,
            kept.settings,
            kept.sheet,
            edited,
            notes,
            author.host,
            pins,
            beside,
            kept.kept_edges_problems,
        )

    if layer is not Layer.EDGES or author.is_person:
        return build(())
    return person.pinned_question(build, _edge_pins(kept))


def beside_lines(kept: Kept) -> Beside:
    """What the kept state's sheet shows beside its lines: each open stale mark under
    its owner, and on an update's state each Δ entry under its block, as its record
    holds it now (a deleted block's stands under none)."""
    handle_of = {ident: h for h, ident in kept.handles().items()}
    deltas = []
    for d in (kept.review.get("update") or {}).get("deltas", []):
        if d["by"] == "rule":
            continue
        if d["block"] not in handle_of:
            raise EditError(
                f"state {kept.state.id}: its record's Δ on {d['block']} names no "
                "block of its cut"
            )
        deltas.append(
            es.DeltaLine(handle_of[d["block"]], d["entry"] or "", d["class"], d["note"])
        )
    return Beside(by_owner(kept.marks), deltas)


def _edge_pins(kept: Kept) -> tuple[Pin, ...]:
    """The state's held edges pins on its sheet, by handle, with their
    parts' words."""
    assert kept.sheet is not None
    handle_of = {ident: h for h, ident in kept.handles().items()}
    held = [
        Pin(
            p.layer,
            handle_of.get(p.owner, p.owner),
            p.kind,
            p.line,
            p.state,
            rank=p.rank,
        )
        for p in kept.pins
        if p.layer is Layer.EDGES and p.held
    ]
    return person.with_words(held, es.parse(kept.sheet))


def shown(kept: Kept, layer: Layer) -> str:
    """The stage's file as the kept state shows it, for editing."""
    return person.shown_file(question(kept, layer, None))


def kept_problems(kept: Kept, layer: Layer) -> tuple[str, ...]:
    """What today's check refuses in the stage's file as the kept state
    shows it: the state's, never an edit's, so no edit is refused for one
    (`docs/model.md` § 7)."""
    if layer is Layer.EDGES:
        return kept.kept_edges_problems
    text = shown(kept, layer)
    asked = question(kept, layer, text)
    if layer is Layer.ROLES:
        return roles_file.kept_problems(asked, kept.document)
    return tuple(checker(kept.document)(asked, text).problems)


@dataclass(frozen=True, slots=True)
class Given:
    """An edit given: refused with its problems (nothing recorded), or its recorded
    answer and the state it made; None where the step it takes is not whole
    (`unanswered`, the questions it asked that the store holds no answer to): the edit
    stays pending, and the same command resumes it. `reports`: the runner's, one per
    question asked."""

    problems: tuple[str, ...] = ()
    recorded: Recorded | None = None
    state: State | None = None
    unanswered: tuple[Question, ...] = ()
    reports: tuple[RunReport, ...] = ()


def edit_problems(
    kept: Kept, layer: Layer, edited: str, author: Author = A_PERSON
) -> tuple[str, ...]:
    """What the stage's own check refuses in `edited` given by `author` on
    the kept state, as `give` checks it; nothing recorded."""
    found_question = question(kept, layer, edited, author)
    return tuple(
        person.checked(found_question, edited, checker(kept.document)).problems
    )


def give(
    kept: Kept,
    layer: Layer,
    edited: str,
    author: Author = A_PERSON,
    reason: str | None = None,
    runner: Runner | None = None,
    marks_shown: bool = True,
) -> Given:
    """`edited`, the stage's file, given by `author` on the kept state:
    checked by its stage's own check, then recorded (host and model the
    author's, cost 0, the version it was given on, an agent's session, the
    reason, and whether its author was shown the state's stale marks:
    `marks_shown` False for lines given without the sheet, which re-read
    no owner), then applied by the person rounds' step (`apply`), which asks
    what a cut, roles or groups edit reopens through `runner`. Refused,
    with its problems, where the check fails. The same edit given again
    while no state applied it resumes it."""
    found_question = question(kept, layer, edited, author)
    # a stale mark's line dropped is information dropped, never an edit
    if _unmarked(edited) == _unmarked(person.shown_file(found_question)):
        raise EditError("the file is as the state shows it: nothing to record")
    refusal = refused(kept, found_question.key)
    if refusal is not None:
        raise EditError(refusal)
    store = kept.store
    given = store.authoritative().get(found_question.key)
    if given is not None:
        pending = versions.pending_answers(store, kept.state.version)
        if given.key not in {a.key for a in pending}:
            raise EditError(
                f"the store holds this edit already ({found_question.key[:12]})"
            )
        recorded = Recorded(given, tuple(store.corrections_of(given.key)))
        resumed = person.checked(found_question, edited, checker(kept.document))
        text = edited if resumed.kept is None else resumed.kept
    else:
        found = person.checked(found_question, edited, checker(kept.document))
        if not found.valid:
            return Given(problems=found.problems)
        if not author.is_person and layer is not Layer.EDGES:
            changed = pins_changed(kept, found.corrections)
            if changed:
                raise EditError(
                    "an agent may not change what a person pinned: "
                    + ", ".join(changed)
                )
        # the text as recorded: the check's kept form where it gave one
        text = edited if found.kept is None else found.kept
        resolver = Resolver(store)
        with resolver.claim():
            recorded = resolver.record(
                Attempt(
                    found_question,
                    author.host,
                    author.model,
                    edited,
                    0.0,
                    0.0,
                    found,
                    version=document_hash(kept.document),
                    session=author.session,
                    reason=reason,
                    marks_shown=None if marks_shown else False,
                    state=kept.state.id,
                )
            )
    made = apply(kept, recorded.answer, text, runner)
    return Given(
        recorded=recorded,
        state=made.state,
        unanswered=made.unanswered,
        reports=made.reports,
    )


# --- the vocabulary map given on a kept state --------------------------------------


def unmapped(kept: Kept) -> Document:
    """The kept state's document read without a map: the candidates' own
    reading, which the map's file shows; of the state's version, as its
    kept files are (`versions.materialize`)."""
    found = read_paper(kept.directory, Path(kept.state.root))
    return dataclasses.replace(found, version=kept.document.version)


def vocabulary_shown(kept: Kept) -> str:
    """`vocabulary.md` as the kept state shows it: every name the reader's
    tables do not know, each answered as the state's map answers it."""
    asked = person.vocabulary_question(unmapped(kept), vocabulary_of(kept.export), "")
    if asked is None:
        raise EditError(f"state {kept.state.id}: the paper has no name to map")
    return person.shown_file(asked)


def give_vocabulary(
    kept: Kept, edited: str, author: Author = A_PERSON, reason: str | None = None
) -> Recorded:
    """`edited`, the whole map, given by `author` on the kept state: checked
    by the map's own check, then recorded as `give` records an edit; applied
    by the update of its version onto itself (`axiomation edit --stage
    vocabulary`), which reads the paper with it, re-cuts what each changed
    entry reads and asks what that reopens. Refused where it changes nothing
    the reader reads; and, for an agent, where it changes a person's answer
    or would re-cut a block or group a person pinned. The same map given
    again while no state applied it resumes it."""
    bare = unmapped(kept)
    before = vocabulary_of(kept.export)
    asked = person.vocabulary_question(bare, before, edited, author.host)
    if asked is None:
        raise EditError(f"state {kept.state.id}: the paper has no name to map")
    if edited == person.shown_file(asked):
        raise EditError("the file is as the state shows it: nothing to record")
    refusal = refused(kept, asked.key)
    if refusal is not None:
        raise EditError(refusal)
    store = kept.store
    given = store.authoritative().get(asked.key)
    if given is not None:
        pending = versions.pending_answers(store, kept.state.version)
        if given.key not in {a.key for a in pending}:
            raise EditError(f"the store holds this edit already ({asked.key[:12]})")
        return Recorded(given, ())
    found = vocabulary_file.check(asked, edited, bare)
    if not found.valid:
        raise EditError("; ".join(found.problems))
    after = vocabulary_file.given_map(
        vocabulary_file.answers_of(found.vocabulary), before, author.host
    )
    changed = vocabulary_file.changed_entries(before, after)
    if not changed:
        raise EditError(
            "the map reads the same: no entry's kind changed, nothing to record"
        )
    if not author.is_person:
        theirs = [
            f"{a.namespace} {a.name}"
            for a in changed
            if (held := before.answer(a.namespace, a.name, a.declaration)) is not None
            and held.by == PERSON
        ]
        mapped = dataclasses.replace(
            read_paper(kept.directory, Path(kept.state.root), after),
            version=kept.document.version,
        )
        reached = _pins_reached(kept, mapped)
        if theirs or reached:
            raise EditError(
                "an agent may not change what a person pinned: "
                + ", ".join([*theirs, *reached])
            )
    resolver = Resolver(store)
    with resolver.claim():
        return resolver.record(
            Attempt(
                asked,
                author.host,
                author.model,
                edited,
                0.0,
                0.0,
                found,
                version=document_hash(kept.document),
                session=author.session,
                reason=reason,
                shown=person.shown_hash(asked),
                state=kept.state.id,
            )
        )


def _pins_reached(kept: Kept, after: Document) -> list[str]:
    """The person's cut, roles and groups pins on the kept state that the map
    `after` was read with would re-cut: each on a block, or a group of
    blocks, owning a span a changed entry reads."""
    marks = vocabulary_marks(kept.document, after)
    if not marks:
        return []
    blocks = {b.id: b for b in kept.cut.blocks}
    groups = {g.id: g for g in kept.cut.groups}
    reached = []
    for pin in kept.pins:
        if pin.layer is Layer.EDGES or not pin.held:
            continue
        owners = groups[pin.owner].blocks if pin.owner in groups else (pin.owner,)
        if any(o in blocks and marks_any(blocks[o].owned, marks.old) for o in owners):
            reached.append(f"{pin.layer.value} {pin.owner}")
    return list(dict.fromkeys(reached))


def corrected_export(kept: Kept, edited: str, author: Author) -> dict[str, Any]:
    """The kept export with an edges edit's lines in place, made and not
    recorded: the graph a review's readings rest on as its own edge lines
    correct it (`docs/model.md` § 8). The edit's records are numbered here
    as the resolver numbers them, onto the kept cut, which an edges edit
    leaves as it is. Refused, with its problems, where its check fails."""
    asked = question(kept, Layer.EDGES, edited, author)
    found = person.checked(asked, edited, checker(kept.document))
    if not found.valid:
        raise EditError("; ".join(found.problems))
    assert asked.input_cut is not None
    ids = (f"shown-{n}" for n in itertools.count(1))
    held = found.edges
    edges = EdgeRecords(
        *(
            tuple(dataclasses.replace(r, id=next(ids), answer=asked.key) for r in kind)
            for kind in (
                held.entries,
                held.edges,
                held.selfs,
                held.standings,
                held.frame,
                held.deltas,
                held.orders,
            )
        )
    )
    answer = AnswerRecord(
        key=asked.key,
        kind=asked.kind,
        stage=asked.stage.value,
        template=asked.template.ref,
        attempt=1,
        host=author.host,
        model=author.model,
        time="",
        seconds=0.0,
        cost=0.0,
        raw=edited,
        valid=True,
    )
    return {
        **kept.export,
        **edges_payload(kept.document, asked.input_cut, edges, answer, asked.handles),
    }


def _unmarked(text: str) -> str:
    """`text` without its stale marks' lines."""
    return "\n".join(line for line in text.split("\n") if not es.MARK.match(line))


def refused(kept: Kept, key: str) -> str | None:
    """Why the kept state takes no edit (its answer `key`) before anything
    is recorded, else None: a state that is not its version's latest (an
    edit there would fork the version's answers, which a replay applies in
    store order); a version that holds another edit no state applied (an
    edit now would be given on a state that does not hold it); a build's
    state whose rule flags do not stand last, where the step writes them
    again."""
    store, state = kept.store, kept.state
    latest = [
        s for s in versions.states(store, waiting=True) if s.version == state.version
    ][-1]
    if latest.id != state.id:
        return (
            f"state {state.id} is not its version's latest ({latest.id}): an edit "
            "there would fork the version; edit the latest"
        )
    pending = [
        a for a in versions.pending_answers(store, state.version) if a.key != key
    ]
    if pending:
        named = ", ".join(f"{a.stage} {a.key[:12]} by {a.host}" for a in pending)
        return (
            f"version {state.version[:12]} holds an edit no state applied: {named}; "
            "an edit now would be given on a state that does not hold it: resume "
            "that edit first, by its own command and file (the store holds the "
            "file as the answer's raw), which asks what it reopens"
        )
    if not kept.is_update and _rule_tail(kept) is None:
        return (
            f"state {state.id}: its rule flags do not stand last, as a build lists them"
        )
    if _flag_parts(kept) is None:
        return f"state {state.id}: its flags are not in a run's order"
    return None


def pins_changed(kept: Kept, corrections: Sequence[CutCorrection]) -> list[str]:
    """The places a person pinned that `corrections`, an agent's, would
    change on the kept cut, as a model's correction is weighed against them
    (`driver.pinned_at`), the roles of a roles-pinned block, and any pin
    the changed cut would lapse (`person.held_on`)."""
    held = [p for p in kept.pins if p.layer is not Layer.EDGES and p.held]
    pinned = person.pinned_of(held, (), kept.cut)
    if not pinned:
        return []
    trial = corrected_cut(kept.document, kept.cut, list(corrections)).cut
    kept_ids = {b.id for b in trial.blocks}
    roles = roles_changed(kept.cut, trial, [b for b in pinned.roles if b in kept_ids])
    # a pin the trial cut would lapse: a group a person dissolved made again
    lapsed = [
        p.owner
        for p, q in zip(held, person.held_on(held, trial), strict=True)
        if not q.held
    ]
    return list(
        dict.fromkeys(
            [*pinned_at(pinned, kept.cut, trial, corrections), *roles, *lapsed]
        )
    )


# --- the step from the kept state ----------------------------------------------


@dataclass(frozen=True, slots=True)
class Applied:
    """An answer applied to the kept state: the state it made, or None with
    the questions still unanswered; the runner's reports."""

    state: State | None
    unanswered: tuple[Question, ...] = ()
    reports: tuple[RunReport, ...] = ()


@dataclass(frozen=True, slots=True, eq=False)
class Step:
    """What the person rounds' step gave from the kept state: the questions
    it asked (or the given edges answer's), the edge half and its answer
    (None where no edges answer is new), the cut corrections it applied,
    the proposals it added and all of them, its rounds, the pins, and the
    texts of its edges answers, by key, as the edit and its run hold them."""

    questions: dict[Stage, tuple[Question, ...]]
    edges: EdgeRecords
    edges_answer: AnswerRecord | None
    corrections: tuple[CutCorrection, ...]
    added: tuple[Proposal, ...]
    proposals: tuple[Proposal, ...]
    done: PersonRounds
    pins: tuple[Pin, ...]
    texts: Mapping[str, str] = dataclasses.field(default_factory=dict)

    @property
    def edges_question(self) -> Question | None:
        """The question of the edge half, where the step gave a new one."""
        if self.edges_answer is None:
            return None
        return next(
            q for q in self.questions[Stage.EDGES] if q.key == self.edges_answer.key
        )


def apply(
    kept: Kept, answer: AnswerRecord, text: str, runner: Runner | None = None
) -> Applied:
    """The answer `answer`, given on the kept state as `text`, applied by the person
    rounds' own step (`driver.run_person_rounds`), seeded with the state's
    rounds and pins: an edges answer becomes the edge half; a cut, roles or
    groups answer asks, through `runner`, what it reopens (a cut edit the
    touched roles, then the edges' re-ask). Whole, it is kept as an `edit`
    state of its version, its parent the kept state; else no state is
    made, and the unanswered questions are returned. Its working state
    (`run_state`) holds the re-ask's text while it runs, and goes once the
    state is kept."""
    document = kept.document
    review = kept.review
    run = RunFile(kept.store.run_directory, "edit", answer.key, kept.state.id)
    if runner is not None:
        runner.resolver.run = run
    # an update's export lists OLD's carried pins (ranked below every answer
    # of its own) apart from the pins of its own rounds
    carried = [p for p in kept.pins if p.rank < 0]
    own = [p for p in kept.pins if p.rank >= 0]
    person_review = review.get("person") or {}
    rounds = tuple(
        PersonRound(
            a["round"],
            a["key"],
            a["stage"],
            tuple(
                p for p in own if p.layer is not Layer.EDGES and p.rank == a["round"]
            ),
            by=a.get("by", PERSON),
        )
        for a in person_review.get("applied", [])
    )
    questions: dict[Stage, tuple[Question, ...]] = {}
    corrections: list[CutCorrection] = []
    proposals: list[Proposal] = list(kept.proposals)
    start = len(proposals)
    reports: list[RunReport] = []
    required: list[Question] = []
    edges, edges_answer, done = run_person_rounds(
        document,
        kept.cut,
        kept.store,
        kept.settings,
        runner,
        checker(document),
        questions=questions,
        corrections=corrections,
        proposals=proposals,
        reports=reports,
        required=required,
        edges=_kept_records(kept),
        edges_answer=None,
        carried=None if kept.raw is None else (kept.raw, kept.as_written),
        pinned=person.pinned_of(
            [p for p in carried if p.layer is not Layer.EDGES],
            _pairs(kept, [p for p in carried if p.layer is Layer.EDGES and p.held]),
            kept.cut,
        ),
        kept=KeptRounds(
            rounds,
            _pairs(kept, [p for p in own if p.layer is Layer.EDGES]),
            (answer,),
            tuple(person_review.get("stale", [])),
            {answer.key: text},
        ),
        run=run,
    )
    if done.pending is not None:
        answered = kept.store.authoritative()
        return Applied(
            None,
            tuple(q for q in required if q.key not in answered),
            tuple(reports),
        )
    if (
        answer.key in done.stale
        or not done.applied
        or done.applied[-1].key != answer.key
    ):
        raise EditError(
            f"answer {answer.key[:12]} is not an edit of state {kept.state.id}: "
            "nothing applied"
        )
    final_cut = corrected_cut(document, kept.cut, corrections).cut
    held = kept.store.authoritative()
    step = Step(
        questions,
        edges,
        edges_answer,
        tuple(corrections),
        tuple(proposals[start:]),
        tuple(proposals),
        done,
        (),
        {
            q.key: text
            if q.key == answer.key
            else text_of(held[q.key], run, kept.store.run_directory)
            for q in questions.get(Stage.EDGES, ())
            if q.key == answer.key or q.key in held
        },
    )
    asked = step.edges_question
    sheet, sheet_cut = (kept.raw, kept.as_written)
    if edges_answer is not None and asked is not None:
        assert asked.input_cut is not None
        sheet = step.texts[edges_answer.key]
        sheet_cut = asked.input_cut
    mine = list(done.pins)
    if edges_answer is None:
        # nothing re-asked: the edges and their sheet are the kept ones, and
        # the rounds decided none of the state's edges pins; a replay
        # decides them on that same sheet, after the others
        mine += [p for p in own if p.layer is Layer.EDGES]
    taken = {person.pin_place(p) for p in mine}
    pins = [
        *(
            p
            for p in _carried_after(kept, carried, sheet, sheet_cut, final_cut, done)
            if person.pin_place(p) not in taken
        ),
        *mine,
    ]
    export = stepped_export(kept, dataclasses.replace(step, pins=tuple(pins)))
    files = {
        path: versions.blob(kept.store, name) for path, name in kept.state.files.items()
    }
    made, _ = versions.record(
        kept.store,
        files,
        kept.state.version,
        kept.state.root,
        _dumps(export),
        Command.EDIT,
        started_from=kept.state,
        identity=kept.state.identity,
        answers=[*kept.state.answers, answer.key],
    )
    run.kept()
    return Applied(made, (), tuple(reports))


def _kept_records(kept: Kept) -> EdgeRecords:
    """The kept edge half's records, read from its sheet on its cut."""
    if kept.sheet is None:
        return EdgeRecords()
    return edges_file.records(kept.sheet, kept.handles())


def _by_handle(kept: Kept, pins: Sequence[Pin]) -> list[Pin]:
    """Edges pins as a sheet names them: by their owners' handles."""
    handle_of = {ident: h for h, ident in kept.handles().items()}
    return [dataclasses.replace(p, owner=handle_of.get(p.owner, p.owner)) for p in pins]


def _pairs(kept: Kept, pins: Sequence[Pin]) -> tuple[person.EdgePins, ...]:
    """Edges pins, held or lapsed, as the pairs of sheets on the kept cut
    the person rounds carry, one pair per answer (its rank): the sheet as
    it stood before the answer's edit beside the sheet with it, each pin's
    line put in or taken off so the pair gives exactly its pins (a kept
    line by its keep mark). The rounds then decide every pin again on the
    new sheet, as a replay does."""
    if kept.sheet is None or not pins:
        return ()
    named = _by_handle(kept, pins)
    sheet = kept.sheet
    written = edges_file.written_lines(sheet)
    return tuple(
        person.EdgePins(
            _lines_as(sheet, written, [p for p in named if p.rank == rank], True),
            _lines_as(sheet, written, [p for p in named if p.rank == rank], False),
            kept.cut,
            rank,
        )
        for rank in sorted({p.rank for p in named})
    )


def _lines_as(
    sheet: str,
    written: Mapping[str, Sequence[str]],
    pins: Sequence[Pin],
    before: bool,
) -> str:
    """`sheet` with each pin's line as the sheet before its answer held it
    (`before`: a written or kept line absent, a deleted one present), else
    as its answer left it (a written line present, a deleted one absent, a
    kept one present and marked); `written`, the sheet's lines by owner."""
    # the kind a pin's line is absent under: a written one before its
    # answer, a deleted one after it; a kept one is there on both sides
    gone = PinKind.WRITTEN if before else PinKind.DELETED
    present = {
        (p.owner, p.line)
        for p in pins
        if p.kind is not gone and p.line not in written.get(p.owner, [])
    }
    absent = {(p.owner, p.line) for p in pins if p.kind is gone}
    kept_lines = {
        (p.owner, p.line) for p in pins if p.kind is PinKind.KEPT and not before
    }
    adds: dict[str, list[str]] = {}
    for owner, line in present:
        adds.setdefault(owner, []).append(line)
    out: list[str] = []
    owner: str | None = None

    def close() -> None:
        if owner is None or owner not in adds:
            return
        at = len(out)
        while at and (out[at - 1] == "" or es.DONE.match(out[at - 1])):
            at -= 1
        out[at:at] = sorted(adds[owner])

    for line in sheet.split("\n"):
        if m := (es.BLOCK.match(line) or es.GROUP.match(line) or es.END.match(line)):
            close()
            owner = None if es.GROUP.match(line) else m.group(1)
        elif owner is not None and (owner, line) in absent:
            continue
        elif owner is not None and (owner, line) in kept_lines:
            line = es.KEEP_MARK + line
        out.append(line)
    close()
    return "\n".join(out)


def _carried_after(
    kept: Kept,
    carried: Sequence[Pin],
    raw: str | None,
    sheet_cut: Cut,
    final_cut: Cut,
    done: PersonRounds,
) -> list[Pin]:
    """OLD's pins an update carried, as its export reports them after the
    step (`update._pins_reported`), each in its place: a cut, roles or
    groups pin as the final cut holds it, one on a block a person's cut
    edit removed lapsed; a held edges pin decided again on the final sheet
    (`raw` on `sheet_cut`, `person.as_left`), keeping its state (held, or
    held with its meaning moved) while it holds; the rest as they were."""
    found = list(carried)
    for n, pin in enumerate(carried):
        if pin.layer is Layer.EDGES or not pin.held:
            continue
        (now,) = person.held_on([pin], final_cut)
        if now.held and now.owner in done.cut_away:
            now = dataclasses.replace(
                now, state=person.lapsed("a person's cut edit changed its block")
            )
        found[n] = now
    edges = [n for n, p in enumerate(carried) if p.layer is Layer.EDGES and p.held]
    if not edges or raw is None:
        return found
    roles = {p.owner for p in kept.pins if p.layer is Layer.ROLES and p.held}
    handle_of = {i: h for h, i in edges_file.handles(kept.document, sheet_cut).items()}
    decided = person.as_left(
        person.with_words(
            [
                dataclasses.replace(
                    carried[n], owner=handle_of.get(carried[n].owner, carried[n].owner)
                )
                for n in edges
            ],
            es.parse(raw),
        ),
        raw,
        edges_file.structure(kept.document, sheet_cut, kept.files, roles),
    )
    for n, pin in zip(edges, decided, strict=True):
        if not pin.held:
            found[n] = dataclasses.replace(found[n], state=pin.state)
    return found


def stepped_export(kept: Kept, step: Step) -> dict[str, Any]:
    """The kept export with the step applied, as a replay of the run writes
    it: the node half by `cut_payload` from the final cut, with the
    corrections it applied or refused; the edge half by `edges_payload`
    where the step gave a new edges answer, each line by the answer that
    first wrote it; the new questions' templates, counts and costs in the
    run's stage order; its cut corrections; the flags (`_flags_after`); an
    update's `review.update` taken onto the final cut (`_update_after`);
    and the step's round and the pins."""
    document = kept.document
    payload = {k: v for k, v in kept.export.items() if k != "review"}
    review = dict(kept.review)
    final_cut = kept.cut
    if step.corrections:
        corrected = corrected_cut(document, kept.cut, list(step.corrections))
        final_cut = corrected.cut
        node_half = cut_payload(document, final_cut)
        for key in NODE_HALF:
            payload[key] = node_half[key]
        review["applied"] = [*review.get("applied", []), *corrected.applied]
        review["problems"] = [
            *review.get("problems", []),
            *(
                {"correction": p.correction, "message": p.message}
                for p in corrected.problems
            ),
        ]
    asked = step.edges_question
    if asked is not None and step.edges_answer is not None:
        assert asked.input_cut is not None
        answered = kept.store.authoritative()
        links = [_kept_link(kept)]
        for q in step.questions[Stage.EDGES]:
            found = answered[q.key]
            links.append(answer_link(kept.store.edge_records_of(found.key), found))
            if found.key == step.edges_answer.key:
                break
        for key, value in edges_payload(
            document,
            asked.input_cut,
            step.edges,
            step.edges_answer,
            asked.handles,
            line_authors(step.edges, links),
        ).items():
            payload[key] = value
    order = _stage_order(kept, step)
    review["templates"] = _templates_after(kept, step, order)
    review["questions"] = _counts_after(kept, step)
    if step.added:
        _place(
            review,
            "cut_corrections",
            [
                *review.get("cut_corrections", []),
                *cut_corrections_payload(document, step.added)["cut_corrections"],
            ],
            before=("edges_carried_from", "flags"),
        )
    review["flags"] = _flags_after(kept, step, final_cut)
    pins = list(step.pins)
    if kept.is_update:
        review["update"], passes = _update_after(kept, step, final_cut, order)
        # a carried pin's moved state follows each Δ the step corrected
        named = edges_file.handles(document, final_cut)
        carried = [p for p in pins if p.rank < 0]
        for record in passes:
            carried = moved_pins(
                carried, carrying(record["deltas"]), moved_ids(record, named)
            )
        pins = [*carried, *(p for p in pins if p.rank >= 0)]
    _place(
        review,
        "person",
        person_payload(dataclasses.replace(step.done, pins=tuple(pins))),
        before=("applied", "problems"),
    )
    return {**payload, "review": review}


def _stage_order(kept: Kept, step: Step) -> list[str]:
    """The stages of the run's questions in the order first asked, as the
    run's `questions` holds them, the step's new ones after (an update's
    count of 0 for the edges is a default, not a stage asked)."""
    order = [
        stage
        for stage, count in kept.review["questions"].items()
        if count or not kept.is_update
    ]
    return [*order, *(s.value for s in step.questions if s.value not in order)]


#: Each template's stage, by the field of `Templates` that holds it.
_TEMPLATE_STAGES = {
    "boundaries": Stage.BOUNDARIES,
    "judge": Stage.JUDGE,
    "roles": Stage.ROLES,
    "roles_review": Stage.ROLES,
    "groups": Stage.GROUPS,
    "edges": Stage.EDGES,
    "edges_review": Stage.EDGES,
    "edges_reask": Stage.EDGES,
    "decide": Stage.DECIDE,
    "edges_update": Stage.EDGES,
}


def _templates_after(kept: Kept, step: Step, order: Sequence[str]) -> dict[str, Any]:
    """`review.templates` with the step's questions': a template the run
    asked under already stays where it is; a new one goes where a replay
    first meets it, before the first template of a later stage."""
    stage_of = {
        getattr(kept.settings.templates, name).name: stage.value
        for name, stage in _TEMPLATE_STAGES.items()
    }
    at = {stage: n for n, stage in enumerate(order)}
    names = list(kept.review["templates"].items())
    for stage, asked in step.questions.items():
        for q in asked:
            if any(name == q.template.name for name, _ in names):
                continue
            later = next(
                (
                    n
                    for n, (name, _) in enumerate(names)
                    if at.get(stage_of.get(name, ""), -1) > at[stage.value]
                ),
                len(names),
            )
            names.insert(later, (q.template.name, q.template.version))
    return dict(names)


def _counts_after(kept: Kept, step: Step) -> dict[str, int]:
    """`review.questions` with the step's questions counted, a new stage
    after the run's; an update's edges counted last where none was asked."""
    counts = {
        stage: count
        for stage, count in kept.review["questions"].items()
        if count or not kept.is_update
    }
    for stage, asked in step.questions.items():
        counts[stage.value] = counts.get(stage.value, 0) + len(asked)
    if kept.is_update:
        counts.setdefault(Stage.EDGES.value, 0)
    return counts


def _handles_of(questions: Mapping[str, str]) -> dict[str, str]:
    """A question's handles by the block or group id each names."""
    return {ident: handle for handle, ident in questions.items()}


def _flags_after(kept: Kept, step: Step, final_cut: Cut) -> list[dict[str, Any]]:
    """The kept flags after the step, as a replay lists them: the roles' and
    the groups' (the step's touched roles' after the roles'), the last
    model edges answer's (the step's re-ask's, where it asked one), then
    the rest (a build's flags shown to the deciding step, an update's
    identity flags on the final cut) and a build's rule flags, read again
    (`_rule_tail`). Each named by the final edge half's handles, and one no
    correction decided yet decided by the step's own where one holds its
    blocks (`driver.decided_on`)."""
    document = kept.document
    flags = kept.review.get("flags", [])
    count = 0 if kept.is_update else _rule_tail(kept)
    parts = _flag_parts(kept)
    # `refused` checked both before the answer was recorded
    assert count is not None and parts is not None
    roles, groups, edges, rest = parts
    asked = step.edges_question
    handle_of = (
        _handles_of(asked.handles)
        if asked is not None
        else _handles_of(edges_file.handles(document, final_cut))
    )
    proposals = step.proposals

    def payload(raised: Raised) -> dict[str, Any]:
        return flag_payload(document, raised, handle_of, decided_by(raised, proposals))

    def kept_flag(f: dict[str, Any]) -> dict[str, Any]:
        f = {
            **f,
            "blocks": [{**b, "handle": handle_of.get(b["id"])} for b in f["blocks"]],
        }
        if f.get("decided") is None:
            f["decided"] = decided_on([b["id"] for b in f["blocks"]], step.added)
        return f

    found = [kept_flag(f) for f in roles]
    found += [
        payload(r) for r in flags_raised(kept.store, step.questions, (Stage.ROLES,))
    ]
    found += [kept_flag(f) for f in groups]
    reasked = (
        step.edges_answer is not None and step.edges_answer.host not in GIVEN_HOSTS
    )
    if reasked:
        assert asked is not None and asked.input_cut is not None
        found += [
            payload(Raised(flag, Stage.EDGES.value, asked.input_cut))
            for flag in kept.store.flags_of(asked.key)
        ]
    else:
        found += [kept_flag(f) for f in edges]
    if kept.is_update:
        return [*found, *_identity_flags(kept, rest, final_cut, step)]
    found += [kept_flag(f) for f in rest]
    tail = flags[len(flags) - count :] if count else []
    groups_rule = rule_flags(final_cut)
    if asked is None:
        # the edges and the cut they were asked on are the kept ones: their
        # rules' flags stand, the groups' rule read again on the final cut
        on_edges = [f for f in tail if f["signal"] in EDGE_RULES]
        return [
            *found,
            *(payload(r) for r in groups_rule),
            *map(kept_flag, on_edges),
            *(payload(r) for r in vocabulary_flags(document, final_cut)),
        ]
    assert asked.input_cut is not None
    return [
        *found,
        *(payload(r) for r in rule_flags(final_cut, step.edges, asked.input_cut)),
        *(payload(r) for r in vocabulary_flags(document, final_cut)),
    ]


def _flag_parts(
    kept: Kept,
) -> tuple[list[Any], list[Any], list[Any], list[Any]] | None:
    """The kept flags before a build's rule tail as a run lists them: the
    roles' and the groups' (by agent), the last model edges answer's, then
    the rest; None where they are not in that order."""
    flags = kept.review.get("flags", [])
    count = 0 if kept.is_update else _rule_tail(kept)
    if count is None:
        return None
    head = flags[: len(flags) - count]

    def by_agent(stage: Stage) -> list[dict[str, Any]]:
        return [f for f in head if f["by"] == BY_AGENT and f["stage"] == stage.value]

    roles, groups, edges = (
        by_agent(s) for s in (Stage.ROLES, Stage.GROUPS, Stage.EDGES)
    )
    known = len(roles) + len(groups) + len(edges)
    if head[:known] != [*roles, *groups, *edges]:
        return None
    return roles, groups, edges, head[known:]


def _identity_flags(
    kept: Kept, kept_flags: Sequence[dict[str, Any]], final_cut: Cut, step: Step
) -> list[dict[str, Any]]:
    """An update's identity flags (`update.dropped_start_flags`), placed on
    the final cut and named by its handles, as `update_payload` places
    them; the kept ones where the step changed no block."""
    if not step.corrections or kept.state.identity is None:
        return list(kept_flags)
    answer = kept.store.authoritative().get(kept.state.identity)
    if answer is None:
        return list(kept_flags)
    raw = versions.identity_raw(kept.store, answer)
    blocks = {b.id for b in final_cut.blocks}
    named = _handles_of(edges_file.handles(kept.document, final_cut))
    return [
        flag_payload(kept.document, Raised(flag, IDENTITY, final_cut), named)
        for flag in dropped_start_flags(raw)
        if set(flag.blocks) <= blocks
    ]


def _update_after(
    kept: Kept, step: Step, final_cut: Cut, order: Sequence[str]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """An update's `review.update` after the step, as `update_payload` takes
    it onto a round's cut: `previous` composed with the lineage from the
    kept cut to the final one; each handle onto the final cut
    (`update.handles_onto`); a writer's Δ onto its heir, its part found by
    its words on the final edge half; the excerpts (`update.excerpts_onto`);
    the cost of each answer the step asked, in the run's stage order; then
    the step's edges answers folded in as a run folds them (`update.folded`):
    what each re-read, and the Δs a person's or an agent's
    corrected, its marks given again on the update's plan rebuilt from the
    store (`_remark`). Returns it, and the record after each correction."""
    document = kept.document
    update = dict(kept.review["update"])
    within = pair(Version(document, kept.cut), Version(document, final_cut))
    # the record names its blocks as the export does
    onto_handle = handles_onto(document, kept.as_written, final_cut, within)

    def onto(handle: str | None) -> str | None:
        return None if handle is None else onto_handle.get(handle, handle)

    update["previous"] = _previous_after(update["previous"], within)
    update["reopened"] = [
        {**r, "handle": onto(r["handle"]), "from": onto(r["from"])}
        for r in update["reopened"]
    ]
    update["not_asked"] = [
        {**r, "handle": onto(r["handle"]), "from": onto(r["from"])}
        for r in update["not_asked"]
    ]
    named = _handles_of(edges_file.handles(document, final_cut))
    entries = step.edges.entries

    def delta(d: dict[str, Any]) -> dict[str, Any]:
        # a writer's entry, or one a person or an agent corrected; the
        # rule's stands under a deleted block, on no cut
        if d["by"] == "rule":
            return d
        link = within.link_of_old(d["block"])
        heirs = link.new if link is not None else ()
        block = heir_of(within, d["block"]) or d["block"]
        entry = d["entry"]
        if d["words"] is not None:
            said = es.norm(d["words"])
            block, entry = next(
                (
                    (e.block, e.label)
                    for e in entries
                    if e.block in heirs and e.words and es.norm(e.words) == said
                ),
                (block, entry),
            )
        return {
            **d,
            "block": block,
            "handle": named.get(block, block),
            "entry": entry,
            **({"root": onto(d["root"])} if d.get("root") else {}),
        }

    update["deltas"] = [delta(d) for d in update["deltas"]]
    if update["excerpts"]:
        update["excerpts"] = [
            {"block": x.block, "excerpt": x.excerpt, "cls": x.cls.value}
            for x in excerpts_onto(
                document,
                [
                    CarriedExcerpt(x["block"], x["excerpt"], ExcerptClass(x["cls"]))
                    for x in update["excerpts"]
                ],
                kept.cut,
                final_cut,
                within,
            )
        ]
    if "cost" in update:
        update["cost"] = _cost_after(kept, step, update["cost"], order)
    if "shape" in update:

        def causes(found: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
            return [
                {
                    **c,
                    "from": onto(c["from"]),
                    "root": onto(c["root"]),
                }
                for c in found
            ]

        update["moved_by_rule"] = [
            {**m, "handle": onto(m["handle"]), "causes": causes(m["causes"])}
            for m in update["moved_by_rule"]
        ]
        update["stale"] = [
            {**s, "handle": onto(s["handle"]), "causes": causes(s["causes"])}
            for s in update["stale"]
        ]
        if "closed_by_delta" in update:
            update["closed_by_delta"] = [onto(h) for h in update["closed_by_delta"]]
    if "lost" in update:
        update["lost"] = [
            {
                **s,
                "handle": onto(s["handle"]),
                "causes": [
                    {
                        **c,
                        "from": onto(c["from"]),
                        "root": onto(c["root"]),
                    }
                    for c in s["causes"]
                ],
            }
            for s in update["lost"]
        ]
    if "reread" in update:
        update["reread"] = [
            {**r, "handle": onto(r["handle"])} for r in update["reread"]
        ]
    kept_handle = {
        ident: h for h, ident in edges_file.handles(document, kept.as_written).items()
    }

    def owner(ident: str) -> str | None:
        return named.get(ident) or onto(kept_handle.get(ident))

    answered = kept.store.authoritative()
    found, passes = folded(
        update,
        [
            (q, answered[q.key], step.texts[q.key])
            for q in step.questions.get(Stage.EDGES, ())
        ],
        kept.store,
        owner,
        lambda q: blocks_onto(document, q.input_cut, final_cut),
        _remark(kept, final_cut) if "shape" in update else None,
    )
    return found, passes


def _remark(kept: Kept, final_cut: Cut) -> Remark:
    """The marks of the kept state's update of one session given again
    from corrected Δ classes: today's rule over the context its step filed
    from the session's answer (`update.marks_from`), each handle on
    `final_cut`; the marks filed before stand."""

    def remark(corrected: Mapping[int, str]) -> dict[str, list[Any]]:
        store, document = kept.store, kept.document
        identity = kept.state.identity
        assert identity is not None
        # a one-session update keeps its marks' context (`Update.session_facts`)
        facts = versions.session_facts(store, identity)
        if facts is None or "marks" not in facts:
            raise EditError(
                f"state {kept.state.id}'s update keeps no marks' context, so a "
                "corrected Δ cannot be marked"
            )
        context = MarkContext.of(facts["marks"])
        rules = CutRules(DisplayRule(context.cut["rules"]["displays"]))
        asked = cut_from_payload(document, context.cut, rules, roles=True)
        within = pair(Version(document, asked), Version(document, final_cut))
        named = edges_file.handles(document, final_cut)
        return marks_entries(
            marks_from(context, corrected),
            sessions_onto(dict(context.handles), named, within),
        )

    return remark


def _previous_after(
    previous: Mapping[str, Sequence[str]], within: Lineage
) -> dict[str, list[str]]:
    """`review.update.previous` (each block's OLD blocks) composed with
    `within`, the kept cut's lineage to the final one, as the update's
    lineage composes with a round's (`Lineage.compose`): the kept map read
    back as links, a split's blocks one link, an added block its own."""
    links: list[LineageLink] = []
    for block, olds in previous.items():
        last = links[-1] if links else None
        if olds and last is not None and list(last.old) == list(olds):
            links[-1] = dataclasses.replace(last, new=(*last.new, block))
        else:
            links.append(
                LineageLink(
                    tuple(olds),
                    (block,),
                    BlockClass.SAME if olds else BlockClass.ADDED,
                    Tier.COMPOSED,
                )
            )
    composed = Lineage(tuple(links)).compose(within)
    return {block: list(link.old) for link in composed.links for block in link.new}


def _cost_after(
    kept: Kept, step: Step, cost: Mapping[str, Any], order: Sequence[str]
) -> dict[str, Any]:
    """An update's `review.update.cost` with each answer the step asked, by
    its key and stage, where a replay lists it: after every cost of its
    stage and the stages before it (the identity answer's first)."""
    at = {stage: n for n, stage in enumerate(order)}
    entries = list(cost.items())
    answered = kept.store.authoritative()
    for stage, asked in step.questions.items():
        for q in asked:
            found = answered.get(q.key)
            if found is None or any(key == found.key for key, _ in entries):
                continue
            later = next(
                (
                    n
                    for n, (_, entry) in enumerate(entries)
                    if at.get(entry["stage"], -1) > at[stage.value]
                ),
                len(entries),
            )
            entries.insert(
                later, (found.key, {"stage": found.stage, "cost": found.cost})
            )
    return dict(entries)


#: The rules' flags on the edges (`flags.rule_flags`), which only the
#: edges' rule raises: a build's export lists them last.
EDGE_RULES = (Signal.QUALIFIES.value, Signal.DEFINITION_USES_CLAIM.value)


def _rule_tail(kept: Kept) -> int | None:
    """How many of a build's kept flags are its rule flags, which it lists
    last (`driver.review_cut`): the group rule's, then every edges rule's,
    each `by` the rule, as the export lists them. None where a rule's flag
    stands before the tail. The flags are not read again: a rule changed
    since the state was kept says nothing of what it kept
    (`docs/model.md` § 7)."""
    flags = kept.review.get("flags", [])
    count = 0
    while count < len(flags) and flags[len(flags) - 1 - count]["by"] == BY_RULE:
        count += 1
    if any(f["by"] == BY_RULE for f in flags[: len(flags) - count]):
        return None
    return count


def _kept_link(kept: Kept) -> Link:
    """The kept state's lines as a link of its chain: each by its key, with
    the author its export names (the read-back holds them in the export's
    order)."""
    if kept.sheet is None or kept.answer is None:
        return {}
    keys = line_keys(edges_file.records(kept.sheet, kept.handles()))
    return {
        key: exported["provenance"]
        for kind, found in keys.items()
        for key, exported in zip(found, kept.export.get(kind, []), strict=True)
    }


def _place(review: dict[str, Any], key: str, value: Any, before: Sequence[str]) -> None:
    """`review[key] = value`, a new key placed before the first of `before`
    the review holds, as the export's writer orders it."""
    if key in review or not any(b in review for b in before):
        review[key] = value
        return
    at = next(b for b in before if b in review)
    items = list(review.items())
    review.clear()
    for k, v in items:
        if k == at:
            review[key] = value
        review[k] = v


def _dumps(payload: dict[str, Any]) -> bytes:
    return dumps(payload).encode("utf-8")


# --- a fragment of sheet lines (`axiomation change`) -----------------------------

_OWNER = re.compile(r"^⟦([^⟧\s]+)⟧$")


def parse_fragment(text: str) -> Fragment:
    """A fragment: `+ <target> [<label>]: "<words>"` legend lines before any
    owner; a `⟦<handle>⟧` owner line; under it `+ <line>`, `- <line>` or
    `= <line>` (the line as the sheet holds it, or by its head and quote
    as `show` prints it: `edges_file.named_lines`), each an edge or standing
    line, or a Δ line an update's state shows. Blank lines and `#`
    comments are skipped."""
    legend: list[str] = []
    lines: list[tuple[str, str, str]] = []
    owner: str | None = None
    for number, line in enumerate(text.split("\n"), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if m := _OWNER.match(stripped):
            owner = m.group(1)
            continue
        op, _, rest = stripped.partition(" ")
        rest = rest.strip()
        if op not in (ADD, RETRACT, KEEP) or not rest:
            raise EditError(
                f"fragment line {number}: begin with `+`, `-` or `=` and a space, "
                f"or name an owner `⟦handle⟧`: {line!r}"
            )
        if owner is None:
            if op == ADD and es.LEGEND.match(f"  {rest}"):
                legend.append(f"  {rest}")
                continue
            raise EditError(
                f"fragment line {number}: a line under no owner; only a legend line "
                f'`+ <target> [<label>]: "<words>"` stands before the first: {line!r}'
            )
        if not rest.startswith(("→ ", "⊢ ", f"{es.DELTA_MARK} ")):
            raise EditError(
                f"fragment line {number}: an edge (`→ …`), standing (`⊢ …`) or Δ "
                f"(`Δ …`) line: {line!r}"
            )
        lines.append((owner, op, rest))
    if not legend and not lines:
        raise EditError("the fragment holds no line")
    return Fragment(tuple(legend), tuple(lines))


def applied_fragment(sheet: str, fragment: Fragment, restating: bool = False) -> str:
    """`sheet` with the fragment applied (`edges_file.applied_fragment`, its
    author writing about lines as restatements or not, `restating`);
    refused (`EditError`), naming it, where it cannot be."""
    try:
        return edges_file.applied_fragment(sheet, fragment, restating)
    except edges_file.FragmentError as error:
        raise EditError(str(error)) from None


def delta_lines(sheet: str, owner: str, line: str) -> list[tuple[str, str, str]]:
    """A Δ set on `owner`'s block and part: the line
    added, and the Δ line the sheet shows on the same part taken off;
    refused where it shows none, since a Δ corrects an update's."""
    m = es.DELTA.match(line)
    if m is None:
        raise EditError(f"a Δ line is `{es.DELTA_FORM}`: {line!r}")
    label = (m.group("label") or "").strip()
    same = [
        x
        for x in edges_file.written_lines(sheet).get(owner, [])
        if (d := es.DELTA.match(x)) and (d.group("label") or "").strip() == label
    ]
    if not same:
        named = f" [{label}]" if label else ""
        raise EditError(
            f"⟦{owner}⟧ shows no Δ line{named} to correct: a Δ corrects the one an "
            "update wrote on a block it changed"
        )
    return [*((owner, RETRACT, x) for x in same), (owner, ADD, line)]


def standing_lines(sheet: str, owner: str, line: str) -> list[tuple[str, str, str]]:
    """A standing set on `owner`: the line added, and the owner's standing
    on the same claim, if any, taken off. The added line keeps the handle
    the sheet shows on that claim where it writes none, so the lines that
    name the claim by it still name it."""
    m = es.SUPPORT.match(line)
    if m is None:
        raise EditError(f'a standing line is `⊢ <status> · "<claim>"`: {line!r}')
    claim = es.norm(m.group("claim"))
    same = [
        x
        for x in edges_file.written_lines(sheet).get(owner, [])
        if (s := es.SUPPORT.match(x)) and es.norm(s.group("claim")) == claim
    ]
    parsed = es.parse(sheet)
    shown = es.shown_handles(parsed)
    handle = next(
        (
            shown[s.line]
            for s in parsed.supports
            if s.source == owner and es.norm(s.claim) == claim and s.line in shown
        ),
        None,
    )
    if handle is not None and not m.group("handle"):
        at = m.end("declared") if m.group("declared") else m.end("status")
        line = f"{line[:at]} [{handle}]{line[at:]}"
    return [*((owner, RETRACT, x) for x in same if x != line), (owner, ADD, line)]


def merged_edges(problems: Sequence[str], shown: str, edited: str) -> tuple[str, ...]:
    """`problems` of an edges edit, each second edge for one source, kind
    and target the edit adds told what to file instead: the line the sheet
    holds, retracted, and where one line says what both say
    (`_merged_edge`), that line. The check's own message names no other
    line (`edges_file.problem_key`), so the edit, which knows its lines,
    names them."""
    held = edges_file.written_lines(shown)
    by_key: dict[tuple[str, str, str], list[str]] = {}
    for owner, lines in edges_file.written_lines(edited).items():
        for line in lines:
            if (m := es.EDGE.match(line)) and m["kind"] != es.SELF:
                key = (owner, m["kind"], m["target"] or "")
                by_key.setdefault(key, []).append(line)
    told: dict[str, str] = {}
    for (owner, kind, target), lines in by_key.items():
        before = [x for x in lines if x in held.get(owner, [])]
        if len(lines) < 2 or not before or len(before) == len(lines):
            continue
        retract = " and ".join(f"`{x}`" for x in before)
        merged = _merged_edge(before[0], lines) if len(before) == 1 else None
        told[f"{owner} → {kind} {target}".rstrip() + f": {edges_file.DUPLICATE}"] = (
            f"; one line names them all: retract {retract} and add `{merged}`"
            if merged is not None and merged != before[0]
            else f"; ⟦{owner}⟧ holds {retract}: retract it to replace it"
        )
    return tuple(
        problem
        + next(
            (
                tail
                for head, tail in told.items()
                if edges_file.problem_key(problem).startswith(head)
            ),
            "",
        )
        for problem in problems
    )


def _merged_edge(base: str, lines: Sequence[str]) -> str | None:
    """`base` naming, at its source end and among its target's parts, what
    each of `lines` names there, in their order; its evidence and note
    `base`'s. None where no one line says what they say: a justifies line
    (two may stand apart, each claim's completeness its own line's:
    `docs/model.md` § 3), lines of another mark or flags, or a source end
    that restates none (`[-]`). A bracket that one line leaves out stays
    out: a line naming none relies on every claim (`docs/model.md` § 4),
    so naming some would narrow it."""
    found = [es.EDGE.match(x) for x in lines]
    m = es.EDGE.match(base)
    if m is None or not all(found) or m["kind"] == "justifies":
        return None
    if len({(f["mark"], f["flag"]) for f in found if f}) > 1:
        return None
    if any(f and f["own"] and RESTATES_NONE in f["own"].split(", ") for f in found):
        return None

    def union(group: str) -> str:
        if not all(f and f[group] for f in found):
            return ""
        names = dict.fromkeys(
            n.strip() for f in found if f for n in f[group].split(",")
        )
        return ", ".join(names)

    out = base
    # the later bracket first, so the earlier one's offsets still hold
    for group in ("parts", "own"):
        names = union(group)
        if names:
            out = f"{out[: m.start(group) - 2]} [{names}]{out[m.end(group) + 1 :]}"
    return out
