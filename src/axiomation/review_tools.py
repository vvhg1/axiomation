"""A review session's tools, served by block (docs/architecture.md § 7):
`check`, its gate's check on its answer; where it reads cited works,
their reads (`cited_search`, `cited_page`, …, the queue sessions' own);
and where it looks for prior work, `fetch`, a found work's open copy
fetched into the paper's cache and read then as a cited work is.

The host writes the tools' settings under the store's `sessions/` while the
session runs (`runner.hosts.written_tools`), where the session cannot
write, and starts `queue.server` on them outside the session's sandbox:
the spec the check reads is the one its host wrote, and the answer it
reads is the session's `review.md`, read as a file, never through a link.
Nothing is run or written; `fetch` alone reaches the network, through the
paper's sources, by an identifier (`arxiv:ID`, `doi:DOI`).

The core only: the cited works' reads come from `axiomation.sources`,
reached inside `_cited_reads` alone.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from axiomation.questions import Reads, gates
from axiomation.questions import review_file as rf
from axiomation.queue import Image, Read, ReadError, Tool

#: The settings' key that names a review session's tools.
REVIEW = "review"
#: The most works a session fetches: each fetch may read a work's pages by
#: OCR, paid outside the session's own budget.
FETCHES_MAX = 12

CHECK = Tool(
    "check",
    f"Check `{rf.REVIEW_FILE}` as it stands: the lines that do not read, each "
    "with why, and what is not yet answered; or that it reads.",
    {"type": "object", "properties": {}},
)
FETCH = Tool(
    "fetch",
    "Fetch an open copy of a work found outside the paper's bibliography, by "
    "its identifier, and read it then with the `cited_*` tools by that key: "
    "its pages, or why it could not be fetched. Each work is tried once.",
    {
        "type": "object",
        "required": ["work"],
        "properties": {
            "work": {
                "type": "string",
                "description": "`arxiv:ID` or `doi:DOI`",
            }
        },
    },
)


class ReviewTools:
    """The tools a review session by block is served: `check` on its
    answer in `work` against `spec`, with `reads` its cited works', and
    with `fetches` a found work's fetch, its pages then read by the same
    tools."""

    def __init__(
        self,
        spec: Mapping[str, Any],
        work: Path,
        reads: Reads | None = None,
        fetches: bool = False,
    ) -> None:
        # a session by block runs nothing: its check never runs a script
        if spec.get("runs") or spec.get("code"):
            raise ValueError("a review session by block's check runs no script")
        self.spec = spec
        self.work = work
        self.cited = _cited_reads(reads) if reads is not None else None
        self.fetches = fetches and reads is not None
        #: each work fetch was asked, and what it said
        self.tried: dict[str, str] = {}

    @classmethod
    def from_settings(cls, given: Mapping[str, Any]) -> ReviewTools:
        reads = given.get("reads")
        return cls(
            json.loads(given["spec"]),
            Path(given["work"]),
            Reads(reads["paper"], reads["store"], tuple(reads["works"]))
            if reads
            else None,
            bool(given.get("fetches")),
        )

    @property
    def reads(self) -> tuple[Read, ...]:
        return () if self.cited is None else self.cited.tools

    @property
    def tools(self) -> tuple[Tool, ...]:
        fetch = (FETCH,) if self.fetches else ()
        return (CHECK, *(r.tool for r in self.reads), *fetch)

    def call(self, name: str, args: Mapping[str, Any]) -> tuple[str | Image, bool]:
        """A call's answer, and whether it was refused."""
        if name == CHECK.name:
            return self.check()
        if name == FETCH.name and self.fetches:
            return self.fetch(str(args.get("work") or ""))
        read = next((r for r in self.reads if r.tool.name == name), None)
        if read is None:
            return f"no tool {name!r}", True
        try:
            return read.answer(None, args), False  # type: ignore[arg-type]
        except ReadError as error:
            return str(error), True

    def fetch(self, given: str) -> tuple[str, bool]:
        """A found work fetched through the paper's sources within the
        gate's time, and made one the `cited_*` tools read; each tried
        once."""
        key = rf.found_key(" ".join(given.split()))
        if not rf.FOUND_KEY.match(key):
            return f"{given!r}: a work is fetched by `arxiv:ID` or `doi:DOI`", True
        if key in self.tried:
            return self.tried[key], False
        if len(self.tried) >= FETCHES_MAX:
            return (
                f"{key} not fetched: a session fetches at most {FETCHES_MAX} works; "
                "name it without pages, as a pointer",
                False,
            )
        assert self.cited is not None
        works = self.cited.reads.works
        if key in works:
            return f"{key} is read already: read it with the `cited_*` tools", False
        from axiomation import evidence

        paper, store = self.cited.reads.paper, self.cited.reads.store
        try:
            sources = evidence.sources_for(paper, store)
        except evidence.EvidenceError as error:
            self.tried[key] = f"could not fetch {key}: {error}"
            return self.tried[key], False
        read = rf.fetched_within(sources, key, rf.GATE_FETCH_SECONDS)
        if read is None:
            said = f"could not fetch {key} within {rf.GATE_FETCH_SECONDS} s"
        elif not read.pages:
            said = f"could not fetch {key}: {read.detail}"
        else:
            self.cited.reads = dataclasses.replace(
                self.cited.reads, works=(*works, key)
            )
            indexes = [p.page_index for p in read.pages]
            said = (
                f"fetched {key}: {len(indexes)} pages, index {min(indexes)} to "
                f"{max(indexes)}; find its place with `cited_grep` and read it "
                "with `cited_page`, by its key (a fetched work is not in "
                "`cited_search`)"
            )
        self.tried[key] = said
        return said, False

    def check(self) -> tuple[str, bool]:
        """The gate's check on `review.md`, every problem told."""
        try:
            text = gates.read_regular(self.work / rf.REVIEW_FILE)
        except gates.NotRegularFile as error:
            return f"{rf.REVIEW_FILE}: {error}", True
        result = rf.checked(self.spec, text)
        if not result.problems:
            return f"{rf.REVIEW_FILE} reads: every line, every subject", False
        listed = "\n".join(f"- {p}" for p in result.problems)
        return (
            f"{rf.REVIEW_FILE} does not read yet ({len(result.problems)}):\n{listed}",
            False,
        )


def _cited_reads(reads: Reads) -> Any:
    """The cited works' reads as tools (`sources.cited.QueueReads`), from
    `axiomation.sources`: the paper's
    corpus and the copies fetched, read without fetching."""
    from axiomation.sources.cited import QueueReads
    from axiomation.sources.providers import sources_for

    sources = sources_for(Path(reads.paper), Path(reads.store), fetch=False)
    return QueueReads(sources, reads)
