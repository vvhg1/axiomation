"""A queue session's reads: narrow, capped and compact, over a kept state
and the lines its run has settled so far (docs/architecture.md § 2).

The paper: `block`, `view` (`VIEW_LINES` lines), `search` (at most
`SEARCH_HITS_MAX` hits), `cited`. The graph: `edges`, `standing`,
`relied`, `group`, `hops` (two, nearest first). And for a task: the
blocks its block names and those near it on the graph, as pointers
without text (`pointers`).

The graph reads show the kept state, then the lines the run settled that
touch the node; `hops` walks both.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict, deque
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from axiomation import queries
from axiomation.questions import edge_sheet as es

SEARCH_HITS_MAX = 50
VIEW_LINES = 100
SNIPPET = 140
POINTERS_MAX = 20
#: The graph reads' items, and `hops`' nodes, shown at most.
GRAPH_ITEMS = 40
#: How far `hops` walks, and the task's order looks for the nearest task.
HOPS = 2
ORDER_HOPS = 4

_LABEL = re.compile(r"\\label\{([^}]+)\}")
_NAMED = r"(?:Proposition|Lemma|Theorem|Corollary|Definition)\s+[A-Z]?\d+(?:\.\d+)*"
_NAME_OPENS = re.compile(rf"\s*\*\*({_NAMED})")
_REF = re.compile(r"\\(?:eq|c|auto|page)?ref\{([^}]+)\}")
_CITE = re.compile(r"\\(?:auto|text|paren|)cite[pt]?\*?(?:\[[^\]]*\])*\{([^}]+)\}")


class ReadError(ValueError):
    """A read that cannot be given, with why; the session is told."""


class Reads:
    """The reads over `view`, the paper's files at its state under
    `files`, and `settled`, the sheet lines `(owner, op, line)` the run
    has settled so far (`settle` adds to them)."""

    def __init__(self, view: queries.View, files: Path) -> None:
        self.view = view
        self.lines: dict[str, list[str]] = {}
        #: (file, line) → the innermost block holding it
        self.at_line: dict[tuple[str, int], str] = {}
        self.adjacent: dict[str, set[str]] = defaultdict(set)
        self.labels: dict[str, str] = {}
        self.names: dict[str, str] = {}
        self.settled: list[tuple[str, str, str]] = []
        for b in view.blocks.values():
            name = b["extent"]["file"]
            if name not in self.lines:
                path = files / name
                self.lines[name] = (
                    path.read_text(encoding="utf-8").splitlines()
                    if path.exists()
                    else []
                )
            (first, _), (last, _) = b["extent"]["start"], b["extent"]["end"]
            for n in range(first, last + 1):
                held = self.at_line.get((name, n))
                if held is None or self._span(b["id"]) < self._span(held):
                    self.at_line[(name, n)] = b["id"]
        for e in view.edges:
            target = e["target"].get("block") or e["target"].get("group")
            if target:
                self._join(e["source"], target)
        for block, groups in view.groups_of.items():
            for g in groups:
                self._join(g, block)
        # a cut leaves a LaTeX `\label` out of its block's text, so a label
        # is placed by where the reader found it; one a text writes (a
        # format with no reader's label) by its text
        for label in view.export.get("document", {}).get("labels", ()):
            held = self._holding(label["at"])
            if held is not None:
                self.labels.setdefault(label["name"], held)
        for ident, text in view.texts.items():
            for label in _LABEL.findall(text):
                self.labels.setdefault(label, ident)
            if m := _NAME_OPENS.match(text):
                self.names.setdefault(m.group(1), ident)

    def _join(self, a: str, b: str) -> None:
        self.adjacent[a].add(b)
        self.adjacent[b].add(a)

    def _holding(self, at: Mapping[str, Any]) -> str | None:
        """The innermost block whose extent holds the place `at`."""
        start, end = tuple(at["start"]), tuple(at["end"])
        held = [
            ident
            for ident, b in self.view.blocks.items()
            if b["extent"]["file"] == at["file"]
            and tuple(b["extent"]["start"]) <= start
            and end <= tuple(b["extent"]["end"])
        ]
        return min(held, key=self._extent_size, default=None)

    def _extent_size(self, ident: str) -> tuple[int, int]:
        (first, start), (last, end) = (
            self.view.blocks[ident]["extent"]["start"],
            self.view.blocks[ident]["extent"]["end"],
        )
        return last - first, end - start

    def _span(self, ident: str) -> int:
        extent = self.view.blocks[ident]["extent"]
        return extent["end"][0] - extent["start"][0]

    def settle(self, lines: Sequence[tuple[str, str, str]]) -> None:
        """Lines the run settled: shown by the graph reads, walked by
        `hops`."""
        for owner, op, line in lines:
            self.settled.append((owner, op, line))
            m = es.EDGE.match(line)
            target = m.group("target") if m else None
            if op == "+" and target:
                try:
                    self._join(self.view.resolve(owner), self.view.resolve(target))
                except queries.QueryError:
                    continue

    # --- names ------------------------------------------------------------------

    def resolve(self, ref: str) -> str:
        """The block or group `ref` names: as the view finds it, else as a
        reader writes it, a handle in its brackets or with other
        punctuation, a label the paper writes, or the one handle it is a
        part of; refused naming the handles it is part of where several."""
        bare = ref.strip().strip("⟦⟧").strip()
        for each in dict.fromkeys((ref, bare)):
            try:
                return self.view.resolve(each)
            except queries.QueryError:
                pass
        handles = {self.handle(i): i for i in [*self.view.blocks, *self.view.groups]}
        plain = _plain(bare)
        same = [i for h, i in handles.items() if _plain(h) == plain]
        if len(same) == 1:
            return same[0]
        if bare in self.labels:
            return self.labels[bare]
        near = sorted(h for h in handles if plain and plain in _plain(h))
        if len(near) == 1:
            return handles[near[0]]
        raise ReadError(
            f"{ref!r} names no block or group"
            + (f"; it is part of several: {', '.join(near[:8])}" if near else "")
        )

    def handle(self, ident: str) -> str:
        return self.view.handle(ident)

    def section(self, ident: str) -> str:
        first = (
            self.view.groups[ident]["blocks"][0] if ident in self.view.groups else ident
        )
        found = self.view.sections.get(first)
        return found.name if found else ""

    def members(self, ident: str) -> list[str]:
        if ident in self.view.groups:
            return list(self.view.groups[ident]["blocks"])
        return [ident]

    def whole(self, ident: str) -> str:
        """A block's group where one holds it, since a group argues as one;
        else the block."""
        if ident in self.view.groups:
            return ident
        return next(iter(self.view.groups_of.get(ident, [])), ident)

    # --- the paper --------------------------------------------------------------

    def block(self, ref: str) -> str:
        ident = self.resolve(ref)
        if ident in self.view.groups:
            return f"⟦{self.handle(ident)}⟧ group {self.section(ident)}: " + ", ".join(
                self.handle(m) for m in self.members(ident)
            )
        extent = self.view.blocks[ident]["extent"]
        where = f"{extent['file']}:{extent['start'][0]}–{extent['end'][0]}"
        head = " ".join(
            filter(None, [f"⟦{self.handle(ident)}⟧", self.section(ident), where])
        )
        return head + "\n" + self.view.texts[ident]

    def view_lines(self, name: str, line: int) -> str:
        if name not in self.lines:
            raise ReadError(
                f"no file {name!r}; the paper's files: {', '.join(self.lines)}"
            )
        rows = self.lines[name]
        first = max(1, line)
        last = min(len(rows), first + VIEW_LINES - 1)
        held = list(
            dict.fromkeys(
                self.handle(self.at_line[(name, n)])
                for n in range(first, last + 1)
                if (name, n) in self.at_line
            )
        )
        more = " …" if len(held) > 12 else ""
        head = (
            f"{name} lines {first}–{last} of {len(rows)}; blocks: "
            f"{', '.join(held[:12])}{more}"
        )
        return (
            head
            + "\n"
            + "\n".join(f"{n}: {rows[n - 1]}" for n in range(first, last + 1))
        )

    def search(self, pattern: str) -> str:
        """A case-insensitive regular expression over the paper's lines: at
        most `SEARCH_HITS_MAX` hits, each its line, its block and a snippet;
        over that, the count alone."""
        try:
            found = re.compile(pattern, re.I)
        except re.error as error:
            raise ReadError(f"/{pattern}/: {error}") from None
        hits = [
            (name, n, row, m.start())
            for name, rows in self.lines.items()
            for n, row in enumerate(rows, 1)
            if (m := found.search(row))
        ]
        if len(hits) > SEARCH_HITS_MAX:
            return (
                f"{len(hits)} hits for /{pattern}/: over {SEARCH_HITS_MAX}, narrow "
                "the pattern"
            )
        out = [f"{len(hits)} hits for /{pattern}/"]
        for name, n, row, at in hits:
            ident = self.at_line.get((name, n))
            start = max(0, at - SNIPPET // 2)
            out.append(
                f"{name}:{n} ⟦{self.handle(ident) if ident else '-'}⟧ "
                f"…{row[start : start + SNIPPET]}…"
            )
        return "\n".join(out)

    def cited(self, ref: str) -> str:
        ident = self.resolve(ref)
        text = " ".join(self.view.texts.get(m, "") for m in self.members(ident))
        works = list(
            dict.fromkeys(
                k.strip() for keys in _CITE.findall(text) for k in keys.split(",")
            )
        )
        return f"⟦{self.handle(ident)}⟧ cites {len(works)}: " + ", ".join(works)

    # --- the graph --------------------------------------------------------------

    def _show(self, ref: str, sections: tuple[str, ...], kinds=()) -> str:
        ident = self.resolve(ref)
        report = queries.show(self.view, ref, kinds=kinds)
        kept = queries.Report(
            head=report.head[:1],
            sections={k: v for k, v in report.sections.items() if k in sections},
        )
        shown = queries.render(kept, limit=GRAPH_ITEMS)
        handle = self.handle(ident)
        settled = [
            f"⟦{owner}⟧ {op} {line}"
            for owner, op, line in self.settled
            if owner == handle or _names(line, handle)
        ]
        if settled:
            shown += "\nsettled in this run:\n" + "\n".join(settled)
        return shown

    def edges(self, ref: str) -> str:
        return self._show(ref, ("rests_on", "rested_on_by"))

    def standing(self, ref: str) -> str:
        return self._show(ref, ("standings", "rulings"))

    def relied(self, ref: str) -> str:
        return self._show(ref, ("rested_on_by",), kinds=("uses",))

    def group(self, ref: str) -> str:
        ident = self.resolve(ref)
        if ident in self.view.groups:
            return self.block(ref)
        return f"⟦{self.handle(ident)}⟧ is no group; its groups: " + ", ".join(
            self.handle(g) for g in self.view.groups_of.get(ident, [])
        )

    def distances(self, ident: str, depth: int = HOPS) -> dict[str, int]:
        seen, todo = {ident: 0}, deque([ident])
        while todo:
            x = todo.popleft()
            if seen[x] == depth:
                continue
            for y in self.adjacent[x]:
                if y not in seen:
                    seen[y] = seen[x] + 1
                    todo.append(y)
        seen.pop(ident)
        return seen

    def ranked_hops(self, ident: str, depth: int = HOPS) -> list[str]:
        """Within `depth` hops, nearest first; among equals, those reached
        by more paths, then the paper's order, then the id: a group and its
        first block tie, and a task's text, which its key binds, must read
        the same in every process, whatever its sets' hash order."""
        found = self.distances(ident, depth)
        paths = Counter(y for x in found if found[x] == 1 for y in self.adjacent[x])
        order = {b: i for i, b in enumerate(self.view.blocks)}
        return sorted(
            found,
            key=lambda x: (found[x], -paths[x], order.get(self.members(x)[0], 0), x),
        )

    def hops(self, ref: str) -> str:
        ident = self.resolve(ref)
        ranked = self.ranked_hops(ident)
        found = self.distances(ident)
        more = "\n  … narrow with `edges`" if len(ranked) > GRAPH_ITEMS else ""
        return (
            f"⟦{self.handle(ident)}⟧ within {HOPS} hops: {len(ranked)}\n"
            + "\n".join(
                f"  {found[x]} ⟦{self.handle(x)}⟧ {self.section(x)}"
                for x in ranked[:GRAPH_ITEMS]
            )
            + more
        )

    # --- a task's pointers ------------------------------------------------------

    def referenced(self, ident: str) -> list[str]:
        """The blocks a block's text names: `\\ref`s to a label, and named
        results (`Lemma A2.1`)."""
        text = " ".join(self.view.texts.get(m, "") for m in self.members(ident))
        found = [self.labels[k] for k in _REF.findall(text) if k in self.labels]
        found += [self.names[n] for n in re.findall(_NAMED, text) if n in self.names]
        return [f for f in dict.fromkeys(found) if f != ident]

    def pointers(self, ident: str) -> list[str]:
        """The blocks a task points at, without text: those its block and
        its section's other blocks name first, then its block's two hops
        ranked; at most `POINTERS_MAX`."""
        named = self.referenced(ident)
        section = self.section(ident)
        for mate in self.view.blocks:
            if mate != ident and self.section(mate) == section:
                named += [
                    n for n in self.referenced(mate) if n not in named and n != ident
                ]
        return list(dict.fromkeys(named[:8] + self.ranked_hops(ident)))[:POINTERS_MAX]

    def place(self, ident: str) -> str:
        """A block's handle, section and where it starts."""
        extent = self.view.blocks[ident]["extent"]
        where = f"{extent['file']}:{extent['start'][0]}"
        return " ".join(
            filter(None, [f"⟦{self.handle(ident)}⟧", self.section(ident), where])
        )


def _plain(name: str) -> str:
    """A name as compared: lower case, each run of characters other than
    letters and digits one hyphen."""
    return re.sub(r"[^0-9a-z]+", "-", name.lower()).strip("-")


def _names(line: str, handle: str) -> bool:
    """Whether a sheet line names `handle` as its target."""
    m = es.EDGE.match(line)
    return m is not None and m.group("target") == handle
