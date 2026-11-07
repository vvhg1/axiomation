"""Profiles: who answers each stage of the cut review, on which model,
under which prompt, and the recipe's settings.

A profile is a table of `profiles.toml`, shipped with the package, or of a
file given with `--profiles`. It gives the recipe's settings, then a table
for each stage (`boundaries`, `boundaries_judge`, `roles`, `groups`,
`edges`, all five required, and `decide`, the deciding step's,
which a file may leave out to have the boundaries' agent and the shipped
`cut-review-decide` template), and a review session's (`review.<session>`),
which a profile without one cannot run, and the support search's steps
(`support_search`: its worker and its settling step, each an agent with its
share of the budget, and the context its sessions are compacted at),
which a profile without one takes as one step on the edges' agent:
the harness, the model as that harness takes it, its
effort (Claude Code's level, or OpenCode's variant of the model), and the
prompt template. A model's price is not a profile's: it is the model's, in
`prices.toml` (`runner.hosts.rates_for`). Nothing about confinement: that
is each host's own, so a profile cannot weaken it, and the
budget is the run's (`--budget`). A profile is checked whole when it is read, before any
spend, a model by its form; whether OpenCode serves a well-formed one is
met at its first attempt (`opencode models` cannot tell).
"""

from __future__ import annotations

import re
import string
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, replace
from dataclasses import field as default_field
from enum import StrEnum
from importlib import resources
from pathlib import Path
from typing import Any

from axiomation.driver import ReviewSettings, Templates
from axiomation.questions import Stage, Template, load_template
from axiomation.questions.chunks import DEFAULT_CHUNK, DEFAULT_OVERLAP
from axiomation.questions.review_file import COLUMNS, Session, session_named
from axiomation.runner import claude_code
from axiomation.runner.claude_code import EFFORTS
from axiomation.runner.hosts import DEFAULT_MODEL, Rates

#: The shipped profiles, beside this module.
SHIPPED = "profiles.toml"


class Harness(StrEnum):
    """The harnesses a stage can run on."""

    CLAUDE_CODE = "claude-code"
    OPENCODE = "opencode"


#: Each harness's model when none is named.
DEFAULT_MODELS = {
    Harness.CLAUDE_CODE: claude_code.DEFAULT_MODEL,
    Harness.OPENCODE: DEFAULT_MODEL,
}

#: The models each harness takes, by form: OpenCode's `<provider>/<id>`,
#: as `opencode/…` (Zen) and `opencode-go/…` (Go); for Claude Code, any
#: alias or id it takes (`opus`, `opus[1m]`, `claude-opus-5-5`, a Bedrock
#: id), which Claude Code alone knows, so only OpenCode's form is refused.
MODEL_FORMS = {
    Harness.CLAUDE_CODE: re.compile(r"^[^\s/]+$"),
    Harness.OPENCODE: re.compile(r"^[A-Za-z0-9_.-]+/\S+$"),
}
MODEL_FORM_NAMES = {
    Harness.CLAUDE_CODE: "an alias or a model id, without a '/'",
    Harness.OPENCODE: "<provider>/<id>",
}


class ProfileError(Exception):
    """A profile that cannot be used, or a profiles file that cannot be
    read."""


#: An OpenCode variant's form: its name, which differs by model
#: (DeepSeek's: `default`, `high`, `max`) and OpenCode cannot list.
VARIANT = re.compile(r"^[A-Za-z0-9_.-]+$")


def model_problem(harness: Harness, model: str) -> str | None:
    """Why `harness` cannot take `model`, else None."""
    if "#" in model:
        return f"the effort is given apart (effort), not in the model {model!r}"
    if MODEL_FORMS[harness].match(model):
        return None
    return f"{harness.value} takes {MODEL_FORM_NAMES[harness]}, not {model!r}"


def effort_problem(harness: Harness, effort: str) -> str | None:
    """Why `harness` cannot take `effort`, else None: Claude Code's levels,
    or any variant name for OpenCode, whose variants it alone knows."""
    if harness is Harness.CLAUDE_CODE and effort not in EFFORTS:
        return f"claude-code's effort takes {', '.join(EFFORTS)}, not {effort!r}"
    if harness is Harness.OPENCODE and not VARIANT.match(effort):
        return f"opencode's effort is a variant's name, not {effort!r}"
    return None


@dataclass(frozen=True, slots=True)
class Agent:
    """Who answers a stage's questions: a harness and its model; its
    effort, Claude Code's level or OpenCode's variant (None: on Claude
    Code the user's own setting for the model, on OpenCode its default);
    the rates its attempts are estimated at, given for a run (`--rates`;
    None: the model's price in `prices.toml`); and on Claude Code the
    tools its sessions have."""

    harness: Harness
    model: str
    effort: str | None = None
    rates: Rates | None = None
    #: The tools its sessions have, on Claude Code (`--tools`); None:
    #: Claude Code's own set but the web.
    tools: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class QueueStep:
    """A step of a queue run: its agent, and its share of the run's budget
    against the other step's."""

    agent: Agent
    budget_share: float = 1.0


@dataclass(frozen=True, slots=True)
class Reviewer:
    """Who answers a review session: its agent and its template,
    and the model its forager runs on (the contribution and relatedwork
    sessions). The
    citationcheck and fieldresults sessions are queues
    (`sources.citation_queue`, `sources.fieldresults_queue`): its agent is
    the reader's step, with its share of the run's budget, after the
    finder's where one is seated (the fieldresults session's always); the
    fieldresults session's agent also answers its inventory. A seat not
    `asked_by_default` is asked only when `--session` names it."""

    agent: Agent
    template: Template
    budget_share: float = 1.0
    finder: QueueStep | None = None
    forager_model: str | None = None
    asked_by_default: bool = True


@dataclass(frozen=True, slots=True)
class SupportSearch:
    """The support search's steps (`support_search`): the worker, and the
    settling step where one is named (None: the worker's answers settle
    alone); the tokens of context its sessions are compacted at (None:
    never)."""

    worker: QueueStep
    settler: QueueStep | None = None
    autocompact_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class Profile:
    """A recipe: an agent per stage and the templates they are asked
    under, with the review's settings (`ReviewSettings`). A profile read
    from a file has every stage's agent; the flags alone (`name` None) give
    the boundaries' only, which every other stage then has."""

    name: str | None
    agents: Mapping[Stage, Agent]
    templates: Templates
    chunk_characters: int | None = DEFAULT_CHUNK
    context_blocks: int = DEFAULT_OVERLAP
    boundary_passes: int = 1
    judge_boundaries: bool = False
    show_outline: bool = True
    role_passes: int = 1
    group_passes: int = 1
    edge_passes: int = 1
    cut_rounds: int = 0
    decide_passes: int = 0
    show_rule_flags_in_edge_review: bool = False
    followup_sessions_max: int = 1
    reverse_prefilter: bool = True
    fresh_edges_above_reopened_fraction: float | None = None
    per_stage_update_above_recut_blocks: int | None = None
    moved_delta_lines_max: int = 60
    reread_marks_after_update: bool = True
    search_support_after_edges: bool = True
    ask_circles_after_edges: bool = True
    session_timeout_seconds: float = 900.0
    description: str = ""
    #: each review session's reviewer, by session
    reviewers: Mapping[Session, Reviewer] = default_field(default_factory=dict)
    #: the support search's steps; None: one step, the edges' agent
    support_search: SupportSearch | None = None

    def agent(self, stage: Stage) -> Agent:
        return self.agents.get(stage, self.agents[Stage.BOUNDARIES])

    def search_steps(self) -> SupportSearch:
        """The support search's steps: the profile's, else one step on the
        edges' agent, compacted at `SEARCH_AUTOCOMPACT_TOKENS`."""
        if self.support_search is not None:
            return self.support_search
        return SupportSearch(
            QueueStep(self.agent(Stage.EDGES)),
            autocompact_tokens=SEARCH_AUTOCOMPACT_TOKENS,
        )


#: A profile's settings, by the names `Profile` gives them, and the types
#: they take; `chunk_characters` also takes "paper".
_SETTINGS: dict[str, type | tuple[type, ...]] = {
    "description": str,
    "chunk_characters": (int, str),
    "context_blocks": int,
    "boundary_passes": int,
    "judge_boundaries": bool,
    "show_outline": bool,
    "role_passes": int,
    "group_passes": int,
    "edge_passes": int,
    "cut_rounds": int,
    "decide_passes": int,
    "show_rule_flags_in_edge_review": bool,
    "followup_sessions_max": int,
    "reverse_prefilter": bool,
    "fresh_edges_above_reopened_fraction": (int, float),
    "per_stage_update_above_recut_blocks": int,
    "moved_delta_lines_max": int,
    "reread_marks_after_update": bool,
    "search_support_after_edges": bool,
    "ask_circles_after_edges": bool,
    "session_timeout_seconds": (int, float),
}
#: A stage's table's keys; the roles' and the edges' also take
#: `review_prompt_template`, their later passes' template, and the edges
#: `reask_prompt_template`, their re-ask's after a round of cut corrections
#:, and `update_prompt_template`, an update's session:
#: each key's field in `Templates`, by stage, in `_EXTRA`.
_AGENT_KEYS = {"harness", "model", "effort", "prompt_template", "tools"}
#: The stages whose sessions have the reading tools where their table names
#: none (`tools`): the edges stage's (the writer, an update's session, the
#: search, the reread, the circles) and every review session's. A tool a
#: job does not use is a distractor, and the Task tool lets a session fork
#: itself, each fork reading the whole paper again.
READING_STAGES = frozenset({Stage.EDGES})
#: The models of a review session's subagents on Claude Code, and the
#: sessions that have each.
_FORAGER = "forager_model"
_HELPER_SESSIONS = {_FORAGER: (Session.CONTRIBUTION, Session.RELATEDWORK)}
#: The subagents a session's brief always asks for: the contribution and
#: relatedwork readers' templates have no form without them.
_NEEDED_HELPERS = {
    Session.CONTRIBUTION: (_FORAGER,),
    Session.RELATEDWORK: (_FORAGER,),
}
_EXTRA = {
    Stage.ROLES: {"review_prompt_template": "roles_review"},
    Stage.EDGES: {
        "review_prompt_template": "edges_review",
        "reask_prompt_template": "edges_reask",
        "update_prompt_template": "edges_update",
    },
}
#: Each stage's table, and its template's field in `Templates`.
_TABLES = {
    "boundaries": (Stage.BOUNDARIES, "boundaries"),
    "boundaries_judge": (Stage.JUDGE, "judge"),
    "roles": (Stage.ROLES, "roles"),
    "groups": (Stage.GROUPS, "groups"),
    "edges": (Stage.EDGES, "edges"),
    "decide": (Stage.DECIDE, "decide"),
    "vocabulary": (Stage.VOCABULARY, "vocabulary"),
    "vocabulary_check": (Stage.VOCABULARY_CHECK, "vocabulary_check"),
}
#: The tables a profile may leave out: the deciding step and the vocabulary
#: map's two, which then have the boundaries' agent.
_OPTIONAL = {"decide", "vocabulary", "vocabulary_check"}
#: The table of the review sessions' agents, one table per session.
_REVIEW = "review"
#: A review seat's key: whether `review` and `sync` ask it with no
#: `--session` (default true).
_ASKED_BY_DEFAULT = "asked_by_default"
#: The support search's table, and its steps' tables, each an agent and its
#: `budget_share`.
_SEARCH = "support_search"
_SEARCH_STEPS = ("worker", "settler")
_SEARCH_KEYS = {"autocompact_tokens", *_SEARCH_STEPS}
_STEP_KEYS = {"harness", "model", "effort", "budget_share"}
#: The review sessions asked as a queue (`sources.citation_queue`,
#: `methods_queue`, `sources.fieldresults_queue`), and what each seat names
#: beside its agent: its own share of the run's budget and, for the
#: citationcheck and fieldresults sessions, the finder's step before its reader.
_FINDER = "finder"
_QUEUE_KEYS = {
    Session.CITATIONCHECK: {"budget_share", _FINDER},
    Session.METHODS: {"budget_share"},
    Session.FIELDRESULTS: {"budget_share", _FINDER},
}
#: The queue sessions whose finder is a step they cannot do without: the
#: fieldresults session's reader is served what its finder found.
_FINDER_NEEDED = (Session.FIELDRESULTS,)
#: The context a search session is compacted at where the profile names
#: none: a long queue session reads its whole context again every turn.
SEARCH_AUTOCOMPACT_TOKENS = 100_000


def read_profiles(path: Path | None = None) -> dict[str, Profile]:
    """The shipped profiles, and those of the file at `path`, which add to
    them or replace one of the same name. Each is checked whole."""
    text = resources.files("axiomation").joinpath(SHIPPED).read_text(encoding="utf-8")
    profiles = _profiles(text, SHIPPED, None)
    if path is not None:
        try:
            given = path.read_text(encoding="utf-8")
        except OSError as error:
            raise ProfileError(f"cannot read {path}: {error}") from None
        profiles.update(_profiles(given, str(path), path.parent))
    return profiles


def _profiles(text: str, source: str, base: Path | None) -> dict[str, Profile]:
    try:
        tables = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ProfileError(f"{source}: {error}") from None
    found = {}
    for name, table in tables.items():
        where = f"{source}: profile {name!r}"
        if not isinstance(table, dict):
            raise ProfileError(f"{where}: expected a table")
        found[name] = _profile(name, table, where, base)
    return found


def _profile(
    name: str, table: dict[str, Any], where: str, base: Path | None
) -> Profile:
    unknown = sorted(set(table) - set(_SETTINGS) - set(_TABLES) - {_REVIEW, _SEARCH})
    if unknown:
        raise ProfileError(f"{where}: unknown {', '.join(unknown)}")
    missing = [key for key in _TABLES if key not in table and key not in _OPTIONAL]
    if missing:
        tables = ", ".join(f"[{name}.{key}]" for key in missing)
        raise ProfileError(f"{where}: needs {tables}")
    settings: dict[str, Any] = {}
    for key, kind in _SETTINGS.items():
        if key not in table:
            continue
        value = table[key]
        # A bool is an int to Python, never to a profile.
        if not isinstance(value, kind) or (
            isinstance(value, bool) and kind is not bool
        ):
            raise ProfileError(f"{where}: {key}: not {value!r}")
        settings[key] = value
    if "chunk_characters" in settings:
        settings["chunk_characters"] = _chunk(settings["chunk_characters"], where)
    for key in (
        "context_blocks",
        "cut_rounds",
        "per_stage_update_above_recut_blocks",
        "moved_delta_lines_max",
    ):
        if settings.get(key, 0) < 0:
            raise ProfileError(f"{where}: {key} takes 0 or more")
    if settings.get("decide_passes", 0) not in (0, 1):
        raise ProfileError(f"{where}: decide_passes takes 0 or 1")
    if settings.get("followup_sessions_max", 1) not in (0, 1):
        raise ProfileError(f"{where}: followup_sessions_max takes 0 or 1")
    fraction = settings.get("fresh_edges_above_reopened_fraction")
    if fraction is not None:
        if not 0 <= fraction <= 1:
            raise ProfileError(
                f"{where}: fresh_edges_above_reopened_fraction takes 0 to 1"
            )
        settings["fresh_edges_above_reopened_fraction"] = float(fraction)
    for key in ("boundary_passes", "role_passes", "group_passes", "edge_passes"):
        if settings.get(key, 1) < 1:
            raise ProfileError(f"{where}: {key} takes 1 or more")
    timeout = settings.get("session_timeout_seconds")
    if timeout is not None:
        if timeout <= 0:
            raise ProfileError(f"{where}: session_timeout_seconds takes more than 0")
        settings["session_timeout_seconds"] = float(timeout)

    shipped = Templates.shipped()
    templates: dict[str, Template] = {}
    agents: dict[Stage, Agent] = {}
    for key, (stage, field) in _TABLES.items():
        if key not in table:
            continue
        entry = table[key]
        at = f"{where}: {key}"
        if not isinstance(entry, dict):
            raise ProfileError(f"{at}: expected a table")
        extra = _EXTRA.get(stage, {})
        unknown = sorted(set(entry) - _AGENT_KEYS - set(extra))
        if unknown:
            raise ProfileError(f"{at}: unknown {', '.join(unknown)}")
        agents[stage] = _agent(
            entry, at, claude_code.READING_TOOLS if stage in READING_STAGES else None
        )
        if "prompt_template" in entry:
            templates[field] = _template(
                entry["prompt_template"], getattr(shipped, field), at, base
            )
        for key, extra_field in extra.items():
            if key in entry:
                templates[extra_field] = _template(
                    entry[key], getattr(shipped, extra_field), at, base
                )
    reviewers = _reviewers(table.get(_REVIEW, {}), f"{where}: {_REVIEW}", base)
    search = (
        _search(table[_SEARCH], f"{where}: {_SEARCH}") if _SEARCH in table else None
    )
    edges = agents.get(Stage.EDGES)
    if (
        search is None
        and settings.get("search_support_after_edges", True)
        and edges is not None
        and edges.harness is not Harness.CLAUDE_CODE
    ):
        raise ProfileError(
            f"{where}: search_support_after_edges ends a build with the support "
            "search, a queue session, which runs on claude-code alone and here "
            f"would take the edges' {edges.harness} agent: name a "
            f"[{name}.{_SEARCH}] table on claude-code, or set "
            "search_support_after_edges = false"
        )
    for setting, what in (
        ("reread_marks_after_update", "an update with the reread"),
        ("ask_circles_after_edges", "a build or an update with the circles question"),
    ):
        if (
            settings.get(setting, True)
            and edges is not None
            and edges.harness is not Harness.CLAUDE_CODE
        ):
            raise ProfileError(
                f"{where}: {setting} ends {what}, a queue session on the edges' "
                f"agent, which runs on claude-code alone, not {edges.harness}: "
                f"set {setting} = false"
            )
    if (
        settings.get("per_stage_update_above_recut_blocks") is not None
        and edges is not None
        and edges.harness is not Harness.CLAUDE_CODE
    ):
        raise ProfileError(
            f"{where}: per_stage_update_above_recut_blocks makes an update one "
            "session, a queue on the edges' agent, which runs on claude-code "
            f"alone, not {edges.harness}: leave it unset, so every update asks "
            "per stage"
        )
    return Profile(
        name,
        agents,
        replace(shipped, **templates),
        reviewers=reviewers,
        support_search=search,
        **settings,
    )


def _search(table: Any, where: str) -> SupportSearch:
    """The support search's steps: `worker`, needed, and `settler`, each
    an agent on Claude Code (a queue session runs there alone) with its
    `budget_share`; and `autocompact_tokens`."""
    if not isinstance(table, dict):
        raise ProfileError(f"{where}: expected a table")
    unknown = sorted(set(table) - _SEARCH_KEYS)
    if unknown:
        raise ProfileError(f"{where}: unknown {', '.join(unknown)}")
    if "worker" not in table:
        raise ProfileError(f"{where}: needs [{_SEARCH}.worker]")
    steps: dict[str, QueueStep] = {}
    for name in _SEARCH_STEPS:
        if name not in table:
            continue
        entry, at = table[name], f"{where}.{name}"
        if not isinstance(entry, dict):
            raise ProfileError(f"{at}: expected a table")
        unknown = sorted(set(entry) - _STEP_KEYS)
        if unknown:
            raise ProfileError(f"{at}: unknown {', '.join(unknown)}")
        steps[name] = _queue_step(entry, at)
    tokens = table.get("autocompact_tokens")
    if tokens is not None and (
        isinstance(tokens, bool) or not isinstance(tokens, int) or tokens <= 0
    ):
        raise ProfileError(f"{where}: autocompact_tokens takes a whole number above 0")
    return SupportSearch(steps["worker"], steps.get("settler"), tokens)


def _queue_step(entry: Mapping[str, Any], at: str) -> QueueStep:
    """A queue's step: an agent on Claude Code (a queue session runs there
    alone, its tools its server's and the host's reads of its folder) and
    its `budget_share`."""
    agent = _agent({k: v for k, v in entry.items() if k != "budget_share"}, at)
    if agent.harness is not Harness.CLAUDE_CODE:
        raise ProfileError(
            f"{at}: a queue session runs on claude-code alone; OpenCode serves none yet"
        )
    return QueueStep(agent, _share(entry.get("budget_share", 1), at))


def _share(share: Any, at: str) -> float:
    if isinstance(share, bool) or not isinstance(share, int | float) or share <= 0:
        raise ProfileError(f"{at}: budget_share takes a number above 0")
    return float(share)


def _reviewers(table: Any, where: str, base: Path | None) -> dict[Session, Reviewer]:
    """Each review session's agent and template: a table per
    session, its keys an agent's; the template the session's shipped one
    (`review-<session>`) unless named."""
    if not isinstance(table, dict):
        raise ProfileError(f"{where}: expected a table of sessions")
    found = {}
    for name, entry in table.items():
        at = f"{where}.{name}"
        try:
            session = session_named(name)
        except ValueError:
            known = ", ".join(s.value for s in Session)
            raise ProfileError(
                f"{at}: no review session {name!r}; there are {known}"
            ) from None
        if session in found:
            raise ProfileError(
                f"{at}: names the {session.value} session a second time, by its "
                "earlier name or its own: keep one table"
            )
        if not isinstance(entry, dict):
            raise ProfileError(f"{at}: expected a table")
        allowed = (
            _AGENT_KEYS
            | {_ASKED_BY_DEFAULT}
            | {k for k, sessions in _HELPER_SESSIONS.items() if session in sessions}
            | _QUEUE_KEYS.get(session, set())
        )
        unknown = sorted(set(entry) - allowed)
        if unknown:
            raise ProfileError(f"{at}: unknown {', '.join(unknown)}")
        for k in _HELPER_SESSIONS:
            if k in entry and not isinstance(entry[k], str):
                raise ProfileError(f"{at}.{k}: expected a model's name")
        asked_by_default = entry.get(_ASKED_BY_DEFAULT, True)
        if not isinstance(asked_by_default, bool):
            raise ProfileError(f"{at}.{_ASKED_BY_DEFAULT}: expected true or false")
        entry = {k: v for k, v in entry.items() if k != _ASKED_BY_DEFAULT}
        shipped = load_template(f"review-{session.value}")
        template = (
            _template(entry["prompt_template"], shipped, at, base)
            if "prompt_template" in entry
            else shipped
        )
        if session in _QUEUE_KEYS:
            if session in _FINDER_NEEDED and _FINDER not in entry:
                raise ProfileError(
                    f"{at}: name its finder's step, [{at}.{_FINDER}]: the "
                    f"{session.value} session's reader is served what it finds"
                )
            found[session] = replace(
                _queued(entry, at, template, _QUEUE_KEYS[session]),
                asked_by_default=asked_by_default,
            )
            continue
        agent = _agent(entry, at, claude_code.READING_TOOLS)
        if COLUMNS[session].by_block and agent.harness is not Harness.CLAUDE_CODE:
            raise ProfileError(
                f"{at}: the {session.value} session answers by block on a tool "
                "server, which claude-code alone serves"
            )
        missing = [k for k in _NEEDED_HELPERS.get(session, ()) if k not in entry]
        if missing:
            raise ProfileError(
                f"{at}: the {session.value} reader's brief asks its subagents: "
                f"name {', '.join(missing)}"
            )
        found[session] = Reviewer(
            agent,
            template,
            forager_model=entry.get(_FORAGER),
            asked_by_default=asked_by_default,
        )
    return found


def _queued(
    entry: Mapping[str, Any], at: str, template: Template, keys: set[str]
) -> Reviewer:
    """A queued session's seats: its agent the reader's step, with its
    `budget_share`, and, for the citationcheck session, the finder's step before
    it (`[...citationcheck.finder]`) where one is named; `keys`, what its table
    may name beside the agent's."""
    if "tools" in entry:
        raise ProfileError(
            f"{at}: tools: a queue session's tools are its server's and the "
            "host's reads of its folder"
        )
    own = {k: v for k, v in entry.items() if k not in keys}
    reader = _queue_step({**own, "budget_share": entry.get("budget_share", 1)}, at)
    finder = None
    if _FINDER in entry:
        given, where = entry[_FINDER], f"{at}.{_FINDER}"
        if not isinstance(given, dict):
            raise ProfileError(f"{where}: expected a table")
        unknown = sorted(set(given) - _STEP_KEYS)
        if unknown:
            raise ProfileError(f"{where}: unknown {', '.join(unknown)}")
        finder = _queue_step(given, where)
    return Reviewer(
        reader.agent, template, budget_share=reader.budget_share, finder=finder
    )


def _chunk(value: int | str, where: str) -> int | None:
    if value == "paper":
        return None
    if isinstance(value, str) or value < 0:
        raise ProfileError(f"{where}: chunk_characters takes 0 or more, or 'paper'")
    return value


def _agent(
    entry: dict[str, Any], at: str, tools: tuple[str, ...] | None = None
) -> Agent:
    """A stage's agent from its table; on Claude Code with `tools` where
    the table names none."""
    for key in ("harness", "model"):
        if not isinstance(entry.get(key), str):
            raise ProfileError(f"{at}: needs {key}, a string")
    try:
        harness = Harness(entry["harness"])
    except ValueError:
        known = ", ".join(h.value for h in Harness)
        raise ProfileError(
            f"{at}: harness takes {known}, not {entry['harness']!r}"
        ) from None
    model = entry["model"]
    problem = model_problem(harness, model)
    if problem:
        raise ProfileError(f"{at}: {problem}")
    effort = entry.get("effort")
    if effort is not None:
        problem = (
            effort_problem(harness, effort)
            if isinstance(effort, str)
            else "effort is a name"
        )
        if problem:
            raise ProfileError(f"{at}: {problem}")
    if "tools" in entry:
        given = entry["tools"]
        if harness is not Harness.CLAUDE_CODE:
            raise ProfileError(f"{at}: tools: OpenCode's tools are its own")
        if not isinstance(given, list) or not all(
            isinstance(n, str) and n for n in given
        ):
            raise ProfileError(f"{at}: tools takes a list of tool names")
        tools = tuple(given)
    elif harness is not Harness.CLAUDE_CODE:
        tools = None
    return Agent(harness, model, effort, tools=tools)


def _template(value: Any, shipped: Template, at: str, base: Path | None) -> Template:
    """A stage's template: a shipped template by name, or a `.md` file
    beside the profiles file. Its placeholders must be the stage's
    shipped template's, so its questions render."""
    if not isinstance(value, str):
        raise ProfileError(f"{at}: a prompt template is a name or a .md path")
    try:
        if value.endswith(".md"):
            if base is None:
                raise ProfileError(f"{at}: a shipped profile names shipped templates")
            path = base / value
            template = Template(path.stem, path.read_text(encoding="utf-8"))
        else:
            template = load_template(value)
    except OSError as error:
        raise ProfileError(f"{at}: no template {value!r}: {error}") from None
    wanted = set(string.Template(shipped.text).get_identifiers())
    found = set(string.Template(template.text).get_identifiers())
    if found != wanted:
        raise ProfileError(
            f"{at}: prompt template {value!r} takes {sorted(found)}, not the "
            f"{sorted(wanted)} {shipped.name!r} takes"
        )
    return template


#: `build`'s stages, in the order the driver asks them.
BUILD_STAGES = (
    Stage.VOCABULARY,
    Stage.BOUNDARIES,
    Stage.ROLES,
    Stage.GROUPS,
    Stage.EDGES,
)


def review_settings(profile: Profile) -> ReviewSettings:
    """Every stage's settings from the profile, as `build` asks them: an
    update's, and an edit's where its state's export records none."""
    return ReviewSettings(
        profile.chunk_characters,
        profile.context_blocks,
        profile.boundary_passes,
        stages=BUILD_STAGES,
        outline=profile.show_outline,
        role_passes=profile.role_passes,
        group_passes=profile.group_passes,
        edge_passes=profile.edge_passes,
        cut_rounds=profile.cut_rounds,
        templates=profile.templates,
    )


def run_settings(options: Mapping[str, Any]) -> ReviewSettings | None:
    """The settings of the profile a queue run's options name (`profile`,
    read from the file `profiles` where given, else the shipped one); None
    where they name none."""
    if options.get("profile") is None:
        return None
    path = options.get("profiles")
    found = read_profiles(Path(path) if path else None)
    return review_settings(found[options["profile"]])
