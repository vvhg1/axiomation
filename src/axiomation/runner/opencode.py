"""The OpenCode host: `opencode run`, one process per attempt, no tools,
or a session on files.

Each attempt runs in a fresh, empty directory outside any Git tree,
holding only `.opencode/opencode.jsonc`, which defines one agent that may
do nothing: every action is denied, and last a rule denies `*`, since
OpenCode 2.0.16 puts `* * allow` first among an agent's rules and the
agent's own last, the last match winning. The question's inputs are all in
its prompt, so the model has nothing to read. The transcript is checked
all the same: a tool use fails the attempt (the runner's check), and a
tool input naming a path outside the directory raises `HostBreach`.

The user's own OpenCode configuration is kept out, and left untouched:
`OPENCODE_CONFIG_DIR` names an empty directory of the
attempt's, since 2.0.16 adds the global config directory's `AGENTS.md` to
every system prompt and loads its plugins, and has no setting that turns
that off.

The prompt goes on stdin: given as an argument, 2.0.16 sends it wrapped
in quotes with every inner `"` escaped.

The answer's text, cost and tokens come from the session's own record,
`opencode session export`, read after the run: `run --format json` exits
once the session is idle, before its last events are printed, so against
a real model its `step_finish` events, which carry the cost, never arrive.
An attempt whose session names no cost raises
`HostUncounted`. The tool uses are those of the events and the record
both.

A session on files runs
in the same kind of directory, holding the workspace's files too. Its
agent's rules are the same denials, then `read` of everything and `edit`
of the answer file allowed; reading outside the directory stays denied
(`external_directory`). The brief is the config directory's `AGENTS.md`,
the answer the answer file's text when the session ends. Its breach check
reads the tools' path fields only, since an edit's text holds `$` and `/`
as LaTeX: a tool that ran other than a read or an edit, a read outside the
directory, or an edit of any file but the answer file. A workspace's gate
runs as Claude Code runs a Stop hook, once the process ends, its
spec in a directory beside the session's; while it blocks, the same
session goes on (`run --session`) with its reason as the message.

Credentials are OpenCode's: the environment is inherited, and our
code reads, passes and stores no key. They live in OpenCode's database,
not in its config directory, which is why the database is the user's.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from axiomation.questions import (
    QueueSession,
    ToolServer,
    Workspace,
)
from axiomation.questions.gates import NotRegularFile, read_regular
from axiomation.runner.hosts import (
    DEFAULT_MODEL,
    HostAnswer,
    HostBreach,
    HostInterrupted,
    HostOutOfCredit,
    HostUncounted,
    PromptTooLong,
)
from axiomation.runner.interruption import Waited, interrupted, waited

AGENT = "answer"
#: A session's agent.
SESSION_AGENT = "session"
SCRATCH_ENV = "AXIOMATION_HOST_SCRATCH"

#: The attempt's own global config directory: empty, or a session's brief
#: as its `AGENTS.md`, which 2.0.16 adds to the system prompt.
GLOBAL_CONFIG = ".host/config"
BRIEF = "AGENTS.md"

#: The actions the agent is denied.
DENIED = (
    "edit",
    "external_directory",
    "webfetch",
    "websearch",
    "subagent",
    "skill",
    "shell",
    "read",
    "glob",
    "grep",
    "list",
    "todo",
)
RULES = [{"action": action, "resource": "*", "effect": "deny"} for action in DENIED]
RULES.append({"action": "*", "resource": "*", "effect": "deny"})

#: What a session's tools may do, by OpenCode's tool names: read anything
#: in the directory, and edit the answer file alone (`edit` is the
#: permission of all three edit tools).
READ_TOOLS = frozenset({"read"})
EDIT_TOOLS = frozenset({"edit", "write", "multiedit"})
#: A tool input's fields that name a path.
PATH_FIELDS = ("path", "filePath", "file_path")

#: The same for every question: what a question is about is its own
#: prompt's to say.
SYSTEM = (
    "You have no tools and need none: everything you need is in the "
    "message. Answer with the JSON object the message asks for."
)

#: A sanity cap, not a pipe's: a long paper's largest prompt, its groups
#: question, is about 155,000 bytes.
PROMPT_LIMIT = 500_000

#: How long the pipes are drained after the process group is ended.
DRAIN_SECONDS = 10.0

#: How long a session's export may take; it takes well under a second.
EXPORT_SECONDS = 60.0

TITLE = "axiomation question"
SESSION_TITLE = "axiomation session"

#: How long a gate may take (Claude Code's hook timeout for it too), and
#: how many times at most a session goes on after a gate blocked, should
#: the gate not stop counting (the gate stops at three).
GATE_SECONDS = 120
GATE_CONTINUATIONS = 4
#: The gate's spec, in a directory of the attempt's beside the session's.
GATE = "gate.json"

#: Words an out-of-credit or billing refusal is recognised by, in the
#: host's error events and its stderr, never in the model's text. Not yet
#: seen from a real refusal.
BILLING = re.compile(
    r"\b(?:status|code|error)\W{0,3}402\b|\b402\s+payment|payment required"
    r"|insufficient[ _-]?(?:balance|credit|funds|quota)"
    r"|out of credit|credit balance|no credit|billing|quota exceeded"
    r"|exceeded your (?:current )?quota|usage limit",
    re.IGNORECASE,
)

#: What separates the words of a tool's input, for the path scan.
_WORDS = re.compile(r"[\s'\"`;|&<>(){}\[\],=:]+")

#: A tool part in these states ran: with every action denied, none may.
RAN = frozenset({"completed", "running"})


class HostSetupError(RuntimeError):
    """The host's directory is not safe to run in."""


def config(model: str) -> dict[str, Any]:
    """The `.opencode/opencode.jsonc` of an attempt's directory."""
    return {
        "$schema": "https://opencode.ai/config.json",
        "agents": {
            AGENT: {
                "description": "Answers one question from its message. Has no tools.",
                "mode": "primary",
                "model": model,
                "system": SYSTEM,
                "permissions": RULES,
            }
        },
    }


def session_config(model: str, edit: str) -> dict[str, Any]:
    """A session's `.opencode/opencode.jsonc`: every action denied, then a
    read of anything and an edit of `edit` allowed, the last match
    winning."""
    allowed = [
        {"action": "read", "resource": "*", "effect": "allow"},
        {"action": "edit", "resource": edit, "effect": "allow"},
    ]
    return {
        "$schema": "https://opencode.ai/config.json",
        "agents": {
            SESSION_AGENT: {
                "description": f"Works on the files in its directory; changes {edit} "
                "alone.",
                "mode": "primary",
                "model": model,
                "permissions": [*RULES, *allowed],
            }
        },
    }


@dataclass(frozen=True, slots=True)
class _Ran:
    """One `opencode run` process: its stdout and stderr, its exit code,
    and how its wait ended (`Waited`)."""

    out: str
    err: str
    exit_code: int
    ended: Waited

    @property
    def timed_out(self) -> bool:
        return self.ended is Waited.TIMED_OUT

    @property
    def interrupted(self) -> bool:
        return self.ended is Waited.INTERRUPTED


def in_git_tree(path: Path) -> bool:
    return any((p / ".git").exists() for p in (path, *path.parents))


class OpenCodeHost:
    """`opencode run --standalone --agent answer -m MODEL[#EFFORT] --format
    json` in a fresh directory per attempt."""

    #: OpenCode's own prompt, not yet measured: the estimate leaves it out.
    prompt_tokens = 0

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        executable: str = "opencode",
        scratch: Path | None = None,
        effort: str | None = None,
    ) -> None:
        self.model = model
        #: The model's variant (`high`), its reasoning effort; None for
        #: OpenCode's default. Recorded apart from the model.
        self.effort = effort
        self.executable = executable
        self.scratch = scratch
        self.prompt_limit = PROMPT_LIMIT
        #: `opencode <version>` once `check()` has read it: the records
        #: name the host's version.
        self.name = "opencode"
        self._live: set[subprocess.Popen[bytes]] = set()
        self._live_lock = threading.Lock()

    @property
    def asked(self) -> str:
        """The model as OpenCode takes it, the variant in its name."""
        return f"{self.model}#{self.effort}" if self.effort else self.model

    def _base(self) -> Path:
        chosen = self.scratch or os.environ.get(SCRATCH_ENV) or tempfile.gettempdir()
        return Path(chosen).resolve()

    def _directory(self, workspace: Workspace | None = None) -> Path:
        directory = Path(
            tempfile.mkdtemp(prefix="axiomation-host-", dir=self._base())
        ).resolve()
        if in_git_tree(directory):
            shutil.rmtree(directory, ignore_errors=True)
            raise HostSetupError(
                f"the host directory {directory} lies inside a Git tree; set "
                f"{SCRATCH_ENV} to a directory outside every repository"
            )
        chosen = (
            config(self.asked)
            if workspace is None
            else session_config(self.asked, workspace.edit)
        )
        (directory / ".opencode").mkdir()
        (directory / ".opencode" / "opencode.jsonc").write_text(
            json.dumps(chosen, indent=1), encoding="utf-8"
        )
        (directory / GLOBAL_CONFIG).mkdir(parents=True)
        if workspace is not None:
            for name, text in workspace.files.items():
                target = (directory / name).resolve()
                if not target.is_relative_to(directory):
                    shutil.rmtree(directory, ignore_errors=True)
                    raise HostSetupError(f"a workspace file lies outside: {name}")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
            (directory / GLOBAL_CONFIG / BRIEF).write_text(
                workspace.instructions, encoding="utf-8"
            )
        return directory

    def environment(self, directory: Path) -> dict[str, str]:
        """The inherited environment, with the attempt's own global config."""
        return {
            **os.environ,
            "PWD": str(directory),
            "OPENCODE_CONFIG_DIR": str(directory / GLOBAL_CONFIG),
        }

    def check(self) -> None:
        """Read the host's version into its name, and make and remove one
        host directory, so a scratch inside a Git tree is refused before
        any question is asked. Model-free."""
        try:
            done = subprocess.run(
                [self.executable, "--version"],
                capture_output=True,
                text=True,
                timeout=60,
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise HostSetupError(
                f"`{self.executable} --version` failed: {error}"
            ) from None
        version = done.stdout.strip().removeprefix("opencode").strip()
        if done.returncode != 0 or not version:
            raise HostSetupError(
                f"`{self.executable} --version` gave no version: {done.stderr.strip()}"
            )
        self.name = f"opencode {version}"
        shutil.rmtree(self._directory(), ignore_errors=True)

    def ask_queue(
        self, session: QueueSession, timeout: float, spend_max: float | None = None
    ) -> HostAnswer:
        """Refused: OpenCode serves no queue session (docs/architecture.md
        § 3)."""
        raise HostSetupError(
            "OpenCode serves no queue session: ask this step under Claude Code "
            "(a profile's harness `claude-code`)"
        )

    def tool_server_error(self, server: ToolServer) -> str | None:
        """None: OpenCode serves no queue session, and says so when asked
        one (`ask_queue`)."""
        return None

    def abort(self) -> None:
        """End every attempt in flight, and its process group."""
        with self._live_lock:
            live = list(self._live)
        for process in live:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(process.pid, signal.SIGKILL)

    def argv(self, session: bool = False, going_on: str | None = None) -> list[str]:
        """The command; the prompt goes on stdin. `session`: a session on
        files' agent; `going_on`: the session it continues."""
        argv = [
            self.executable,
            "run",
            "--standalone",
            "--agent",
            SESSION_AGENT if session else AGENT,
            "-m",
            self.asked,
            "--format",
            "json",
            "--title",
            SESSION_TITLE if session else TITLE,
        ]
        if going_on is not None:
            argv += ["--session", going_on]
        return argv

    def export_argv(self, session: str) -> list[str]:
        return [self.executable, "session", "export", "--standalone", session]

    def _export(self, directory: Path, session: str) -> tuple[Any, str]:
        """The session's record, or why there is none."""
        process = subprocess.Popen(
            self.export_argv(session),
            cwd=directory,
            env=self.environment(directory),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            out, err = process.communicate(timeout=EXPORT_SECONDS)
        except subprocess.TimeoutExpired:
            return None, f"`session export` timed out after {EXPORT_SECONDS:g} s"
        finally:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        if process.returncode != 0:
            detail = err.decode("utf-8", errors="replace").strip()[-500:]
            return None, f"`session export` exited with {process.returncode}: {detail}"
        try:
            return json.loads(out), ""
        except json.JSONDecodeError:
            return None, "`session export` gave no JSON"

    def ask(
        self,
        prompt: str,
        timeout: float,
        workspace: Workspace | None = None,
        spend_max: float | None = None,
    ) -> HostAnswer:
        """`spend_max` is not kept: OpenCode takes no spending limit, so
        an attempt here is bounded by its timeout alone. A session a tool
        server serves (a review by block) is refused, as a queue session
        is (docs/architecture.md § 3)."""
        if workspace is not None and workspace.served is not None:
            raise HostSetupError(
                "OpenCode serves no tool server: ask a review session by block "
                "under Claude Code (a profile's harness `claude-code`)"
            )
        size = len(prompt.encode("utf-8"))
        if size > self.prompt_limit:
            raise PromptTooLong(
                f"the prompt is {size:,} bytes, over the limit of {self.prompt_limit:,}"
            )
        directory = self._directory(workspace)
        gate: Path | None = None
        try:
            if workspace is not None and workspace.gate is not None:
                gate = Path(
                    tempfile.mkdtemp(prefix="axiomation-gate-", dir=self._base())
                ).resolve()
                (gate / GATE).write_text(workspace.gate.spec, encoding="utf-8")
            return self._run(directory, prompt, timeout, workspace, gate)
        finally:
            shutil.rmtree(directory, ignore_errors=True)
            if gate is not None:
                shutil.rmtree(gate, ignore_errors=True)

    def _gate(self, workspace: Workspace, gate: Path, directory: Path) -> str | None:
        """The gate's reason to go on, run as Claude Code runs a Stop hook
        (`ClaudeCodeHost.ask`); None when it lets the session end. Like
        the hook, it never fails the session: a gate that cannot run, or
        says nothing it can read, lets it end, and the runner's check
        decides."""
        assert workspace.gate is not None
        command = [
            sys.executable,
            "-I",
            "-m",
            workspace.gate.module,
            str(gate / GATE),
            str(directory / workspace.edit),
        ]
        try:
            done = subprocess.run(
                command,
                cwd=gate,
                input=json.dumps({"hook_event_name": "Stop"}),
                capture_output=True,
                text=True,
                timeout=GATE_SECONDS,
            )
            decision = json.loads(done.stdout) if done.stdout.strip() else {}
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            return None
        if isinstance(decision, dict) and decision.get("decision") == "block":
            return str(decision.get("reason") or "")
        return None

    def _run(
        self,
        directory: Path,
        prompt: str,
        timeout: float,
        workspace: Workspace | None = None,
        gate: Path | None = None,
    ) -> HostAnswer:
        started = time.monotonic()
        ran = self._process(
            directory, self.argv(workspace is not None), prompt, timeout
        )
        transcript, stderr = ran.out, ran.err
        session = session_of(transcript)
        if workspace is not None and gate is not None:
            for _ in range(GATE_CONTINUATIONS):
                left = timeout - (time.monotonic() - started)
                if (
                    ran.timed_out
                    or ran.interrupted
                    # no new process once the run is interrupted: what
                    # this one wrote is the answer
                    or interrupted()
                    or ran.exit_code != 0
                    or session is None
                    or left <= 0
                ):
                    break
                reason = self._gate(workspace, gate, directory)
                if reason is None:
                    break
                ran = self._process(directory, self.argv(True, session), reason, left)
                transcript += ran.out
                stderr += ran.err
        seconds = time.monotonic() - started
        answer = parse(transcript, self.model)
        uncounted = ""
        if session is None:
            uncounted = "the host's events name no session"
        else:
            record, uncounted = self._export(directory, session)
            if record is not None:
                recorded = exported(record, self.model)
                if recorded is None:
                    uncounted = "the session's record names no cost"
                else:
                    transcript += (
                        json.dumps({"type": "session_export", "export": record}) + "\n"
                    )
                    answer = HostAnswer(
                        text=recorded.text or answer.text,
                        model=self.model,
                        cost=recorded.cost,
                        tokens=recorded.tokens,
                        tool_uses=answer.tool_uses
                        + tuple(
                            use
                            for use in recorded.tool_uses
                            if use not in answer.tool_uses
                        ),
                        error=answer.error,
                    )
        errors = [answer.error] if answer.error else []
        if ran.timed_out:
            errors.append(f"timed out after {timeout:g} s")
        if ran.interrupted:
            errors.append("interrupted")
        exit_code = ran.exit_code
        if stderr.strip() and (exit_code != 0 or errors):
            errors.append(stderr.strip()[-2000:])
        if exit_code != 0 and not errors:
            errors.append(f"opencode exited with {exit_code}")
        text = answer.text
        planted = None
        if workspace is not None:
            edited = directory / workspace.edit
            # read through no link the session could have made
            try:
                text = read_regular(edited) if edited.exists() else ""
            except NotRegularFile as error:
                text, planted = "", f"the session's answer file is no file: {error}"
        answer = HostAnswer(
            text=text,
            model=self.model,
            cost=answer.cost,
            tokens=answer.tokens,
            seconds=seconds,
            tool_uses=answer.tool_uses,
            transcript=transcript,
            exit_code=exit_code,
            error="\n".join(errors),
        )
        if planted:
            raise HostBreach(planted, answer)
        if workspace is not None:
            breach = session_breach(answer.tool_uses, directory, workspace.edit)
            if breach:
                raise HostBreach(breach, answer)
        else:
            ran_tools = [use for use in answer.tool_uses if use.get("status") in RAN]
            if ran_tools:
                raise HostBreach(
                    f"the model's {ran_tools[0].get('tool')} ran, though every "
                    "action is denied",
                    answer,
                )
            outside = outside_paths(answer.tool_uses, directory)
            if outside:
                tool, path = outside[0]
                raise HostBreach(
                    f"the model's {tool} named {path}, outside its directory", answer
                )
        if ran.interrupted:
            # its `session export` says what it spent so far, where it can
            raise HostInterrupted(
                "the session was cut off by an interrupt", answer, whole=not uncounted
            )
        if answer.error and BILLING.search(answer.error):
            raise HostOutOfCredit(answer.error, answer)
        if uncounted:
            raise HostUncounted(
                f"what the attempt cost is unknown: {uncounted}", answer
            )
        return answer

    def _process(
        self,
        directory: Path,
        argv: list[str],
        prompt: str,
        timeout: float,
    ) -> _Ran:
        """One `opencode run` in `directory`, `prompt` on its stdin."""
        process = subprocess.Popen(
            argv,
            cwd=directory,
            env=self.environment(directory),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        with self._live_lock:
            self._live.add(process)
        out: list[bytes] = []
        err: list[bytes] = []
        readers = [
            threading.Thread(target=_drain, args=(process.stdout, out), daemon=True),
            threading.Thread(target=_drain, args=(process.stderr, err), daemon=True),
            # A thread of its own, so a host that never reads its stdin
            # still times out.
            threading.Thread(
                target=_feed, args=(process.stdin, prompt.encode("utf-8")), daemon=True
            ),
        ]
        for reader in readers:
            reader.start()
        ended = Waited.TIMED_OUT
        try:
            ended = waited(process, timeout)
        finally:
            # `--standalone` starts a server of its own: end the whole
            # group, whether the leader finished or not, so nothing
            # outlives the attempt or holds its pipes.
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            for reader in readers:
                reader.join(DRAIN_SECONDS)
            with self._live_lock:
                self._live.discard(process)
        return _Ran(
            out=b"".join(out).decode("utf-8", errors="replace"),
            err=b"".join(err).decode("utf-8", errors="replace"),
            exit_code=process.returncode if ended is Waited.EXITED else -signal.SIGKILL,
            ended=ended,
        )


def _feed(pipe: Any, data: bytes) -> None:
    with contextlib.suppress(OSError, ValueError), pipe:
        pipe.write(data)


def _drain(pipe: Any, into: list[bytes]) -> None:
    with contextlib.suppress(OSError, ValueError):
        for chunk in iter(lambda: pipe.read(65536), b""):
            into.append(chunk)


def _events(transcript: str) -> Iterator[dict[str, Any]]:
    for line in transcript.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            yield event


def session_of(transcript: str) -> str | None:
    """The session the events belong to."""
    for event in _events(transcript):
        session = event.get("sessionID")
        if isinstance(session, str) and session:
            return session
    return None


def _tokens(counts: Any) -> dict[str, int]:
    counts = counts if isinstance(counts, dict) else {}
    cache = counts.get("cache") if isinstance(counts.get("cache"), dict) else {}
    return {
        "input": int(counts.get("input") or 0),
        "output": int(counts.get("output") or 0),
        "reasoning": int(counts.get("reasoning") or 0),
        "cache_read": int(cache.get("read") or 0),
        "cache_write": int(cache.get("write") or 0),
    }


def _tool_use(part: dict[str, Any]) -> dict[str, Any]:
    """An event's tool part (`tool`) or an export's tool item (`name`)."""
    state = part.get("state") if isinstance(part.get("state"), dict) else {}
    return {
        "tool": part.get("tool") or part.get("name"),
        "input": state.get("input"),
        "status": state.get("status"),
    }


def exported(record: Any, model: str) -> HostAnswer | None:
    """`opencode session export`'s record: the assistant messages' text in
    order, the session's cost and tokens, and each tool use (any content
    item that is a tool, as in `parse`). None when it names no cost: a
    zero cost, as a Go model reports, is a cost."""
    if not isinstance(record, dict) or not isinstance(record.get("info"), dict):
        return None
    info = record["info"]
    cost = info.get("cost")
    if isinstance(cost, bool) or not isinstance(cost, int | float):
        return None
    texts: list[str] = []
    tools: list[dict[str, Any]] = []
    messages = (
        record.get("messages") if isinstance(record.get("messages"), list) else []
    )
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = (
            message.get("content") if isinstance(message.get("content"), list) else []
        )
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "tool" or "tool" in item:
                tools.append(_tool_use(item))
            elif item.get("type") == "text" and message.get("type") == "assistant":
                texts.append(str(item.get("text", "")))
    return HostAnswer(
        text="".join(texts),
        model=model,
        cost=float(cost),
        tokens=_tokens(info.get("tokens")),
        tool_uses=tuple(tools),
    )


def parse(transcript: str, model: str) -> HostAnswer:
    """`opencode run --format json`'s events: the text parts in order, the
    cost and tokens summed over the steps, each tool use (any event whose
    part is a tool, whatever the event is called, so an event a later
    OpenCode renames still fails closed), and the error events as the answer's
    error."""
    texts: list[str] = []
    cost = 0.0
    tokens = {
        "input": 0,
        "output": 0,
        "reasoning": 0,
        "cache_read": 0,
        "cache_write": 0,
    }
    tools: list[dict[str, Any]] = []
    errors: list[str] = []
    for event in _events(transcript):
        kind = event.get("type")
        part = event.get("part") if isinstance(event.get("part"), dict) else {}
        if kind == "text":
            texts.append(str(part.get("text", "")))
        elif kind == "step_finish":
            cost += float(part.get("cost") or 0.0)
            for name, value in _tokens(part.get("tokens")).items():
                tokens[name] += value
        elif kind == "tool_use" or part.get("type") == "tool" or "tool" in part:
            tools.append(_tool_use(part))
        elif kind == "error":
            errors.append(json.dumps(event.get("error"), ensure_ascii=False))
    return HostAnswer(
        text="".join(texts),
        model=model,
        cost=cost,
        tokens=tokens,
        tool_uses=tuple(tools),
        error="\n".join(errors),
    )


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _resolved(value: str, directory: Path) -> Path:
    path = Path(os.path.expanduser(value))
    if not path.is_absolute():
        path = directory / path
    return Path(os.path.normpath(path)).resolve()


def session_breach(
    tool_uses: tuple[dict[str, Any], ...],
    directory: Path,
    edit: str,
) -> str:
    """Why a session on files breached its confinement, or empty: a tool
    that ran other than a read or an edit; a read outside
    `directory`; an edit of any file but `edit`; or a read or an edit that
    ran on no path this can check. Path fields only: an edit's text is the
    model's, and holds `$` and `/` as LaTeX."""
    directory = directory.resolve()
    answer_file = _resolved(edit, directory)
    for use in tool_uses:
        if use.get("status") not in RAN:
            continue
        tool = str(use.get("tool"))
        given = use.get("input") if isinstance(use.get("input"), dict) else {}
        if tool not in READ_TOOLS | EDIT_TOOLS:
            return f"the model's {tool} ran, though it was not given"
        paths = [
            value for name in PATH_FIELDS if isinstance(value := given.get(name), str)
        ]
        if not paths:
            return f"the model's {tool} ran on no path the host can check"
        for value in paths:
            resolved = _resolved(value, directory)
            if tool in EDIT_TOOLS and resolved != answer_file:
                return f"the model's {tool} changed {value}, not {edit}"
            if not resolved.is_relative_to(directory):
                return f"the model's {tool} reached {value}, outside its directory"
    return ""


def outside_paths(
    tool_uses: tuple[dict[str, Any], ...], directory: Path
) -> list[tuple[str, str]]:
    """Each tool use's input words that could name a place outside
    `directory`, as `(tool, word)`. Fails closed: a word from the root or
    from home, a variable that does not expand to a place inside, or one
    that climbs out, counts, even a lone `/` in prose."""
    found = []
    for use in tool_uses:
        for text in _strings(use.get("input")):
            for word in _WORDS.split(text):
                if not word or not (
                    word.startswith(("/", "~", "$")) or ".." in word.split("/")
                ):
                    continue
                expanded = os.path.expanduser(os.path.expandvars(word))
                if "$" in expanded:
                    found.append((str(use.get("tool")), word))
                    continue
                path = Path(expanded)
                if not path.is_absolute():
                    path = directory / path
                resolved = Path(os.path.normpath(path)).resolve()
                if not resolved.is_relative_to(directory):
                    found.append((str(use.get("tool")), word))
    return found
