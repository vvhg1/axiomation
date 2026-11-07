"""The OpenCode adapter, against a fake `opencode`.

The fake is a script run by this Python: it logs what it was given and
where, and prints canned events in `opencode run`'s shape. `opencode` itself is
never run.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from axiomation.questions import Workspace, load_template
from axiomation.questions.groups_file import (
    GROUPS_FILE,
    SESSION_TEMPLATE,
    session_questions,
)
from axiomation.runner.hosts import HostBreach, HostOutOfCredit, HostUncounted
from axiomation.runner.opencode import (
    AGENT,
    DENIED,
    PROMPT_LIMIT,
    SESSION_AGENT,
    HostSetupError,
    OpenCodeHost,
    PromptTooLong,
    outside_paths,
    session_breach,
)
from questions.review_paper import make_paper
from runner.fakes import (
    error,
    events,
    fake_opencode,
    step_finish,
    step_start,
    text,
    tool_use,
)


@pytest.fixture
def setup(tmp_path: Path, monkeypatch):
    executable = fake_opencode(tmp_path / "bin")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    log = tmp_path / "log.jsonl"
    monkeypatch.setenv("FAKE_OPENCODE_LOG", str(log))
    monkeypatch.delenv("FAKE_OPENCODE_EVENTS", raising=False)
    monkeypatch.delenv("FAKE_OPENCODE_EXIT", raising=False)
    monkeypatch.delenv("FAKE_OPENCODE_SLEEP", raising=False)
    monkeypatch.delenv("FAKE_OPENCODE_STDERR", raising=False)
    monkeypatch.delenv("FAKE_OPENCODE_EXPORT", raising=False)
    monkeypatch.delenv("FAKE_OPENCODE_EXPORT_EXIT", raising=False)
    monkeypatch.delenv("FAKE_OPENCODE_EXPORT_LOG", raising=False)
    for name in ("EDIT", "EDIT_RE", "FIX", "SESSIONS"):
        monkeypatch.delenv(f"FAKE_OPENCODE_{name}", raising=False)
    monkeypatch.delenv("OPENCODE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("OPENCODE_DB", raising=False)

    def play(*items) -> None:
        path = tmp_path / "events.jsonl"
        path.write_text(events(*items), encoding="utf-8")
        monkeypatch.setenv("FAKE_OPENCODE_EVENTS", str(path))

    def calls() -> list[dict]:
        return [json.loads(line) for line in log.read_text().splitlines()]

    host = OpenCodeHost(executable=str(executable), scratch=scratch)
    return host, play, calls, scratch


ANSWER = '```json\n{"corrections": []}\n```'


def test_it_runs_the_designed_command_without_auto(setup) -> None:
    host, play, calls, _ = setup
    play(step_start(), text(ANSWER), step_finish(0.001, 100, 20))

    host.ask("# Review a cut", timeout=30)

    (call,) = calls()
    assert call["argv"] == [
        "run",
        "--standalone",
        "--agent",
        AGENT,
        "-m",
        "opencode/deepseek-v4.1-flash",
        "--format",
        "json",
        "--title",
        "axiomation question",
    ]
    assert "--auto" not in call["argv"]


def test_the_prompt_goes_on_stdin_byte_for_byte(setup) -> None:
    host, play, calls, _ = setup
    play(text(ANSWER))
    prompt = '--- a prompt\nwith "quotes", $vars and \\ \n\nand ü\n' + "x" * 200_000

    host.ask(prompt, timeout=30)

    (call,) = calls()
    assert call["stdin"] == prompt
    assert not any("a prompt" in word for word in call["argv"])


def test_the_owner_s_configuration_is_kept_out_and_untouched(setup) -> None:
    host, play, calls, _ = setup
    play(text(ANSWER))

    host.ask("# Review", timeout=30)

    (call,) = calls()
    directory = Path(call["cwd"])
    assert Path(call["global_config"]) == directory / ".host" / "config"
    assert call["global_listing"] == []
    assert call["database"] is None
    assert not directory.exists()


def test_both_providers_pass_through(setup) -> None:
    host, play, calls, _ = setup
    play(text(ANSWER))

    for model in ("opencode/deepseek-v4.1-flash", "opencode-go/glm-5.3-flash"):
        host.model = model
        host.ask("# Review", timeout=30)

    assert [c["argv"][c["argv"].index("-m") + 1] for c in calls()] == [
        "opencode/deepseek-v4.1-flash",
        "opencode-go/glm-5.3-flash",
    ]
    assert json.loads(calls()[1]["config"])["agents"][AGENT]["model"] == (
        "opencode-go/glm-5.3-flash"
    )


def test_the_effort_is_the_model_s_variant(setup) -> None:
    """OpenCode takes the effort as the model's variant: asked
    as `model#effort`, recorded apart from the model."""
    _, play, calls, scratch = setup
    host = OpenCodeHost(executable=setup[0].executable, scratch=scratch, effort="high")
    play(text(ANSWER), step_finish(0.001, 100, 20))

    answer = host.ask("# Review", timeout=30)

    [call] = calls()
    assert call["argv"][call["argv"].index("-m") + 1] == (
        "opencode/deepseek-v4.1-flash#high"
    )
    assert json.loads(call["config"])["agents"][AGENT]["model"] == (
        "opencode/deepseek-v4.1-flash#high"
    )
    assert (answer.model, host.effort) == ("opencode/deepseek-v4.1-flash", "high")


def test_it_runs_in_an_empty_directory_outside_git_removed_after(setup) -> None:
    host, play, calls, scratch = setup
    play(text(ANSWER))

    host.ask("# Review", timeout=30)

    (call,) = calls()
    directory = Path(call["cwd"])
    assert call["pwd"] == call["cwd"]
    assert directory.parent == scratch.resolve()
    assert directory.name.startswith("axiomation-host-")
    assert call["listing"] == [
        ".host",
        ".host/config",
        ".opencode",
        ".opencode/opencode.jsonc",
    ]
    assert not any((p / ".git").exists() for p in (directory, *directory.parents))
    assert not directory.exists()


def test_every_action_is_denied_and_last_everything(setup) -> None:
    host, play, calls, _ = setup
    play(text(ANSWER))

    host.ask("# Review", timeout=30)

    agent = json.loads(calls()[0]["config"])["agents"][AGENT]
    rules = agent["permissions"]
    assert all(rule["effect"] == "deny" for rule in rules)
    assert all(rule["resource"] == "*" for rule in rules)
    assert [rule["action"] for rule in rules] == [*DENIED, "*"]
    assert agent["mode"] == "primary" and agent["system"]


def test_text_cost_tokens_and_tools_are_parsed(setup) -> None:
    host, play, _, _ = setup
    play(
        step_start(),
        tool_use("read", {"path": "notes.txt"}),
        step_finish(0.002, 1000, 50, reasoning=10, read=200, write=5),
        text("Here it is.\n"),
        text(ANSWER),
        step_finish(0.003, 1100, 400, reasoning=30),
    )

    answer = host.ask("# Review", timeout=30)

    assert answer.text == "Here it is.\n" + ANSWER
    assert answer.cost == pytest.approx(0.005)
    assert answer.tokens == {
        "input": 2100,
        "output": 450,
        "reasoning": 40,
        "cache_read": 200,
        "cache_write": 5,
    }
    assert answer.tool_uses == (
        {"tool": "read", "input": {"path": "notes.txt"}, "status": "error"},
    )
    assert answer.exit_code == 0 and answer.error == "" and not answer.failed
    assert '"type": "tool_use"' in answer.transcript


def test_a_timeout_is_a_failed_answer_with_the_session_s_cost(
    setup, monkeypatch, tmp_path
) -> None:
    """The run printed its events, then hangs past its time. The timeout
    is made to come after the fake printed (its marker file), not after a
    wall-clock time a loaded machine may spend starting the fake."""
    host, play, _, _ = setup
    play(step_start(), step_finish(0.004, 900, 0))
    monkeypatch.setenv("FAKE_OPENCODE_SLEEP", "20")
    printed = tmp_path / "printed"
    monkeypatch.setenv("FAKE_OPENCODE_PRINTED", str(printed))
    wait = subprocess.Popen.wait
    runs: list[object] = []

    def timed_out_once_printed(process, timeout=None):
        # the run's own wait times out; `session export`'s and the rest wait
        if timeout is None or runs:
            return wait(process, timeout)
        runs.append(process)
        deadline = time.monotonic() + 30  # a fake that never prints fails
        while not printed.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        raise subprocess.TimeoutExpired(process.args, timeout)

    monkeypatch.setattr(subprocess.Popen, "wait", timed_out_once_printed)

    answer = host.ask("# Review", timeout=0.5)

    assert answer.failed
    assert "timed out after 0.5 s" in answer.error
    assert answer.seconds < 10
    assert answer.cost == pytest.approx(0.004)


def test_a_timeout_before_any_event_is_uncounted(setup, monkeypatch) -> None:
    host, _, _, _ = setup
    monkeypatch.setenv("FAKE_OPENCODE_SLEEP", "20")

    with pytest.raises(HostUncounted, match="events name no session") as raised:
        host.ask("# Review", timeout=0.5)
    assert "timed out after 0.5 s" in raised.value.answer.error


def test_cost_tokens_and_text_come_from_the_session_s_record(
    tmp_path, setup, monkeypatch
) -> None:
    """Against a real model, `run --format json` exits before its
    `step_finish` events: the stream is cut short, the
    session's record is whole."""
    host, play, _, _ = setup
    play(step_start(), text('```json\n{"corr'))
    record = {
        "info": {
            "cost": 0.000237,
            "tokens": {
                "input": 398,
                "output": 3,
                "reasoning": 95,
                "cache": {"read": 7, "write": 0},
            },  # fmt: skip
        },
        "messages": [
            {"type": "user", "text": "# Review"},
            {
                "type": "assistant",
                "content": [
                    {"type": "reasoning", "text": "They want JSON."},
                    {"type": "text", "text": ANSWER},
                ],
            },
        ],
    }
    exported = tmp_path / "export.json"
    exported.write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setenv("FAKE_OPENCODE_EXPORT", str(exported))
    exports = tmp_path / "exports.jsonl"
    monkeypatch.setenv("FAKE_OPENCODE_EXPORT_LOG", str(exports))

    answer = host.ask("# Review", timeout=30)

    assert answer.text == ANSWER
    assert answer.cost == pytest.approx(0.000237)
    assert answer.tokens == {
        "input": 398,
        "output": 3,
        "reasoning": 95,
        "cache_read": 7,
        "cache_write": 0,
    }
    assert not answer.failed
    assert '"type": "session_export"' in answer.transcript.splitlines()[-1]
    (call,) = [json.loads(line) for line in exports.read_text().splitlines()]
    assert call["session"] == "ses_fake"
    assert call["global_config"] == str(Path(call["cwd"]) / ".host" / "config")


def test_a_zero_cost_is_a_cost(tmp_path, setup, monkeypatch) -> None:
    host, play, _, _ = setup
    play(text(ANSWER), step_finish(0.0, 100, 20))

    answer = host.ask("# Review", timeout=30)

    assert answer.cost == 0.0 and answer.tokens["output"] == 20


def test_a_failed_export_is_uncounted(setup, monkeypatch) -> None:
    host, play, _, _ = setup
    play(text(ANSWER))
    monkeypatch.setenv("FAKE_OPENCODE_EXPORT_EXIT", "1")

    with pytest.raises(HostUncounted, match="Session not found: ses_fake") as raised:
        host.ask("# Review", timeout=30)
    assert raised.value.answer.text == ANSWER


@pytest.mark.parametrize(
    "record",
    [
        "not json",
        {"messages": []},
        {"info": {"tokens": {}}},
        {"info": {"cost": None}},
        {"info": {"cost": "0.1"}},
        {"info": {"cost": True}},
    ],
)
def test_a_record_without_a_cost_is_uncounted(
    tmp_path, setup, monkeypatch, record
) -> None:
    host, play, _, _ = setup
    play(text(ANSWER))
    exported = tmp_path / "export.json"
    exported.write_text(
        record if isinstance(record, str) else json.dumps(record), encoding="utf-8"
    )
    monkeypatch.setenv("FAKE_OPENCODE_EXPORT", str(exported))

    with pytest.raises(HostUncounted):
        host.ask("# Review", timeout=30)


def test_events_without_a_session_are_uncounted(tmp_path, setup, monkeypatch):
    host, _, _, _ = setup
    path = tmp_path / "events.jsonl"
    path.write_text(events(text(ANSWER), session=None), encoding="utf-8")
    monkeypatch.setenv("FAKE_OPENCODE_EVENTS", str(path))

    with pytest.raises(HostUncounted, match="events name no session"):
        host.ask("# Review", timeout=30)


def test_a_tool_only_the_record_shows_still_counts(tmp_path, setup, monkeypatch):
    host, play, _, _ = setup
    play(text(ANSWER))
    record = {
        "info": {"cost": 0.001, "tokens": {}},
        "messages": [
            {
                "type": "assistant",
                "content": [
                    # 2.0.16's shape: the tool's name is `name`.
                    {
                        "type": "tool",
                        "name": "read",
                        "executed": False,
                        "state": {"input": {"path": "x"}, "status": "error"},
                    },
                    {"type": "text", "text": ANSWER},
                ],
            }
        ],
    }
    exported = tmp_path / "export.json"
    exported.write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setenv("FAKE_OPENCODE_EXPORT", str(exported))

    answer = host.ask("# Review", timeout=30)

    assert answer.tool_uses == (
        {"tool": "read", "input": {"path": "x"}, "status": "error"},
    )


def test_a_non_zero_exit_is_a_failed_answer(setup, monkeypatch) -> None:
    host, play, _, _ = setup
    play(error("model not found"))
    monkeypatch.setenv("FAKE_OPENCODE_EXIT", "1")
    monkeypatch.setenv("FAKE_OPENCODE_STDERR", "Error: model not found\n")

    answer = host.ask("# Review", timeout=30)

    assert answer.failed and answer.exit_code == 1
    assert "model not found" in answer.error
    assert answer.text == ""


def test_an_over_long_prompt_is_refused(setup) -> None:
    host, _, calls, _ = setup

    with pytest.raises(PromptTooLong, match="over the limit of 500,000"):
        host.ask("#" + "x" * PROMPT_LIMIT, timeout=30)


def test_a_directory_inside_a_git_tree_is_refused(tmp_path, setup) -> None:
    fake, _, _, _ = setup
    repository = tmp_path / "repository"
    (repository / ".git").mkdir(parents=True)
    host = OpenCodeHost(executable=fake.executable, scratch=repository)

    with pytest.raises(HostSetupError, match="inside a Git tree"):
        host.check()
    assert [p.name for p in repository.iterdir()] == [".git"]


def test_the_scratch_comes_from_the_environment(tmp_path, setup, monkeypatch) -> None:
    _, play, calls, _ = setup
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("AXIOMATION_HOST_SCRATCH", str(other))
    play(text(ANSWER))
    host = OpenCodeHost(executable=setup[0].executable)

    host.ask("# Review", timeout=30)

    assert Path(calls()[0]["cwd"]).parent == other.resolve()


def test_a_tool_input_outside_the_directory_is_a_breach(setup) -> None:
    host, play, _, _ = setup
    play(tool_use("shell", {"command": "find / -name paper.tex"}), text(ANSWER))

    with pytest.raises(HostBreach, match="shell named /, outside") as raised:
        host.ask("# Review", timeout=30)
    assert raised.value.answer.text == ANSWER


def test_a_billing_refusal_is_out_of_credit(setup, monkeypatch) -> None:
    host, play, _, _ = setup
    play(error("Insufficient balance: add credits to continue"))
    monkeypatch.setenv("FAKE_OPENCODE_EXIT", "1")

    with pytest.raises(HostOutOfCredit, match="Insufficient balance"):
        host.ask("# Review", timeout=30)


def test_billing_words_in_the_model_s_text_are_not_a_refusal(setup) -> None:
    host, play, _, _ = setup
    play(text("The paper's section on billing has no quota exceeded."))

    answer = host.ask("# Review", timeout=30)

    assert not answer.failed


@pytest.mark.parametrize(
    ("value", "outside"),
    [
        ("notes.txt", False),
        ("./notes.txt", False),
        ("src/a/b", False),
        ("/etc/passwd", True),
        ("cat ~/.ssh/id_rsa", True),
        ("../../other/keys", True),
        ("grep x /", True),
        ("cat $HOME/.ssh/id_rsa", True),
        ("ls a/../../x", True),
        ("ls ./../x", True),
        ("cd ..", True),
        ("cd ~", True),
        ("echo $UNSET_VARIABLE_XYZ/x", True),
        ("a/../b", False),
    ],
)
def test_paths_outside_are_found(tmp_path, value, outside) -> None:
    directory = tmp_path.resolve()
    uses = ({"tool": "shell", "input": {"command": value}},)

    assert bool(outside_paths(uses, directory)) is outside


def test_a_path_inside_the_directory_is_no_breach(tmp_path) -> None:
    directory = tmp_path.resolve()
    uses = ({"tool": "read", "input": {"path": str(directory / "a.txt")}},)

    assert outside_paths(uses, directory) == []


def test_a_child_holding_the_pipes_does_not_hold_the_attempt(setup, monkeypatch):
    """OpenCode's server outliving the run, holding stdout: the attempt
    ends when the leader does, and the group is ended with it."""
    host, play, _, _ = setup
    play(text(ANSWER))
    monkeypatch.setenv("FAKE_OPENCODE_CHILD", "30")

    answer = host.ask("# Review", timeout=20)

    assert not answer.failed and answer.text == ANSWER
    assert answer.seconds < 15


def test_a_tool_that_ran_is_a_breach(setup) -> None:
    host, play, _, _ = setup
    play(tool_use("read", {"path": "notes.txt"}, status="completed"), text(ANSWER))

    with pytest.raises(HostBreach, match="read ran, though every action is denied"):
        host.ask("# Review", timeout=30)


def test_a_tool_part_under_another_event_name_is_still_a_tool(setup) -> None:
    host, play, _, _ = setup
    renamed = tool_use("grep", {"pattern": "x"})
    renamed["type"] = "tool_call"
    play(renamed, text(ANSWER))

    answer = host.ask("# Review", timeout=30)

    assert [use["tool"] for use in answer.tool_uses] == ["grep"]


def test_check_reads_the_version_into_the_host_s_name(setup) -> None:
    host, _, calls, _ = setup

    host.check()

    assert host.name == "opencode 2.0.16-fake"


def test_a_402_in_a_stack_trace_is_not_a_billing_refusal(setup, monkeypatch) -> None:
    host, play, _, _ = setup
    play(error("TypeError: undefined at server.js:402:15"))
    monkeypatch.setenv("FAKE_OPENCODE_EXIT", "1")

    answer = host.ask("# Review", timeout=30)

    assert answer.failed
    play(error("request failed with status 402"))
    with pytest.raises(HostOutOfCredit):
        host.ask("# Review", timeout=30)


def test_abort_ends_an_attempt_in_flight(setup, monkeypatch) -> None:
    import threading

    host, play, _, _ = setup
    play(step_start())
    monkeypatch.setenv("FAKE_OPENCODE_SLEEP", "30")
    found = []

    def ask() -> None:
        # Ended before its first event, the attempt names no session.
        try:
            found.append(host.ask("# R", 60))
        except HostUncounted as uncounted:
            found.append(uncounted.answer)

    worker = threading.Thread(target=ask)
    worker.start()
    for _ in range(200):
        if host._live:
            break
        threading.Event().wait(0.05)

    host.abort()
    worker.join(15)

    assert not worker.is_alive()
    assert found[0].failed


# --- sessions on files ---------------------------------------------------


WORKSPACE = Workspace(
    files={"cut.md": "⟦b1 none⟧ — …\nOne sentence. Another.\n", "outline.md": "# O\n"},
    edit="cut.md",
    instructions="# The brief\n",
)


def test_a_session_edits_its_file_and_answers_with_it(setup, monkeypatch) -> None:
    host, play, calls, _ = setup
    play(step_start(), text("Done."), step_finish(0.002, 300, 40))
    split = "One sentence. |||One sentence.\n⟦split⟧\n"
    monkeypatch.setenv("FAKE_OPENCODE_EDIT", split)

    answer = host.ask("Correct the cut in cut.md.", 30, WORKSPACE)

    assert answer.text == "⟦b1 none⟧ — …\nOne sentence.\n⟦split⟧\nAnother.\n"
    assert answer.cost == 0.002 and not answer.failed
    (call,) = calls()
    assert call["stdin"] == "Correct the cut in cut.md."
    assert call["argv"][call["argv"].index("--agent") + 1] == SESSION_AGENT
    assert call["brief"] == "# The brief\n"
    assert {"cut.md", "outline.md"} <= set(call["listing"])
    assert not Path(call["cwd"]).exists()


def test_a_session_may_read_everything_and_edit_its_file_alone(setup) -> None:
    host, play, calls, _ = setup
    play(text("Done."), step_finish(0.001, 10, 1))

    host.ask("Go.", 30, WORKSPACE)

    agent = json.loads(calls()[0]["config"])["agents"][SESSION_AGENT]
    rules = agent["permissions"]
    assert [r["action"] for r in rules] == [*DENIED, "*", "read", "edit"]
    assert all(r["effect"] == "deny" for r in rules[:-2])
    assert rules[-2:] == [
        {"action": "read", "resource": "*", "effect": "allow"},
        {"action": "edit", "resource": "cut.md", "effect": "allow"},
    ]
    assert "system" not in agent


def test_an_edit_outside_the_answer_file_refused_is_no_breach(setup) -> None:
    host, play, _, _ = setup
    play(
        tool_use("edit", {"path": "outline.md", "oldString": "# O"}),
        tool_use("shell", {"command": "cat /etc/passwd"}),
        tool_use("read", {"path": "/etc/passwd"}),
        text("Done."),
        step_finish(0.001, 10, 1),
    )

    answer = host.ask("Go.", 30, WORKSPACE)

    assert answer.text == WORKSPACE.files["cut.md"]
    assert [u["tool"] for u in answer.tool_uses] == ["edit", "shell", "read"]


@pytest.mark.parametrize(
    ("tool", "given", "why"),
    [
        ("edit", {"path": "outline.md"}, "edit changed outline.md, not cut.md"),
        ("write", {"filePath": "../cut.md"}, "write changed ../cut.md, not cut.md"),
        ("read", {"path": "/etc/passwd"}, "read reached /etc/passwd, outside"),
        ("read", {"path": "../../keys"}, "read reached ../../keys, outside"),
        ("shell", {"command": "ls"}, "shell ran, though it was not given"),
        ("edit", {"oldString": "a"}, "edit ran on no path the host can check"),
    ],
)
def test_a_tool_that_ran_beyond_the_session_s_reach_is_a_breach(
    setup, tool, given, why
) -> None:
    host, play, _, _ = setup
    play(tool_use(tool, given, status="completed"), step_finish(0.001, 10, 1))

    with pytest.raises(HostBreach, match=why):
        host.ask("Go.", 30, WORKSPACE)


def test_latex_in_an_edit_s_text_is_no_breach(tmp_path) -> None:
    directory = tmp_path.resolve()
    (directory / "cut.md").write_text("x")
    edit = {
        "path": str(directory / "cut.md"),
        "oldString": "$a/b$ and $\\frac{1}{2}$",
        "newString": "$a/b$ ⟦split⟧ /",
    }
    uses = (
        {"tool": "edit", "input": edit, "status": "completed"},
        {"tool": "read", "input": {"path": str(directory)}, "status": "completed"},
        {"tool": "read", "input": {"path": "outline.md"}, "status": "completed"},
    )

    assert session_breach(uses, directory, "cut.md") == ""


def test_a_session_out_of_credit_is_a_clean_stop(setup, monkeypatch) -> None:
    host, play, _, _ = setup
    play(error("Insufficient balance: add credits to continue"))
    monkeypatch.setenv("FAKE_OPENCODE_EXIT", "1")

    with pytest.raises(HostOutOfCredit, match="Insufficient balance"):
        host.ask("Go.", 30, WORKSPACE)


def test_a_session_without_a_cost_is_uncounted(setup, monkeypatch, tmp_path) -> None:
    host, play, _, _ = setup
    play(text("Done."))
    record = tmp_path / "export.json"
    record.write_text(json.dumps({"info": {"id": "ses_fake"}, "messages": []}))
    monkeypatch.setenv("FAKE_OPENCODE_EXPORT", str(record))

    with pytest.raises(HostUncounted, match="names no cost"):
        host.ask("Go.", 30, WORKSPACE)


def _groups_question(tmp_path: Path):
    paper = make_paper(tmp_path / "paper")
    (question,) = session_questions(
        paper.document, paper.cut, None, load_template(SESSION_TEMPLATE)
    )
    return question


def test_the_gate_goes_on_in_the_same_session_until_the_file_reads(
    setup, monkeypatch, tmp_path
) -> None:
    """The session renames the proof's end line; the gate, run as Claude
    Code runs a Stop hook, blocks, and the same session goes on with its
    reason and fixes it. The cost is the whole session's."""
    host, play, calls, _ = setup
    question = _groups_question(tmp_path)
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    monkeypatch.setenv("FAKE_OPENCODE_SESSIONS", str(sessions))
    play(text("Done."), step_finish(0.003, 100, 10))
    monkeypatch.setenv(
        "FAKE_OPENCODE_EDIT",
        "⟦end group by_definition_the_gain⟧|||⟦end group the_proof⟧",
    )
    monkeypatch.setenv(
        "FAKE_OPENCODE_FIX",
        "⟦end group the_proof⟧|||⟦end group by_definition_the_gain⟧",
    )

    answer = host.ask(question.prompt, 60, question.workspace)

    first, second = calls()
    assert "--session" not in first["argv"]
    assert second["argv"][second["argv"].index("--session") + 1] == "ses_fake"
    assert second["cwd"] == first["cwd"]
    assert "⟦end group the_proof⟧ ends no group" in second["stdin"]
    assert answer.text == question.workspace.files[GROUPS_FILE]
    assert answer.cost == pytest.approx(0.006)
    # The session's directory holds no spec of the gate's.
    assert not any("gate" in path for path in first["listing"])


def test_a_gate_that_never_lets_go_is_left_after_its_rounds(
    setup, monkeypatch, tmp_path
) -> None:
    """A fix that never comes: the gate stops blocking after its own
    rounds, the session ends, and the runner's check decides."""
    host, play, calls, _ = setup
    question = _groups_question(tmp_path)
    play(text("Done."), step_finish(0.001, 10, 1))
    monkeypatch.setenv(
        "FAKE_OPENCODE_EDIT",
        "⟦end group by_definition_the_gain⟧|||⟦end group the_proof⟧",
    )

    answer = host.ask(question.prompt, 60, question.workspace)

    assert len(calls()) == 4  # the session, and the gate's three rounds
    assert "⟦end group the_proof⟧" in answer.text


def test_a_file_that_reads_ends_the_session_at_once(setup, tmp_path) -> None:
    host, play, calls, _ = setup
    question = _groups_question(tmp_path)
    play(text("Done."), step_finish(0.001, 10, 1))

    answer = host.ask(question.prompt, 60, question.workspace)

    assert len(calls()) == 1
    assert answer.text == question.workspace.files[GROUPS_FILE]


def test_a_review_by_block_is_refused(setup, tmp_path) -> None:
    """OpenCode serves no tool server: a review session by block, which
    one serves its check, is refused before anything runs, as a queue
    session is."""
    from dataclasses import replace

    from axiomation.questions import SessionTools
    from axiomation.runner.opencode import HostSetupError

    host, _, _, _ = setup
    served = replace(WORKSPACE, served=SessionTools(str(tmp_path / "store")))

    with pytest.raises(HostSetupError, match="OpenCode serves no tool server"):
        host.ask("Review.", 60, served)


def test_a_shell_command_is_a_breach(tmp_path) -> None:
    """A session on files reads and edits: the shell is not given."""
    uses = (
        {
            "tool": "shell",
            "input": {"command": "grep -r 'Lemma (2|3)' ."},
            "status": "completed",
        },
    )

    assert "not given" in session_breach(uses, tmp_path, "cut.md")
