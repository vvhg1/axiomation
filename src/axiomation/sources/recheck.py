"""The cited works' files checked again.

Each cited work's files are stamped through the paper's sources (its
holder, the file check, its SHA-256 and its content hash), each filed
where it differs from the store (`corpus.observe`). A file that prints
other content, or is absent or held back, lapses the judgments that read
a page of it, unless another file of the work prints what it printed, as
`readings` computes; no page is read again, since another reading of an
unchanged file lapses nothing. A provisional judgment whose files the
version of record (the library, or the work's PDF here) now holds with
the same content is reaffirmed by code, its stamps the version of
record's, so it is provisional no more.

What a work read again cannot lapse is listed: the readings that did not
verify whose evidence grew, which the next round asks again
(`judgments.grown`, docs/architecture.md § 9), and the open findings on a work
named, with what answers them.
"""

from __future__ import annotations

from collections.abc import Collection, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from axiomation import corpus, rulings, settings, versions
from axiomation.evidence import FileStamp, ReadStatus, same_content, work_of
from axiomation.judgments import (
    PAGED_ASPECTS,
    Author,
    CauseKind,
    Readings,
    StatusKind,
    file_contents,
    files_rested_on,
    files_seen,
    judge,
    readings,
)
from axiomation.queries import finding_clears
from axiomation.records import AnchorKind, Aspect, RecordStore
from axiomation.sources.config import SourcesError, load_settings, resolve
from axiomation.sources.providers import sources_for

#: Code's author for what a recheck files: a host and the command.
RECHECK = Author("axiomation", "sources recheck")
#: What a reading listed to ask again is asked by.
NEXT_ROUND = "the next round asks it (`axiomation sync`)"


def kept_store(paper: Path, store: Path | None) -> RecordStore | None:
    directory = versions.store_of(paper) if store is None else store
    if not versions.keeps_state(directory):
        return None
    return RecordStore(directory)


def recheck_lines(
    paper: Path,
    store: Path | None,
    keys: Collection[str] | None,
    state: str | None,
    asked: Collection[str] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Each cited work's files (of `keys`' works alone, where named, and
    each a judgment read a page of), stamped and filed where they differ
    (`corpus.observe`); the provisional judgments whose files the version
    of record now holds with the same content reaffirmed at `state` (default: the
    latest); what lapsed by a file listed, judgments and rulings; and what to ask again
    (`ask_again`) of the works in `asked` (default: `keys`): the grown
    subjects on them, and the open findings on them."""
    if isinstance(keys, str) or isinstance(asked, str):
        raise TypeError("recheck takes a collection of keys, never one key bare")
    keys = None if keys is None else frozenset(keys)
    asked = keys if asked is None else frozenset(asked)
    found = kept_store(paper, store)
    if found is None:
        where = versions.store_of(paper) if store is None else store
        return {"files": []}, [f"{where} keeps no state: nothing judged to recheck"]
    kept = versions.state(found, state) if state else versions.latest(found)
    assert kept is not None
    sources = sources_for(paper, found.run_directory)
    before = files_seen(found.judgment_records().files)
    observed = corpus.observe(found, kept, sources, keys)
    corpus.file(found, observed, RECHECK)
    contents = file_contents(found.judgment_records().files)
    works = None if keys is None else {work_of(k) for k in keys}
    # what it says is of the works named; the observation files the rest too
    now = {f.key: f for f in observed.files if works is None or work_of(f.key) in works}
    told = {k: _told(before.get(k), f, contents) for k, f in now.items()}
    lines = [
        f"{k}  {f.read_status.value}  {(f.content or f.file)[:12] or '-'}  {told[k]}"
        for k, f in sorted(now.items())
    ] or ["no cited file" + _of(keys)]

    seen = readings(found, kept)
    reaffirmed, lapsed = [], []
    for s in seen.statuses:
        if s.aspect not in PAGED_ASPECTS or (
            works is not None
            and not any(
                work_of(p.key) in works for r in s.reads for p in r.judgment.pages
            )
        ):
            continue
        if s.status is StatusKind.LAPSED and any(
            c.kind is CauseKind.CITED_FILE for r in s.reads for c in r.walked.causes
        ):
            lapsed.append(s.reads[-1].judgment.id)
        if (
            s.aspect is not Aspect.CITED
            or s.status is not StatusKind.HELD
            or s.held is None
            or not s.held.provisional
        ):
            continue
        j = s.held.judgment
        rests, _ = files_rested_on(found, j)
        read = dict.fromkeys(p.key for p in j.pages)
        if not all(
            (f := now.get(key)) is not None
            and f.read_status is ReadStatus.OK
            and key in rests
            and same_content(rests[key], f, contents)
            for key in read
        ):
            continue
        filed = judge(
            found,
            kept,
            j.aspect,
            s.held.subject,
            j.verdict,
            j.depth,
            f"{j.evidence} [the version of record holds its files with the same "
            "content: " + ", ".join(read) + "]",
            RECHECK,
            reaffirms=j.id,
            pages=[(p.key, p.page_index) for p in j.pages],
            sources=sources,
        )
        reaffirmed.append((j.id, filed.records.judgments[0].id))
    # a claim's latest ruling that rested on pages lapses by its file as a
    # reading does, and speaks no more; one a later ruling replaced, or a
    # proposal, says nothing of the claim
    ruled_lapsed = sorted(
        last.ruling.id
        for ruled in rulings.rulings(found, kept).values()
        if ruled.asked_again
        for last in [[r for r in ruled.reads if not r.proposed][-1]]
        if any(c.kind is CauseKind.CITED_FILE for c in last.walked.causes)
        and (works is None or any(work_of(p.key) in works for p in last.ruling.pages))
    )
    again = ask_again(seen, asked)
    undecided = observed.undecided
    lines += [
        f"reaffirmed {old} as {new}: provisional no more" for old, new in reaffirmed
    ]
    lines += [f"lapsed by a file: {j} (`axiomation reask` it)" for j in lapsed]
    lines += [
        f"lapsed by a file: ruling {r}, which speaks no more" for r in ruled_lapsed
    ]
    lines += [item.line for item in again]
    lines += [
        f"could not tell whether {k} reads: {why}" for k, why in undecided.items()
    ]
    payload = {
        "files": [
            {
                "key": f.key,
                "read_status": f.read_status.value,
                "file": f.file,
                "content": f.content,
                "changed": told[f.key] not in (UNCHANGED, FIRST_SEEN),
            }
            for _, f in sorted(now.items())
        ],
        "reaffirmed": [{"judgment": old, "by": new} for old, new in reaffirmed],
        "lapsed": lapsed,
        "rulings_lapsed": ruled_lapsed,
        "ask_again": [item.as_dict() for item in again],
        "undecided": undecided,
    }
    return payload, lines


def recheck_shared(
    directory: Path,
    store: Path | None,
    keys: Collection[str],
    asked: Collection[str] | None = None,
    collection: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """`recheck_lines` in every paper that reads the works through the same
    bibliography (and, where named, the same collection): the directory
    given, its store `store`, and each paper of its workspace, each in its
    own store, so none waits for its next command to learn what to ask
    again (docs/architecture.md § 9). A work's file and its confirmation are the
    bibliography's, whichever directory the command was given."""
    payload: dict[str, Any] = {"papers": {}, "no_state": [], "unread": {}}
    lines: list[str] = []
    for name, paper, own, error in _sharing(directory, store, collection):
        if error:
            payload["unread"][name] = error
            lines.append(f"{name}: its settings do not read: {error}")
        elif kept_store(paper, own) is None:
            payload["no_state"].append(name)
        else:
            payload["papers"][name], rechecked = recheck_lines(
                paper, own, keys, None, asked
            )
            lines += [f"{name}:", *(f"  {line}" for line in rechecked)]
    if payload["no_state"]:
        lines.append(
            "keeping no state, nothing judged to recheck: "
            + ", ".join(payload["no_state"])
        )
    return payload, lines


def judged_shared(
    directory: Path,
    store: Path | None,
    keys: Collection[str],
    collection: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """The judgments in force that read a page of `keys`, in every paper
    `recheck_shared` would recheck: what an ingest that reads those works
    again may lapse, told before it runs. Reads the stores, writes nothing."""
    keys = frozenset(keys)
    payload: dict[str, Any] = {"papers": {}, "unread": {}}
    lines: list[str] = []
    for name, paper, own, error in _sharing(directory, store, collection):
        if error:
            payload["unread"][name] = error
            lines.append(f"{name}: its settings do not read: {error}")
            continue
        found = kept_store(paper, own)
        kept = None if found is None else versions.latest(found)
        if found is None or kept is None:
            continue
        by_work: dict[str, list[str]] = {}
        for s in readings(found, kept).statuses:
            if s.status is not StatusKind.HELD or s.held is None:
                continue
            for key in dict.fromkeys(p.key for p in s.held.judgment.pages):
                if key in keys:
                    by_work.setdefault(key, []).append(s.held.judgment.id)
        if not by_work:
            continue
        payload["papers"][name] = by_work
        count = len({j for ids in by_work.values() for j in ids})
        lines.append(
            f"{name}: the judgments that read a page of a work read again, "
            f"which lapse where its file prints other content: {count}"
        )
        lines += [f"  {key}: {', '.join(ids)}" for key, ids in sorted(by_work.items())]
    return payload, lines


def _sharing(
    directory: Path, store: Path | None, collection: str | None
) -> Iterator[tuple[str, Path, Path | None, str]]:
    """Each paper that reads the works through the bibliography of the
    paper in `directory` (and, where named, the same collection): its name,
    its directory, the store given for it, and why its settings do not
    read (empty where they do); the directory given first. A workspace's
    root is no paper: it is one only where it keeps a store."""
    directory = directory.resolve()
    bib = _same_file(resolve(load_settings(directory, store)).bib)
    found = settings.members(directory)
    root, papers = found if found is not None else (directory.parent, ())
    rooted = directory == root and kept_store(directory, store) is None
    candidates = [] if rooted else [directory]
    candidates += [p for p in papers if p != directory]
    for paper in candidates:
        name = str(paper.relative_to(root)) if paper != root else str(paper)
        own = store if paper == directory else None
        try:
            resolved = resolve(load_settings(paper, own))
        except (SourcesError, settings.SettingsError) as error:
            yield name, paper, own, str(error)
            continue
        if paper != directory and (
            _same_file(resolved.bib) != bib
            or (collection is not None and resolved.collection != collection)
        ):
            continue
        yield name, paper, own, ""


def _same_file(path: Path | None) -> Path | None:
    return None if path is None else path.resolve()


@dataclass(frozen=True, slots=True)
class AskAgain:
    """A reading or a finding a work's new pages may change: its id, what
    it is on, why, and the command that asks it again."""

    record: str
    work: str
    on: str
    why: str
    command: str

    @property
    def line(self) -> str:
        return f"ask again: {self.record} ({self.on}): {self.why}; {self.command}"

    def as_dict(self) -> dict[str, str]:
        return {
            "record": self.record,
            "work": self.work,
            "on": self.on,
            "why": self.why,
            "command": self.command,
        }


def ask_again(seen: Readings, keys: Collection[str] | None) -> list[AskAgain]:
    """What a work read again cannot lapse, listed (docs/architecture.md § 9):
    each subject whose reading did not verify and whose evidence grew
    (`judgments.grown`), which the next round asks again, of a work in
    `keys` (any, for None); and for a work named, each open finding
    anchored on it or that read its pages, with what answers it."""
    works = None if keys is None else {work_of(k) for k in keys}
    found: list[AskAgain] = []
    for s in seen.statuses:
        if not s.grown:
            continue
        # the works it grew by: its subject's, or one it read a page of
        grew = {g.work for g in s.grown}
        if works is not None and not grew & works:
            continue
        assert s.held is not None
        j = s.held.judgment
        on = f"{j.aspect.value} {s.subject.node}, {j.verdict.value}"
        for work in sorted(grew if works is None else grew & works):
            why = "; ".join(g.why for g in s.grown if g.work == work)
            if why:
                found.append(AskAgain(j.id, work, on, why, NEXT_ROUND))
    if works is None:
        return found
    for f in seen.findings:
        on_works = {a.work for a in f.finding.anchors if a.kind is AnchorKind.WORK} | {
            work_of(p.key) for p in f.finding.pages
        }
        clears = finding_clears(f)
        for key in sorted(on_works & works):
            if clears is not None:
                found.append(
                    AskAgain(
                        f.finding.id,
                        key,
                        f"finding, {f.finding.aspect.value}",
                        f"found on {key}'s pages before",
                        clears,
                    )
                )
    return found


def _of(keys: Collection[str] | None) -> str:
    return "" if not keys else " of " + ", ".join(sorted(keys))


#: What a recheck says of a file the store knew as it is.
UNCHANGED = "unchanged"
#: ... of a file the store never saw.
FIRST_SEEN = "first seen"


def _told(was: FileStamp | None, now: FileStamp, contents: Mapping[str, str]) -> str:
    """What a file's stamp says against the store's before: first seen,
    printing other content, read otherwise (held back, absent, or read
    again), or unchanged; a file annotated or saved again prints the
    same."""
    if was is None:
        return FIRST_SEEN
    if was.read and now.read and not same_content(was, now, contents):
        return "prints other content"
    if was.read_status is not now.read_status:
        return f"{now.read_status.value} now, {was.read_status.value} before"
    return UNCHANGED
