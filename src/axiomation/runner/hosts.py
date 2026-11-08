"""The host interface: a prompt goes in, an answer comes out.

One adapter per host behind it: Claude Code's
(`axiomation.runner.claude_code`), OpenCode's (`axiomation.runner.opencode`)
and the tests' fakes. A question with a workspace is an agent session on
its files, which both adapters run. The rates
here, the models' prices in `prices.toml`, serve the runner's
estimate only; the cost recorded is always the host's own.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tomllib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any, Protocol

from axiomation.questions import (
    QueueSession,
    SessionTools,
    ToolServer,
    Workspace,
)
from axiomation.records import SESSIONS_DIRECTORY


@dataclass(frozen=True, slots=True)
class HostAnswer:
    """What a host gave for one prompt. `text` is the model's answer;
    `tokens` the attempt's counts over every model it ran, and
    `tokens_by_model` the same per model, where the host names them;
    `tool_uses` each tool the transcript shows (its name and input);
    `transcript` the host's own log, kept beside the store; `error` what
    the host reported going wrong, empty when nothing did."""

    text: str
    model: str
    cost: float = 0.0
    tokens: dict[str, int] = field(default_factory=dict)
    tokens_by_model: dict[str, dict[str, int]] = field(default_factory=dict)
    seconds: float = 0.0
    tool_uses: tuple[dict[str, Any], ...] = ()
    transcript: str = ""
    exit_code: int = 0
    error: str = ""

    @property
    def failed(self) -> bool:
        """The host, not the model, failed: an error, a non-zero exit or a
        timeout."""
        return bool(self.error) or self.exit_code != 0


class Host(Protocol):
    name: str
    model: str
    #: The effort level the host runs the model at, where it takes one.
    effort: str | None

    def ask(
        self,
        prompt: str,
        timeout: float,
        workspace: Workspace | None = None,
        spend_max: float | None = None,
    ) -> HostAnswer:
        """The answer to `prompt`; with a workspace, the edited file's text
        when the session ends. `spend_max`, in dollars, ends the attempt
        once it has spent that much (`HostOverBudget`), where the host can;
        None: no limit but the timeout."""
        ...

    def ask_queue(
        self, session: QueueSession, timeout: float, spend_max: float | None = None
    ) -> HostAnswer:
        """A queue session: it answers its tasks through its tool server,
        which spools them; the answer is its last message, cost and tokens.
        `spend_max` as `ask` takes it. A host that serves none refuses
        (`HostSetupError`)."""
        ...

    def tool_server_error(self, server: ToolServer) -> str | None:
        """Why a queue session's tool server would not start as this host
        starts it, else None: asked of every step before a pair of
        sessions runs, so none is paid for on a server that fails."""
        ...

    def abort(self) -> None:
        """End every attempt in flight at once (a breach)."""
        ...


class PromptTooLong(ValueError):
    """A prompt over a host's limit."""


class HostBreach(Exception):
    """A host that let the model touch a path outside its directory. The
    run stops at once."""

    def __init__(self, message: str, answer: HostAnswer) -> None:
        super().__init__(message)
        self.answer = answer


class HostOutOfCredit(Exception):
    """A host that refused for billing: out of credit, over a usage limit.
    A clean stop, never a failed answer."""

    def __init__(self, message: str, answer: HostAnswer) -> None:
        super().__init__(message)
        self.answer = answer


class HostOverBudget(Exception):
    """A host that ended an attempt at its `spend_max`: what the run had
    left of its budget. The run stops, cleanly and resumably; what the
    attempt wrote is recorded, never re-asked as a failed answer."""

    def __init__(self, message: str, answer: HostAnswer) -> None:
        super().__init__(message)
        self.answer = answer


class HostUncounted(Exception):
    """A host that could not say what an attempt cost. The budget cannot
    be kept without it, so the run stops, cleanly and resumably."""

    def __init__(self, message: str, answer: HostAnswer) -> None:
        super().__init__(message)
        self.answer = answer


@dataclass(frozen=True, slots=True)
class Rates:
    """Dollars per million tokens. Reasoning is counted as output. A cache
    write is priced by how long it is kept: an hour at `cache_write_1h`,
    as Claude Code writes a session's text, five minutes at
    `cache_write_5m`, as a subagent's fork writes it; a rate not given is
    the other's, then `input`."""

    input: float
    output: float
    cache_read: float = 0.0
    cache_write_1h: float | None = None
    cache_write_5m: float | None = None

    @property
    def written_1h(self) -> float:
        return next(
            r
            for r in (self.cache_write_1h, self.cache_write_5m, self.input)
            if r is not None
        )

    @property
    def written_5m(self) -> float:
        return next(
            r
            for r in (self.cache_write_5m, self.cache_write_1h, self.input)
            if r is not None
        )

    def priced(self, tokens: Mapping[str, int]) -> float:
        """What a record's `tokens` cost at these rates, in dollars: its
        cache writes kept an hour (`cache_write_1h`) at that rate, the rest
        at the 5-minute rate, or all at the hour's where the record does
        not split them."""
        written = tokens.get("cache_write", 0)
        hour = tokens.get("cache_write_1h", written)
        return (
            tokens.get("input", 0) * self.input
            + tokens.get("output", 0) * self.output
            + tokens.get("cache_read", 0) * self.cache_read
            + hour * self.written_1h
            + (written - hour) * self.written_5m
        ) / 1_000_000


DEFAULT_MODEL = "opencode/deepseek-v4.1-flash"

#: The known prices, shipped beside the package.
PRICES = "prices.toml"


#: A model's table in `prices.toml`: the rates it must give, and those it may.
_NEEDED_RATES = ("input", "output")
_RATES = (*_NEEDED_RATES, "cache_read", "cache_write_1h", "cache_write_5m")


@cache
def known_rates() -> dict[str, Rates]:
    """Each priced model's rates, from `prices.toml`."""
    text = resources.files("axiomation").joinpath(PRICES).read_text(encoding="utf-8")
    found = {}
    for model, table in tomllib.loads(text).items():
        valid = (
            isinstance(table, dict)
            and set(_NEEDED_RATES) <= set(table) <= set(_RATES)
            and all(
                isinstance(n, int | float) and not isinstance(n, bool) and n >= 0
                for n in table.values()
            )
        )
        if not valid:
            raise ValueError(
                f"{PRICES}: {model}: not a price: {table!r}; it takes "
                f"{', '.join(_NEEDED_RATES)} and may take {', '.join(_RATES[2:])}"
            )
        found[model] = Rates(**{k: float(n) for k, n in table.items()})
    return found


def rates_for(model: str) -> Rates | None:
    """The rates `prices.toml` knows for `model`, whatever its effort;
    None for a model it does not price."""
    return known_rates().get(model)


#: The estimate's assumptions: characters a token, output tokens an
#: attempt.
CHARS_PER_TOKEN = 3
OUTPUT_TOKENS = 6000


def estimate(
    prompt: str,
    rates: Rates,
    workspace: Workspace | None = None,
    harness_tokens: int = 0,
) -> float:
    """What one attempt at `prompt` is expected to cost, in dollars: the
    harness's own prompt (`harness_tokens`) written to the cache once, the
    workspace read once, but for the files it searches in place. A session
    reads its context again every turn, so a long one costs several times
    this and a short one about this."""
    shown = len(prompt)
    if workspace is not None:
        shown += len(workspace.instructions)
        shown += sum(
            len(text)
            for name, text in workspace.files.items()
            if name not in workspace.searched
        )
    tokens_in = shown / CHARS_PER_TOKEN
    written = rates.written_1h
    return (
        tokens_in * rates.input
        + harness_tokens * written
        + OUTPUT_TOKENS * rates.output
    ) / 1_000_000


#: How long a review session's tool may take: a cited page drawn as a
#: picture, a search of the corpus, a found work fetched (the gate's 80 s).
REVIEW_CALL_SECONDS = 120.0


@contextlib.contextmanager
def written_tools(
    served: SessionTools | None, spec: str, session: Path, name: str
) -> Iterator[ToolServer | None]:
    """A review session's tool server (`review_tools`): its settings, the
    gate's `spec`, the directory it runs in and its reads, written under
    its store's `sessions/` as `name`, where the session cannot write;
    removed when the session ends. None without tools."""
    if served is None:
        yield None
        return
    from axiomation.queue import SERVER_NAME
    from axiomation.review_tools import REVIEW

    reads = served.reads
    path = Path(served.store).resolve() / SESSIONS_DIRECTORY / f"{name}.tools.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                REVIEW: {
                    "spec": spec,
                    "work": str(session.resolve()),
                    "reads": None
                    if reads is None
                    else {
                        "paper": reads.paper,
                        "store": reads.store,
                        "works": list(reads.works),
                    },
                    "fetches": served.fetches,
                }
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    env = {k: v for k, v in os.environ.items() if k.startswith("AXIOMATION_")}
    # the person's user file is found from the home or `XDG_CONFIG_HOME`
    for variable in ("HOME", "PATH", "XDG_CONFIG_HOME"):
        if variable in os.environ:
            env[variable] = os.environ[variable]
    try:
        yield ToolServer(
            SERVER_NAME,
            (
                sys.executable,
                "-I",
                "-m",
                "axiomation.queue.server",
                str(path.resolve()),
            ),
            env,
            REVIEW_CALL_SECONDS,
        )
    finally:
        path.unlink(missing_ok=True)
