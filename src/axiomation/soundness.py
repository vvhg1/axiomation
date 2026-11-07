"""What the kept graph got wrong or cannot settle: four kinds
over a kept state, each $0 over its document layer, its export and its
readings.

- **A dangling reference**: a reference to a label no block holds. The cut
  lost the edge it would have made.
- **A duplicate label**: a label two or more blocks hold. The first
  definition wins (`cutter.groups.label_holders`), so each reference to it
  picked its block blindly.
- **An unused premise**: an assumption nothing uses and that qualifies
  nothing: an environment the paper declares an assumption or condition,
  its blocks one unit, or a block whose role is `assumption` outside one.
  Dead weight, or an edge the writer missed. Definitions are not premises
  here: an unused prose definition is the ambient frame's.
- **An undeclared assumption**: a premise a justified claim's J reaches
  that the claim's own statement does not (`established.undeclared`),
  with its cause and its path.

Labels resolve as the cutter resolves them, over the state's own blocks,
so an item says exactly what the cut did. `open` and `check` both read
this module; neither owns the question. Nothing here writes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from axiomation.cutter.blocks import BlockIndex
from axiomation.cutter.groups import definition_holders
from axiomation.cutter.model import ROLE_OF_MEANING, Role
from axiomation.established import (
    Hop,
    PathStep,
    Undeclared,
    UndeclaredCause,
    Unnamed,
    undeclared,
)
from axiomation.exchange import placed_blocks
from axiomation.identity import norm
from axiomation.judgments import Readings
from axiomation.reader import Document, Span

ASSUMPTION = Role.ASSUMPTION.value


@dataclass(frozen=True)
class Unsound:
    """One item: the blocks it sits on (it shows on the first), the words
    it is anchored on in the first where any, its line, its cause, and what
    clears it."""

    blocks: tuple[str, ...]
    words: str | None
    line: str
    cause: Any
    clears: str


class Kept:
    """A kept state as this module reads it: its document layer, its
    export and its blocks placed on the document (where each stands, all a
    label's holder needs: a key's export carries no roles)."""

    def __init__(
        self,
        document: Document,
        export: Mapping[str, Any],
        names: Mapping[str, str] | None = None,
    ) -> None:
        self.document = document
        self.export = export
        self.blocks = placed_blocks(document, dict(export))
        self.index = BlockIndex(self.blocks)
        #: id → the handle shown: `names` (a kept state's kept handles,
        #: `docs/model.md` § 7) where given, else the export's
        self.handles: Mapping[str, str] = names or export.get("handles") or {}
        self.order = {b.id: i for i, b in enumerate(self.blocks)}

    def handle(self, block: str) -> str:
        return self.handles.get(block, block)


def dangling_references(kept: Kept) -> list[Unsound]:
    """Each reference to a label no block holds, one item per label it
    names so, on the block that owns the reference, in reading order."""
    holders: dict[str, str | None] = {}
    for label, holder in definition_holders(kept.document, kept.index):
        holders.setdefault(label.name, holder)
    found: list[Unsound] = []
    for reference in kept.document.references:
        at = reference.at
        owner = kept.index.owner(at.file, at.start)
        if owner is None:
            continue
        words = norm(kept.document.text(at))
        for target in reference.targets:
            if holders.get(target) is not None:
                continue
            cause = (
                "defined nowhere" if target not in holders else "outside every block"
            )
            found.append(
                Unsound(
                    (owner,),
                    words,
                    f"{kept.handle(owner)}  {words}: no block holds {target} ({cause})",
                    {"label": target, "why": cause},
                    f"the label {target} on the block meant, or the reference "
                    "corrected",
                )
            )
    return found


def duplicate_labels(kept: Kept) -> list[Unsound]:
    """Each label two or more blocks hold, on every holder, the first (the
    one each reference reads) first; in the order of first definitions."""
    holders: dict[str, list[str]] = {}
    for label, holder in definition_holders(kept.document, kept.index):
        if holder is not None:
            held = holders.setdefault(label.name, [])
            if holder not in held:
                held.append(holder)
    found: list[Unsound] = []
    for name, held in holders.items():
        if len(held) < 2:
            continue
        names = ", ".join(kept.handle(b) for b in held)
        found.append(
            Unsound(
                tuple(held),
                None,
                f"{name} held by {names}: each reference to it reads "
                f"{kept.handle(held[0])}",
                {"label": name, "holders": [kept.handle(b) for b in held]},
                f"{name} renamed on all but one, and each reference meant for "
                "another corrected",
            )
        )
    return found


def _inside(span: Span, outer: Span) -> bool:
    return (
        span.file == outer.file and outer.start <= span.start and span.end <= outer.end
    )


def unused_premises(kept: Kept) -> list[Unsound]:
    """Each assumption no uses edge reaches and that qualifies nothing, in
    the paper's order: an environment whose meaning is an assumption, its
    blocks one unit used where any is, and each block with the role
    `assumption` outside one. A block of the ambient frame is reached by
    every block after it."""
    export = kept.export
    members = {g["id"]: list(g["blocks"]) for g in export.get("groups", [])}

    def blocks_of(ident: str | None) -> list[str]:
        return [] if ident is None else members.get(ident, [ident])

    used = set(export.get("ambient") or [])
    for e in export.get("edges", []):
        t = e["target"]
        if e["kind"] == "uses":
            used.update(blocks_of(t.get("block", t.get("group"))))
        elif e["kind"] == "qualifies":
            used.update(blocks_of(e["source"]))
    units: list[tuple[list[str], str | None, str]] = []
    in_unit: set[str] = set()
    for env in kept.document.environments:
        if ROLE_OF_MEANING.get(env.meaning or "") is not Role.ASSUMPTION:
            continue
        blocks = [b.id for b in kept.blocks if _inside(b.extent, env.span)]
        if not blocks or in_unit.intersection(blocks):
            continue
        in_unit.update(blocks)
        label = next(
            (x.name for x in kept.document.labels if _inside(x.at, env.span)), None
        )
        units.append((blocks, label, env.name))
    for b in export.get("blocks", []):
        roles = set(b.get("roles", [])) | {s["role"] for s in b.get("segments", [])}
        if ASSUMPTION in roles and b["id"] not in in_unit:
            units.append(([b["id"]], None, "assumption"))
    units.sort(key=lambda u: kept.order.get(u[0][0], len(kept.order)))
    found: list[Unsound] = []
    for blocks, label, what in units:
        if used.intersection(blocks):
            continue
        name = label or kept.handle(blocks[0])
        found.append(
            Unsound(
                tuple(blocks),
                None,
                f"{name}  {what} ({', '.join(kept.handle(b) for b in blocks)}): no "
                "uses edge reaches it and it qualifies nothing: dead weight, or an "
                "edge the writer missed",
                {"label": label, "environment": what},
                "a uses or qualifies edge where a block relies on it "
                "(`axiomation edit --stage edges`), or the assumption removed",
            )
        )
    return found


#: A quote on a path's line, at most this many characters.
QUOTE_CHARACTERS = 70


def undeclared_assumptions(readings: Readings) -> list[Unsound]:
    """Each premise a justified claim's J rests on that its statement does
    not (`established.undeclared`), on the claim's block, in the export's
    order of standings, each saying its cause (`established.UndeclaredCause`) and
    printing its path. One fault is one item: a statement with no uses
    edge lists its premises once; a line naming no claim of a block that
    holds several lists once the premises it made."""
    at = readings.at
    h = at.handle

    def block(node: str) -> str:
        return readings.block_of(node) or node

    def what(u: Undeclared) -> str:
        premise = h(u.premise)
        found = (
            f'conjectured {premise} "{u.conjecture.words}"'
            if u.conjecture is not None
            else premise
        )
        return found if u.through is None else f"{found} through {h(u.through.node)}"

    def step_line(s: PathStep) -> str:
        if s.hop is Hop.MEMBER:
            return f"{h(s.source)} ∋ {h(s.target)}"
        if s.hop is Hop.CONTEXT:
            return f"{h(s.source)} within the argument for {h(s.target)}"
        found = f"{h(s.source)} → {s.hop.value} {h(s.target)}"
        if s.edge is not None and s.edge.get("assumed"):
            # a hypothesis the statement takes: rested on, never asked
            found += " assumed"
        if s.quote:
            quote = norm(s.quote)
            if len(quote) > QUOTE_CHARACTERS:
                quote = quote[:QUOTE_CHARACTERS].rstrip() + "…"
            found += f' · "{quote}"'
        return found + (" (names no claim)" if s.fallback else "")

    def path(
        us: Sequence[Undeclared], extra: Sequence[PathStep] = ()
    ) -> list[PathStep]:
        """The steps of every path, each once, in the order walked."""
        steps: dict[tuple[str, Hop, str], PathStep] = {}
        for s in (*extra, *(s for u in us for s in u.path)):
            steps.setdefault((s.source, s.hop, s.target), s)
        return list(steps.values())

    def premises_data(us: Sequence[Undeclared]) -> list[dict[str, Any]]:
        return [
            {
                "via": h(u.via),
                "premise": h(u.premise),
                "conjectured": u.conjecture is not None,
                "through": None if u.through is None else h(u.through.node),
                "narrows": None if u.narrows is None else h(u.narrows),
                "case": None if u.case is None else h(u.case),
                "discharged_by": [h(n) for n in u.discharged_by],
            }
            for u in us
        ]

    def item(
        us: Sequence[Undeclared],
        line: str,
        why: str,
        fix: str,
        extra: Sequence[PathStep] = (),
    ) -> Unsound:
        """An item: `why` its cause in the line's own words."""
        u = us[0]
        steps = path(us, extra)
        nodes = [u.claim.node]
        for x in us:
            nodes += [x.via, *([x.through.node] if x.through else []), x.premise]
        nodes += [s.source for s in extra]
        return Unsound(
            tuple(dict.fromkeys(block(n) for n in nodes)),
            u.claim.words,
            "\n".join([line, *("  " + step_line(s) for s in steps)]),
            {
                "cause": u.cause.value,
                "why": why,
                "claim": u.claim.words,
                "premises": premises_data(us),
                "lines": [
                    {"where": where.value, "line": step_line(s)}
                    for where, s in dict.fromkeys(x for v in us for x in v.unnamed)
                ],
                "path": [
                    {
                        "source": h(s.source),
                        "hop": s.hop.value,
                        "target": h(s.target),
                        "quote": s.quote,
                    }
                    for s in steps
                ],
            },
            fix,
        )

    def listing(us: Sequence[Undeclared]) -> str:
        by: dict[str, list[str]] = {}
        for u in us:
            by.setdefault(h(u.via), []).append(what(u))
        return "; ".join(f"{j}: {', '.join(ps)}" for j, ps in by.items())

    def claimed(u: Undeclared) -> str:
        return f'{h(u.claim.node)} "{u.claim.words}"'

    def one(u: Undeclared) -> Unsound:
        j, premise = h(u.via), h(u.premise)
        line = (
            f"{claimed(u)}: its J {j} rests on {what(u)}, which its statement does not"
        )
        # a conjecture is argued, never discharged nor a wider premise
        premised = (
            ""
            if u.conjecture is not None
            else f", a justifies edge onto it from a block {j} holds (a discharge), "
            f"a `generalizes` edge from {premise} onto what the statement declares "
            "where that is a case of it"
        )
        fix = (
            f"the statement naming {premise} (a uses edge onto it, or a qualifies "
            f"edge from it){premised}, or {j} not relying on it"
        )
        if u.cause is UndeclaredCause.DISCHARGED_ELSEWHERE:
            why = (
                f"{', '.join(h(n) for n in u.discharged_by)} discharges it, outside {j}"
            )
            line += f"; {why}"
        elif u.cause is UndeclaredCause.NARROWS and u.case is not None:
            wider, case = h(u.narrows or ""), h(u.case)
            why = (
                f"{wider}, which the statement declares, generalizes {case} "
                f"({wider} → about {case} generalizes)"
            )
            line += f"; {why}: the J rests on the narrower case"
            # the block whose edge reaches the case: the J's, or a lemma's
            # statement's where the case is on an asked claim's path
            relier = next((h(s.source) for s in u.path if s.target == u.case), j)
            fix = (
                f"the statement naming {case} (a uses edge onto it), or {relier} "
                f"relying on {wider} in its place"
            )
        elif u.cause is UndeclaredCause.NARROWS:
            narrowed = h(u.narrows or "")
            why = f"it narrows or adds to {narrowed}, which the statement declares"
            line += f"; {why}: a premise of its own, or a copy missing its about edge"
            fix = (
                f"an about edge from {premise} onto {narrowed} where it restates it "
                f"(one premise), or {fix}"
            )
        else:
            why = "a premise it leaves out"
            line += f": {why}"
        return item([u], line, why, fix)

    def missing(us: Sequence[Undeclared]) -> Unsound:
        return item(
            us,
            f"{claimed(us[0])}: its statement has no uses edge, so it declares "
            f"nothing its J rests on ({listing(us)}): its edges were missed, or it "
            "states nothing it rests on",
            "its statement has no uses edge",
            "the statement's uses edges, the premises it relies on among them "
            "(`axiomation edit --stage edges`), or its J not relying on them",
        )

    def unnamed(
        lines: Sequence[tuple[Unnamed, PathStep]], us: Sequence[Undeclared]
    ) -> Unsound:
        def so(where: Unnamed, s: PathStep) -> str:
            return {
                Unnamed.STATEMENT: "its statement declares nothing through it",
                Unnamed.LINE: f"{h(s.source)} may argue another of its claims",
                Unnamed.ASK: "the J rests on each claim's premises",
            }[where]

        named = "; ".join(
            f"{h(s.source)} → {s.hop.value} {h(s.target)}, so {so(where, s)}"
            for where, s in lines
        )
        return item(
            us,
            f"{claimed(us[0])}: a line naming no claim of a block that holds "
            f"several ({named}) leaves undeclared what its J rests on "
            f"({listing(us)})",
            "a line naming no claim of a block that holds several",
            "that line naming the claim it means (the claim's handle in its "
            "bracket: at an ask's or a justifies line's target, or at a "
            "restating about edge's source end), or the J not relying on those "
            "premises",
            [s for _, s in lines],
        )

    def line_key(s: PathStep) -> tuple[str, Hop, str]:
        return s.source, s.hop, s.target

    groups: dict[tuple[Any, ...], list[Undeclared]] = {}
    for u in undeclared(readings):
        if u.cause is UndeclaredCause.MISSING_EDGES:
            key: tuple[Any, ...] = (u.cause, u.claim)
        elif u.cause is UndeclaredCause.UNNAMED_LINE:
            key = (
                u.cause,
                u.claim,
                frozenset((where, line_key(s)) for where, s in u.unnamed),
            )
        else:
            key = (u,)
        groups.setdefault(key, []).append(u)
    found: list[Unsound] = []
    for us in groups.values():
        cause = us[0].cause
        if cause is UndeclaredCause.MISSING_EDGES:
            found.append(missing(us))
        elif cause is UndeclaredCause.UNNAMED_LINE:
            lines = {
                (where, line_key(s)): (where, s) for u in us for where, s in u.unnamed
            }
            found.append(unnamed(list(lines.values()), us))
        else:
            found.append(one(us[0]))
    return found
