"""Which block of one cut continues which of another cut of the same
text: the rule a node id follows within one version (`docs/model.md`
§ 7), and a kept handle with it."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Mapping, Sequence


def by_overlap(
    before: Mapping[str, Sequence[tuple[str, int, int]]],
    after: Mapping[str, Sequence[tuple[str, int, int]]],
) -> dict[str, str]:
    """Two cuts of one text: each block of `after` continuing the block of
    `before` it shares most characters with, each block of `before`
    continued once, the largest shares first."""
    found: dict[str, str] = {}
    taken: set[str] = set()
    for _, _, _, new, old in sorted(_shares(before, after)):
        if new in found or old in taken:
            continue
        found[new] = old
        taken.add(old)
    return found


def _shares(
    before: Mapping[str, Sequence[tuple[str, int, int]]],
    after: Mapping[str, Sequence[tuple[str, int, int]]],
) -> list[tuple[int, int, int, str, str]]:
    """Each pair of blocks of two cuts of one text that share characters:
    `(-shared, after's order, before's order, after's block, before's)`,
    in after's order, then before's."""
    order_after = {block: i for i, block in enumerate(after)}
    order_before = {block: i for i, block in enumerate(before)}
    # before's runs per file by start, so each run of after meets only the
    # runs that can overlap it: a paper's thousand blocks are not paired
    # with every other
    runs: dict[str, list[tuple[int, int, str]]] = {}
    for old, old_runs in before.items():
        for file, start, end in old_runs:
            runs.setdefault(file, []).append((start, end, old))
    starts: dict[str, list[int]] = {}
    longest: dict[str, int] = {}
    for file, held in runs.items():
        held.sort(key=lambda r: r[0])
        starts[file] = [r[0] for r in held]
        longest[file] = max(end - start for start, end, _ in held)
    shares = []
    for new, new_runs in after.items():
        shared: dict[str, int] = {}
        for file, a_start, a_end in new_runs:
            if file not in runs:
                continue
            held = runs[file]
            # a run starting at or before a_start - longest ends by a_start
            first = bisect_right(starts[file], a_start - longest[file])
            for b_start, b_end, old in held[first : bisect_left(starts[file], a_end)]:
                overlap = min(b_end, a_end) - max(b_start, a_start)
                if overlap > 0:
                    shared[old] = shared.get(old, 0) + overlap
        for old in sorted(shared, key=order_before.__getitem__):
            shares.append((-shared[old], order_after[new], order_before[old], new, old))
    return shares


def sharing(
    before: Mapping[str, Sequence[tuple[str, int, int]]],
    after: Mapping[str, Sequence[tuple[str, int, int]]],
) -> dict[str, list[str]]:
    """Each block of `after` and the blocks of `before` it shares characters
    with, in `before`'s order."""
    found: dict[str, list[str]] = {b: [] for b in after}
    for _, _, _, new, old in sorted(_shares(before, after), key=lambda s: s[2]):
        found[new].append(old)
    return found


def within(
    before: Mapping[str, Sequence[tuple[str, int, int]]],
    after: Mapping[str, Sequence[tuple[str, int, int]]],
) -> dict[str, str]:
    """Two cuts of one text: each block of `after` that starts where a block
    of `before` started continuing it (its block id the same), the others
    by `by_overlap` among the blocks the first pass left. A
    split's first piece and a merge's result keep the first block's node; a
    boundary moved between two blocks keeps both."""
    starts = {runs[0][:2]: block for block, runs in before.items() if runs}
    found = {
        block: starts[runs[0][:2]]
        for block, runs in after.items()
        if runs and runs[0][:2] in starts
    }
    taken = set(found.values())
    found |= by_overlap(
        {b: r for b, r in before.items() if b not in taken},
        {b: r for b, r in after.items() if b not in found},
    )
    return found
