"""`sources publications`: each cited work's publication looked up and
filed, and a published preprint's finding (docs/model.md § 8).

A work's publication is read from what the resolvers answer of its
identifier, its DOI, else its arXiv identifier as arXiv's DOI: a
retraction or a correction first, then the type Crossref or OpenAlex
gives it, never the bibliography's entry type. A preprint's versions of
record are then looked up (`resolvers.record`); one found makes it
`preprint-published`. An entry with no identifier is `unknown`, which a
person sets.

What is filed is a work seen (`judgments.see_work`), by code, only where
it differs from the resolvers' latest for the work; a person's own speaks
over it (`judgments.speaking_works`). A published preprint the state cites
is a finding, severity 1, aspect `cited`, on the work and each citation of
it: cite the version of record. It is filed once per work: a live finding
of the lookup's on the work, open or answered, holds it, so a person's
dismissal stands until a citation of the work changes.

A network pass, cached for thirty days; no model is called.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from axiomation import versions
from axiomation.judgments import (
    Author,
    JudgmentError,
    Readings,
    anchored,
    author_of,
    find,
    readings,
    see_work,
    seen,
)
from axiomation.queries import View
from axiomation.records import (
    AnchorKind,
    Aspect,
    Publication,
    RecordStore,
    VersionOfRecord,
    WorkSeen,
)
from axiomation.review import citation_sites
from axiomation.sources.bib import BibRecord, records
from axiomation.sources.config import (
    Settings,
    SourcesError,
    contact,
    core_api_key,
    no_contact,
    resolve,
)
from axiomation.sources.recheck import kept_store
from axiomation.sources.resolvers import ResolvedSource, Resolver
from axiomation.sources.resolvers.record import Lookup, look_up

#: Code's author for a publication looked up and a published preprint's
#: finding.
LOOKUP = Author("axiomation", "sources publications")

#: The published preprint's finding: its severity, and its fix.
PUBLISHED_SEVERITY = 1
PUBLISHED_FIX = (
    "cite the version of record, and check that what the paper takes from "
    "the preprint is in it and under which number: a conference version is "
    "often shorter, and its theorems renumbered"
)

#: What a DOI a preprint names must be published as to be its version of
#: record; a correction or a retraction of it is still the published work.
OF_RECORD = (
    Publication.REFEREED,
    Publication.BOOK,
    Publication.ERRATUM,
    Publication.RETRACTED,
)

#: arXiv's DOI prefix: an arXiv identifier asked as a DOI.
ARXIV_DOI = "10.48550/arXiv."

#: Each publication by the type the resolvers give (`chain._item_type`'s
#: names, then Crossref's and OpenAlex's own).
_BY_TYPE = {
    "journalArticle": Publication.REFEREED,
    "conferencePaper": Publication.REFEREED,
    "book": Publication.BOOK,
    "bookSection": Publication.BOOK,
    "monograph": Publication.BOOK,
    "edited-book": Publication.BOOK,
    "reference-book": Publication.BOOK,
    "book-set": Publication.BOOK,
    "book-part": Publication.BOOK,
    "preprint": Publication.PREPRINT,
    "thesis": Publication.THESIS_OR_REPORT,
    "report": Publication.THESIS_OR_REPORT,
    "report-component": Publication.THESIS_OR_REPORT,
}


@dataclass(frozen=True, slots=True)
class Found:
    """One work's publication as looked up: the identifier asked, the
    publication and where it came from, a preprint's versions of record,
    the title hits passed, the sources that did not answer, and whether
    what was asked answered, so a lookup that failed files nothing."""

    key: str
    doi: str
    publication: Publication
    source: str
    versions: tuple[VersionOfRecord, ...] = ()
    passed: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()
    answered: bool = True


def classify(work: ResolvedSource) -> tuple[Publication, str]:
    """A resolved work's publication, the first that holds: retracted, an
    erratum, then by its type (an article in a repository is a preprint);
    and where it came from, in words."""
    said = ", ".join(work.resolvers)
    if work.retracted:
        return Publication.RETRACTED, f"{said}: retracted"
    if work.corrected:
        named = ", ".join(f"doi:{doi}" for doi in work.corrections)
        return Publication.ERRATUM, "crossref: corrected" + (
            f" by {named}" if named else ""
        )
    kind = work.item_type or "no type"
    where = f" in a {work.source_type}" if work.source_type else ""
    source = f"{said}: {kind}{where}"
    found = _BY_TYPE.get(work.item_type, Publication.UNKNOWN)
    if found is Publication.REFEREED and work.source_type == "repository":
        return Publication.PREPRINT, source
    return found, source


def identifier(record: BibRecord) -> str:
    """The identifier a work is asked by: its DOI, else its arXiv
    identifier as arXiv's DOI; empty where it has neither."""
    if record.doi:
        return record.doi
    return ARXIV_DOI + record.arxiv if record.arxiv else ""


def look_up_work(resolver: Resolver, record: BibRecord, fresh: bool = False) -> Found:
    """One cited work's publication, from its entry's identifier and what
    the resolvers answer of it; a preprint's versions of record looked up
    by its record, its title and its first author."""
    asked = identifier(record)
    if not asked:
        return Found(
            record.key,
            "",
            Publication.UNKNOWN,
            "no DOI or arXiv identifier in the entry",
        )
    work = resolver.resolve(asked, bib_key=record.key, fresh=fresh)
    if not work.resolvers:
        return Found(
            record.key,
            work.doi,
            Publication.UNKNOWN,
            work.detail or "no resolver answered",
            failed=(work.detail,) if work.detail else (),
            answered=False,
        )
    publication, source = classify(work)
    if publication is not Publication.PREPRINT:
        return Found(record.key, work.doi, publication, source)

    def venue(doi: str) -> tuple[str, str] | None:
        target = resolver.resolve(doi, fresh=fresh)
        if classify(target)[0] not in OF_RECORD:
            return None
        return target.venue, target.issued[:4]

    lookup: Lookup = look_up(
        resolver.connection,
        doi=work.doi,
        arxiv=record.arxiv,
        title=record.title,
        authors=record.author,
        email=resolver.email,
        fresh=fresh,
        resolve=venue,
    )
    return Found(
        record.key,
        work.doi,
        Publication.PREPRINT_PUBLISHED if lookup.versions else Publication.PREPRINT,
        source,
        lookup.versions,
        lookup.passed,
        lookup.failed,
    )


def resolver_for(config: Settings) -> Resolver:
    if config.cache_dir is None:
        raise SourcesError("no cache directory: the paper keeps no store")
    return Resolver(
        config.cache_dir / "sources.db",
        email=contact(config).address,
        core_key=core_api_key(config),
    )


# --- filing --------------------------------------------------------------------------


def latest_looked_up(store: RecordStore) -> dict[str, WorkSeen]:
    """Each work's latest record by code (a lookup or a resolve), by key:
    what a new lookup is compared with."""
    authors = store.authoritative()
    return {
        w.key: w
        for w in store.judgment_records().works
        if not author_of(authors, w.answer).person
    }


def moved(found: Found, before: WorkSeen | None) -> bool:
    """Whether a lookup says something the resolvers' latest record does
    not."""
    return before is None or (
        before.publication,
        before.doi,
        before.source,
        before.records,
    ) != (found.publication, found.doi or None, found.source, found.versions)


def file_found(store: RecordStore, found: Found) -> str | None:
    """A lookup filed as code's work seen where it answered and moved; the
    record's id, else None. A lookup that found no version of record while
    a source did not answer files nothing over an earlier record: a miss
    is never read as "none"."""
    before = latest_looked_up(store).get(found.key)
    if not found.answered or not moved(found, before):
        return None
    if before is not None and found.failed and not found.versions:
        return None
    filed = see_work(
        store,
        found.key,
        found.doi or None,
        found.publication,
        LOOKUP,
        found.source,
        found.versions,
    )
    return filed.records.works[0].id


def set_by_person(
    store: RecordStore, key: str, publication: Publication, why: str, author: Author
) -> str:
    """A person's setting of a work's publication, with why: it speaks
    over every lookup, before or after."""
    if not author.person:
        raise JudgmentError("only a person sets a work's publication")
    if not why.strip():
        raise JudgmentError("say why: --why")
    filed = see_work(store, key, None, publication, author, why)
    return filed.records.works[0].id


def published_problem(key: str, found: Sequence[VersionOfRecord]) -> str:
    """The published preprint's finding's problem: each version of record,
    where it was found and how it was matched."""
    named = "; ".join(
        f"{v.venue or 'a venue not named'}"
        + (f" {v.year}" if v.year and v.year not in v.venue else "")
        + (f", doi:{v.doi}" if v.doi else "")
        + (f", {v.url}" if v.url else "")
        + f" (found by {v.source}, by {v.match.value})"
        for v in found
    )
    unsure = (
        " A match by title and first author can be wrong: check it is this work."
        if all(v.match.value == "title-and-authors" for v in found)
        else ""
    )
    return f"{key} is cited as a preprint, published since: {named}.{unsure}"


def _held(found: Readings) -> set[str]:
    """The works the lookup holds a live finding on at the readings' state,
    open or answered: a person's dismissal holds."""
    return {
        a.work
        for f in found.findings
        if f.author == LOOKUP and f.live
        for a in f.finding.anchors
        if a.kind is AnchorKind.WORK and a.work
    }


def file_published(
    store: RecordStore, kept: versions.State, key: str, found: Sequence[VersionOfRecord]
) -> str:
    """The published preprint's finding, on the work and each citation of
    it at `kept`."""
    view = View(store, kept)
    at = seen(store, kept)
    anchors = [anchored(at, AnchorKind.WORK, work=key)]
    sites, _ = citation_sites(view)
    for site in sites:
        on = site.subject
        block = at.ident_of.get(on.node)
        if on.work != key or block is None or not on.words:
            continue
        anchor = anchored(at, AnchorKind.SPAN, block, on.words, text=view.texts[block])
        if anchor not in anchors:
            anchors.append(anchor)
    filed = find(
        store,
        kept,
        Aspect.CITED,
        anchors,
        PUBLISHED_SEVERITY,
        published_problem(key, found),
        PUBLISHED_FIX,
        LOOKUP,
    )
    return filed.records.findings[0].id


# --- the command ---------------------------------------------------------------------


@dataclass
class Pass:
    """What one pass looked up, filed and left."""

    state: str
    found: list[Found] = field(default_factory=list)
    filed: dict[str, str] = field(default_factory=dict)
    findings: dict[str, str] = field(default_factory=dict)
    held: list[str] = field(default_factory=list)
    #: a person's setting that speaks, by key
    set_by: dict[str, WorkSeen] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    #: what the pass lost for want of a contact address, and its fix;
    #: empty where one is set
    no_contact: str = ""


def cited_keys(store: RecordStore, kept: versions.State) -> list[str]:
    """The works the state cites, each once, in the paper's order."""
    at = seen(store, kept)
    order = {n.node: i for i, n in enumerate(kept.nodes)}
    cited = sorted(at.works_of.items(), key=lambda item: order.get(item[0], len(order)))
    return list(dict.fromkeys(k for _, keys in cited for k in keys))


def look_up_all(
    config: Settings,
    store_dir: Path | None,
    keys: Collection[str] = (),
    fresh: bool = False,
    dry_run: bool = False,
) -> Pass:
    """Each work the latest state cites (of `keys` alone, where named)
    looked up, its publication filed where it moved, and each published
    preprint's finding filed once; `dry_run` files nothing."""
    store = kept_store(config.root, store_dir)
    if store is None:
        where = versions.store_of(config.root) if store_dir is None else store_dir
        raise SourcesError(f"{where} keeps no state: build the paper first")
    bib = resolve(config).bib
    if bib is None:
        raise SourcesError("no bibliography. Set [paper].bib before looking up works.")
    kept = versions.latest(store)
    assert kept is not None
    cited = cited_keys(store, kept)
    unknown = [k for k in keys if k not in cited]
    if unknown:
        raise SourcesError(
            f"the paper cites no {', '.join(unknown)} at state {kept.id[:12]}"
        )
    entries = records(bib)
    run = Pass(kept.id)
    read = readings(store, kept)
    held = _held(read)
    speaking = read.works
    authors = store.authoritative()
    with resolver_for(config) as resolver:
        if not resolver.email:
            run.no_contact = no_contact(config)
        for key in keys or cited:
            record = entries.get(key)
            if record is None:
                run.missing.append(key)
                continue
            found = look_up_work(resolver, record, fresh)
            run.found.append(found)
            spoken = speaking.get(key)
            if spoken is not None and author_of(authors, spoken.answer).person:
                run.set_by[key] = spoken
            if dry_run:
                continue
            filed = file_found(store, found)
            if filed:
                run.filed[key] = filed
            if (
                found.publication is not Publication.PREPRINT_PUBLISHED
                or key in run.set_by
            ):
                continue
            if key in held:
                run.held.append(key)
                continue
            run.findings[key] = file_published(store, kept, key, found.versions)
    return run


def pass_lines(run: Pass, dry_run: bool) -> tuple[dict[str, Any], list[str]]:
    """A pass as the command prints it, and its JSON."""
    lines = [f"state {run.state}" + (" (dry run: nothing filed)" if dry_run else "")]
    if run.no_contact:
        lines.append(f"no contact address: {run.no_contact}")
    for found in run.found:
        lines += list(_found_lines(found))
        person = run.set_by.get(found.key)
        if person is not None:
            lines.append(
                f"  a person's setting speaks: {person.publication.value} "
                f"({person.source})"
            )
        if found.key in run.filed:
            lines.append(f"  filed {run.filed[found.key]}")
        if found.key in run.findings:
            lines.append(f"  filed finding {run.findings[found.key]}")
        if found.key in run.held:
            lines.append("  its finding filed before holds (open or answered)")
    lines += [f"{k}  not in the bibliography" for k in run.missing]
    counted = Counter(f.publication.value for f in run.found)
    lines.append(
        "publications: "
        + (", ".join(f"{n} {p}" for p, n in counted.most_common()) or "none")
    )
    payload = {
        "state": run.state,
        "dry_run": dry_run,
        "works": [
            {
                "key": f.key,
                "doi": f.doi or None,
                "publication": f.publication.value,
                "source": f.source,
                "versions": [_version_data(v) for v in f.versions],
                "passed": list(f.passed),
                "failed": list(f.failed),
                "answered": f.answered,
                "filed": run.filed.get(f.key),
                "finding": run.findings.get(f.key),
                "held": f.key in run.held,
                "set_by_person": (
                    run.set_by[f.key].publication.value if f.key in run.set_by else None
                ),
            }
            for f in run.found
        ],
        "missing": run.missing,
        "no_contact": run.no_contact or None,
        "counts": dict(counted),
    }
    return payload, lines


def _found_lines(found: Found) -> Iterator[str]:
    yield f"{found.key}  {found.publication.value}  ({found.source})"
    for v in found.versions:
        yield (
            f"  version of record: {v.venue}"
            + (f" {v.year}" if v.year and v.year not in v.venue else "")
            + (f", doi:{v.doi}" if v.doi else "")
            + (f", {v.url}" if v.url else "")
            + f" ({v.source}, {v.match.value})"
        )
    for passed in found.passed:
        yield f"  passed: {passed}"
    for failed in found.failed:
        yield f"  not answered: {failed}"
    if not found.answered:
        yield "  nothing filed until a resolver answers"


def _version_data(v: VersionOfRecord) -> dict[str, Any]:
    return {
        "venue": v.venue,
        "year": v.year,
        "doi": v.doi,
        "url": v.url,
        "source": v.source,
        "match": v.match.value,
    }
