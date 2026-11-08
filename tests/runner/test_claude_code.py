"""The Claude Code adapter, against a fake `claude`.

The fake is a script run by this Python: it logs what it was given and
where, edits `cut.md` as told, and prints stream-json events in Claude
Code's shape. `claude` itself is never run.
"""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path

import pytest

from axiomation.questions import (
    QueueSession,
    ToolServer,
    Workspace,
    gates,
    load_template,
)
from axiomation.questions.groups_file import (
    GROUPS_FILE,
    SESSION_TEMPLATE,
    session_questions,
)
from axiomation.runner import claude_code
from axiomation.runner.claude_code import (
    CLAUDE_TMP_BYTES,
    GATE_SECONDS,
    READING_TOOLS,
    SERVED_CACHE_TTL,
    SERVER_START_SECONDS,
    TMP,
    ClaudeCodeHost,
    attempt_tmp,
    closed_paths,
    inherited_effort,
    parse,
)
from axiomation.runner.hosts import (
    HostBreach,
    HostOutOfCredit,
    HostOverBudget,
    HostUncounted,
)
from axiomation.runner.opencode import HostSetupError
from questions.review_paper import make_paper
from runner.fakes import FAKE_CLAUDE_ENV, fake_claude

WORKSPACE = Workspace(
    files={"cut.md": "⟦b1 none⟧ — …\nOne sentence. Another.\n", "outline.md": "# O\n"},
    edit="cut.md",
    instructions="# The brief\n",
)


@pytest.fixture
def setup(tmp_path: Path, monkeypatch, host_scratch):
    executable = fake_claude(tmp_path / "bin")
    scratch = host_scratch
    projects = tmp_path / "projects"
    log = tmp_path / "log.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    for name in FAKE_CLAUDE_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FAKE_CLAUDE_PROJECTS", str(projects))
    host = ClaudeCodeHost(
        model="haiku", executable=str(executable), scratch=scratch, projects=projects
    )

    def calls() -> list[dict]:
        return [json.loads(line) for line in log.read_text().splitlines()]

    return host, calls, scratch, projects


def test_a_session_edits_its_file_and_answers_with_it(setup, monkeypatch) -> None:
    host, calls, _, _ = setup
    monkeypatch.setenv("FAKE_CLAUDE_EDIT", "Another.|||\n⟦new⟧\nAnother.")

    answer = host.ask("Correct the cut.", 60, WORKSPACE)

    assert answer.text == "⟦b1 none⟧ — …\nOne sentence. \n⟦new⟧\nAnother.\n"
    assert answer.cost == pytest.approx(0.01)
    assert answer.tokens == {
        "input": 10,
        "output": 200,
        "reasoning": 50,
        "cache_read": 3000,
        "cache_write": 500,
    }
    assert answer.error == "" and not answer.failed
    assert [use["tool"] for use in answer.tool_uses] == ["Edit"]
    (call,) = calls()
    assert call["stdin"] == "Correct the cut."
    assert call["listing"] == ["cut.md", "outline.md"]
    assert call["brief"] == "# The brief\n"
    assert Path(call["cwd"]).name == "work" and call["pwd"] == call["cwd"]


def test_the_command_confines_the_session(setup, monkeypatch) -> None:
    host, calls, _, _ = setup
    monkeypatch.delenv("CLAUDE_CODE_PROMPT_CACHE_TTL", raising=False)

    host.ask("Correct the cut.", 60, WORKSPACE)

    argv = calls()[0]["argv"]
    assert argv[:3] == ["-p", "--model", "haiku"]
    assert argv[argv.index("--setting-sources") + 1] == ""
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    at = argv.index("--disallowedTools")
    assert argv[at + 1 : at + 3] == ["WebFetch", "WebSearch"]
    assert "--tools" not in argv and "--restricted" not in argv
    for flag in ("--no-session-persistence", "--strict-mcp-config"):
        assert flag in argv
    work = Path(calls()[0]["cwd"])
    brief = Path(argv[argv.index("--append-system-prompt-file") + 1])
    assert brief.parent == work.parent  # beside, not in
    assert Path(argv[argv.index("--settings") + 1]).parent == work.parent
    assert calls()[0]["cache_ttl"] is None  # Claude Code's own choice


def test_a_reading_session_has_its_tools_alone(setup, monkeypatch) -> None:
    given, calls, scratch, projects = setup
    host = ClaudeCodeHost(
        model="haiku",
        executable=given.executable,
        scratch=scratch,
        projects=projects,
        tools=READING_TOOLS,
    )

    host.ask("Read the proof.", 60, WORKSPACE)

    argv = calls()[0]["argv"]
    assert argv[argv.index("--tools") + 1] == "Bash,Read,Edit,Write,Grep,Glob"
    assert "--exclude-dynamic-system-prompt-sections" in argv
    assert "--disallowedTools" in argv  # the web stays refused besides
    tool = {"name": "Task", "input": {"prompt": "read it for me"}, "refused": False}
    monkeypatch.setenv("FAKE_CLAUDE_TOOL", json.dumps(tool))
    with pytest.raises(HostBreach, match="Task ran, though it was not given"):
        host.ask("Read the proof.", 60, WORKSPACE)


def test_a_review_by_block_is_served_its_check_and_has_its_brief_alone(
    setup, outside_tmp
) -> None:
    """A review's brief is its whole system prompt; a session that answers
    by block has the tools its workspace names (no Bash where it computes
    nothing), and a tool server serves its check: the server's settings
    (the gate's spec, the session's directory) lie under the store's
    `sessions/` while it runs, the store is closed to the session, and
    nothing is let out of the sandbox."""
    from dataclasses import replace

    from axiomation.questions import REVIEW_TOOLS, Gate, SessionTools

    given, calls, scratch, projects = setup
    store = outside_tmp / "store"
    host = ClaudeCodeHost(
        model="haiku",
        executable=given.executable,
        scratch=scratch,
        projects=projects,
        tools=READING_TOOLS,
    )
    review = replace(
        WORKSPACE,
        gate=Gate(module="json.tool", spec='{"a": 1}'),
        tools=REVIEW_TOOLS,
        brief_alone=True,
        served=SessionTools(str(store)),
    )

    host.ask("Review.", 60, review)
    host.ask("Read the proof.", 60, WORKSPACE)

    by_block, plain = calls()
    argv = by_block["argv"]
    assert by_block["brief_flag"] == "--system-prompt-file"
    assert argv[argv.index("--tools") + 1] == "Read,Grep,Edit,Write"
    assert by_block["excluded"] == [] and "mcp__q" in by_block["allowed"]
    assert str(store) in by_block["closed"] and str(store) not in plain["closed"]
    (server,) = by_block["mcp"]["mcpServers"].values()
    assert server["args"][:3] == ["-I", "-m", "axiomation.queue.server"]
    assert Path(server["args"][3]).parent == (store / "sessions").resolve()
    (settings,) = by_block["served"]
    assert settings["review"]["spec"] == '{"a": 1}'
    assert Path(settings["review"]["work"]) == Path(by_block["cwd"]).resolve()
    assert settings["review"]["reads"] is None
    assert not list((store / "sessions").iterdir())
    assert plain["brief_flag"] == "--append-system-prompt-file"
    assert "mcp" not in plain and plain["excluded"] == []
    # its calls come seconds apart: the five-minute cache, not the host's own
    assert by_block["cache_ttl"] == SERVED_CACHE_TTL and plain["cache_ttl"] is None


def test_a_served_session_s_tools_are_given_and_bash_is_not(tmp_path) -> None:
    """The breach check takes its tool server's tools and its own, and
    still calls Bash a breach in a review by block given none (the
    contribution session's); not in one that computes."""
    from axiomation.questions import REVIEW_COMPUTE_TOOLS, REVIEW_TOOLS
    from axiomation.runner.claude_code import _breach

    def ran(tool: str) -> tuple[dict[str, object], ...]:
        return ({"tool": tool, "input": {}, "status": "completed"},)

    served = ran("mcp__q__check")
    assert _breach(served, tmp_path, WORKSPACE, [], REVIEW_TOOLS, "q") == ""
    assert "not given" in _breach(served, tmp_path, WORKSPACE, [], REVIEW_TOOLS)
    assert "Bash ran" in _breach(
        ran("Bash"), tmp_path, WORKSPACE, [], REVIEW_TOOLS, "q"
    )
    assert (
        _breach(ran("Bash"), tmp_path, WORKSPACE, [], REVIEW_COMPUTE_TOOLS, "q") == ""
    )


def test_a_review_by_block_that_computes_has_bash_in_its_sandbox(
    setup, outside_tmp
) -> None:
    """The argument and findings sessions compute in Bash: it is given,
    nothing is let out of the sandbox, and their store stays closed."""
    from dataclasses import replace

    from axiomation.questions import REVIEW_COMPUTE_TOOLS, Gate, SessionTools

    given, calls, scratch, projects = setup
    store = outside_tmp / "store"
    host = ClaudeCodeHost(
        model="haiku",
        executable=given.executable,
        scratch=scratch,
        projects=projects,
        tools=READING_TOOLS,
    )
    host.ask(
        "Review.",
        60,
        replace(
            WORKSPACE,
            gate=Gate(module="json.tool", spec='{"a": 1}'),
            tools=REVIEW_COMPUTE_TOOLS,
            brief_alone=True,
            served=SessionTools(str(store)),
        ),
    )
    (asked,) = calls()
    argv = asked["argv"]
    assert argv[argv.index("--tools") + 1] == "Bash,Read,Grep,Edit,Write"
    assert asked["excluded"] == [] and str(store) in asked["closed"]


def test_a_store_where_a_session_writes_takes_no_tool_server(setup) -> None:
    """A tool server's settings name the spec the check reads: a store in
    the temporary folder the session writes is refused before it runs."""
    from dataclasses import replace

    from axiomation.questions import Gate, SessionTools
    from axiomation.runner.claude_code import TMP
    from axiomation.runner.opencode import HostSetupError

    given, _, scratch, projects = setup
    host = ClaudeCodeHost(
        model="haiku", executable=given.executable, scratch=scratch, projects=projects
    )
    review = replace(
        WORKSPACE,
        gate=Gate(module="json.tool", spec="{}"),
        served=SessionTools(str(TMP / "a-store")),
    )
    with pytest.raises(HostSetupError, match="lies where a session writes"):
        host.ask("Review.", 60, review)


def test_an_answer_file_made_a_link_is_not_read(setup, monkeypatch, tmp_path) -> None:
    """A session may make its file a link to one its sandbox closes (an
    answer key): the host reads through none, and calls it a breach."""
    given, calls, scratch, projects = setup
    closed = tmp_path / "key.md"
    closed.write_text("the key's lines")
    monkeypatch.setenv("FAKE_CLAUDE_LINK", f"{WORKSPACE.edit}|||{closed}")
    host = ClaudeCodeHost(
        model="haiku", executable=given.executable, scratch=scratch, projects=projects
    )

    with pytest.raises(HostBreach, match="answer file is no file") as raised:
        host.ask("Correct the cut.", 60, WORKSPACE)
    assert raised.value.answer.text == ""


def test_a_workspace_s_own_tools_hold_where_the_profile_names_none(
    setup, monkeypatch
) -> None:
    """A session that answers as lines names its tools; a host with Claude
    Code's own set (a cut stage's default) gives it those alone."""
    from dataclasses import replace

    given, calls, scratch, projects = setup
    host = ClaudeCodeHost(
        model="haiku", executable=given.executable, scratch=scratch, projects=projects
    )
    lines = replace(WORKSPACE, tools=("Bash", "Read", "Edit", "Write"))

    host.ask("Write the lines.", 60, lines)
    host.ask("Read the proof.", 60, WORKSPACE)

    named, own = calls()
    assert named["argv"][named["argv"].index("--tools") + 1] == "Bash,Read,Edit,Write"
    assert "--tools" not in own["argv"]
    tool = {"name": "Grep", "input": {"pattern": "x"}, "refused": False}
    monkeypatch.setenv("FAKE_CLAUDE_TOOL", json.dumps(tool))
    with pytest.raises(HostBreach, match="Grep ran, though it was not given"):
        host.ask("Write the lines.", 60, lines)


def test_a_session_hands_a_task_only_to_the_subagent_its_workspace_defines(
    setup, monkeypatch
) -> None:
    """A workspace's helper reaches Claude Code as
    `--agents`, on its own model and tools, with the tool that asks it;
    that tool's use is then no breach. Without a helper it stays one."""
    from dataclasses import replace

    from axiomation.questions import Helper

    given, calls, scratch, projects = setup
    host = ClaudeCodeHost(
        model="haiku",
        executable=given.executable,
        scratch=scratch,
        projects=projects,
        tools=READING_TOOLS,
    )
    finder = Helper(
        "page-finder", "Finds pages.", "Point, never judge.", "haiku", ("Grep",)
    )
    helped = replace(WORKSPACE, helpers=(finder,))
    tool = {
        "name": "Agent",
        "input": {"subagent_type": "page-finder"},
        "refused": False,
    }
    monkeypatch.setenv("FAKE_CLAUDE_TOOL", json.dumps(tool))

    host.ask("Read the citations.", 60, helped)

    argv = calls()[0]["argv"]
    assert argv[argv.index("--tools") + 1] == "Bash,Read,Edit,Write,Grep,Glob,Agent"
    assert json.loads(argv[argv.index("--agents") + 1]) == {
        "page-finder": {
            "description": "Finds pages.",
            "prompt": "Point, never judge.",
            "model": "haiku",
            "tools": ["Grep"],
        }
    }
    with pytest.raises(HostBreach, match="Agent ran, though it was not given"):
        host.ask("Read the citations.", 60, WORKSPACE)
    assert "--agents" not in calls()[-1]["argv"]


def test_web_search_is_given_only_through_a_subagent_given_it(
    setup, monkeypatch
) -> None:
    """A helper with web search takes the
    tool off the disallowed list, into the tools and the permissions; its
    use is then no breach. Without such a helper it stays one, and web
    fetch is never given."""
    from dataclasses import replace

    from axiomation.questions import Helper

    given, calls, scratch, projects = setup
    host = ClaudeCodeHost(
        model="haiku",
        executable=given.executable,
        scratch=scratch,
        projects=projects,
        tools=READING_TOOLS,
    )
    searcher = Helper("searcher", "Searches.", "Point.", "haiku", ("WebSearch",))
    finder = Helper("page-finder", "Finds.", "Point.", "haiku", ("Grep",))
    searching = replace(WORKSPACE, helpers=(searcher,))
    tool = {"name": "WebSearch", "input": {"query": "q"}, "refused": False}
    monkeypatch.setenv("FAKE_CLAUDE_TOOL", json.dumps(tool))

    host.ask("Search.", 60, searching)

    argv = calls()[0]["argv"]
    assert argv[argv.index("--tools") + 1].endswith(",Agent,WebSearch")
    disallowed = argv[argv.index("--disallowedTools") + 1 : argv.index("--settings")]
    assert disallowed == ["WebFetch"]
    with pytest.raises(HostBreach, match="WebSearch ran, though it was not given"):
        host.ask("Search.", 60, replace(WORKSPACE, helpers=(finder,)))
    assert (
        "WebSearch" not in calls()[-1]["argv"][calls()[-1]["argv"].index("--tools") + 1]
    )
    settings = ClaudeCodeHost.settings(Path("/w"), Path("/t"), web=("WebSearch",))
    assert "WebSearch" in settings["permissions"]["allow"]
    assert (
        "WebSearch"
        not in ClaudeCodeHost.settings(Path("/w"), Path("/t"))["permissions"]["allow"]
    )


def test_the_sandbox_closes_the_home_and_claude_s_scratch(tmp_path) -> None:
    from axiomation.runner.claude_code import TMP, ClaudeCodeHost

    work = TMP / "axiomation-host" / "x" / "work"
    attempt = TMP / "ax-attempt"
    settings = ClaudeCodeHost.settings(work, attempt)

    sandbox = settings["sandbox"]
    assert sandbox["enabled"] and sandbox["failIfUnavailable"]
    assert not sandbox["allowUnsandboxedCommands"]
    assert str(Path.home()) in sandbox["filesystem"]["denyRead"]
    assert sandbox["filesystem"]["allowWrite"] == [str(work), str(attempt)]
    assert sandbox["network"] == {"allowedDomains": [], "strictAllowlist": True}
    assert f"Read(/{Path.home()}/**)" in settings["permissions"]["deny"]
    assert "Bash" in settings["permissions"]["allow"]


def test_the_sandbox_writes_no_host_file_beside_the_session(setup) -> None:
    """The session writes its own folder and its attempt's temporary
    folder alone: never the temporary directory whole, where the host's
    settings, the gate's spec and the tool server's command lie beside
    its folder, which the hooks and the server run outside the sandbox
    on."""
    from axiomation.runner.claude_code import TMP

    host, calls, _, _ = setup
    host.ask("Correct the cut.", 60, WORKSPACE)
    (call,) = calls()
    root, attempt = Path(call["root"]), Path(call["claude_tmp"])

    assert call["written"] == [str(root / "work"), str(attempt)]
    assert str(TMP) not in call["written"]
    assert attempt.parent == TMP and attempt.name.startswith("ax-")
    assert not root.is_relative_to(attempt)
    tmp_rules = [
        rule
        for rule in call["allowed"]
        if rule.startswith(("Read(/", "Edit(/", "Write(/"))
    ]
    assert tmp_rules and all(attempt.name in rule for rule in tmp_rules)


def test_a_question_without_a_workspace_has_no_tools(setup) -> None:
    host, calls, _, _ = setup

    answer = host.ask("Answer in JSON.", 60)

    assert answer.text == "Done."
    argv = calls()[0]["argv"]
    assert argv[argv.index("--tools") + 1] == ""
    assert "--allowedTools" not in argv and calls()[0]["listing"] == []


def test_every_attempt_s_directory_and_project_entry_go(setup) -> None:
    host, _, scratch, projects = setup

    host.ask("Correct the cut.", 60, WORKSPACE)

    assert list(scratch.iterdir()) == []
    assert list(projects.iterdir()) == []


def test_no_cost_is_an_uncounted_attempt(setup, monkeypatch) -> None:
    host, _, _, _ = setup
    monkeypatch.setenv("FAKE_CLAUDE_NO_COST", "1")

    with pytest.raises(HostUncounted):
        host.ask("Correct the cut.", 60, WORKSPACE)


def test_a_usage_limit_is_out_of_credit(setup, monkeypatch) -> None:
    host, _, _, _ = setup
    monkeypatch.setenv("FAKE_CLAUDE_ERROR", "Claude AI usage limit reached")
    monkeypatch.setenv("FAKE_CLAUDE_EXIT", "1")

    with pytest.raises(HostOutOfCredit):
        host.ask("Correct the cut.", 60, WORKSPACE)


def test_a_session_at_its_limit_is_over_budget(setup, monkeypatch) -> None:
    """The limit goes to Claude Code as `--max-budget-usd`; a session that
    reaches it raises `HostOverBudget`, its answer and cost kept."""
    host, calls, _, _ = setup
    monkeypatch.setenv("FAKE_CLAUDE_ERROR", "spent")
    monkeypatch.setenv("FAKE_CLAUDE_SUBTYPE", "error_max_budget_usd")

    with pytest.raises(HostOverBudget) as stopped:
        host.ask("Correct the cut.", 60, WORKSPACE, spend_max=0.25)

    argv = calls()[0]["argv"]
    assert argv[argv.index("--max-budget-usd") + 1] == "0.2500"
    assert stopped.value.answer.cost == 0.01
    monkeypatch.delenv("FAKE_CLAUDE_ERROR")
    monkeypatch.delenv("FAKE_CLAUDE_SUBTYPE")
    host.ask("Correct the cut.", 60, WORKSPACE)
    assert "--max-budget-usd" not in calls()[1]["argv"]


def test_an_error_result_fails_the_attempt(setup, monkeypatch) -> None:
    host, _, _, _ = setup
    monkeypatch.setenv("FAKE_CLAUDE_ERROR", "the model stopped")

    answer = host.ask("Correct the cut.", 60, WORKSPACE)

    assert answer.failed
    assert "error_during_execution: the model stopped" in answer.error


def test_a_timeout_fails_the_attempt(setup, monkeypatch) -> None:
    host, _, _, _ = setup
    monkeypatch.setenv("FAKE_CLAUDE_SLEEP", "30")

    with pytest.raises(HostUncounted) as raised:
        host.ask("Correct the cut.", 1, WORKSPACE)

    assert "timed out after 1 s" in raised.value.answer.error


@pytest.mark.parametrize(
    ("tool", "why"),
    [
        ({"name": "WebFetch", "input": {"url": "x"}, "refused": False}, "not given"),
        (
            {"name": "Read", "input": {"file_path": "/etc/passwd"}, "refused": False},
            "outside",
        ),
        (
            {
                "name": "Read",
                "input": {"file_path": str(Path.home() / "keys")},
                "refused": False,
            },
            "outside",
        ),
    ],
)
def test_a_breach_is_raised(setup, monkeypatch, tool, why) -> None:
    host, _, _, _ = setup
    monkeypatch.setenv("FAKE_CLAUDE_TOOL", json.dumps(tool))

    with pytest.raises(HostBreach, match=why):
        host.ask("Correct the cut.", 60, WORKSPACE)


def test_a_refused_use_is_no_breach(setup, monkeypatch) -> None:
    host, _, _, _ = setup
    tool = {"name": "Read", "input": {"file_path": "/etc/passwd"}}
    monkeypatch.setenv("FAKE_CLAUDE_TOOL", json.dumps(tool))

    answer = host.ask("Correct the cut.", 60, WORKSPACE)

    assert answer.tool_uses[-1]["status"] == "refused"


@pytest.mark.parametrize(
    "tool",
    [
        {"name": "Bash", "input": {"command": "python3 edit.py"}, "refused": False},
        {"name": "Write", "input": {"file_path": "notes.md"}, "refused": False},
        {"name": "Write", "input": {"file_path": "/tmp/cut.orig.md"}, "refused": False},
    ],
)
def test_the_session_s_own_tools_and_tmp_are_no_breach(setup, monkeypatch, tool):
    host, _, _, _ = setup
    monkeypatch.setenv("FAKE_CLAUDE_TOOL", json.dumps(tool))

    answer = host.ask("Correct the cut.", 60, WORKSPACE)

    assert answer.tool_uses[-1]["status"] == "completed"


@pytest.fixture
def claudes_tmp(setup, monkeypatch) -> Path:
    """Claude's directory under /tmp, a stand-in under the test's scratch,
    holding one other session's scratch space from before the attempt."""
    directory = setup[2] / "claude-tmp"
    (directory / "other-session").mkdir(parents=True)
    (directory / "other-session" / "key.md").write_text("a key's quote\n")
    monkeypatch.setattr("axiomation.runner.claude_code.CLAUDE_TMP", directory)
    return directory


def test_claude_code_keeps_its_files_in_the_attempt_s_own_directory(
    setup, monkeypatch, claudes_tmp
) -> None:
    """Claude Code is given a short directory of the attempt's own for its
    temporary files, its Bash's `TMPDIR` among them: a file the session
    writes there goes with the attempt."""
    host, calls, _, _ = setup
    monkeypatch.setenv("FAKE_CLAUDE_SCRATCH", "merge.py")

    answer = host.ask("Correct the cut.", 60, WORKSPACE)

    given = Path(calls()[0]["claude_tmp"])
    assert given.parent == TMP and given.name.startswith("ax-")
    assert len(str(given / f"claude-{os.getuid()}").encode()) <= CLAUDE_TMP_BYTES
    assert not given.exists()
    assert not answer.failed


def test_a_claude_code_that_keeps_its_files_in_claude_s_tmp_is_refused(
    setup, monkeypatch, claudes_tmp
) -> None:
    """A Claude Code that no longer reads the variable keeps its folder in
    Claude's directory under /tmp, closed, where its Bash is refused:
    refused loudly, the folder removed."""
    host, _, _, _ = setup
    monkeypatch.setenv("FAKE_CLAUDE_IGNORES_TMPDIR", str(claudes_tmp))

    with pytest.raises(HostSetupError, match="no longer reads CLAUDE_CODE_TMPDIR"):
        host.ask("Correct the cut.", 60, WORKSPACE)
    assert [p.name for p in claudes_tmp.iterdir()] == ["other-session"]


def test_a_scratch_space_there_at_the_start_is_closed(
    setup, monkeypatch, claudes_tmp
) -> None:
    host, _, _, _ = setup
    key = claudes_tmp / "other-session" / "key.md"
    tool = {"name": "Read", "input": {"file_path": str(key)}, "refused": False}
    monkeypatch.setenv("FAKE_CLAUDE_TOOL", json.dumps(tool))

    with pytest.raises(HostBreach, match="outside"):
        host.ask("Correct the cut.", 60, WORKSPACE)


def test_a_scratch_space_another_session_makes_during_the_attempt_is_closed(
    setup, monkeypatch, claudes_tmp
) -> None:
    """Claude's directory under /tmp is closed whole, so an entry made
    there after the attempt started is closed too."""
    host, _, _, _ = setup
    later = claudes_tmp / "later-session" / "key.md"
    tool = {"name": "Read", "input": {"file_path": str(later)}, "refused": False}
    monkeypatch.setenv("FAKE_CLAUDE_TOOL", json.dumps(tool))
    monkeypatch.setenv("FAKE_CLAUDE_PLANT", f"{later}|||a later key's quote\n")

    with pytest.raises(HostBreach, match="outside"):
        host.ask("Correct the cut.", 60, WORKSPACE)
    assert later.exists()


def test_the_settings_close_claude_s_tmp_whole(claudes_tmp) -> None:
    settings = ClaudeCodeHost.settings(
        TMP / "x" / "work", TMP / "ax-attempt", closed=closed_paths()
    )

    denied = settings["sandbox"]["filesystem"]["denyRead"]
    assert str(claudes_tmp) in denied
    assert f"Read(/{claudes_tmp}/**)" in settings["permissions"]["deny"]


def test_a_directory_too_long_for_claude_code_is_refused(
    host_scratch, monkeypatch
) -> None:
    """Past `CLAUDE_TMP_BYTES` Claude Code would give its Bash Claude's
    directory under /tmp again: refused, the directory made removed."""
    long = host_scratch / ("t" * CLAUDE_TMP_BYTES)
    long.mkdir()
    monkeypatch.setattr("axiomation.runner.claude_code.TMP", long)

    with pytest.raises(HostSetupError, match="passes 44 bytes"), attempt_tmp():
        pass
    assert list(long.iterdir()) == []


def test_a_scratch_the_session_may_not_read_is_refused(setup, tmp_path) -> None:
    host, _, _, _ = setup

    with pytest.raises(HostSetupError, match="may not read"):
        ClaudeCodeHost(executable=host.executable, scratch=Path.home() / "x").check()


def test_check_reads_the_version_and_refuses_a_scratch_in_git(setup, tmp_path) -> None:
    host, _, _, _ = setup

    host.check()
    assert host.name == "claude-code 2.1.285"

    tree = setup[2] / "tree"
    (tree / ".git").mkdir(parents=True)
    inside = ClaudeCodeHost(executable=host.executable, scratch=tree)
    with pytest.raises(HostSetupError, match="inside a Git tree"):
        inside.check()


def test_parse_takes_the_result_and_each_tool_s_fate() -> None:
    transcript = "\n".join(
        json.dumps(event)
        for event in [
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "a", "name": "Read", "input": {}},
                {"type": "tool_use", "id": "b", "name": "Edit", "input": {}},
            ]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "a"},
                {"type": "tool_result", "tool_use_id": "b", "is_error": True},
            ]}},
            {"type": "result", "result": "done", "total_cost_usd": 0.5},
        ]
    )  # fmt: skip

    found = parse(transcript + "\nnot json\n")

    assert found.text == "done" and found.cost == 0.5
    assert [use["status"] for use in found.tool_uses] == ["completed", "refused"]


def test_the_tokens_are_the_session_s_over_every_model_and_wake() -> None:
    """A session woken again (a fork's end) writes a `result` each time;
    the last one's `modelUsage` holds the whole session per model, its
    `usage` only the last turn."""
    last = {
        "type": "result",
        "result": "done",
        "total_cost_usd": 8.8,
        "usage": {"input_tokens": 18, "cache_read_input_tokens": 723_519},
        "modelUsage": {
            "claude-opus-5-5": {
                "inputTokens": 264,
                "outputTokens": 133_300,
                "thinkingTokens": 81_083,
                "cacheReadInputTokens": 12_817_185,
                "cacheCreationInputTokens": 664_362,
                "cacheCreationEphemeral1hInputTokens": 85_241,
                "costUSD": 8.7,
            },
            "claude-haiku-5-5": {"inputTokens": 6, "outputTokens": 40, "costUSD": 0.1},
        },
    }
    first = {**last, "total_cost_usd": 2.0, "modelUsage": {}}
    transcript = "\n".join(json.dumps(e) for e in (first, last))

    found = parse(transcript)

    assert found.cost == 8.8
    assert found.tokens == {
        "input": 270,
        "output": 133_340,
        "reasoning": 81_083,
        "cache_read": 12_817_185,
        "cache_write": 664_362,
        "cache_write_1h": 85_241,
    }
    assert found.tokens_by_model["claude-haiku-5-5"] == {"input": 6, "output": 40}


def test_a_session_ended_at_its_limit_is_over_budget() -> None:
    event = {
        "type": "result",
        "subtype": "error_max_budget_usd",
        "is_error": True,
        "total_cost_usd": 0.5,
    }
    found = parse(json.dumps(event))
    assert found.over_budget and found.cost == 0.5
    assert not parse(json.dumps({**event, "subtype": "success"})).over_budget


def test_without_model_usage_the_tokens_are_the_last_usage_s() -> None:
    event = {
        "type": "result",
        "result": "done",
        "total_cost_usd": 0.1,
        "usage": {
            "input_tokens": 5,
            "output_tokens": 9,
            "cache_creation_input_tokens": 30,
            "cache_creation": {"ephemeral_1h_input_tokens": 20},
        },
    }

    found = parse(json.dumps(event))

    assert found.tokens == {
        "input": 5,
        "output": 9,
        "reasoning": 0,
        "cache_read": 0,
        "cache_write": 30,
        "cache_write_1h": 20,
    }
    assert found.tokens_by_model == {}


# --- the effort level ------------------------------------------------------


def test_no_effort_is_passed_unless_one_is_set(setup) -> None:
    host, calls, _, _ = setup

    host.ask("Correct the cut.", 60, WORKSPACE)

    assert "--effort" not in calls()[0]["argv"] and host.effort is None


def test_an_effort_level_is_passed_to_claude(setup) -> None:
    host, calls, _, _ = setup
    host.effort = "high"

    host.ask("Correct the cut.", 60, WORKSPACE)
    host.ask("A question.", 60)

    for call in calls():
        argv = call["argv"]
        assert argv[argv.index("--effort") + 1] == "high"


def _settings(directory: Path, **values) -> Path:
    (directory / "settings.json").write_text(json.dumps(values), encoding="utf-8")
    return directory


def test_the_inherited_effort_is_the_model_s_own_then_the_global(tmp_path) -> None:
    config = _settings(
        tmp_path,
        effortLevel="xhigh",
        modelSettings={"claude-opus-5-5": {"effortLevel": "high"}},
    )

    assert inherited_effort("claude-opus-5-5", config) == "high"
    assert inherited_effort("sonnet", config) == "xhigh"


def test_no_setting_inherits_no_effort(tmp_path) -> None:
    assert inherited_effort("haiku", tmp_path) is None
    assert inherited_effort("haiku", _settings(tmp_path, model="opus")) is None


def test_the_inherited_effort_reads_claude_s_config_directory(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(_settings(tmp_path, effortLevel="low")))

    assert inherited_effort("haiku") == "low"


@pytest.mark.parametrize("text", ["{not json", '{"effortLevel": "huge"}'])
def test_a_bad_setting_is_an_error(tmp_path, text) -> None:
    (tmp_path / "settings.json").write_text(text, encoding="utf-8")

    with pytest.raises(HostSetupError):
        inherited_effort("haiku", tmp_path)


# --- the gate -------------------------------------------------------------


def test_only_a_gated_workspace_gets_a_stop_hook() -> None:
    from axiomation.runner.claude_code import TMP

    work = TMP / "axiomation-host" / "x" / "work"

    tmp = TMP / "ax-attempt"
    assert "hooks" not in ClaudeCodeHost.settings(work, tmp)
    (entry,) = ClaudeCodeHost.settings(work, tmp, "check it")["hooks"]["Stop"]
    assert entry["hooks"] == [
        {"type": "command", "command": "check it", "timeout": GATE_SECONDS}
    ]


def test_the_gate_runs_until_the_file_reads(setup, monkeypatch, tmp_path) -> None:
    """The session renames the proof's end line; the
    gate, run as Claude Code runs a Stop hook, blocks with the problem, and
    the session's fix lets it end."""
    host, calls, _, _ = setup
    paper = make_paper(tmp_path / "paper")
    (question,) = session_questions(
        paper.document, paper.cut, None, load_template(SESSION_TEMPLATE)
    )
    stops = tmp_path / "stops.json"
    monkeypatch.setenv("FAKE_CLAUDE_STOPS", str(stops))
    monkeypatch.setenv(
        "FAKE_CLAUDE_EDIT", "⟦end group by_definition_the_gain⟧|||⟦end group the_proof⟧"
    )
    monkeypatch.setenv(
        "FAKE_CLAUDE_FIX", "⟦end group the_proof⟧|||⟦end group by_definition_the_gain⟧"
    )

    answer = host.ask(question.prompt, 60, question.workspace)

    first, second = json.loads(stops.read_text())
    decision = json.loads(first["stdout"])
    assert decision["decision"] == "block"
    assert "⟦end group the_proof⟧ ends no group" in decision["reason"]
    assert second == {"stdout": "", "stderr": "", "code": 0}
    assert answer.text == question.workspace.files[GROUPS_FILE]
    # The session's directory holds its file alone: no spec, no code.
    assert calls()[0]["listing"] == [GROUPS_FILE]


def test_the_gate_imports_nothing_the_session_writes(setup, monkeypatch, tmp_path):
    """A module the session plants in its directory is not what the gate
    runs: the hook's Python is isolated (`-I`), so its working directory,
    the session's, is not on the import path."""
    host, calls, _, _ = setup
    paper = make_paper(tmp_path / "paper")
    (question,) = session_questions(
        paper.document, paper.cut, None, load_template(SESSION_TEMPLATE)
    )
    stops = tmp_path / "stops.json"
    planted = tmp_path / "planted"
    monkeypatch.setenv("FAKE_CLAUDE_STOPS", str(stops))
    monkeypatch.setenv(
        "FAKE_CLAUDE_TOOL",
        json.dumps(
            {
                "name": "Write",
                "input": {"file_path": "axiomation/__init__.py"},
                "refused": False,
            }
        ),
    )
    plant = f"open({str(planted)!r}, 'w').write('ran')\n"
    monkeypatch.setenv("FAKE_CLAUDE_PLANT", f"axiomation/__init__.py|||{plant}")

    host.ask(question.prompt, 60, question.workspace)

    (stop,) = json.loads(stops.read_text())
    assert stop["code"] == 0 and not planted.exists()


# --- the gate on every write ----------------------------------------------


def test_a_gate_on_write_also_gets_a_post_tool_use_hook() -> None:
    from axiomation.runner.claude_code import TMP

    work = TMP / "axiomation-host" / "x" / "work"

    tmp = TMP / "ax-attempt"
    assert set(ClaudeCodeHost.settings(work, tmp, "check it")["hooks"]) == {"Stop"}
    hooks = ClaudeCodeHost.settings(work, tmp, "check it", "check it write")["hooks"]
    (entry,) = hooks["PostToolUse"]
    assert entry == {
        "matcher": "*",
        "hooks": [
            {"type": "command", "command": "check it write", "timeout": GATE_SECONDS}
        ],
    }
    assert (
        hooks["Stop"] == ClaudeCodeHost.settings(work, tmp, "check it")["hooks"]["Stop"]
    )


def test_the_edges_gate_runs_on_a_write_and_at_the_end(
    setup, monkeypatch, tmp_path
) -> None:
    """An edges session that edits the sheet itself (a re-ask's, an
    update's) as Claude Code runs it: the write hook shows what is wrong
    but not what is unfinished; the Stop hook blocks, the unfinished
    included, for its rounds, then lets the session end."""
    from axiomation.questions import edges_file
    from axiomation.questions.edges_file import EDGES_FILE, SESSION_TEMPLATE

    host, calls, _, _ = setup
    paper = make_paper(tmp_path / "paper")
    writer = load_template(SESSION_TEMPLATE)
    question = edges_file._question(
        paper.document, paper.cut, writer, ["main.tex"], None
    )
    writes, stops = tmp_path / "writes.json", tmp_path / "stops.json"
    monkeypatch.setenv("FAKE_CLAUDE_WRITES", str(writes))
    monkeypatch.setenv("FAKE_CLAUDE_STOPS", str(stops))
    monkeypatch.setenv(
        "FAKE_CLAUDE_EDIT",
        '\\section{More}|||\\section{More}\n→ uses no-such · "More"',
    )

    answer = host.ask(question.prompt, 60, question.workspace)

    (write,) = json.loads(writes.read_text())
    decision = json.loads(write["stdout"])
    assert write["matcher"] == "*" and decision["decision"] == "block"
    assert "no such target" in decision["reason"]
    assert "not done" not in decision["reason"]
    ends = json.loads(stops.read_text())
    assert len(ends) == gates.ROUNDS + 1
    assert all("not done" in json.loads(e["stdout"])["reason"] for e in ends[:-1])
    assert ends[-1] == {"stdout": "", "stderr": "", "code": 0}
    assert calls()[0]["listing"] == [EDGES_FILE]
    # Every check the gate ran is in the transcript, after the session's.
    checks = [
        event
        for line in answer.transcript.splitlines()
        if (event := json.loads(line))["type"] == "gate_check"
    ]
    assert [(c["mode"], c["blocked"]) for c in checks] == [
        ("write", True),
        *[("stop", True)] * gates.ROUNDS,
        ("stop", False),
    ]


def test_the_edges_first_pass_is_checked_when_it_tries_to_finish(
    setup, monkeypatch, tmp_path
) -> None:
    """The build's first edges pass as Claude Code runs it: no write hook;
    the Stop hook lays `lines.md` into the sheet and blocks, naming a line
    that does not read and the owners not written, for its rounds, then
    lets the session end; the answer is the lines as written."""
    from axiomation.questions.edges_file import (
        EDGES_FILE,
        LINES_FILE,
        SESSION_TEMPLATE,
        session_question,
    )

    host, calls, _, _ = setup
    paper = make_paper(tmp_path / "paper")
    question = session_question(
        paper.document, paper.cut, load_template(SESSION_TEMPLATE), ["main.tex"]
    )
    writes, stops = tmp_path / "writes.json", tmp_path / "stops.json"
    written = '@@ more\n→ uses no-such · "More"'
    monkeypatch.setenv("FAKE_CLAUDE_WRITES", str(writes))
    monkeypatch.setenv("FAKE_CLAUDE_STOPS", str(stops))
    monkeypatch.setenv("FAKE_CLAUDE_PLANT", f"{LINES_FILE}|||{written}")

    answer = host.ask(question.prompt, 60, question.workspace)

    assert json.loads(writes.read_text()) == []
    ends = json.loads(stops.read_text())
    assert len(ends) == gates.ROUNDS + 1
    reasons = [json.loads(e["stdout"])["reason"] for e in ends[:-1]]
    assert all(r.startswith(f"{LINES_FILE} does not read yet") for r in reasons)
    assert all("no such target" in r and "not done" in r for r in reasons)
    assert ends[-1] == {"stdout": "", "stderr": "", "code": 0}
    assert sorted(calls()[0]["listing"]) == sorted([EDGES_FILE, LINES_FILE])
    assert answer.text == written


def test_a_later_edges_pass_is_checked_when_it_tries_to_finish(
    setup, monkeypatch, tmp_path
) -> None:
    """A later edges pass as Claude Code runs it: no write hook; the Stop
    hook applies `lines.md`'s changes to the sheet shown and blocks on a
    change that does not apply, before the sheet is read, for its rounds;
    the answer is the changes as written."""
    from axiomation.questions.edges_file import (
        EDGES_FILE,
        LINES_FILE,
        REVIEW_TEMPLATE,
        SESSION_TEMPLATE,
        review_question,
        session_question,
    )

    host, calls, _, _ = setup
    paper = make_paper(tmp_path / "paper")
    writer = load_template(SESSION_TEMPLATE)
    first = session_question(paper.document, paper.cut, writer, ["main.tex"])
    question = review_question(
        paper.document,
        paper.cut,
        writer,
        load_template(REVIEW_TEMPLATE),
        ["main.tex"],
        first.workspace.files[EDGES_FILE],
        first.key,
        2,
    )
    writes, stops = tmp_path / "writes.json", tmp_path / "stops.json"
    written = '@@ more\n- → uses no-such · "More"'
    monkeypatch.setenv("FAKE_CLAUDE_WRITES", str(writes))
    monkeypatch.setenv("FAKE_CLAUDE_STOPS", str(stops))
    monkeypatch.setenv("FAKE_CLAUDE_PLANT", f"{LINES_FILE}|||{written}")

    answer = host.ask(question.prompt, 60, question.workspace)

    assert json.loads(writes.read_text()) == []
    ends = json.loads(stops.read_text())
    assert len(ends) == gates.ROUNDS + 1
    reasons = [json.loads(e["stdout"])["reason"] for e in ends[:-1]]
    assert all(r.startswith(f"{LINES_FILE} does not read yet") for r in reasons)
    assert all("⟦more⟧ holds no line" in r and "not done" not in r for r in reasons)
    assert ends[-1] == {"stdout": "", "stderr": "", "code": 0}
    assert sorted(calls()[0]["listing"]) == sorted([EDGES_FILE, LINES_FILE])
    assert answer.text == written


# --- a queue session ----------------------------------------------------------------

QUEUE = QueueSession(
    "Call `next` and work through your tasks.",
    "# The worker's brief\n",
    ToolServer(
        "q",
        ("/usr/bin/python3", "-I", "-m", "a.server", "/s/s1.json"),
        {"A": "1"},
        600.0,
    ),
    100_000,
)


def test_a_queue_session_has_its_server_s_tools_alone(setup) -> None:
    """No built-in tool and no MCP server but its own, compacted at its
    bound, under its spending limit; the server's tools allowed and its
    longest call within the tool limit."""
    host, calls, _, _ = setup
    answer = host.ask_queue(QUEUE, 60, spend_max=0.5)
    assert answer.text == "Done." and answer.cost == pytest.approx(0.01)
    (call,) = calls()
    argv = call["argv"]
    assert argv[argv.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in argv
    assert argv[argv.index("--autocompact") + 1] == "100000"
    assert argv[argv.index("--max-budget-usd") + 1] == "0.5000"
    assert call["mcp"] == {
        "mcpServers": {
            "q": {
                "command": "/usr/bin/python3",
                "args": ["-I", "-m", "a.server", "/s/s1.json"],
                "env": {"A": "1"},
            }
        }
    }
    assert call["mcp_limits"] == ["600000", str(round(SERVER_START_SECONDS * 1000))]
    assert call["cache_ttl"] == SERVED_CACHE_TTL == "5m"
    assert "mcp__q" in call["allowed"]
    assert call["brief"] == "# The worker's brief\n"
    assert call["brief_flag"] == "--system-prompt-file"
    assert call["stdin"] == QUEUE.prompt
    assert call["listing"] == []


def test_a_queue_session_s_server_is_started_as_claude_code_starts_it(
    setup, monkeypatch
) -> None:
    """Asked before a pair runs, the host starts the server once
    (`queue.started`) and says why it does not start."""
    host, _, _, _ = setup
    tried = []
    monkeypatch.setattr(
        claude_code, "started", lambda server: tried.append(server) or "gone"
    )
    assert host.tool_server_error(QUEUE.server) == "gone"
    assert tried == [QUEUE.server]


def test_a_queue_session_s_server_tools_are_no_breach(setup, monkeypatch) -> None:
    host, _, _, _ = setup
    monkeypatch.setenv(
        "FAKE_CLAUDE_TOOL",
        json.dumps({"name": "mcp__q__next", "input": {}, "refused": False}),
    )
    assert host.ask_queue(QUEUE, 60).text == "Done."


def test_a_queue_session_s_other_tool_is_a_breach(setup, monkeypatch) -> None:
    host, _, _, _ = setup
    monkeypatch.setenv(
        "FAKE_CLAUDE_TOOL",
        json.dumps({"name": "Bash", "input": {"command": "ls"}, "refused": False}),
    )
    with pytest.raises(HostBreach, match="Bash ran, though it was not given"):
        host.ask_queue(QUEUE, 60)


READING = dataclasses.replace(QUEUE, files={"paper.md": "the paper\n"})


def test_a_queue_session_given_files_reads_them_with_the_host_s_reads(
    setup, monkeypatch
) -> None:
    """The files lie in its folder, and Read and Grep are its only tools
    besides its server's."""
    host, calls, _, _ = setup
    monkeypatch.setenv(
        "FAKE_CLAUDE_TOOL",
        json.dumps(
            {"name": "Read", "input": {"file_path": "paper.md"}, "refused": False}
        ),
    )
    assert host.ask_queue(READING, 60).text == "Done."
    (call,) = calls()
    argv = call["argv"]
    assert argv[argv.index("--tools") + 1] == "Read,Grep"
    assert call["listing"] == ["paper.md"]
    assert "Grep(./**)" in call["allowed"]


@pytest.mark.parametrize(
    ("tool", "given", "why"),
    [
        ("Read", {"file_path": "/etc/hosts"}, "Read reached /etc/hosts, outside"),
        ("Grep", {"pattern": "x", "path": "/tmp"}, "Grep reached /tmp, outside"),
        ("Edit", {"file_path": "paper.md"}, "Edit ran, though it was not given"),
    ],
)
def test_a_queue_session_s_reads_stay_in_its_folder(
    setup, monkeypatch, tool, given, why
) -> None:
    host, _, _, _ = setup
    monkeypatch.setenv(
        "FAKE_CLAUDE_TOOL", json.dumps({"name": tool, "input": given, "refused": False})
    )
    with pytest.raises(HostBreach, match=why):
        host.ask_queue(READING, 60)


def test_opencode_serves_no_queue_session(tmp_path) -> None:
    from axiomation.runner.opencode import OpenCodeHost

    with pytest.raises(HostSetupError, match="serves no queue session"):
        OpenCodeHost().ask_queue(QUEUE, 60)
