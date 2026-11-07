"""`axiomation sources`: build a corpus, and read one.

`ingest` is the human-run batch command; everything else is what an agent
reaches, through this CLI and bash alone (no MCP server). Every
action takes `--json` and every read prints a `read_status`, because a
verifier has to record what kind of nothing it got. `locate`, `grep`
and `toc` are the exact reads: a string over one source's own
text, and the shape of the source, so an agent can find a labelled result,
establish a negative and pick a chapter without a search standing in for
any of them.

Every read goes through the core's `evidence.Sources`, the library (or, without
it, each work's PDF on this machine) first and a fetched copy where only it
holds a work, each page stamped and fenced there.
`recheck` checks the cited works' files again; `verify` runs the quotation and locator
verifiers; `publications` looks up each cited work's publication and files it
in the paper's store (`resolve` files one work's), with a finding where a
preprint cited has been published since; `accept` records a person's word that
the paper rests on a work as it is. A work whose file is not its entry's work
reads as `mismatch` until `confirm` records a person's word that it is."""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from axiomation.evidence import (
    NO_PAGE,
    EvidenceError,
    ReadStatus,
    Results,
    Sources,
    fenced,
    fenced_payload,
)
from axiomation.paper_argument import STORE_HELP, PaperNotFound, with_paper
from axiomation.sources.bib import BibRecord, records
from axiomation.sources.config import (
    OcrBackend,
    ResolvedSources,
    Settings,
    SourcesError,
    api_key,
    library_off,
    library_on,
    load_settings,
    ocr_api_key,
    resolve,
)
from axiomation.sources.providers import sources_for
from axiomation.user_settings import UserKey, where_to_put

EXIT_OK = 0
EXIT_ERROR = 1

Answer = tuple[dict[str, Any], list[str], int]

TEXT_PREVIEW = 200


@dataclass(frozen=True, slots=True)
class Action:
    """One subcommand: what it is called, what it takes, what it does."""

    name: str
    help: str
    arguments: Callable[[argparse.ArgumentParser], None]
    run: Callable[[Settings, argparse.Namespace], Answer]


# --- Arguments ---


def _ingest_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--bib",
        type=Path,
        default=None,
        help="the .bib to read (default: the paper's settings', else the one "
        ".bib in the paper's directory)",
    )
    parser.add_argument(
        "--collection",
        default=None,
        help="the collection to build (default: the one `axiomation settings` shows)",
    )
    parser.add_argument("--key", default=None, help="one bib key only")
    parser.add_argument(
        "--force",
        action="store_true",
        help="ingest again whether or not anything changed",
    )
    parser.add_argument(
        "--prune", action="store_true", help="delete keys the bib no longer has"
    )
    parser.add_argument(
        "--reread",
        action="store_true",
        help=(
            "read again the stored pages the ingest lists as needing it (each "
            "cause: docs/ocr.md); without it they are listed"
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="say what would happen and stop"
    )


def _search_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("query", help="what to search for, in words")
    parser.add_argument("--key", default=None, help="search within one cited work")
    parser.add_argument(
        "--top-k", type=int, default=5, help="how many results (default 5)"
    )


def _page_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("key", help="the cited work's bib key")
    parser.add_argument("page", type=int, help="the page's position in its PDF, from 1")
    parser.add_argument("--to", type=int, default=None, help="the end of a range")


def _image_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("key", help="the cited work's bib key")
    parser.add_argument("page", type=int, help="the page's position in its PDF, from 1")
    parser.add_argument(
        "-o", "--output", type=Path, default=None, help="write the PNG here"
    )
    parser.add_argument(
        "--dpi", type=int, default=120, help="the image's resolution (default 120)"
    )


def _status_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="list every cited work: each one in the corpus, or each read from its PDF",
    )


def _key_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("key", help="a bib key")


def _resolve_arguments(parser: argparse.ArgumentParser) -> None:
    _key_argument(parser)
    parser.add_argument(
        "--fresh", action="store_true", help="bypass the resolver cache"
    )


def _fetch_arguments(parser: argparse.ArgumentParser) -> None:
    _resolve_arguments(parser)
    parser.add_argument(
        "--url", default="", help="a web-discovered PDF candidate to validate"
    )


def _locate_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("key", help="a bib key")
    parser.add_argument(
        "locator",
        help='a label the source prints: "Theorem 2.2.3", "\\S4.6", "Chapter 7"',
    )


def _grep_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("key", help="a bib key")
    parser.add_argument(
        "pattern",
        help="a regular expression; case-insensitive, normalized like the text",
    )


# --- Actions ---


def _sources(config: Settings, args: argparse.Namespace) -> Sources:
    """The paper's sources, read through the core: the library, a fetched
    copy where only it holds a work."""
    return sources_for(config.root, args.store)


def _settings(config: Settings, args: argparse.Namespace) -> ResolvedSources:
    settings = resolve(config)
    collection = getattr(args, "collection", None)
    if collection:
        settings = replace(settings, collection=collection)
    bib = getattr(args, "bib", None)
    if bib:
        settings = replace(settings, bib=Path(bib).absolute())
    return settings


def _run_ingest(config: Settings, args: argparse.Namespace) -> Answer:
    from axiomation.sources.ingest.run import apply_ingest, plan_ingest
    from axiomation.sources.library.qdrant import connect

    settings = _settings(config, args)
    if settings.bib is None:
        raise SourcesError(
            "no bibliography to ingest. Set [paper].bib or [sources].bib, "
            "or pass --bib."
        )
    client = connect(settings)
    try:
        plan = plan_ingest(
            client,
            settings,
            bib_path=settings.bib,
            key=args.key,
            force=args.force,
            prune=args.prune,
            reread=args.reread,
        )
        if args.dry_run:
            refused = bool(plan.refusal or plan.prune_refusal)
            code = EXIT_ERROR if refused else EXIT_OK
            payload, lines = plan.as_dict(), list(_plan_lines(plan))
            again = [item.entry.key for item in plan.pending] + [
                k for m in plan.moves for k in (m.source, m.entry.key)
            ]
            if again:  # what read the works it would read again may lapse
                from axiomation.sources.recheck import judged_shared

                payload["judged"], judged = judged_shared(
                    config.root, args.store, again, settings.collection
                )
                lines += ["", *judged] if judged else []
            return (payload, lines, code)
        report = apply_ingest(
            client,
            settings,
            plan,
            api_key=api_key(config),
            ocr_key=ocr_api_key(config),
            progress=_progress(args.json),
        )
    finally:
        client.close()
    payload = report.as_dict()
    lines = list(_report_lines(report, settings))
    ingested = [i for i in plan.pending if i.entry.key in report.read]
    # a file moved between keys changes what both keys hold
    moved = list(dict.fromkeys(k for m in plan.moves for k in (m.source, m.entry.key)))
    if (
        ingested or moved
    ):  # works in anew: what read them before is read again or listed
        from axiomation.sources.ingest.run import Reason
        from axiomation.sources.recheck import recheck_shared

        # a forced re-ingest of the same file changes no work: its files are
        # checked again, its other readings not listed
        changed = [i.entry.key for i in ingested if i.reason is not Reason.FORCED]
        payload["recheck"], rechecked = recheck_shared(
            config.root,
            args.store,
            [*(i.entry.key for i in ingested), *moved],
            [*changed, *moved],
            settings.collection,
        )
        lines.extend(["", *rechecked])
    return (payload, lines, EXIT_ERROR if report.stopped else EXIT_OK)


def _run_search(config: Settings, args: argparse.Namespace) -> Answer:
    if library_on(_settings(config, args)):
        api_key(config)  # a search embeds its query: say so before Qdrant does
    sources = _sources(config, args)
    results = sources.search(args.query, bib_key=args.key, top_k=args.top_k)
    copy = sources.copy(args.key) if args.key else ""
    return (
        {**fenced_payload(results.as_dict()), "copy": copy},
        [*_copy_lines(args.key, copy), *_result_lines(results)],
        _code(results),
    )


def _run_page(config: Settings, args: argparse.Namespace) -> Answer:
    sources = _sources(config, args)
    results = sources.pages(args.key, args.page, args.to or args.page)
    copy = sources.copy(args.key)
    return (
        {**fenced_payload(results.as_dict()), "copy": copy},
        [*_copy_lines(args.key, copy), *_page_lines(results)],
        _code(results),
    )


def _run_image(config: Settings, args: argparse.Namespace) -> Answer:
    from axiomation.sources.read.render import render

    settings = _settings(config, args)
    rendered = render(
        _sources(config, args),
        args.key,
        args.page,
        dpi=args.dpi,
        zotero_url=settings.zotero_url,
    )
    if not rendered.png:
        return (
            rendered.as_dict(),
            [f"{rendered.read_status}: {rendered.detail}"],
            EXIT_ERROR,
        )
    if args.output:
        args.output.write_bytes(rendered.png)
        lines = [f"wrote {len(rendered.png)} bytes to {args.output}"]
    elif args.json:
        lines = []
    else:
        sys.stdout.buffer.write(rendered.png)
        return ({}, [], EXIT_OK)
    return (rendered.as_dict(), lines, EXIT_OK)


def _run_status(config: Settings, args: argparse.Namespace) -> Answer:
    from axiomation.sources.read.status import collect

    status = collect(config)
    return (
        status.as_dict(),
        list(_status_lines(status, verbose=args.verbose)),
        EXIT_OK if status.ok else EXIT_ERROR,
    )


def _run_resolve(config: Settings, args: argparse.Namespace) -> Answer:
    from axiomation.sources.read.resolve import resolve as resolve_key

    settings = _settings(config, args)
    _made_store(config, args)
    # the bibliography as resolved: a workspace's `[sources].bib` included
    config = replace(config, bib=settings.bib)
    resolution = resolve_key(
        _sources(config, args),
        args.key,
        config=config,
        fresh=args.fresh,
        zotero_url=settings.zotero_url,
    )
    payload, lines = resolution.as_dict(), list(_resolution_lines(resolution))
    looked = _file_publication(config, args)
    if looked is not None:
        found, filed = looked
        payload["publication"] = found.publication.value
        lines += ["", f"publication: {found.publication.value} ({found.source})"]
        if filed:
            payload["filed"] = filed
            lines.append(f"filed: {filed}")
    return (
        payload,
        lines,
        EXIT_OK if resolution.read_status is ReadStatus.OK else EXIT_ERROR,
    )


def _file_publication(config: Settings, args: argparse.Namespace) -> Any:
    """The resolved work's publication looked up and filed in the paper's
    store, where it keeps a state and its bibliography holds the key, as
    `publications` files it: the lookup and its record id, else None."""
    from axiomation.sources.publication import file_found, look_up_work, resolver_for
    from axiomation.sources.recheck import kept_store

    store = kept_store(config.root, args.store)
    record = None if config.bib is None else records(config.bib).get(args.key)
    if store is None or record is None:
        return None
    with resolver_for(config) as resolver:
        found = look_up_work(resolver, record, args.fresh)
    return found, file_found(store, found)


def _made_store(config: Settings, args: argparse.Namespace) -> None:
    """The paper's own store, with its `.gitignore`, before a cache goes in
    it."""
    if args.store is None:
        from axiomation.versions import default_store

        default_store(config.root)


def _run_fetch(config: Settings, args: argparse.Namespace) -> Answer:
    from axiomation.sources.bib import records
    from axiomation.sources.config import contact, core_api_key
    from axiomation.sources.resolvers import Resolver
    from axiomation.sources.resolvers.fetch import fetch_source

    settings = _settings(config, args)
    if settings.bib is None:
        raise SourcesError("no bibliography. Set [paper].bib before fetching a source.")
    record = records(settings.bib).get(args.key)
    if record is None:
        raise SourcesError(f"{args.key!r} is not in {settings.bib}")
    assert settings.cache_dir is not None
    _made_store(config, args)
    with Resolver(
        settings.cache_dir / "sources.db",
        email=contact(config).address,
        core_key=core_api_key(config),
    ) as resolver:
        work = resolver.resolve(record.doi, bib_key=args.key, fresh=args.fresh)
    fetched = fetch_source(
        settings, record, work, url=args.url, ocr_key=ocr_api_key(config)
    )
    return (
        fetched.as_dict(),
        list(_fetch_lines(fetched)),
        EXIT_OK if fetched.read_status is ReadStatus.TRANSIENT else EXIT_ERROR,
    )


def _run_locate(config: Settings, args: argparse.Namespace) -> Answer:
    from axiomation.sources.read.locate import LabelKind, locate
    from axiomation.sources.verify.locators import parse, why_not_a_label

    locator = parse(args.locator)
    if not locator.is_label:
        raise SourcesError(why_not_a_label(locator))
    settings = _settings(config, args)
    sources = _sources(config, args)
    found = locate(
        sources,
        args.key,
        locator.word,
        locator.number,
        kind=LabelKind(locator.kind.value),
        zotero_url=settings.zotero_url,
    )
    copy = sources.copy(args.key)
    return (
        {**fenced_payload(found.as_dict()), "copy": copy},
        [*_copy_lines(args.key, copy), *_located_lines(found)],
        _status_code(found),
    )


def _run_grep(config: Settings, args: argparse.Namespace) -> Answer:
    from axiomation.sources.read.grep import grep_work

    sources = _sources(config, args)
    first, *others = grep_work(sources, args.key, args.pattern)
    copy = sources.copy(args.key)
    return (
        {
            **fenced_payload(first.as_dict()),
            "copy": copy,
            "other_files": [fenced_payload(g.as_dict()) for g in others],
        },
        [*_copy_lines(args.key, copy), *_work_grep_lines([first, *others])],
        _status_code(first),
    )


def _copy_lines(key: str, copy: str) -> list[str]:
    """Which file of its work a key is, and the work's other files, where
    it has any: said before every read of the key."""
    return [f"{key}: {copy}"] if copy else []


def _work_grep_lines(found: Sequence[Any]) -> Iterator[str]:
    """A grep over a work's files, each file's result under its key."""
    for index, one in enumerate(found):
        if index:
            yield ""
        yield from _grep_lines(one)


def _run_toc(config: Settings, args: argparse.Namespace) -> Answer:
    from axiomation.sources.read.structure import contents

    settings = _settings(config, args)
    sources = _sources(config, args)
    found = contents(sources, args.key, zotero_url=settings.zotero_url)
    copy = sources.copy(args.key)
    return (
        {**fenced_payload(found.as_dict()), "copy": copy},
        [*_copy_lines(args.key, copy), *_contents_lines(found)],
        _status_code(found),
    )


# --- Renderers ---


def field_lines(rows: Iterable[tuple[str, str]]) -> Iterator[str]:
    """Label-value rows, the labels padded to one width."""
    rows = list(rows)
    width = max((len(label) for label, _ in rows), default=0)
    for label, value in rows:
        yield f"{label:<{width}}  {value}"


def emit(payload: Mapping[str, Any], lines: Iterable[str], *, as_json: bool) -> None:
    """One JSON document, or the text lines."""
    if as_json:
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return
    for line in lines:
        print(line)


def _code(results: Results) -> int:
    failed = results.read_status in NO_PAGE
    return EXIT_ERROR if failed else EXIT_OK


def _status_code(found: Any) -> int:
    """A miss within a work is an answer and exits 0; a read that gave no
    page exits 1, its status saying why: `mismatch`, its file is not its
    entry's work; `absent`, no source holds the work; or `infrastructure`,
    an outage."""
    failed = found.read_status in NO_PAGE
    return EXIT_ERROR if failed else EXIT_OK


def _progress(as_json: bool) -> Callable[[str], None]:
    """An ingest can run for an hour; under `-j` it says nothing until done."""
    if as_json:
        return lambda _: None
    return lambda line: print(line, file=sys.stderr, flush=True)


def _plan_lines(plan: Any) -> Iterator[str]:
    yield f"collection {plan.collection}" + ("" if plan.exists else " (would create)")
    if plan.refusal:
        yield f"REFUSED: {plan.refusal}"
    for key in plan.stale:
        yield f"prune      {key}"
    if plan.prune_refusal:
        yield f"REFUSED: {plan.prune_refusal}"
    for item in plan.pending:
        line = f"{item.reason.value:<10} {item.entry.key}: {item.entry.title[:60]}"
        if item.pages:
            line += f" (pp. {_page_list(item.pages)})"
        elif item.count:
            line += f" ({item.count} pages)"
        if item.refreshed:
            line += f"; its layer refreshed on pp. {_page_list(item.refreshed)}"
        if item.entry.label:
            line += f" ({item.entry.label})"
        yield line
        if item.entry.choice is not None and not item.entry.label:
            yield f"           {item.entry.choice.said()}"
    for entry in plan.metadata_only:
        yield f"{'meta':<10} {entry.key}: {entry.title[:60]}"
    yield from _reader_lines(plan.readers)
    for key, pages in plan.unwitnessed:
        yield (
            f"{'unwitnessed':<10} {key}: {pages} pages the declared OCR read that "
            "today's witness has not held; `--reread` reads afresh those an "
            "older witness left disputed, and holds the rest against Tesseract, "
            "reading again only those it finds a fault in"
        )
    for found in plan.untested:
        if found.images:
            yield (
                f"{'untested':<10} {found.bib_key}: pp. {_page_list(found.images)} "
                "carry images never tested for text; `--reread` reads their "
                "figures"
            )
        if found.plain:
            yield (
                f"{'figures':<10} {found.bib_key}: {len(found.plain)} pages "
                "without an image recorded as tested, nothing re-embedded"
            )
    for found in plan.layers:
        if found.outdated:
            again = (
                "its other pages need OCR, so only `--reread` under a declared "
                "OCR backend reads it again"
                if found.needs_backend
                else "`--reread` reads the work again"
            )
            yield (
                f"{'outdated':<10} {found.bib_key}: pp. {_page_list(found.outdated)} "
                f"read otherwise by today's layer reader; {again}"
            )
        if found.alike:
            yield (
                f"{'layer':<10} {found.bib_key}: {len(found.alike)} pages read "
                "alike by today's layer reader, its version recorded, nothing "
                "re-embedded"
            )
    for m in plan.moves:
        label = f" ({m.entry.label})" if m.entry.label else " (the cited edition)"
        yield f"{'move':<10} {m.source} -> {m.entry.key}{label}: nothing re-embedded"
    yield from _skipped_lines(plan.skipped)
    yield from _undecided_lines(plan.undecided)
    if plan.empty:
        yield "nothing to do"
    elif plan.refusal:
        # The refusal is the second line, and `--force` on a large bib makes
        # the plan hundreds long, so the reason has scrolled off by the time
        # the plan ends. Say it again at the bottom: a reader who pipes this
        # through `tail` would otherwise see a plan and conclude it will run.
        yield ""
        yield f"REFUSED: {plan.refusal} Nothing above would be written."


def _undecided_lines(undecided: Any) -> Iterator[str]:
    for choice in undecided:
        yield f"{'undecided':<10} {choice.said()}"


def _report_lines(report: Any, settings: ResolvedSources) -> Iterator[str]:
    from axiomation.sources.library.embed import cost

    if report.created:
        yield f"created {settings.collection}"
    rows = [
        ("pruned", str(len(report.pruned))),
        ("metadata updated", str(report.metadata_updated)),
        ("papers ingested", str(report.ingested)),
        ("points moved", str(report.moved)),
        ("chunks", str(report.chunks)),
        ("tokens", f"{report.tokens:,}"),
    ]
    spent = cost(settings.voyage_model, report.tokens)
    if spent is not None:
        rows.append(("cost", f"${spent:.4f} ({settings.voyage_model})"))
    yield from field_lines(rows)
    for key, message in report.failures:
        yield f"FAILED {key}: {message}"
    for key, why in report.held:
        yield f"HELD {key}: {why}"
    yield from _undecided_lines(report.plan.undecided)
    if report.settled:
        yield (
            f"settled the records of {report.settled} stored pages, "
            f"{report.relabelled} of them recorded as their layer (`none`)"
        )
    if report.figureless:
        yield (
            f"recorded {report.figureless} stored pages without an image as "
            "tested for figures"
        )
    if report.stamped:
        yield (
            f"recorded today's layer reader on {report.stamped} stored pages "
            "it reads alike"
        )
    for work in report.witnessed:
        if work.disputed:
            yield (
                f"DISPUTED {work.bib_key}: pp. {_page_list(work.disputed)}, "
                "findings no reading settled; read those pages as images"
            )
    yield from _skipped_lines(report.plan.skipped)
    if report.stopped:
        yield f"STOPPED: {report.stopped}"


def _reader_lines(readers: Iterable[Any]) -> Iterator[str]:
    """Each stored work whose records the run settles, and the pages it
    leaves for a declared backend to read again."""
    for found in readers:
        if found.relabelled:
            pages = _page_list([record.page_index for record in found.relabelled])
            yield (
                f"{'reader':<10} {found.bib_key}: pp. {pages} recorded as their "
                "layer (`none`), text and vectors kept"
            )
        if found.reread:
            yield (
                f"{'unread':<10} {found.bib_key}: pp. {_page_list(found.reread)} "
                "stored as their layer, which only OCR serves; `--reread` reads "
                "them again under a declared OCR backend"
            )


def _page_list(pages: list[int] | tuple[int, ...]) -> str:
    """Page numbers as runs: `1-4, 7`."""
    runs: list[list[int]] = []
    for page in sorted(pages):
        if runs and page == runs[-1][1] + 1:
            runs[-1][1] = page
        else:
            runs.append([page, page])
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in runs)


def _skipped_lines(skipped: Iterable[Any]) -> Iterator[str]:
    """Each work asked for by name and left as it is, and why: a person who
    replaced its file learns the bytes are the same, and a held one stays
    held."""
    for item in skipped:
        yield f"{'unchanged':<10} {item.key}: {item.why}"
        if item.held:
            yield f"{'':<10} HELD {item.held}"


def _result_lines(results: Results) -> Iterator[str]:
    if results.detail:
        yield f"{results.read_status}: {results.detail}"
    for page in results.pages:
        marker = "" if page.read_status is ReadStatus.OK else f" [{page.read_status}]"
        yield f"{page.bib_key} p{page.page_index}{marker}  {page.title[:60]}"
        # A preview is one line of the page, so it is fenced as one page: a
        # sentence that addresses the reader is as readable in a preview.
        yield _fenced_from(page, _preview(page.text))


def _page_lines(results: Results) -> Iterator[str]:
    if results.detail:
        yield f"{results.read_status}: {results.detail}"
    for page in results.pages:
        if page.read_status is ReadStatus.CONTENT:
            yield (
                f"{page.bib_key} page index {page.page_index}: no usable text; "
                f"read the page as an image (`sources image {page.bib_key} "
                f"{page.page_index}`)"
            )
        elif page.disputed:
            yield (
                f"{page.bib_key} page index {page.page_index}: {page.disputed} "
                "findings its OCR could not settle; read the page as an image"
            )
        # The stamp is the separator: it says what the page is, and it
        # closes.
        yield _fenced_from(page, page.text)


def _located_lines(found: Any) -> Iterator[str]:
    head = f"{found.bib_key}  {found.label}  [{found.read_status}]"
    if found.form:
        head += f"  form {found.form}"
    yield head
    if found.detail:
        yield found.detail
    for candidate in found.candidates:
        # The stamp below carries the page index, so this line carries only
        # what it does not: whether the match starts its line.
        yield "  line-initial" if candidate.line_initial else "  in text"
        yield fenced(
            candidate.line[:100],
            key=found.bib_key,
            page_index=candidate.page_index,
            read_status=found.read_status.value,
        )
    if found.dropped:
        yield (
            f"  ({found.dropped} more candidate(s) dropped; the ranking above has "
            f"already chosen)"
        )
    if found.quality:
        yield f"text  {found.quality.summary()}"


def _grep_lines(found: Any) -> Iterator[str]:
    head = f"{found.bib_key}  /{found.pattern}/  [{found.read_status}]"
    if found.matches:
        head += f"  {len(found.matches)} line(s) on {len(found.pages_matched)} page(s)"
    yield head
    if found.detail:
        yield found.detail
    for page_index, lines in _by_page(found.matches):
        yield fenced(
            "\n".join(line[:120] for line in lines),
            key=found.bib_key,
            page_index=page_index,
            read_status=found.read_status.value,
        )
    if found.dropped:
        yield f"  ({found.dropped} more matching line(s) dropped)"
    if found.quality:
        yield f"text  {found.quality.summary()}"


def _contents_lines(found: Any) -> Iterator[str]:
    yield f"{found.bib_key}  [{found.read_status}]  {found.kind}"
    if found.detail:
        yield found.detail
    for entry in found.outline:
        indent = "  " * (entry.level - 1)
        yield f"  {indent}{entry.title}  (page index {entry.page_index})"
    for page in found.pages:
        yield _fenced_from(page, page.text)


def _preview(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= TEXT_PREVIEW else f"{flat[:TEXT_PREVIEW]}..."


def _fenced_from(page: Any, text: str) -> str:
    """`text` read off `page`, stamped with where it came from."""
    return fenced(
        text,
        key=page.bib_key,
        page_index=page.page_index,
        read_status=page.read_status.value,
    )


def _by_page(matches: Iterable[Any]) -> Iterator[tuple[int, list[str]]]:
    """Matching lines grouped by the page they are on, in page order.

    One fence per page rather than one per line: `grep` returns up to forty
    lines and a fence around each of them would be three quarters frame.
    """
    for page_index, group in itertools.groupby(matches, key=lambda m: m.page_index):
        yield page_index, [match.line for match in group]


def _status_lines(status: Any, *, verbose: bool) -> Iterator[str]:
    settings = status.settings
    plain = status.contract is None
    tier = (
        f"plain: each cited work's PDF, read here ({status.library_off})"
        if plain
        else "library: the corpus in Qdrant"
    )
    yield from field_lines(
        [
            (
                "user file",
                status.user_file
                + ("" if status.user_file_found else "  (none: shell and defaults)"),
            ),
            *(("ignored", f"{f}: axiomation reads no .env") for f in status.ignored),
            ("tier", tier),
            ("qdrant", _from(settings.qdrant_url or "-", settings, "qdrant_url")),
            *(_plain_rows(settings) if plain else _library_rows(status)),
            (
                "contact email",
                f"{status.contact.address}  ({status.contact.source})"
                if status.contact.address
                else f"MISSING: {status.no_contact}",
            ),
            (
                "zotero",
                _from(
                    f"{'available' if status.zotero else 'unavailable (bib only)'} "
                    f"at {settings.zotero_url}",
                    settings,
                    "zotero_url",
                ),
            ),
        ]
    )
    yield ""
    if plain:
        yield from _works_lines(status, verbose=verbose)
        return
    yield from _library_lines(status, verbose=verbose)


def _plain_rows(settings: ResolvedSources) -> list[tuple[str, str]]:
    """The plain tier's settings: where a work's PDF is found."""
    return [
        ("bib", _from(str(settings.bib) if settings.bib else "-", settings, "bib")),
        (
            "pdf dir",
            _from(
                str(settings.pdf_dir) if settings.pdf_dir else "-", settings, "pdf_dir"
            ),
        ),
    ]


def _works_lines(status: Any, *, verbose: bool) -> Iterator[str]:
    """Each entry of the bibliography, by how the plain tier reads it."""
    if status.outage:
        yield f"the bibliography cannot be read: {status.outage}"
        return
    if status.unchecked:
        yield status.unchecked
        return
    works = status.works

    def keys(found: list[Any]) -> str:
        return ", ".join(w.key for w in found)

    read = [w for w in works if w.read_status is ReadStatus.OK and not w.unchecked]
    copies = [
        w for w in works if w.read_status is ReadStatus.TRANSIENT and not w.unchecked
    ]
    unchecked = [w for w in works if w.unchecked]
    held = [w for w in works if w.read_status is ReadStatus.MISMATCH]
    undecided = [w for w in works if w.undecided]
    absent = [
        w for w in works if w.read_status is ReadStatus.ABSENT and not w.undecided
    ]
    down = [w for w in works if w.read_status is ReadStatus.INFRASTRUCTURE]
    yield from field_lines(
        [
            ("entries", str(len(works))),
            ("read here", str(len(read)) + (f": {keys(read)}" if verbose else "")),
            ("fetched", str(len(copies)) + (f": {keys(copies)}" if copies else "")),
        ]
    )
    if unchecked:
        yield ""
        yield (
            f"{len(unchecked)} source(s) whose file is not checked against its "
            "entry: its first pages' text layer cannot show its title page. Read "
            "the title page as an image (`axiomation sources image DIR KEY 1`) "
            "and confirm it (`axiomation sources confirm`):"
        )
        yield f"  {keys(unchecked)}"
    if held:
        yield ""
        yield (
            f"{len(held)} source(s) whose file is not their entry's work, held "
            "unread until confirmed (`axiomation sources confirm`) or replaced:"
        )
        for w in held:
            yield f"  {w.key}: {w.detail}"
    if undecided:
        yield ""
        yield (
            f"{len(undecided)} source(s) whose entry lists several files and "
            "does not say which is the cited edition: a person names it "
            "(`axiomation sources confirm DIR KEY --file PATH --cited --why …`):"
        )
        yield f"  {keys(undecided)}"
    if absent:
        yield ""
        yield (
            f"{len(absent)} source(s) with no PDF here: name it in the entry's "
            "`file`, put it at `<pdf_dir>/<key>.pdf`, or fetch an open copy "
            "(`axiomation sources fetch DIR KEY`):"
        )
        yield f"  {keys(absent)}"
    if down:
        yield ""
        yield f"{len(down)} source(s) whose PDF cannot be read:"
        for w in down:
            yield f"  {w.key}: {w.detail}"


def _library_rows(status: Any) -> list[tuple[str, str]]:
    """The library's settings: its corpus, its model, its OCR and keys."""
    settings = status.settings
    return [
        ("collection", _from(settings.collection, settings, "collection")),
        (
            "model",
            _from(
                f"{settings.voyage_model} / {settings.voyage_dims} dims",
                settings,
                "voyage_model",
                "voyage_dims",
            ),
        ),
        ("bib", _from(str(settings.bib) if settings.bib else "-", settings, "bib")),
        ("ocr backend", _from(settings.ocr_backend.value, settings, "ocr_backend")),
        ("ocr url", _from(settings.ocr_url or "-", settings, "ocr_url")),
        *(
            [("ocr model", _from(settings.ocr_model or "-", settings, "ocr_model"))]
            if settings.ocr_backend is OcrBackend.CHAT
            else []
        ),
        (
            "voyage key",
            f"found  ({status.key_source})" if status.api_key else "MISSING",
        ),
        (
            "tesseract",
            f"MISSING: {status.tesseract_missing}"
            if status.tesseract_missing
            else "found",
        ),
    ]


def _library_lines(status: Any, *, verbose: bool) -> Iterator[str]:
    """What the library's corpus holds, and what its file checks found."""
    if not status.contract.built:
        yield (
            f"collection     not built yet: `axiomation sources ingest` builds "
            f"{status.contract.collection} at {status.settings.qdrant_url}"
        )
        return
    if not status.contract.ok:
        yield f"contract FAILED: {status.contract.problem}"
        return
    marker = status.contract.marker
    yield from field_lines(
        [
            ("contract", "ok"),
            ("points", str(status.contract.points)),
            ("papers", str(len(status.papers))),
            ("chunks", str(status.chunks)),
            (
                "read by",
                ", ".join(f"{source} {n}" for source, n in status.read_by.items())
                or "-",
            ),
            ("creator", marker.creator if marker else "not axiomation (read only)"),
            (
                "chunks are",
                status.contract.chunk_scheme.value
                if status.contract.chunk_scheme
                else "-"
                if not status.contract.points
                else "pages plus 500 chars of each neighbour (the neighbours' scheme)",
            ),
            *(
                [("context window", f"{marker.context_window} pages")]
                if marker and marker.context_window
                else []
            ),
        ]
    )
    if status.contract.legacy_fields:
        yield ""
        yield (
            "the sampled point lacks "
            + ", ".join(status.contract.legacy_fields)
            + "; change detection treats such points as unchanged (--force rebuilds)"
        )
    if status.unreadable:
        yield ""
        yield f"{len(status.unreadable)} source(s) with unreadable pages: " + ", ".join(
            status.unreadable
        )
    if status.mismatched:
        yield ""
        yield (
            f"{len(status.mismatched)} source(s) whose file is not their entry's "
            "work, held unread until confirmed (`axiomation sources confirm`) or "
            "replaced:"
        )
        other = dict(status.confirmed_other)
        for key, found in status.mismatched:
            yield f"  {key}: {found.why}"
            if key in other:
                yield f"  {other[key]}"
            if found.says:
                page = "title" if found.title_page else "first"
                yield f"  what its {page} page says:"
                yield fenced(
                    found.says,
                    key=key,
                    page_index=found.says_page,
                    read_status=ReadStatus.MISMATCH.value,
                )
    if status.several:
        yield ""
        yield f"{len(status.several)} source(s) whose entry holds several files:"
        for entry in status.several:
            yield f"  {entry.choice.said()}"
            for line in entry.lines:
                yield f"    {line}"
    if status.unchecked:
        yield ""
        yield status.unchecked
    if status.year_notes:
        yield ""
        yield (
            f"{len(status.year_notes)} source(s) whose first pages print another "
            "year than their entry's" + (":" if verbose else " (--verbose lists them)")
        )
        if verbose:
            for key, note in status.year_notes:
                yield f"  {key}: {note}"
    if verbose:
        yield ""
        for paper in status.papers:
            yield f"  {paper.bib_key:<24} {paper.chunks:>5}  {paper.title[:60]}"


def _from(value: str, settings: ResolvedSources, *names: str) -> str:
    """A value and where it came from: `(paper)`, `(derived)`, and so on.

    A row that shows two fields — the model and its dimensions — names both
    origins when they differ: a person's value `shell` or `user` (the user
    file).
    """
    origins = [_origin_label(settings, name) for name in names]
    if len(set(origins)) == 1:
        return f"{value}  ({origins[0]})"
    each = ", ".join(f"{n} {o}" for n, o in zip(names, origins, strict=True))
    return f"{value}  ({each})"


def _origin_label(settings: ResolvedSources, name: str) -> str:
    return settings.origin(name).value


def _resolution_lines(resolution: Any) -> Iterator[str]:
    rows = [
        ("bib key", resolution.bib_key),
        ("indexed", "yes" if resolution.indexed else "no"),
        (
            "file check",
            "does not match its entry: held, unread"
            if resolution.held
            else ("matches its entry" if resolution.checked else "not checked"),
        ),
        ("chunks", str(resolution.chunks)),
        ("title", resolution.title or "-"),
        ("file", resolution.file_path or "-"),
        ("file exists", "yes" if resolution.file_exists else "no"),
        ("zotero pdf", resolution.zotero_pdf or "-"),
        ("zotero link", resolution.zotero_link or "-"),
        ("doi", resolution.doi or "-"),
        ("resolution", resolution.resolution_status.value),
        ("item type", resolution.item_type or "-"),
        ("creators", "; ".join(resolution.creators) or "-"),
        ("issued", resolution.issued or "-"),
        ("venue", resolution.venue or "-"),
        ("publisher", resolution.publisher or "-"),
        ("canonical url", resolution.canonical_url or "-"),
        ("resolvers", ", ".join(resolution.resolvers) or "-"),
        ("fingerprint", resolution.fingerprint or "-"),
        (
            "citations",
            str(resolution.citation_count)
            if resolution.citation_count is not None
            else "-",
        ),
        (
            "retracted",
            "unknown"
            if resolution.retracted is None
            else str(resolution.retracted).lower(),
        ),
        ("version", resolution.version or "-"),
        ("open copies", str(len(resolution.candidates))),
        ("read status", resolution.read_status.value),
    ]
    yield from field_lines(rows)
    if resolution.detail:
        yield ""
        yield resolution.detail


def _fetch_lines(fetched: Any) -> Iterator[str]:
    yield from field_lines(
        [
            ("bib key", fetched.bib_key),
            ("resolver", fetched.resolver or "-"),
            ("url", fetched.url or "-"),
            ("file", fetched.file_path or "-"),
            ("pages", str(fetched.pages)),
            ("read status", fetched.read_status.value),
        ]
    )
    if fetched.detail:
        yield ""
        yield fetched.detail


def _confirm_arguments(parser: argparse.ArgumentParser) -> None:
    _key_argument(parser)
    parser.add_argument(
        "--why",
        required=True,
        help="why the file is the entry's work: a translation, a scan whose "
        "title page did not read, a chapter without its book's front matter",
    )
    parser.add_argument(
        "--file",
        type=Path,
        default=None,
        help="where the entry holds several files: the one confirmed, by its "
        "path; --why says what it is (a translation, a supplement)",
    )
    parser.add_argument(
        "--cited",
        action="store_true",
        help="with --file: the file is the edition the entry cites, which keeps "
        "the entry's key",
    )


def _run_confirm(config: Settings, args: argparse.Namespace) -> Answer:
    from axiomation.sources import confirm
    from axiomation.sources.files import choose
    from axiomation.sources.recheck import recheck_shared

    settings = _settings(config, args)
    if settings.bib is None:
        raise SourcesError("no bibliography. Set [paper].bib before confirming a file.")
    record = records(settings.bib).get(args.key)
    several = record is not None and choose(record, confirm.load(settings.bib)).several
    if args.file is not None or several:
        return _confirm_file(config, args, settings.bib, record)
    if args.cited:
        raise SourcesError("--cited names one of several files: give it with --file")
    sources = _sources(config, args)
    provider, failures = sources.holding(args.key)
    if provider is None and failures:
        raise SourcesError(
            f"cannot tell whether a source holds {args.key}: " + "; ".join(failures)
        )
    if provider is None:
        raise SourcesError(
            f"no source holds {args.key}: give its entry its PDF, or `fetch` an "
            "open copy, first"
        )
    from axiomation.sources.providers import UNCHECKED

    why = provider.held(args.key)
    before = f"it was held: {why}"
    if not why and UNCHECKED in sources.copy(args.key):
        # served unchecked: a person who read its title page says it is
        why = before = "it was unchecked: its text layer cannot show its title page"
    if not why:
        return (
            {"bib_key": args.key, "confirmed": False},
            [f"{args.key}'s file matches its entry: nothing to confirm"],
            EXIT_OK,
        )
    file_sha256 = _file_sha256(provider, args.key)
    written = confirm.write(settings.bib, args.key, file_sha256, args.why)
    payload: dict[str, Any] = {
        "bib_key": args.key,
        "confirmed": True,
        "file_sha256": file_sha256,
        "written": str(written),
    }
    lines = [
        f"confirmed {args.key}'s file ({file_sha256[:12]}) as its entry's work, in "
        f"{written}",
        before,
    ]
    payload["recheck"], rechecked = recheck_shared(config.root, args.store, [args.key])
    return (payload, [*lines, "", *rechecked], EXIT_OK)


def _confirm_file(
    config: Settings, args: argparse.Namespace, bib: Path, record: BibRecord | None
) -> Answer:
    """A person says what one of an entry's files is: the edition it cites
    (`--cited`), whatever the file check finds, or another file of it,
    labelled by `--why`."""
    from axiomation.sources import confirm
    from axiomation.sources.files import choose, file_sha256
    from axiomation.sources.recheck import recheck_shared

    files = record.files if record is not None else ()
    listed = "; ".join(str(path) for path in files) or "none on this machine"
    if args.file is None:
        raise SourcesError(
            f"{args.key} holds {len(files)} files: name the one confirmed with "
            f"--file PATH, and --cited where it is the edition the entry cites "
            f"({listed})"
        )
    given = args.file.expanduser().resolve()
    named = [path for path in files if path.resolve() == given]
    if not named:
        raise SourcesError(
            f"{args.file} is not a file of {args.key}'s entry: its files are {listed}"
        )
    sha = file_sha256(named[0])
    if (
        not args.cited
        and record is not None
        and not choose(record, confirm.load(bib)).several
    ):
        raise SourcesError(
            f"{args.key} holds one file, its cited edition: confirm it with "
            "--cited, or without --file"
        )
    written = confirm.write(bib, args.key, sha, args.why, cited=args.cited)
    payload: dict[str, Any] = {
        "bib_key": args.key,
        "confirmed": True,
        "cited": args.cited,
        "file_sha256": sha,
        "file": str(named[0]),
        "written": str(written),
    }
    what = (
        f"the edition {args.key} cites"
        if args.cited
        else f"another file of {args.key}: {args.why}"
    )
    lines = [f"confirmed {named[0]} ({sha[:12]}) as {what}, in {written}"]
    if record is not None:
        lines.append(choose(record, confirm.load(bib)).said())
        stored = _stored_sha256(config, args)
        if args.cited and stored is not None and stored != sha:
            lines.append(
                f"the corpus holds another file under {args.key}: `axiomation "
                f"sources ingest DIR --key {args.key}` reads this one under it"
            )
    payload["recheck"], rechecked = recheck_shared(config.root, args.store, [args.key])
    return (payload, [*lines, "", *rechecked], EXIT_OK)


def _stored_sha256(config: Settings, args: argparse.Namespace) -> str | None:
    """The hash of the file the corpus holds under the key; empty where it
    holds none, never recorded one, or cannot be read; None where no
    library is read (the plain tier reads the entry's file as it is)."""
    from axiomation.sources.match import FIRST_PAGES

    if not library_on(_settings(config, args)):
        return None
    from axiomation.sources.providers import Library

    try:
        provider, _ = _sources(config, args).holding(args.key)
        if not isinstance(provider, Library):
            return ""
        opening = provider.reader.openings(FIRST_PAGES, args.key).get(args.key)
    except Exception:  # noqa: BLE001 - an outage leaves the ingest's line said
        return ""
    return opening.file_sha256 if opening is not None else ""


def _file_sha256(provider: Any, key: str) -> str:
    """The SHA-256 of the file `provider` holds under `key`: the corpus's
    record of it, or the file's own (a PDF here, a fetched copy)."""
    from axiomation.sources.match import FIRST_PAGES
    from axiomation.sources.providers import Library

    if isinstance(provider, Library):
        opening = provider.reader.openings(FIRST_PAGES, key).get(key)
        if opening is not None and opening.file_sha256:
            return opening.file_sha256
        raise SourcesError(
            f"the corpus never recorded {key}'s file hash: "
            f"`axiomation sources ingest --key {key} --force` first"
        )
    sha = provider.file(key)
    if sha:
        return sha
    raise SourcesError(f"no file of {key} to confirm")


def _recheck_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("key", nargs="?", default=None, help="one bib key only")
    parser.add_argument(
        "--state", help="the kept state (default: the latest), a prefix of its id"
    )


def _run_recheck(config: Settings, args: argparse.Namespace) -> Answer:
    from axiomation.sources.recheck import recheck_lines

    payload, lines = recheck_lines(
        config.root, args.store, [args.key] if args.key else None, args.state
    )
    return (payload, lines, EXIT_OK)


def _verify_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("key", nargs="?", default=None, help="one bib key only")
    parser.add_argument(
        "--state", help="the kept state (default: the latest), a prefix of its id"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="check and report; file nothing"
    )


def _run_verify(config: Settings, args: argparse.Namespace) -> Answer:
    from axiomation.judgments import JudgmentError
    from axiomation.sources.verify.run import verify_lines

    try:
        payload, lines = verify_lines(
            config.root, args.store, args.key, args.state, args.dry_run
        )
    except JudgmentError as error:
        raise SourcesError(str(error)) from None
    return (payload, lines, EXIT_OK)


def _person_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--person",
        default=None,
        metavar="NAME",
        help="who files it, by name (default: an unnamed person)",
    )


def _person(args: argparse.Namespace) -> Any:
    from axiomation.judgments import Author
    from axiomation.records import PERSON

    if args.person is None:
        return Author()
    name = " ".join(args.person.split())
    if not name:
        raise SourcesError("--person names the person")
    return Author(PERSON, name)


def _publications_arguments(parser: argparse.ArgumentParser) -> None:
    from axiomation.records import Publication

    parser.add_argument(
        "key", nargs="*", help="these bib keys only (default: every work cited)"
    )
    parser.add_argument(
        "--fresh", action="store_true", help="bypass the resolver cache"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="look up and report; file nothing"
    )
    parser.add_argument(
        "--set",
        choices=[p.value for p in Publication],
        default=None,
        metavar="PUBLICATION",
        help="set one work's publication yourself, which overrides every "
        "lookup: " + ", ".join(p.value for p in Publication),
    )
    parser.add_argument("--why", default="", help="with --set: why")
    _person_argument(parser)


def _run_publications(config: Settings, args: argparse.Namespace) -> Answer:
    from axiomation.judgments import JudgmentError
    from axiomation.records import Publication
    from axiomation.sources.publication import look_up_all, pass_lines, set_by_person
    from axiomation.sources.recheck import kept_store

    if args.set is None:
        if args.why or args.person is not None:
            raise SourcesError("--why and --person go with --set")
        _made_store(config, args)
        try:
            run = look_up_all(config, args.store, args.key, args.fresh, args.dry_run)
        except JudgmentError as error:
            raise SourcesError(str(error)) from None
        payload, lines = pass_lines(run, args.dry_run)
        return (payload, lines, EXIT_OK)
    if len(args.key) != 1 or args.dry_run or args.fresh:
        raise SourcesError("--set sets one work's publication: give its key alone")
    store = kept_store(config.root, args.store)
    if store is None:
        raise SourcesError("the paper keeps no state: build it first")
    (key,) = args.key
    try:
        filed = set_by_person(
            store, key, Publication(args.set), args.why, _person(args)
        )
    except JudgmentError as error:
        raise SourcesError(str(error)) from None
    return (
        {"key": key, "publication": args.set, "filed": filed},
        [f"filed {filed}: {key} is {args.set}, by a person: {args.why}"],
        EXIT_OK,
    )


def _accept_arguments(parser: argparse.ArgumentParser) -> None:
    _key_argument(parser)
    parser.add_argument(
        "--why", required=True, help="why the paper may rest on the work as it is"
    )
    parser.add_argument(
        "--withdraw", action="store_true", help="take an earlier acceptance back"
    )
    _person_argument(parser)


def _run_accept(config: Settings, args: argparse.Namespace) -> Answer:
    from axiomation.judgments import JudgmentError
    from axiomation.publications import accept
    from axiomation.sources.recheck import kept_store

    store = kept_store(config.root, args.store)
    if store is None:
        raise SourcesError("the paper keeps no state: build it first")
    try:
        filed = accept(store, args.key, args.why, _person(args), args.withdraw)
    except JudgmentError as error:
        raise SourcesError(str(error)) from None
    record = filed.records.accepted[0].id
    said = "withdrawn" if args.withdraw else "accepted"
    return (
        {"key": args.key, "withdrawn": args.withdraw, "filed": record},
        [f"filed {record}: resting on {args.key} {said}: {args.why}"],
        EXIT_OK,
    )


def _adopt_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "source", help="the collection to copy from; it is never written to"
    )
    parser.add_argument(
        "--collection",
        default=None,
        help="the collection to create (default: this paper's, as "
        "`axiomation settings` shows it)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="say what would be copied and stop"
    )


def _run_adopt(config: Settings, args: argparse.Namespace) -> Answer:
    from axiomation.sources.library.adopt import adopt, plan_adopt
    from axiomation.sources.library.qdrant import connect

    settings = _settings(config, args)
    client = connect(settings)
    try:
        plan = plan_adopt(client, settings, args.source)
        if args.dry_run or plan.blocked:
            code = EXIT_ERROR if plan.blocked else EXIT_OK
            return (plan.as_dict(), list(_adopt_plan_lines(plan)), code)
        report = adopt(client, settings, plan, progress=_progress(args.json))
        return (report.as_dict(), list(_adopt_report_lines(report)), EXIT_OK)
    finally:
        client.close()


def _adopt_plan_lines(plan: Any) -> Iterator[str]:
    yield f"from       {plan.source}"
    yield f"into       {plan.target}" + ("" if not plan.create else " (would create)")
    if plan.refusal:
        yield f"REFUSED: {plan.refusal}"
        return
    yield f"points     {plan.points:,}"
    named = ", ".join(plan.sparse)
    yield f"vectors    {plan.vector_size}-dim dense" + (
        f", and sparse ({named})" if plan.sparse else ""
    )
    yield ""
    yield "The source is only ever read. Vectors are copied, not recomputed,"
    yield "so nothing is embedded and nothing is charged."


def _adopt_report_lines(report: Any) -> Iterator[str]:
    if report.created:
        yield f"created {report.target}"
    yield f"copied  {report.copied:,} point(s) from {report.source}"
    yield f"stamped {report.target} as axiomation"
    yield ""
    yield "Next: `axiomation sources ingest --prune --dry-run` says what the"
    yield "bibliography no longer names and what it has gained."


ACTIONS: tuple[Action, ...] = (
    Action("ingest", "build or update a collection", _ingest_arguments, _run_ingest),
    Action(
        "adopt",
        "copy another collection into one of this paper's own",
        _adopt_arguments,
        _run_adopt,
    ),
    Action("search", "hybrid search over the corpus", _search_arguments, _run_search),
    Action("page", "one page's text, or a range", _page_arguments, _run_page),
    Action(
        "locate",
        "the pages where a cited work prints a label, ranked",
        _locate_arguments,
        _run_locate,
    ),
    Action(
        "grep",
        "every line of a cited work matching a pattern, with its page",
        _grep_arguments,
        _run_grep,
    ),
    Action(
        "toc", "a cited work's outline, or its contents pages", _key_argument, _run_toc
    ),
    Action("image", "render a page as a PNG", _image_arguments, _run_image),
    Action(
        "status",
        "what the cited works are read from (the library or each work's PDF) "
        "and whether they read",
        _status_arguments,
        _run_status,
    ),
    Action(
        "resolve",
        "where one cited work can be read from",
        _resolve_arguments,
        _run_resolve,
    ),
    Action("fetch", "fetch and validate an open copy", _fetch_arguments, _run_fetch),
    Action(
        "confirm",
        "confirm that a file the check holds back is its entry's work",
        _confirm_arguments,
        _run_confirm,
    ),
    Action(
        "recheck",
        "check each cited work's files against what the judgments read: one "
        "that prints other content, or is gone, lapses them",
        _recheck_arguments,
        _run_recheck,
    ),
    Action(
        "verify",
        "check the paper's quotations and label locators against the cited "
        "pages, and report a misquote or a label printed nowhere",
        _verify_arguments,
        _run_verify,
    ),
    Action(
        "publications",
        "look up how each cited work is published (refereed, book, preprint, "
        "…) and file it, and report a preprint published since; --set files "
        "yours",
        _publications_arguments,
        _run_publications,
    ),
    Action(
        "accept",
        "accept that the paper rests on a cited work as it is: `check`'s "
        "cited-publications gate lists it apart",
        _accept_arguments,
        _run_accept,
    ),
)

_BY_NAME = {action.name: action for action in ACTIONS}
#: The actions only the library serves: its corpus built, copied, read.
LIBRARY_ACTIONS = frozenset({"ingest", "adopt"})


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Each action, after the paper's directory and before its own
    arguments: `axiomation sources ACTION DIR …`."""
    actions = parser.add_subparsers(dest="action", required=True, metavar="ACTION")
    for action in ACTIONS:
        subparser = actions.add_parser(action.name, help=action.help)
        subparser.add_argument(
            "directory",
            type=_directory,
            metavar="PAPER",
            help="the paper's directory, or the workspace's root (default: the "
            "paper the current directory is in, else its workspace's root)",
        )
        subparser.set_defaults(paper_first=True)
        action.arguments(subparser)
        subparser.add_argument("--store", type=Path, help=STORE_HELP)
        subparser.add_argument("--json", action="store_true", help="JSON, not lines")


def _directory(given: str) -> Path:
    """The paper's directory, where given: a word in its place that names
    none is said as such, never read as a paper without settings."""
    path = Path(given)
    if not path.is_dir():
        raise argparse.ArgumentTypeError(
            f"{given!r} is no directory: the paper's directory, where given, "
            "comes first (`axiomation sources ACTION [DIR] …`)"
        )
    return path


def main(argv: list[str]) -> int:
    """`axiomation sources ACTION DIR …`: the core's CLI hands the rest of
    the command line here."""
    parser = argparse.ArgumentParser(
        prog="axiomation sources",
        description="Read the cited works' pages, each marked with where it came "
        "from, and build "
        "the corpus.",
    )
    add_arguments(parser)
    try:
        found = with_paper(parser, argv, workspace=True)
    except PaperNotFound as error:
        print(f"axiomation sources {argv[0]}: {error}", file=sys.stderr)
        return EXIT_ERROR
    argv = found.argv
    if found.guessed is not None:
        print(
            f"axiomation sources {argv[0]}: note: no paper above here; "
            f"{found.guessed} taken as the paper",
            file=sys.stderr,
        )
    return run(parser.parse_args(argv))


def run(args: argparse.Namespace) -> int:
    """One action on the paper's settings; a failure printed, exit 1."""
    try:
        config = load_settings(args.directory, args.store)
        off = library_off(resolve(config)) if args.action in LIBRARY_ACTIONS else ""
        if off:
            raise SourcesError(
                f"`{args.action}` needs the library, and {off}: install the "
                "`[library]` extra (`pip install 'axiomation[library]'`) and "
                f"name a Qdrant server ({where_to_put(UserKey.QDRANT_URL)}); "
                "without the library each cited work is read from its PDF"
            )
        payload, lines, code = _BY_NAME[args.action].run(config, args)
    except (SourcesError, EvidenceError, OSError) as error:
        print(f"axiomation sources {args.action}: {error}", file=sys.stderr)
        return EXIT_ERROR
    if payload or lines:
        emit(payload, lines, as_json=args.json)
    return code
