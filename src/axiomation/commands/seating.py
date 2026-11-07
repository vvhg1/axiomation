"""Who answers a command's questions, and within what: its profile with
the flags given over it, each stage's agent and host, the runner, the
budget (the paper's `[budget]` where none is given), and the words a dry
run prices them in."""

from __future__ import annotations

import argparse
import os
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from axiomation import support_search, user_settings
from axiomation.commands import Say, UsageError, requested
from axiomation.cutter import Cut
from axiomation.driver import ReviewSettings, Templates, planned_questions
from axiomation.profiles import (
    DEFAULT_MODELS,
    SEARCH_AUTOCOMPACT_TOKENS,
    Agent,
    Harness,
    Profile,
    ProfileError,
    effort_problem,
    model_problem,
    read_profiles,
)
from axiomation.questions import REASK_ANSWER_LIMIT, Question, Stage
from axiomation.reader import Document
from axiomation.records import EntryKind, RecordError, RecordStore, holds
from axiomation.resolver import Resolver
from axiomation.run_state import RunFile
from axiomation.runner import UNMEASURED_FACTOR, LikeRuns, Runner, Seat, like_runs
from axiomation.runner import like as like_name
from axiomation.runner.claude_code import (
    PROMPT_TOKENS,
    ClaudeCodeHost,
    inherited_effort,
)
from axiomation.runner.hosts import Host, Rates, rates_for
from axiomation.runner.opencode import OpenCodeHost
from axiomation.runner.queued import Price
from axiomation.settings import (
    BUDGET_SECTION,
    CONFIG_NAME,
    RUN_DOLLARS_MAX,
    Origin,
    SettingsError,
    run_dollars_max,
)
from axiomation.settings import load as load_settings

#: The `review-cut` flags that override a profile's settings,
#: and the setting each overrides; `--chunk` is read apart.
OVERRIDES = {
    "overlap": "context_blocks",
    "boundary_passes": "boundary_passes",
    "judge": "judge_boundaries",
    "outline": "show_outline",
    "role_passes": "role_passes",
    "group_passes": "group_passes",
    "edge_passes": "edge_passes",
    "cut_rounds": "cut_rounds",
    "decide_passes": "decide_passes",
    "rule_flags_in_edge_review": "show_rule_flags_in_edge_review",
    "timeout": "session_timeout_seconds",
}

#: The executable each harness runs.
EXECUTABLES = {Harness.CLAUDE_CODE: "claude", Harness.OPENCODE: "opencode"}

#: The switch the OpenCode host needs (`1`): it runs none of the review's
#: queue sessions, and its profiles' quality is unmeasured.
EXPERIMENTAL_OPENCODE = "AXIOMATION_EXPERIMENTAL_OPENCODE"


def allowed(harness: Harness) -> None:
    """Refused (`UsageError`) where `harness` is experimental and its
    switch is not set."""
    if harness is Harness.OPENCODE and os.environ.get(EXPERIMENTAL_OPENCODE) != "1":
        raise UsageError(
            "the OpenCode host is experimental: it runs none of the review's "
            "queue sessions, and its profiles' quality is unmeasured; "
            f"{EXPERIMENTAL_OPENCODE}=1 turns it on"
        )


#: The profile `build` and `update` run under when neither `--profile`
#: nor `--host` says how to ask: a person's run asks the shipped recipe,
#: and a replay is asked for by `--host none`.
DEFAULT_PROFILE = "standard"


@dataclass(frozen=True)
class Asking:
    """A request's part that says who answers and within what: the
    profile and the flags given over it, the budget, the questions at a
    time, and what like runs cost where the budget is the paper's
    (`take_budget`). A command that takes none of these flags leaves each
    unset."""

    profile: str | None = None
    profiles: Path | None = None
    host: str | None = None
    model: str | None = None
    effort: str | None = None
    rates: str | None = None
    judge: bool | None = None
    judge_host: str | None = None
    judge_model: str | None = None
    judge_rates: str | None = None
    chunk: str | None = None
    overlap: int | None = None
    boundary_passes: int | None = None
    outline: bool | None = None
    role_passes: int | None = None
    group_passes: int | None = None
    edge_passes: int | None = None
    cut_rounds: int | None = None
    decide_passes: int | None = None
    rule_flags_in_edge_review: bool | None = None
    timeout: float | None = None
    budget: float | None = None
    jobs: int = 1
    expect: LikeRuns | None = None


def asking(arguments: argparse.Namespace) -> Asking:
    """The asking flags a command's parsed arguments give."""
    return requested(Asking, arguments)


def default_profile(given: Asking) -> tuple[Asking, bool]:
    """`given` under the default profile where nothing says how to ask, and
    whether it took it; `--profiles` alone stays refused by `profile_of`."""
    if given.profile is None and given.host is None and given.profiles is None:
        return replace(given, profile=DEFAULT_PROFILE), True
    return given, False


def budget_needed(given: Asking, command: str, defaulted: bool) -> str:
    """The refusal of a run that would ask a model without `--budget`,
    naming what makes it ask."""
    if given.profile is None:
        asks = f"--host {given.host} asks a model"
    elif defaulted:
        asks = f"{command} asks under the {given.profile} profile by default"
    else:
        asks = f"--profile {given.profile} asks a model"
    return f"{asks}, and needs --budget (--host none replays the store)"


def take_budget(
    given: Asking,
    directory: Path,
    store: Path,
    command: str,
    refusal: str,
    say: Say,
    own: frozenset[str] = frozenset(),
) -> Asking:
    """A run that asks a model: where no `--budget` is given, the budget
    default taken as its budget (`settings.run_dollars_max`: the paper's,
    the workspace's or the person's), said beside the report,
    with what like runs cost in its store kept for the runner's check
    (`Asking.expect`); refused with `refusal`, naming the key, where
    none is set. Refused first where a person's filing waits for
    their word: a run kept on it would be lost with a drop. `own`: the
    keys of the edits the run itself answers, which never refuse it: a
    drop of one takes back its own run with it."""
    if holds(Path(store), EntryKind.ANSWER):
        try:
            RecordStore(Path(store)).refuse_waiting(f"`{command}`", own)
        except RecordError as error:
            raise UsageError(str(error)) from None
    if given.budget is not None:
        return given
    try:
        cap, came = run_dollars_max(load_settings(Path(directory)))
    except SettingsError as error:
        raise UsageError(str(error)) from None
    if cap is None:
        raise UsageError(
            f"{refusal}; or set {RUN_DOLLARS_MAX} in the user file "
            f"({user_settings.path()}), or [{BUDGET_SECTION}].{RUN_DOLLARS_MAX} "
            f"in the paper's or the workspace's {CONFIG_NAME}"
        )
    store = Path(store)
    expect = like_runs(
        RecordStore(store).answers() if holds(store, EntryKind.ANSWER) else ()
    )
    say.note(
        f"axiomation {command}: budget ${cap:g}, {_budget_from(came)} "
        "(--budget overrides); a step expected to cost more than is left "
        "asks nothing"
    )
    return replace(given, budget=cap, expect=expect)


def _budget_from(came: Origin) -> str:
    """Where the budget a run took came from, as its note names it."""
    key = user_settings.UserKey.RUN_DOLLARS_MAX
    if came is Origin.SHELL:
        return f"{key.env} in the shell"
    if came is Origin.USER:
        return f"the user file's {RUN_DOLLARS_MAX}"
    return f"the {came.value}'s [{BUDGET_SECTION}].{RUN_DOLLARS_MAX}"


def hosted(given: Asking) -> bool:
    """Whether the run has hosts to ask: a profile's, unless `--host none`;
    without a profile, only a host `--host` names."""
    return given.host != "none" and (
        given.host is not None or given.profile is not None
    )


def profile_of(given: Asking) -> Profile:
    """The profile the run is under, each flag given overriding
    one value of it: `--host`, `--model` and `--rates` every stage's agent
    but the judge's, `--judge-*` the judge's, `--effort` every agent. A
    judge without an agent of its own has the author's.
    Without `--profile`, the flags and their defaults alone."""
    if given.profile is None:
        if given.profiles is not None:
            raise UsageError("--profiles needs --profile")
        harness = (
            Harness.OPENCODE if given.host in (None, "none") else Harness(given.host)
        )
        profile = Profile(
            None,
            {Stage.BOUNDARIES: Agent(harness, DEFAULT_MODELS[harness])},
            Templates.shipped(),
        )
    else:
        try:
            profiles = read_profiles(given.profiles)
        except ProfileError as error:
            raise UsageError(str(error)) from None
        if given.profile not in profiles:
            raise UsageError(
                f"no profile {given.profile!r}; there are {', '.join(sorted(profiles))}"
            )
        profile = profiles[given.profile]
    judging = (given.judge_host, given.judge_model, given.judge_rates)
    if any(value is not None for value in judging):
        judged = profile.judge_boundaries if given.judge is None else given.judge
        if not judged:
            raise UsageError(
                "--judge-host, --judge-model and --judge-rates need --judge"
            )
        if not hosted(given):
            raise UsageError("--host none asks nothing: give the author's --host")
    settings: dict[str, Any] = {
        setting: getattr(given, flag)
        for flag, setting in OVERRIDES.items()
        if getattr(given, flag) is not None
    }
    if given.chunk is not None:
        settings["chunk_characters"] = chunk_of(given.chunk)

    host = None if given.host in (None, "none") else Harness(given.host)
    rates = None if given.rates is None else rates_of(given.rates, "--rates")
    agents = {
        stage: _overridden(agent, host, given.model, rates)
        for stage, agent in profile.agents.items()
        if stage is not Stage.JUDGE
    }
    judge = profile.agents.get(Stage.JUDGE)
    if judge is not None or any(value is not None for value in judging):
        judge_rates = given.judge_rates
        agents[Stage.JUDGE] = _overridden(
            judge or agents[Stage.BOUNDARIES],
            None if given.judge_host is None else Harness(given.judge_host),
            given.judge_model,
            None if judge_rates is None else rates_of(judge_rates, "--judge-rates"),
        )
    if given.effort is not None:
        agents = {
            stage: replace(agent, effort=given.effort)
            for stage, agent in agents.items()
        }
    return replace(profile, agents=agents, **settings)


def _overridden(
    agent: Agent, harness: Harness | None, model: str | None, rates: Rates | None
) -> Agent:
    """`agent` on another harness or model, which drops its effort and
    rates (another harness its tools too), or at other rates."""
    if harness is not None and harness is not agent.harness:
        agent = Agent(harness, DEFAULT_MODELS[harness])
    if model is not None and model != agent.model:
        agent = Agent(agent.harness, model, tools=agent.tools)
    if rates is not None:
        agent = replace(agent, rates=rates)
    return agent


def seated(
    profile: Profile,
    settings: ReviewSettings,
    priced: bool,
    hosted: bool,
) -> dict[Stage, Agent]:
    """The agent of each stage the run asks, its effort the user's Claude
    Code setting where none is given; `priced`: a run that asks or prices,
    so each needs its rates; `hosted`: a run on hosts, so each model's
    form is checked."""
    asked = []
    if Stage.VOCABULARY in settings.stages:
        asked += [Stage.VOCABULARY, Stage.VOCABULARY_CHECK]
    if Stage.BOUNDARIES in settings.stages and not settings.given_cut:
        asked.append(Stage.BOUNDARIES)
        if settings.judge:
            asked.append(Stage.JUDGE)
    asked += [
        s for s in (Stage.ROLES, Stage.GROUPS, Stage.EDGES) if s in settings.stages
    ]
    if settings.decide_passes and Stage.GROUPS in settings.stages:
        asked.append(Stage.DECIDE)
    agents = {stage: profile.agent(stage) for stage in asked}
    seated = {}
    for stage, agent in agents.items():
        judge = stage is Stage.JUDGE
        problem = model_problem(agent.harness, agent.model)
        if hosted and problem:
            raise UsageError(f"{'--judge-model' if judge else '--model'}: {problem}")
        if agent.effort is not None:
            problem = effort_problem(agent.harness, agent.effort)
            if problem:
                raise UsageError(f"--effort: {problem}")
        if agent.harness is Harness.CLAUDE_CODE and agent.effort is None:
            agent = replace(agent, effort=inherited_effort(agent.model))
        if priced and agent.rates is None:
            rates = rates_for(agent.model)
            if rates is None:
                flag = "--judge-rates" if judge else "--rates"
                raise UsageError(
                    f"no rates are known for {agent.model}; give {flag} "
                    "IN,OUT[,CACHE] or add its price to prices.toml"
                )
            agent = replace(agent, rates=rates)
        seated[stage] = agent
    return seated


def seats_checked(
    agents: dict[Stage, Agent],
    executables: dict[Harness, str],
    document: Document,
    found: Cut,
    settings: ReviewSettings,
) -> dict[Stage, Seat]:
    """Each stage's host and rates; one host for the stages whose agents
    are the same, each checked before any question is asked, and an
    OpenCode host's against its prompt limit."""
    planned = planned_questions(document, found, settings)
    hosts: dict[tuple[Harness, str, str | None, tuple[str, ...] | None], Host] = {}
    seats = {}
    for stage, agent in agents.items():
        same = (agent.harness, agent.model, agent.effort, agent.tools)
        if same not in hosts:
            hosts[same] = host_of(agent, executables[agent.harness])
        host = hosts[same]
        if isinstance(host, OpenCodeHost):
            within_limit(host, planned.get(stage, ()))
        assert agent.rates is not None
        seats[stage] = Seat(host, agent.rates)
    return seats


def host_of(agent: Agent, executable: str) -> Host:
    """The agent's host, checked; on Claude Code, with the agent's tools
    alone where it names them (OpenCode's tools are its own)."""
    allowed(agent.harness)
    if agent.harness is Harness.CLAUDE_CODE:
        host = ClaudeCodeHost(
            model=agent.model,
            executable=executable,
            effort=agent.effort,
            tools=agent.tools,
        )
        host.check()
        return host
    opencode = OpenCodeHost(
        model=agent.model, executable=executable, effort=agent.effort
    )
    opencode.check()
    return opencode


def within_limit(host: OpenCodeHost, questions: Sequence[Question]) -> None:
    margin = 4 * REASK_ANSWER_LIMIT + 10_000
    for question in questions:
        size = len(question.prompt.encode("utf-8"))
        if size + margin > host.prompt_limit:
            raise UsageError(
                f"a {question.stage.value} prompt is {size:,} bytes; with "
                f"a re-ask it could pass the host's limit of "
                f"{host.prompt_limit:,}"
            )


def executables_of(agents: Mapping[Any, Agent]) -> dict[Harness, str]:
    """Each harness the agents run on, its executable found on PATH."""
    executables: dict[Harness, str] = {}
    for harness in {agent.harness for agent in agents.values()}:
        allowed(harness)
        command = EXECUTABLES[harness]
        found = shutil.which(command)
        if found is None:
            raise UsageError(f"--host {harness.value} needs `{command}` on PATH")
        executables[harness] = found
    return executables


def seats_of(
    agents: dict[Stage, Agent], executables: dict[Harness, str]
) -> dict[Stage, Seat]:
    """Each stage's host and rates, one host for the stages whose agents
    are the same, each checked before any question is asked."""
    hosts: dict[tuple[Harness, str, str | None, tuple[str, ...] | None], Host] = {}
    seats = {}
    for stage, agent in agents.items():
        same = (agent.harness, agent.model, agent.effort, agent.tools)
        if same not in hosts:
            hosts[same] = host_of(agent, executables[agent.harness])
        assert agent.rates is not None
        seats[stage] = Seat(hosts[same], agent.rates)
    return seats


def runner(
    given: Asking,
    profile: Profile,
    seats: dict[Stage, Seat],
    store: RecordStore,
    run: RunFile | None = None,
) -> Runner:
    """The runner of an update's or an edit's questions, each stage on its
    seat, the edges' the default, within `--budget` across them all; `run`
    the update's working state, which holds its answers' texts."""
    return Runner(
        seats[Stage.EDGES].host,
        Resolver(store, run=run),
        given.budget,
        seats[Stage.EDGES].rates,
        jobs=given.jobs,
        timeout=profile.session_timeout_seconds,
        transcripts=store.transcripts,
        seats=seats,
        profile=profile.name,
        expect=given.expect,
    )


@dataclass(frozen=True, slots=True)
class QueueSeats:
    """A queue's steps as the profile seats them: each step's agent, the
    first step's first, with its rates; their shares of the budget; the
    context its sessions are compacted at."""

    agents: tuple[Agent, ...]
    shares: tuple[float, ...]
    autocompact_tokens: int | None

    @property
    def model(self) -> str:
        """Who gives the run's edit: its last step's model."""
        return self.agents[-1].model


def search_seated(profile: Profile, hosted: bool) -> QueueSeats:
    """The profile's search steps (`Profile.search_steps`), seated
    (`queue_seated`)."""
    steps = profile.search_steps()
    given = [steps.worker, *([steps.settler] if steps.settler else [])]
    return queue_seated(
        [(step.agent, step.budget_share) for step in given],
        steps.autocompact_tokens,
        hosted,
        "the support search",
        "name claude-code agents in the profile's support_search",
    )


def edges_seated(agent: Agent, hosted: bool, what: str) -> QueueSeats:
    """A queue of one step on the edges' agent, its sessions compacted at
    `SEARCH_AUTOCOMPACT_TOKENS` (`queue_seated`): the reread and the
    circles."""
    return queue_seated(
        [(agent, 1.0)],
        SEARCH_AUTOCOMPACT_TOKENS,
        hosted,
        what,
        "seat the profile's edges on claude-code",
    )


def queue_seated(
    given: Sequence[tuple[Agent, float]],
    autocompact_tokens: int | None,
    hosted: bool,
    what: str,
    fix: str,
) -> QueueSeats:
    """A queue's steps, each agent with its share of the budget, its rates
    and, on Claude Code, the user's effort for its model where none is
    given; `hosted` (a run that asks, or a dry run pricing one): each on
    Claude Code, its model's form checked."""
    agents = []
    for agent, _ in given:
        if hosted and agent.harness is not Harness.CLAUDE_CODE:
            raise UsageError(
                f"{what} runs on claude-code alone, not {agent.harness}: a queue "
                "session's tools are a tool server's, which OpenCode serves not "
                f"yet; {fix}"
            )
        problem = model_problem(agent.harness, agent.model)
        if hosted and problem:
            raise UsageError(f"{what}'s model: {problem}")
        if agent.harness is Harness.CLAUDE_CODE and agent.effort is None:
            agent = replace(agent, effort=inherited_effort(agent.model))
        if agent.rates is None:
            rates = rates_for(agent.model)
            if rates is None:
                raise UsageError(
                    f"no rates are known for {agent.model}: add its price to "
                    "prices.toml"
                )
            agent = replace(agent, rates=rates)
        agents.append(agent)
    return QueueSeats(
        tuple(agents), tuple(share for _, share in given), autocompact_tokens
    )


def queue_hosts(seated: QueueSeats) -> list[Host]:
    """A host a queue's step, each checked; two steps, two hosts, since
    their sessions run at once."""
    by_step = dict(enumerate(seated.agents))
    executables = executables_of(by_step)
    return [host_of(agent, executables[agent.harness]) for agent in seated.agents]


def queue_runner(
    given: Asking,
    profile: Profile,
    store: RecordStore,
    seated: QueueSeats,
    hosts: Sequence[Host],
    budget: float | None = None,
    review_round: str | None = None,
) -> Runner:
    """The runner of a queue's sessions, within `budget` (None: `--budget`),
    its rates the last step's; its answers in `review_round` where it is a
    review session's."""
    return Runner(
        hosts[-1],
        Resolver(store),
        given.budget if budget is None else budget,
        seated.agents[-1].rates or Rates(0.0, 0.0),
        jobs=1,
        timeout=profile.session_timeout_seconds,
        transcripts=store.transcripts,
        profile=profile.name,
        expect=given.expect,
        review_round=review_round,
    )


def run_profile(profile: Profile, profiles: str | Path | None) -> dict[str, str | None]:
    """A queue run's options naming its profile, and the file it is read from
    where one is given (`profiles.run_settings`)."""
    return {
        "profile": profile.name,
        "profiles": (
            str(Path(profiles).resolve())
            if profiles is not None and profile.name is not None
            else None
        ),
    }


def search_cost(store: RecordStore, seated: QueueSeats, claims: int) -> float:
    """What a search of `claims` claims is expected to cost: each step's,
    on its model (`support_search.price`)."""
    kinds = (support_search.WORKER_KIND, support_search.SETTLER_KIND)
    try:
        return sum(
            support_search.price(store, kind, agent.model).cost(claims)
            for kind, agent in zip(kinds, seated.agents, strict=False)
        )
    except support_search.SearchError as error:
        raise UsageError(str(error)) from None


def reviewer_agent(agent: Agent) -> Agent:
    """A reviewer's agent with its effort (the user's Claude Code setting
    where none is given) and its rates, checked."""
    problem = model_problem(agent.harness, agent.model)
    if problem:
        raise UsageError(f"the reviewer's model: {problem}")
    if agent.effort is not None:
        problem = effort_problem(agent.harness, agent.effort)
        if problem:
            raise UsageError(f"the reviewer's effort: {problem}")
    if agent.harness is Harness.CLAUDE_CODE and agent.effort is None:
        agent = replace(agent, effort=inherited_effort(agent.model))
    if agent.rates is None:
        rates = rates_for(agent.model)
        if rates is None:
            raise UsageError(
                f"no rates are known for {agent.model}; add its price to prices.toml"
            )
        agent = replace(agent, rates=rates)
    return agent


def harness_tokens(agent: Agent) -> int:
    """The harness's own prompt, which every session of the agent writes to
    the cache once."""
    if agent.harness is Harness.CLAUDE_CODE:
        return PROMPT_TOKENS
    return OpenCodeHost.prompt_tokens


def described(agent: Agent) -> dict[str, Any]:
    assert agent.rates is not None
    return {
        "host": agent.harness.value,
        "model": agent.model,
        "effort": agent.effort,
        "rates": {
            "input": agent.rates.input,
            "output": agent.rates.output,
            "cache_read": agent.rates.cache_read,
            **{
                name: rate
                for name in ("cache_write_1h", "cache_write_5m")
                if (rate := getattr(agent.rates, name)) is not None
            },
        },
    }


def expected_words(
    like: LikeRuns, template: str, estimated: float, fitted: bool = False
) -> str:
    """A run's expected cost, and how it was worked out from its estimate
    and what like runs in the store cost."""
    name = like_name(template)
    held = like.counts.get(name)
    return (
        f"expected ${like.expected(template, estimated, fitted):.2f}: the estimate "
        f"× {like.factor(template, fitted):.2f}, "
        + (
            f"what {held} {name} answers in this store cost over theirs"
            if held
            else f"as no {name} answer in this store records its estimate"
        )
    )


def like_words(like: LikeRuns) -> str:
    """How an expected cost was worked out: each kind of run the store holds
    an estimate for, its cost over estimate, and the factor for the rest."""
    held = "; ".join(
        f"{name} ×{factor:.2f} ({like.counts[name]} answers)"
        for name, factor in sorted(like.factors.items())
    )
    return (
        "each estimate times the store's cost over estimate for like runs"
        + (f": {held}" if held else "")
        + f"; ×{UNMEASURED_FACTOR:g} for a run the store holds none of"
    )


def priced_words(kind: str, model: str, price: Price, per: str) -> str:
    """A step's price as a dry run shows it: its session's start, fitted on
    recorded runs, and a task's, measured in this store where it can be."""
    how = (
        f"what {price.tasks} of its tasks in this store cost"
        if price.tasks
        else "fitted on recorded runs"
    )
    return (
        f"  {kind} on {model}: ${price.start:.4f} its session's start, fitted "
        f"on recorded runs; ${price.task:.4f} {per}, {how}"
    )


def chunk_of(value: str) -> int | None:
    if value == "paper":
        return None
    try:
        chunk = int(value)
    except ValueError:
        raise UsageError(
            f"--chunk takes a number of characters or 'paper', not {value!r}"
        ) from None
    if chunk < 0:
        raise UsageError("--chunk takes a number of characters, 0 or more")
    return chunk


def rates_of(value: str, flag: str) -> Rates:
    try:
        numbers = [float(x) for x in value.split(",")]
    except ValueError:
        numbers = []
    if len(numbers) not in (2, 3) or any(n < 0 for n in numbers):
        raise UsageError(
            f"{flag} takes IN,OUT[,CACHE] in dollars a million, not {value!r}"
        )
    return Rates(*numbers)


#: `--host`'s word on OpenCode, wherever a host is chosen.
OPENCODE_HELP = f"opencode is experimental: it needs {EXPERIMENTAL_OPENCODE}=1"

#: `--budget`'s help on a command that asks agents.
BUDGET_HELP = (
    "dollars the run may spend (default: the budget `axiomation settings` shows)"
)

#: `--rates`' help.
RATES_HELP = (
    "IN,OUT[,CACHE]: US dollars per million input, output and cache-read "
    "tokens, for the estimate; needed for a model without known rates"
)


def asking_arguments(
    command: argparse.ArgumentParser,
    asked: str | None,
    run: str,
    profiled_by_default: bool = False,
) -> None:
    """A run's profile (`profile_of`), and where it asks (`asked`, what its
    host serves; None: it asks nothing) its host, model, effort, budget,
    rates, jobs and timeout, as build's; `profile_of`'s other flags, which
    only `review-cut` and `build` take, unset."""
    command.add_argument(
        "--profile",
        help="the recipe, as build's"
        + (
            f" (default: {DEFAULT_PROFILE}, unless --host is given)"
            if profiled_by_default
            else ""
        ),
    )
    command.add_argument(
        "--profiles", type=Path, help="a TOML file of profiles, as build's"
    )
    unset: dict[str, Any] = {
        "judge_host": None,
        "judge_model": None,
        "judge_rates": None,
        "chunk": None,
        **{flag: None for flag in OVERRIDES if flag != "timeout"},
    }
    if asked is None:
        unset |= {"host": None, "model": None, "rates": None, "effort": None}
        unset["timeout"] = None
        command.set_defaults(**unset)
        return
    command.add_argument(
        "--host",
        choices=[*(h.value for h in Harness), "none"],
        help=f"the host of {asked}; none asks no model: {run} from what the "
        "store already answered (free)"
        + ("" if profiled_by_default else "; the default without --profile")
        + f"; {OPENCODE_HELP}",
    )
    command.add_argument("--model", help="the host's model, as build's")
    command.add_argument("--effort", help="the effort, as build's")
    command.add_argument("--budget", type=float, help=BUDGET_HELP)
    command.add_argument("--rates", help=RATES_HELP)
    # tuning, hidden from --help: --timeout overrides the profile's value,
    # --jobs is the run's own
    command.add_argument("--jobs", type=int, default=1, help=argparse.SUPPRESS)
    command.add_argument("--timeout", type=float, help=argparse.SUPPRESS)
    command.set_defaults(**unset)
