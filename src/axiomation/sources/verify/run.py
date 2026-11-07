"""`sources verify`: the quotation and locator verifiers over a kept state,
and what they settle filed as code.

- A quotation the work prints, exactly or by the aligned tier at cost zero,
  is a `cited` judgment, `verified`, depth `computational`; one a small
  alignment cost away, `not-shown`, its edits named. Its subject the quoted
  words, its work the key, its page read again and stamped at filing
  (`judgments.judge`). A site the script cannot settle is left to a reader.
- A label locator the work prints nowhere is a finding, aspect `cited`,
  severity 2, anchored on the citation and its work. A label found, or an
  inconclusive search, is reported and files nothing.

A site the verifier already holds a live record on is not read again, so a
second run files nothing; one whose record lapsed (its block or its page
changed) is checked again. A finding a person answered is still held: a
dismissal stands until the citation changes. `--dry-run` reports and files
nothing.

What it prints, lines or JSON, names pages and edits, never a page's text:
page text reaches an agent fenced, through `sources page`. The
judgment's evidence quotes the passage, as any judgment's evidence quotes
what it read.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from axiomation import versions
from axiomation.judgments import (
    Author,
    JudgmentError,
    anchored,
    find,
    judge,
    readings,
    seen,
    subject,
)
from axiomation.queries import View
from axiomation.records import AnchorKind, Aspect, Depth, RecordStore, Verdict
from axiomation.sites import Site, SiteKind, Unplaced, sites
from axiomation.sources.providers import sources_for
from axiomation.sources.recheck import kept_store
from axiomation.sources.verify import locator, quotation

#: Code's author for what a verification files: a host and the command.
VERIFY = Author("axiomation", "sources verify")

#: A locator's finding: the problem's severity, and its fix.
LOCATOR_SEVERITY = 2
LOCATOR_FIX = "name the result the work prints, or cite the page"


@dataclass
class Verified:
    """What one run checked, filed and left."""

    quotations: list[quotation.QuotationCheck] = field(default_factory=list)
    locators: list[locator.LocatorCheck] = field(default_factory=list)
    #: each check's filed record id, by its index in its list
    filed: dict[tuple[SiteKind, int], str] = field(default_factory=dict)
    held: list[Site] = field(default_factory=list)
    unplaced: list[Unplaced] = field(default_factory=list)
    refused: list[tuple[Site, str]] = field(default_factory=list)


def verify(
    paper: Path,
    store_dir: Path | None,
    key: str | None = None,
    state: str | None = None,
    dry_run: bool = False,
) -> tuple[Verified, str]:
    """Every quotation and label locator at `state` (default: the latest),
    of `key` alone where named, checked and what settles filed; the run and
    the kept state's id."""
    store = kept_store(paper, store_dir)
    if store is None:
        where = versions.store_of(paper) if store_dir is None else store_dir
        raise JudgmentError(f"{where} keeps no state: build the paper first")
    kept = versions.state(store, state) if state else versions.latest(store)
    assert kept is not None
    placed, unplaced = sites(store, kept, key)
    run = Verified(unplaced=unplaced)
    held_quotes, held_locators = _held(store, kept)
    view = View(store, kept)
    at = seen(store, kept)
    sources = sources_for(paper, store.run_directory)
    pages = locator.PagesOnce(sources)
    for site in placed:
        if site.kind is SiteKind.QUOTATION:
            on = _subject(at, view, site, run)
            if on is None:
                continue
            if (on.node, on.words, on.work) in held_quotes:
                run.held.append(site)
                continue
            checked = quotation.check(sources, site)
            run.quotations.append(checked)
            if checked.verdict is None or dry_run:
                continue
            assert checked.page_index is not None
            filed = judge(
                store,
                kept,
                Aspect.CITED,
                on,
                checked.verdict,
                Depth.COMPUTATIONAL,
                f"{_evidence(checked)} [sources verify, {checked.how}]",
                VERIFY,
                pages=[(site.key, checked.page_index)],
                sources=sources,
            )
            run.filed[(SiteKind.QUOTATION, len(run.quotations) - 1)] = (
                filed.records.judgments[0].id
            )
            continue
        try:
            anchors = (
                anchored(
                    at,
                    AnchorKind.SPAN,
                    site.block,
                    site.words,
                    text=view.texts[site.block],
                ),
                anchored(at, AnchorKind.WORK, work=site.key),
            )
        except JudgmentError as error:
            run.refused.append((site, str(error)))
            continue
        if anchors in held_locators:
            run.held.append(site)
            continue
        found = locator.check(pages, site)
        run.locators.append(found)
        if found.outcome is not locator.Outcome.NOT_PRINTED or dry_run:
            continue
        filed = find(
            store,
            kept,
            Aspect.CITED,
            list(anchors),
            LOCATOR_SEVERITY,
            f"{site.locator} is printed nowhere in {site.key}: {found.note}",
            LOCATOR_FIX,
            VERIFY,
        )
        run.filed[(SiteKind.LOCATOR, len(run.locators) - 1)] = filed.records.findings[
            0
        ].id
    return run, kept.id


def _subject(at, view: View, site: Site, run: Verified):
    try:
        return subject(
            at,
            Aspect.CITED,
            site.block,
            span=site.words,
            text=view.texts[site.block],
            work=site.key,
        )
    except JudgmentError as error:
        run.refused.append((site, str(error)))
        return None


def _held(store: RecordStore, kept: versions.State):
    """The verifier's live records at `kept`: its judgments' subjects, and
    its findings' anchors, open or answered."""
    found = readings(store, kept)
    quotes = {
        (r.subject.node, r.subject.words, r.subject.work)
        for r in found.reads
        if r.author == VERIFY and r.live and r.judgment.aspect is Aspect.CITED
    }
    located = {
        tuple(f.finding.anchors)
        for f in found.findings
        if f.author == VERIFY and f.live
    }
    return quotes, located


def _evidence(checked: quotation.QuotationCheck) -> str:
    if checked.verdict is Verdict.VERIFIED:
        return f"quoted as the work prints it ({checked.note}): {checked.evidence}"
    return f"misquoted: {checked.note}; the page reads {checked.evidence}"


def verify_lines(
    paper: Path,
    store_dir: Path | None,
    key: str | None,
    state: str | None,
    dry_run: bool,
) -> tuple[dict[str, Any], list[str]]:
    """`verify`'s run as the command prints it, and its JSON."""
    run, kept = verify(paper, store_dir, key, state, dry_run)
    lines = [f"state {kept}" + (" (dry run: nothing filed)" if dry_run else "")]
    for i, q in enumerate(run.quotations):
        filed = run.filed.get((SiteKind.QUOTATION, i))
        lines.append(f"quotation  {q.site.where}  {q.site.key} {q.site.locator or ''}")
        if q.verdict is None:
            lines.append(f"  left to a reader: {q.note}" + _how(q.how))
        else:
            lines.append(
                f"  {q.verdict.value} ({q.note}): {q.site.key} page index "
                f"{q.page_index}{_how(q.how)}"
            )
        if filed:
            lines.append(f"  filed judgment {filed}")
    for i, c in enumerate(run.locators):
        filed = run.filed.get((SiteKind.LOCATOR, i))
        lines.append(f"locator    {c.site.where}  {c.site.key} {c.site.locator}")
        if c.outcome is locator.Outcome.FOUND:
            lines.append(
                f"  found on page index {c.page_index} (nothing filed: a label "
                "printed is not the claim supported)"
            )
        else:
            lines.append(f"  {c.outcome.value}: {c.note}")
        if filed:
            lines.append(f"  filed finding {filed}")
    lines += [
        f"held       {s.where}  {s.key} {s.locator or ''}: checked before"
        for s in run.held
    ]
    lines += [
        f"unplaced   {u.file}:{u.line}  {u.key} {u.locator or ''}: {u.why}"
        for u in run.unplaced
    ]
    lines += [f"refused    {s.where}  {s.key}: {why}" for s, why in run.refused]
    if len(lines) == 1:
        lines.append("no quotation and no label locator" + _of(key))
    payload = {
        "state": kept,
        "dry_run": dry_run,
        "quotations": [
            q.as_dict() | {"filed": run.filed.get((SiteKind.QUOTATION, i))}
            for i, q in enumerate(run.quotations)
        ],
        "locators": [
            c.as_dict() | {"filed": run.filed.get((SiteKind.LOCATOR, i))}
            for i, c in enumerate(run.locators)
        ],
        "held": [
            {"kind": s.kind.value, "block": s.block, "key": s.key} for s in run.held
        ],
        "unplaced": [
            {"kind": u.kind.value, "file": u.file, "line": u.line, "why": u.why}
            for u in run.unplaced
        ],
        "refused": [
            {"kind": s.kind.value, "block": s.block, "why": why}
            for s, why in run.refused
        ],
    }
    return payload, lines


def _how(how: str) -> str:
    return f" [{how}]" if how else ""


def _of(key: str | None) -> str:
    return "" if key is None else f" of {key}"
