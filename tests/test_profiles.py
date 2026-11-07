"""Profiles: the read, the shipped ones, and what is refused."""

from __future__ import annotations

import re
from dataclasses import replace
from importlib import resources
from pathlib import Path

import pytest

from axiomation.driver import Templates
from axiomation.profiles import Agent, Harness, ProfileError, read_profiles
from axiomation.questions import Stage, load_template
from axiomation.questions.review_file import Session, session_named
from axiomation.runner.claude_code import READING_TOOLS
from axiomation.runner.hosts import Rates, known_rates, rates_for

OPUS = Agent(Harness.CLAUDE_CODE, "claude-opus-5-5", "high")
DEEPSEEK = Agent(Harness.OPENCODE, "opencode/deepseek-v4.1-flash")
TABLES = ("boundaries", "boundaries_judge", "roles", "groups", "edges")
BY_TARGET = "edges-bytarget"


def test_the_shipped_profiles_are_the_recipes() -> None:
    profiles = read_profiles()

    assert list(profiles) == ["standard", "cheap"]
    standard, cheap = profiles.values()
    shipped = Templates.shipped()
    for profile in profiles.values():
        # standard's second edges pass is the pass by target
        by_target = profile is standard
        assert (
            profile.chunk_characters,
            profile.context_blocks,
            profile.show_outline,
            profile.boundary_passes,
            profile.role_passes,
            profile.group_passes,
            profile.edge_passes,
            profile.cut_rounds,
            profile.session_timeout_seconds,
        ) == (500_000, 2, False, 1, 1, 1, 2 if by_target else 1, 0, 3600.0)
        assert profile.templates == (
            replace(shipped, edges_review=load_template(BY_TARGET))
            if by_target
            else shipped
        )
        # every product stage seated; `argument` has no seat, and a
        # review session's agent is its reviewer's
        assert set(profile.agents) == set(Stage) - {Stage.ARGUMENT, Stage.REVIEW}
    # Standard: Opus on Claude Code, the roles at xhigh; the vocabulary map
    # on Sonnet at medium and its second round on Haiku.
    assert not standard.judge_boundaries
    assert standard.agent(Stage.ROLES) == Agent(
        Harness.CLAUDE_CODE, "claude-opus-5-5", "xhigh"
    )
    assert standard.agent(Stage.VOCABULARY) == Agent(
        Harness.CLAUDE_CODE, "claude-sonnet-5-5", "medium"
    )
    assert standard.agent(Stage.VOCABULARY_CHECK) == Agent(
        Harness.CLAUDE_CODE, "claude-haiku-5-5", "low"
    )
    vocabulary = (Stage.VOCABULARY, Stage.VOCABULARY_CHECK)
    assert {
        standard.agent(s)
        for s in Stage
        if s not in (Stage.ROLES, Stage.REVIEW, Stage.EDGES, *vocabulary)
    } == {OPUS}
    # the edges stage's sessions read with the reading tools alone: no fork
    assert standard.agent(Stage.EDGES) == replace(OPUS, tools=READING_TOOLS)
    assert cheap.agent(Stage.EDGES).tools is None
    # The argument reader and the methods reader: Opus
    # at xhigh; the citation reader (it sets a sentence beside a page) and
    # the findings reader (it reads in order, checking no proof) at high;
    # each on its shipped template; the others have none yet.
    assert list(standard.reviewers) == [
        Session.ARGUMENT,
        Session.CITATIONCHECK,
        Session.METHODS,
        Session.FINDINGS,
        Session.CONTRIBUTION,
        Session.FIELDRESULTS,
        Session.RELATEDWORK,
    ]
    for session, reviewer in standard.reviewers.items():
        effort = (
            "xhigh"
            if session in (Session.ARGUMENT, Session.METHODS)
            else "medium"
            if session is Session.RELATEDWORK
            else "high"
        )
        # a queue session's tools are its server's and the host's reads
        tools = (
            None
            if session in (Session.CITATIONCHECK, Session.METHODS, Session.FIELDRESULTS)
            else READING_TOOLS
        )
        assert reviewer.agent == Agent(
            Harness.CLAUDE_CODE, "claude-opus-5-5", effort, tools=tools
        )
        assert reviewer.template == load_template(f"review-{session.value}")
    # the citationcheck session is a queue: Haiku's finder at effort low, then
    # the reader, with their shares of the budget; no page-finder subagent
    citation = standard.reviewers[Session.CITATIONCHECK]
    assert citation.finder is not None
    assert (citation.finder.agent.model, citation.finder.agent.effort) == (
        "claude-haiku-5-5",
        "low",
    )
    assert (citation.finder.budget_share, citation.budget_share) == (1.0, 9.0)
    assert citation.forager_model is None
    assert standard.reviewers[Session.ARGUMENT].forager_model is None
    # the fieldresults session is a queue too: Haiku's finder at effort
    # low, then the reader, who also answers its inventory; no subagent
    fieldresults = standard.reviewers[Session.FIELDRESULTS]
    assert fieldresults.finder is not None
    assert (fieldresults.finder.agent.model, fieldresults.finder.agent.effort) == (
        "claude-haiku-5-5",
        "low",
    )
    assert (fieldresults.finder.budget_share, fieldresults.budget_share) == (1.0, 9.0)
    assert fieldresults.forager_model is None
    # the contribution and relatedwork readers' forager
    for session in (Session.CONTRIBUTION, Session.RELATEDWORK):
        assert standard.reviewers[session].forager_model == "haiku"
    # the paper's own four asked by default, the field's three on request
    field = {Session.CONTRIBUTION, Session.FIELDRESULTS, Session.RELATEDWORK}
    assert {s for s, r in standard.reviewers.items() if not r.asked_by_default} == field
    assert not cheap.reviewers
    # Cheap: DeepSeek on OpenCode throughout.
    assert {cheap.agent(stage) for stage in Stage} == {DEEPSEEK}


def test_standard_asks_a_second_edges_pass_by_target() -> None:
    """`standard` reviews the edges once, by
    target, under the `edges-bytarget` brief (with the common-term sentence),
    with the sentence on a claim's justifications and their flags, with the
    argument an analogy carries over; the other recipes keep the shipped
    review."""
    profiles = read_profiles()
    standard, cheap = profiles["standard"], profiles["cheap"]

    assert standard.edge_passes == 2
    assert standard.templates.edges_review.name == "edges-bytarget"
    assert standard.templates.edges_review.version == "cd3cc09d8603"
    assert cheap.templates.edges_review == Templates.shipped().edges_review
    assert cheap.templates.edges_review.name == "edges-review"


def test_a_price_is_the_model_s_own() -> None:
    """Prices live in prices.toml, by model; a model it does not price has
    none."""
    assert known_rates()["claude-opus-5-5"] == Rates(4, 20, 0.20, 8, 5)
    assert rates_for("opencode/deepseek-v4.1-flash") == Rates(0.3, 1.2, 0.006)
    assert rates_for("haiku") is None


def test_a_session_s_tokens_price_to_its_cost() -> None:
    """A support search session that forked: its writes kept five minutes
    and an hour priced apart give the cost Claude Code records for those
    tokens."""
    tokens = {
        "input": 264,
        "output": 133_300,
        "reasoning": 81_083,
        "cache_read": 12_817_185,
        "cache_write": 664_362,
        "cache_write_1h": 85_241,
    }
    assert known_rates()["claude-opus-5-5"].priced(tokens) == pytest.approx(
        8.808026, abs=5e-6
    )
    # a record that does not split its writes prices them all at the hour's
    assert Rates(0, 0, 0, 8, 5).priced({"cache_write": 10**6}) == 8.0


def _profile(name: str = "p", settings: str = "", **tables: str) -> str:
    """A profile's text: its settings, and each stage's table, haiku on
    Claude Code unless given."""
    text = f"[{name}]\n{settings}"
    for table in TABLES:
        body = tables.get(table, 'harness = "claude-code"\nmodel = "haiku"\n')
        text += f"[{name}.{table}]\n{body}"
    return text


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "profiles.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_file_adds_profiles_and_replaces_one_of_the_same_name(tmp_path) -> None:
    glm = 'harness = "opencode"\nmodel = "opencode-go/glm-5.3-flash"\neffort = "high"\n'
    path = _write(
        tmp_path,
        _profile("cheap", 'chunk_characters = "paper"\n', roles=glm)
        + _profile(
            "mine",
            "judge_boundaries = true\nsession_timeout_seconds = 60\n",
            boundaries_judge='harness = "claude-code"\nmodel = "sonnet"\n',
        ),
    )

    profiles = read_profiles(path)

    assert {"standard", "cheap", "mine"} <= set(profiles)
    cheap, mine = profiles["cheap"], profiles["mine"]
    # The defaults, not the shipped profile's.
    assert cheap.chunk_characters is None and cheap.show_outline
    assert cheap.agent(Stage.ROLES) == Agent(
        Harness.OPENCODE, "opencode-go/glm-5.3-flash", "high"
    )
    assert cheap.agent(Stage.BOUNDARIES).model == "haiku"
    assert mine.judge_boundaries and mine.session_timeout_seconds == 60.0
    assert mine.agent(Stage.JUDGE) == Agent(Harness.CLAUDE_CODE, "sonnet")


def test_a_template_beside_the_file_changes_the_version(tmp_path) -> None:
    """A stage's prompt belongs to the profile; its version is in the
    questions' keys, so answers under two templates never share one."""
    shipped = Templates.shipped()
    (tmp_path / "terse.md").write_text(
        shipped.groups.text + "\nBe brief.\n", encoding="utf-8"
    )
    haiku = 'harness = "claude-code"\nmodel = "haiku"\n'
    path = _write(
        tmp_path,
        _profile(
            "terse",
            groups=haiku + 'prompt_template = "terse.md"\n',
            roles=haiku + 'review_prompt_template = "roles-lines"\n',
            edges=haiku
            + 'review_prompt_template = "terse-review.md"\n'
            + 'reask_prompt_template = "terse-reask.md"\n',
        ),
    )
    (tmp_path / "terse-reask.md").write_text(
        shipped.edges_reask.text + "\nBe brief.\n", encoding="utf-8"
    )
    (tmp_path / "terse-review.md").write_text(
        shipped.edges_review.text + "\nBe brief.\n", encoding="utf-8"
    )

    templates = read_profiles(path)["terse"].templates

    assert templates.groups.name == "terse"
    assert templates.groups.version != shipped.groups.version
    assert templates.roles_review == shipped.roles
    assert templates.edges_review.name == "terse-review"
    assert templates.edges_reask.name == "terse-reask"
    assert templates.edges == shipped.edges
    assert templates.boundaries == shipped.boundaries


HAIKU = 'harness = "claude-code"\nmodel = "haiku"\n'


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("[p]\n[p.boundaries]\n" + HAIKU, "needs [p.boundaries_judge], [p.roles]"),
        (_profile(settings="chunk = 1\n"), "unknown chunk"),
        (_profile(settings="boundary_passes = 0\n"), "boundary_passes takes 1"),
        (_profile(settings="boundary_passes = true\n"), "boundary_passes: not True"),
        (_profile(settings='chunk_characters = "half"\n'), "chunk_characters takes 0"),
        (_profile(settings="session_timeout_seconds = 0\n"), "takes more than 0"),
        (_profile() + "[p.judge]\n" + HAIKU, "unknown judge"),
        (
            _profile(boundaries='harness = "aider"\nmodel = "x/y"\n'),
            "harness takes claude-code, opencode, not 'aider'",
        ),
        (
            _profile(boundaries='harness = "opencode"\nmodel = "deepseek"\n'),
            "opencode takes <provider>/<id>, not 'deepseek'",
        ),
        (
            _profile(groups='harness = "claude-code"\nmodel = "opencode/x"\n'),
            "groups: claude-code takes an alias or a model id, without a '/'",
        ),
        (_profile(roles='harness = "opencode"\n'), "roles: needs model"),
        (
            _profile(roles='harness = "opencode"\nmodel = "a/b"\neffort = "hi gh"\n'),
            "opencode's effort is a variant's name, not 'hi gh'",
        ),
        (
            _profile(roles='harness = "opencode"\nmodel = "a/b#high"\n'),
            "the effort is given apart (effort)",
        ),
        (_profile(roles=HAIKU + 'effort = "huge"\n'), "effort takes low"),
        (
            _profile(boundaries=HAIKU + "price_per_million_tokens = [1, 5]\n"),
            "boundaries: unknown price_per_million_tokens",
        ),
        (_profile(boundaries=HAIKU + 'sandbox = "off"\n'), "unknown sandbox"),
        (
            _profile(roles='harness = "opencode"\nmodel = "a/b"\ntools = ["Read"]\n'),
            "roles: tools: OpenCode's tools are its own",
        ),
        (
            _profile(edges=HAIKU + 'tools = "Read"\n'),
            "edges: tools takes a list of tool names",
        ),
        (_profile(settings="edge_passes = 0\n"), "edge_passes takes 1 or more"),
        (_profile(settings="cut_rounds = -1\n"), "cut_rounds takes 0 or more"),
        (
            _profile(roles=HAIKU + 'reask_prompt_template = "edges-reask"\n'),
            "roles: unknown reask_prompt_template",
        ),
        (
            _profile(boundaries=HAIKU + 'review_prompt_template = "roles-review"\n'),
            "unknown review_prompt_template",
        ),
        (
            _profile(boundaries=HAIKU + 'prompt_template = "no-such"\n'),
            "no template 'no-such'",
        ),
        (
            _profile(boundaries=HAIKU + 'prompt_template = "groups-file"\n'),
            "prompt template 'groups-file' takes",
        ),
    ],
)
def test_a_profile_it_cannot_use_is_refused(tmp_path, text, message) -> None:
    path = _write(tmp_path, text)

    with pytest.raises(ProfileError) as refused:
        read_profiles(path)

    assert str(refused.value).startswith(f"{path}: profile 'p'")
    assert message in str(refused.value)


def test_a_stage_s_tools_are_its_table_s_else_its_default(tmp_path) -> None:
    """A table's `tools` are its sessions' alone; without one, the edges
    stage reads with the reading tools and a cut stage has Claude Code's
    own set."""
    path = _write(tmp_path, _profile(groups=HAIKU + 'tools = ["Read", "Edit"]\n'))

    found = read_profiles(path)["p"]

    assert found.agent(Stage.GROUPS).tools == ("Read", "Edit")
    assert found.agent(Stage.EDGES).tools == READING_TOOLS
    assert found.agent(Stage.ROLES).tools is None


def test_a_file_that_is_not_toml_is_refused(tmp_path) -> None:
    with pytest.raises(ProfileError, match="profiles.toml"):
        read_profiles(_write(tmp_path, "[p\n"))
    with pytest.raises(ProfileError, match="cannot read"):
        read_profiles(tmp_path / "missing.toml")


def test_a_profile_without_the_edges_table_is_refused_by_name(tmp_path) -> None:
    """A profiles file written before the edges stage names it."""
    path = tmp_path / "profiles.toml"
    text = "".join(
        f'[p.{table}]\nharness = "claude-code"\nmodel = "haiku"\n'
        for table in TABLES
        if table != "edges"
    )
    path.write_text("[p]\n" + text, encoding="utf-8")

    with pytest.raises(ProfileError, match=r"profile 'p': needs \[p\.edges\]$"):
        read_profiles(path)


def test_the_standard_edges_agent_is_the_probes_opus_at_high() -> None:
    standard = read_profiles()["standard"]

    assert standard.agent(Stage.EDGES) == Agent(
        Harness.CLAUDE_CODE, "claude-opus-5-5", "high", tools=READING_TOOLS
    )
    assert standard.templates.edges.name == "edges-file"


# --- the deciding step --------------------------------------------------


def test_a_profile_without_a_decide_table_gives_it_the_boundaries_agent(
    tmp_path,
) -> None:
    profile = read_profiles(_write(tmp_path, _profile(settings="decide_passes = 1\n")))[
        "p"
    ]

    assert profile.decide_passes == 1
    assert Stage.DECIDE not in profile.agents
    assert profile.agent(Stage.DECIDE) == profile.agent(Stage.BOUNDARIES)
    assert profile.templates.decide == Templates.shipped().decide


def test_a_decide_table_names_its_agent_and_template(tmp_path) -> None:
    (tmp_path / "decide.md").write_text(
        Templates.shipped().decide.text + "\nMine.\n", encoding="utf-8"
    )
    text = _profile() + '[p.decide]\nharness = "claude-code"\nmodel = "sonnet"\n'
    text += 'prompt_template = "decide.md"\n'

    profile = read_profiles(_write(tmp_path, text))["p"]

    assert profile.agent(Stage.DECIDE) == Agent(Harness.CLAUDE_CODE, "sonnet")
    assert profile.templates.decide.text.endswith("Mine.\n")
    assert profile.decide_passes == 0


def test_decide_passes_takes_0_or_1(tmp_path) -> None:
    path = _write(tmp_path, _profile(settings="decide_passes = 2\n"))

    with pytest.raises(ProfileError, match="decide_passes takes 0 or 1"):
        read_profiles(path)


def test_the_rules_flags_are_shown_to_the_edges_review_only_where_asked(
    tmp_path,
) -> None:
    shipped = read_profiles()
    asked = read_profiles(
        _write(tmp_path, _profile(settings="show_rule_flags_in_edge_review = true\n"))
    )["p"]

    assert not any(p.show_rule_flags_in_edge_review for p in shipped.values())
    assert asked.show_rule_flags_in_edge_review


def test_an_update_s_template_is_the_edges_table_s_own(tmp_path) -> None:
    """`update_prompt_template` under the edges' table, the shipped
    `edges-update` by default."""
    shipped = Templates.shipped()
    assert shipped.edges_update.name == "edges-update"
    assert read_profiles()["standard"].templates.edges_update == shipped.edges_update
    (tmp_path / "terse-update.md").write_text(
        shipped.edges_update.text + "\nBe brief.\n", encoding="utf-8"
    )
    path = _write(
        tmp_path,
        _profile("terse", edges=HAIKU + 'update_prompt_template = "terse-update.md"\n'),
    )

    templates = read_profiles(path)["terse"].templates

    assert templates.edges_update.name == "terse-update"
    assert templates.edges == shipped.edges
    with pytest.raises(ProfileError, match="roles: unknown update_prompt_template"):
        read_profiles(
            _write(
                tmp_path,
                _profile(roles=HAIKU + 'update_prompt_template = "edges-update"\n'),
            )
        )


def test_an_update_s_follow_up_and_switch_settings_are_read(tmp_path) -> None:
    """One follow-up session and the reverse prefilter on
    in every shipped profile, the switch unset; a file sets each."""
    for profile in read_profiles().values():
        assert (
            profile.followup_sessions_max,
            profile.reverse_prefilter,
            profile.fresh_edges_above_reopened_fraction,
        ) == (1, True, None)
    settings = (
        "followup_sessions_max = 0\nreverse_prefilter = false\n"
        "fresh_edges_above_reopened_fraction = 0.4\n"
    )
    mine = read_profiles(_write(tmp_path, _profile(settings=settings)))["p"]

    assert mine.followup_sessions_max == 0 and not mine.reverse_prefilter
    assert mine.fresh_edges_above_reopened_fraction == 0.4
    whole = read_profiles(
        _write(tmp_path, _profile(settings="fresh_edges_above_reopened_fraction = 1\n"))
    )["p"]
    assert whole.fresh_edges_above_reopened_fraction == 1.0


def test_standard_updates_in_one_session_below_1000_recut_blocks(tmp_path) -> None:
    """`standard` asks an update per stage only past 1000 re-cut
    blocks, in effect never; the other profiles leave it unset, every
    update per stage; a file sets it."""
    shipped = read_profiles()
    assert shipped["standard"].per_stage_update_above_recut_blocks == 1000
    assert {
        name: p.per_stage_update_above_recut_blocks
        for name, p in shipped.items()
        if name != "standard"
    } == {"cheap": None}
    settings = "per_stage_update_above_recut_blocks = 0\n"
    mine = read_profiles(_write(tmp_path, _profile(settings=settings)))["p"]
    assert mine.per_stage_update_above_recut_blocks == 0


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        (
            "per_stage_update_above_recut_blocks = -1\n",
            "per_stage_update_above_recut_blocks takes 0 or more",
        ),
        ("followup_sessions_max = 2\n", "followup_sessions_max takes 0 or 1"),
        ("followup_sessions_max = true\n", "followup_sessions_max: not True"),
        ('reverse_prefilter = "yes"\n', "reverse_prefilter: not 'yes'"),
        (
            "fresh_edges_above_reopened_fraction = 1.5\n",
            "fresh_edges_above_reopened_fraction takes 0 to 1",
        ),
    ],
)
def test_an_update_setting_out_of_range_is_refused(tmp_path, settings, message) -> None:
    with pytest.raises(ProfileError, match=message):
        read_profiles(_write(tmp_path, _profile(settings=settings)))


def test_a_review_seat_says_whether_it_is_asked_by_default(tmp_path) -> None:
    """`asked_by_default`, on a file session's seat and a queue's alike:
    true unless set; anything but true or false is refused."""
    path = tmp_path / "profiles.toml"
    standard = (
        resources.files("axiomation").joinpath("profiles.toml").read_text("utf-8")
    )
    body = standard.split("[cheap]")[0].replace("[standard", "[mine")
    for seat in ("argument", "citationcheck"):
        table = f"[mine.review.{seat}]\n"
        path.write_text(body.replace(table, table + "asked_by_default = false\n"))
        mine = read_profiles(path)["mine"]
        assert not mine.reviewers[session_named(seat)].asked_by_default
        assert mine.reviewers[Session.METHODS].asked_by_default
    table = "[mine.review.argument]\n"
    path.write_text(body.replace(table, table + 'asked_by_default = "no"\n'))
    with pytest.raises(ProfileError, match="asked_by_default: expected true or false"):
        read_profiles(path)


def test_a_review_session_takes_an_agent_and_a_template(tmp_path) -> None:
    """A profile's `review.<session>`: an agent, its template the
    session's shipped one unless a file beside the profiles names its own
    with the same placeholders; an unknown session is refused."""
    (tmp_path / "mine.md").write_text("Read the proofs.\n", encoding="utf-8")
    path = tmp_path / "profiles.toml"
    standard = (
        resources.files("axiomation").joinpath("profiles.toml").read_text("utf-8")
    )
    body = standard.split("[cheap]")[0].replace("[standard", "[mine")
    path.write_text(
        body.replace(
            "[mine.review.argument]\n",
            '[mine.review.argument]\nprompt_template = "mine.md"\n',
        ),
        encoding="utf-8",
    )
    mine = read_profiles(path)["mine"]
    assert mine.reviewers[Session.ARGUMENT].template.text == "Read the proofs.\n"

    path.write_text(body.replace("[mine.review.argument]", "[mine.review.hostile]"))
    with pytest.raises(ProfileError, match="no review session 'hostile'"):
        read_profiles(path)
    finder_elsewhere = body.replace(
        "[mine.review.argument]\n",
        '[mine.review.argument]\npage_finder_model = "haiku"\n',
    )
    path.write_text(finder_elsewhere)
    with pytest.raises(ProfileError, match="unknown page_finder_model"):
        read_profiles(path)
    # the contribution and relatedwork readers' briefs ask their forager,
    # and a subagent runs on Claude Code alone; the relatedwork reader takes
    # no searcher or page finder of its own
    path.write_text(body.replace('forager_model = "haiku"\n', ""))
    with pytest.raises(ProfileError, match="name forager_model"):
        read_profiles(path)
    related = "[mine.review.relatedwork]\n"
    assert related in body
    path.write_text(body.replace(related, related + 'searcher_model = "haiku"\n'))
    with pytest.raises(ProfileError, match="unknown searcher_model"):
        read_profiles(path)
    # the fieldresults session's finder is a step it cannot do without;
    # a subagent seat beside it is refused
    finder = "[mine.review.fieldresults.finder]\n"
    seat = body[body.index(finder) :]
    seat = seat[: seat.index("\n\n") + 2]
    path.write_text(body.replace(seat, ""))
    with pytest.raises(ProfileError, match="fieldresults.*name its finder's step"):
        read_profiles(path)
    reader = '[mine.review.fieldresults]\nharness = "claude-code"\n'
    path.write_text(body.replace(reader, reader + 'forager_model = "haiku"\n'))
    with pytest.raises(ProfileError, match="unknown forager_model"):
        read_profiles(path)
    on_opencode = body.replace(
        '[mine.review.citationcheck]\nharness = "claude-code"\n'
        'model = "claude-opus-5-5"',
        '[mine.review.citationcheck]\nharness = "opencode"\nmodel = "opencode/x"',
    )
    assert on_opencode != body
    path.write_text(on_opencode)
    with pytest.raises(ProfileError, match="a queue session runs on claude-code"):
        read_profiles(path)
    # a session by block runs on a tool server, which Claude Code alone
    # serves; the relatedwork reader's subagents run on Claude Code alone
    contribution = body.replace(
        '[mine.review.contribution]\nharness = "claude-code"\n'
        'model = "claude-opus-5-5"',
        '[mine.review.contribution]\nharness = "opencode"\nmodel = "opencode/x"',
    )
    assert contribution != body
    path.write_text(contribution)
    with pytest.raises(ProfileError, match="answers by block on a tool server"):
        read_profiles(path)
    relatedwork = body.replace(
        '[mine.review.relatedwork]\nharness = "claude-code"\nmodel = "claude-opus-5-5"',
        '[mine.review.relatedwork]\nharness = "opencode"\nmodel = "opencode/x"',
    )
    assert relatedwork != body
    path.write_text(relatedwork)
    with pytest.raises(ProfileError, match="answers by block on a tool server"):
        read_profiles(path)


def test_a_session_s_earlier_name_seats_it(tmp_path) -> None:
    """A profile naming `citationcheck` by its earlier name, `citation`,
    seats it as its own; one naming it by both is refused."""
    path = tmp_path / "profiles.toml"
    standard = (
        resources.files("axiomation").joinpath("profiles.toml").read_text("utf-8")
    )
    body = standard.split("[cheap]")[0].replace("[standard", "[mine")
    earlier = body.replace("[mine.review.citationcheck", "[mine.review.citation")
    assert earlier != body
    path.write_text(earlier, encoding="utf-8")
    seated = read_profiles(path)["mine"].reviewers[Session.CITATIONCHECK]
    assert seated.finder is not None
    reader = earlier.split("[mine.review.citation]")[1].split("\n\n")[0]
    both = earlier + "\n[mine.review.citationcheck]" + reader + "\n"
    path.write_text(both, encoding="utf-8")
    with pytest.raises(ProfileError, match="a second time"):
        read_profiles(path)


def test_the_citation_session_seats_a_reader_and_a_finder(tmp_path) -> None:
    """`review.citationcheck` is a queue's reader, with its `budget_share`,
    and `review.citationcheck.finder` the finder's step before it, each on Claude
    Code; without the finder table the reader finds its pages itself, and
    it takes no page-finder subagent."""
    path = tmp_path / "profiles.toml"
    standard = (
        resources.files("axiomation").joinpath("profiles.toml").read_text("utf-8")
    )
    body = standard.split("[cheap]")[0].replace("[standard", "[mine")
    finder = (
        '[mine.review.citationcheck.finder]\nharness = "claude-code"\n'
        'model = "claude-haiku-5-5"\neffort = "low"\nbudget_share = 1\n'
    )
    assert finder in body
    path.write_text(body.replace(finder, ""), encoding="utf-8")
    alone = read_profiles(path)["mine"].reviewers[Session.CITATIONCHECK]
    assert (alone.finder, alone.budget_share) == (None, 9.0)
    for wrong, said in (
        (finder + 'page_finder_model = "haiku"\n', "unknown page_finder_model"),
        (
            finder.replace('"claude-code"', '"opencode"').replace(
                '"claude-haiku-5-5"', '"opencode/x"'
            ),
            "claude-code alone",
        ),
        (finder.replace("budget_share = 1", "budget_share = 0"), "above 0"),
        (finder + 'tools = ["Read"]\n', "unknown tools"),
        (
            finder.replace(
                "[mine.review.citationcheck.finder]",
                'tools = ["Read"]\n\n[mine.review.citationcheck.finder]',
            ),
            "a queue session's tools are its server's",
        ),
    ):
        path.write_text(body.replace(finder, wrong), encoding="utf-8")
        with pytest.raises(ProfileError, match=said):
            read_profiles(path)
    reader = '[mine.review.citationcheck]\nharness = "claude-code"\n'
    path.write_text(
        body.replace(reader, reader + 'page_finder_model = "haiku"\n'),
        encoding="utf-8",
    )
    with pytest.raises(ProfileError, match="unknown page_finder_model"):
        read_profiles(path)


def test_the_support_search_takes_a_worker_and_a_settler() -> None:
    """Standard's search: Haiku's worker, Opus's settling step, each at
    effort low with its share of the budget, compacted at 100k; a profile
    without the table searches in one step on the edges' agent."""
    profiles = read_profiles()
    steps = profiles["standard"].search_steps()
    assert (steps.worker.agent.model, steps.worker.agent.effort) == (
        "claude-haiku-5-5",
        "low",
    )
    assert steps.settler is not None
    assert (steps.settler.agent.model, steps.settler.agent.effort) == (
        "claude-opus-5-5",
        "low",
    )
    assert (steps.worker.budget_share, steps.settler.budget_share) == (1.0, 4.0)
    assert steps.autocompact_tokens == 100_000
    cheap = profiles["cheap"].search_steps()
    assert cheap.worker.agent == profiles["cheap"].agent(Stage.EDGES)
    assert cheap.settler is None and cheap.autocompact_tokens == 100_000


@pytest.mark.parametrize(
    ("table", "message"),
    [
        (
            "[p.support_search]\nautocompact_tokens = 1\n",
            "needs [support_search.worker]",
        ),
        (
            '[p.support_search.worker]\nharness = "opencode"\n'
            'model = "opencode/deepseek-v4.1-flash"\n',
            "a queue session runs on claude-code alone",
        ),
        (
            '[p.support_search.worker]\nharness = "claude-code"\nmodel = "haiku"\n'
            "budget_share = 0\n",
            "budget_share takes a number above 0",
        ),
        (
            '[p.support_search.worker]\nharness = "claude-code"\nmodel = "haiku"\n'
            'prompt_template = "mine.md"\n',
            "unknown prompt_template",
        ),
        (
            "[p.support_search]\nautocompact_tokens = 0\n[p.support_search.worker]\n"
            'harness = "claude-code"\nmodel = "haiku"\n',
            "autocompact_tokens takes a whole number above 0",
        ),
        ("[p.support_search]\nsettle = true\n", "unknown settle"),
    ],
)
def test_a_support_search_table_it_cannot_use_is_refused(
    tmp_path, table, message
) -> None:
    path = _write(tmp_path, _profile("p") + table)
    with pytest.raises(ProfileError, match=re.escape(message)):
        read_profiles(path)


QUEUES_OFF = (
    "reread_marks_after_update = false\nsearch_support_after_edges = false\n"
    "ask_circles_after_edges = false\n"
)


@pytest.mark.parametrize(
    "setting",
    [
        "reread_marks_after_update",
        "search_support_after_edges",
        "ask_circles_after_edges",
    ],
)
def test_a_queue_at_a_run_s_end_on_opencode_edges_is_refused(tmp_path, setting) -> None:
    """The reread, the search and the circles are queue sessions on the
    edges' agent where the profile seats no other: on OpenCode, which serves
    none, a profile that leaves one on is refused when read."""
    opencode = 'harness = "opencode"\nmodel = "opencode/deepseek-v4.1-flash"\n'
    off = QUEUES_OFF.replace(f"{setting} = false\n", "")
    path = _write(tmp_path, _profile("p", off, edges=opencode))
    with pytest.raises(ProfileError, match=f"{setting}.*claude-code alone"):
        read_profiles(path)
    path = _write(tmp_path, _profile("p", QUEUES_OFF, edges=opencode))
    assert read_profiles(path)["p"].agent(Stage.EDGES).harness is Harness.OPENCODE


def test_an_update_of_one_session_on_opencode_edges_is_refused(tmp_path) -> None:
    """An update of one session is a queue on the edges' agent: on
    OpenCode, a profile that sets the cutover is refused when read; unset,
    every update asks per stage."""
    opencode = 'harness = "opencode"\nmodel = "opencode/deepseek-v4.1-flash"\n'
    one = QUEUES_OFF + "per_stage_update_above_recut_blocks = 10\n"
    path = _write(tmp_path, _profile("p", one, edges=opencode))
    with pytest.raises(
        ProfileError, match="per_stage_update_above_recut_blocks.*claude-code"
    ):
        read_profiles(path)
    path = _write(tmp_path, _profile("p", QUEUES_OFF, edges=opencode))
    assert read_profiles(path)["p"].per_stage_update_above_recut_blocks is None
