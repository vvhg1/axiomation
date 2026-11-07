"""The Claude Code host: `claude -p`, headless, one process per attempt.

Each attempt runs in a fresh directory outside any Git tree, holding the
workspace's files and nothing else. The session has Claude Code's own tool set
but the web, under `--permission-mode dontAsk` with these allowed: Bash, in
Claude Code's sandbox; Read, Edit and Write in the directory and in `/tmp`;
Task and TodoWrite. The sandbox lets Bash write only there, reach no network,
and read nothing under the user's home (where their repositories and private
files are), Claude's directory under `/tmp`, where other sessions keep scratch
spaces that may hold private files, or the per-user temporary directories; the file
tools are denied the same. Any other use is refused. A host given `tools` (a review
session's, `READING_TOOLS`) has those alone, and its prompt's per-machine
sections move into the first message, so sessions share the prompt's cache.
Without a workspace the session has no tool; a workspace that names its
tools (`Workspace.tools`) has those. The brief goes in as an appended
system prompt, or as the whole one where the workspace says so
(`Workspace.brief_alone`, a review's), and the settings as a file, both
beside the directory, not in it. A review session by block is served its
check and its cited works' reads by a tool server (`Workspace.served`,
`hosts.written_tools`). A workspace's gate is a Stop
hook in those settings: Claude Code runs it, as this process's Python on
this package in isolated mode (`-I`, so nothing the session writes in its
directory is imported), outside the sandbox, each time
the session tries to end, and hands its problems back to the session; its spec
lies beside the settings, and is read as JSON only. A gate `on_write` is also a
PostToolUse hook, run the same way after each tool use, in its write mode.

The user's Claude Code configuration is kept out: no setting source
(`--setting-sources ""`: no CLAUDE.md, settings, hooks or plugins of
theirs), no
MCP server but those given (none), and no session kept
(`--no-session-persistence`). Claude Code still makes a project entry
for the directory in `~/.claude/projects`, where it stores large tool
outputs; it is removed after the attempt.

The answer's cost, tokens and text are the last `result` event's: its
`modelUsage` holds the session's tokens per model, every subagent's and
every turn it was woken for, where its `usage` holds the last turn's. An attempt
whose events name no cost raises `HostUncounted`. A web tool that ran
where no subagent of the workspace is given it,
or a file tool that ran on a path outside the directory and `/tmp` or
inside a closed one, raises `HostBreach`; a refused use is no breach.
Claude Code keeps its temporary files, its Bash's `TMPDIR` among them, in
a directory of the attempt's own, removed after it, not in Claude's
directory under `/tmp`, which is closed; a Claude Code that keeps them
there again raises `HostSetupError`.
A session given a spending limit runs under `--max-budget-usd`, and one
that reaches it raises `HostOverBudget`.
Credentials are Claude Code's own: the environment is inherited, and our
code reads, passes and stores no key.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from axiomation.questions import (
    GATE_LOG,
    Helper,
    QueueSession,
    ToolServer,
    Workspace,
)
from axiomation.questions.gates import NotRegularFile, read_regular
from axiomation.queue import SERVER_NAME as SERVED_NAME
from axiomation.runner.hosts import (
    HostAnswer,
    HostBreach,
    HostOutOfCredit,
    HostOverBudget,
    HostUncounted,
    written_tools,
)
from axiomation.runner.opencode import (
    BILLING,
    GATE_SECONDS,
    HostSetupError,
    in_git_tree,
)

SCRATCH_ENV = "AXIOMATION_HOST_SCRATCH"
DEFAULT_MODEL = "haiku"
#: Claude Code's own prompt and tools, written to the cache once a session
#: whatever the question: about 21,000 tokens in its recorded sessions,
#: most of a short session's cost.
PROMPT_TOKENS = 21_000

#: The web tools: a session never gets them, but where a subagent its
#: workspace defines is given one (a forager).
WEB = ("WebFetch", "WebSearch")
#: The web tool a queue session whose step searches the web is given
#: (`QueueSession.web`): never a fetch, a page comes through the paper's
#: sources.
QUEUE_WEB = ("WebSearch",)

#: A reading session's tools: the file tools and
#: Bash, no to-do list, and no subagent but one its workspace defines
#: (`SUBAGENT`, `Workspace.helpers`). Claude Code's prompt is then about
#: 8k tokens, not 17k, paid again in every session and turn.
READING_TOOLS = ("Bash", "Read", "Edit", "Write", "Grep", "Glob")
#: The tool that hands a task to a subagent the workspace defines.
SUBAGENT = "Agent"

#: The temporary directory a session may use besides its own.
TMP = Path("/private/tmp") if Path("/private/tmp").is_dir() else Path("/tmp")

#: Where the host's directories go unless told otherwise.
DEFAULT_SCRATCH = TMP / "axiomation-host"
#: Claude's directory under /tmp, where Claude Code keeps its temporary
#: files unless `CLAUDE_TMP_ENV` names another place; other sessions keep
#: their scratch here.
CLAUDE_TMP = TMP / f"claude-{os.getuid()}"
#: The variable that names where Claude Code makes its directory
#: (`claude-<uid>`): its Bash's state, its scratchpad and the `TMPDIR` its
#: sandbox gives Bash all go there.
CLAUDE_TMP_ENV = "CLAUDE_CODE_TMPDIR"
#: The longest such directory Claude Code gives its Bash as `TMPDIR`; past
#: it, Bash gets `CLAUDE_TMP` again (Claude Code 2.1.308).
CLAUDE_TMP_BYTES = 44


def closed_paths() -> tuple[Path, ...]:
    """What a session may not read: every user's home,
    the per-user temporary directories, mounted volumes, and Claude's
    directory under /tmp (other sessions' scratch spaces, which may hold
    private files). A session's own Claude Code keeps its temporary
    files in the attempt's directory (`attempt_tmp`), not there."""
    return (
        Path.home(),
        Path("/Users"),
        CLAUDE_TMP,
        TMP / "cc-socks",
        Path("/private/var/folders"),
        Path("/Volumes"),
    )


@contextlib.contextmanager
def attempt_tmp() -> Iterator[Path]:
    """A directory of the attempt's own for Claude Code's temporary files,
    named by `CLAUDE_TMP_ENV`, removed with all it holds once the attempt
    ends. Short, under /tmp: a longer one would send the session's Bash
    back to `CLAUDE_TMP`, refused here instead."""
    made = Path(tempfile.mkdtemp(prefix="ax-", dir=TMP))
    try:
        within = made / f"claude-{os.getuid()}"
        if len(str(within).encode("utf-8")) > CLAUDE_TMP_BYTES:
            raise HostSetupError(
                f"Claude Code's directory {within} passes {CLAUDE_TMP_BYTES} bytes; "
                f"its Bash would keep its files in {CLAUDE_TMP} instead"
            )
        yield made
    finally:
        shutil.rmtree(made, ignore_errors=True)


def _as_tmp(path: Path) -> tuple[str, ...]:
    """A permission rule's path for `path` as written under `/tmp`, where
    it lies under `/private/tmp`, which `/tmp` links to."""
    private = Path("/private/tmp")
    if not path.is_relative_to(private):
        return ()
    return (f"//tmp/{path.relative_to(private)}/**",)


def _kept_in_claude_tmp(work: Path) -> None:
    """Refuse an attempt whose Claude Code kept its folder for `work` in
    Claude's directory under /tmp, removed: it no longer reads
    `CLAUDE_TMP_ENV`, so its Bash's `TMPDIR` lay there, closed, and its
    Bash was refused. Every later session would be the same."""
    kept = CLAUDE_TMP / re.sub(r"[^A-Za-z0-9]", "-", str(work))
    if kept.exists():
        shutil.rmtree(kept, ignore_errors=True)
        raise HostSetupError(
            f"Claude Code kept the session's files in {kept}, not in the "
            f"attempt's directory: it no longer reads {CLAUDE_TMP_ENV}"
        )


#: The subdirectory the model works in; its siblings hold the brief and
#: the settings.
WORK = "work"
BRIEF = "brief.md"
SETTINGS = "settings.json"
#: The gate's spec, beside the settings; Claude Code waits
#: `GATE_SECONDS` for the gate.
GATE = "gate.json"
#: The gate's last argument in its write mode: run after each
#: tool use, it checks what is wrong so far and never ends the session.
WRITE_MODE = "write"

#: How long the pipes are drained after the process group is ended.
DRAIN_SECONDS = 10.0

#: A tool input's fields that name a path.
PATH_FIELDS = ("file_path", "path", "notebook_path")
#: The host's own tools a queue session reads the files laid in its folder
#: with, if any are laid there.
QUEUE_READS = ("Read", "Grep")


#: A queue session's tool server settings, beside the settings.
MCP_CONFIG = "mcp.json"
#: How long Claude Code waits for a queue session's tool server to start:
#: it opens the kept state first.
SERVER_START_SECONDS = 300.0

#: How long a session on a tool server (a queue's, a review's by block)
#: keeps its prompt cache (Claude Code's `CLAUDE_CODE_PROMPT_CACHE_TTL`).
#: Its calls come seconds apart, a subagent's included, so a five-minute
#: write, at about two thirds of an hour's, is never written again; a wait
#: past five minutes (in a step's `next`) writes the context once more.
#: Claude Code's own choice, an hour on a subscription, holds for the
#: other sessions.
SERVED_CACHE_TTL = "5m"

#: The `result` event's subtype where a session ended at its
#: `--max-budget-usd`, checked after each turn, so the session may pass it
#: by that turn (Claude Code 2.1.312: `is_error`, a cost, exit code 1).
OVER_BUDGET = "error_max_budget_usd"

#: Claude Code's effort levels (`claude --effort`).
EFFORTS = ("low", "medium", "high", "xhigh", "max")


def inherited_effort(model: str, config: Path | None = None) -> str | None:
    """The effort level the user's Claude Code would run `model` at: its
    entry in the user settings' `modelSettings`, else their `effortLevel`;
    `None` where neither is set. The session itself loads no settings
    (`--setting-sources ""`), so the level is read here and passed as
    `--effort`. `config` is Claude Code's directory (`CLAUDE_CONFIG_DIR`,
    else `~/.claude`)."""
    base = config or Path(
        os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude"
    )
    path = base / "settings.json"
    if not path.exists():
        return None
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise HostSetupError(
            f"cannot read the effort level from {path}: {error}"
        ) from None
    by_model = (settings.get("modelSettings") or {}).get(model) or {}
    level = by_model.get("effortLevel") or settings.get("effortLevel")
    if level is None:
        return None
    if level not in EFFORTS:
        raise HostSetupError(f"{path}: effortLevel {level!r} is not one of {EFFORTS}")
    return level


class ClaudeCodeHost:
    """`claude -p --model MODEL --output-format stream-json` in a fresh
    directory per attempt."""

    prompt_tokens = PROMPT_TOKENS

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        executable: str = "claude",
        scratch: Path | None = None,
        projects: Path | None = None,
        effort: str | None = None,
        tools: tuple[str, ...] | None = None,
    ) -> None:
        self.model = model
        #: `--effort`; `None` leaves Claude Code's built-in default, since
        #: the session loads no settings.
        self.effort = effort
        #: The tools a session with a workspace has (`--tools`), its system
        #: prompt's per-machine sections moved into the first message so
        #: sessions share its cache; `None`: Claude Code's own set but the
        #: web.
        self.tools = tools
        self.executable = executable
        self.scratch = scratch
        #: Claude Code's project entries, where the attempt's empty one is
        #: removed.
        self.projects = projects or Path.home() / ".claude" / "projects"
        #: `claude-code <version>` once `check()` has read it.
        self.name = "claude-code"
        self._live: set[subprocess.Popen[bytes]] = set()
        self._live_lock = threading.Lock()

    def _base(self) -> Path:
        chosen = self.scratch or os.environ.get(SCRATCH_ENV) or DEFAULT_SCRATCH
        base = Path(chosen).resolve()
        closed = [p for p in closed_paths() if base.is_relative_to(p.resolve())]
        if closed:
            raise HostSetupError(
                f"the host's scratch {base} lies in {closed[0]}, which a session "
                f"may not read; set {SCRATCH_ENV} to a directory under {TMP}"
            )
        base.mkdir(parents=True, exist_ok=True)
        return base

    def _directory(self) -> Path:
        root = Path(
            tempfile.mkdtemp(prefix="axiomation-host-", dir=self._base())
        ).resolve()
        if in_git_tree(root):
            shutil.rmtree(root, ignore_errors=True)
            raise HostSetupError(
                f"the host directory {root} lies inside a Git tree; set "
                f"{SCRATCH_ENV} to a directory outside every repository"
            )
        (root / WORK).mkdir()
        return root

    @staticmethod
    def settings(
        work: Path,
        tmp: Path,
        gate: str | None = None,
        on_write: str | None = None,
        closed: Sequence[Path] | None = None,
        web: Sequence[str] = (),
    ) -> dict[str, Any]:
        """The session's settings file: the sandbox and the permissions,
        writing `work` and `tmp`, the attempt's own temporary directory
        (`attempt_tmp`), and nothing else: the host's files beside `work`
        (the settings, the gate's spec, the tool server's command) lie
        under the same temporary directory, and a session that rewrote
        them would run code outside the sandbox. Allowing `web`, the web
        tools a subagent of its workspace is given, closing `closed` (the
        attempt's `closed_paths()`, else taken now), with `gate`, the
        command run as a Stop hook each time the session tries to end, and
        with `on_write`, the command run as a PostToolUse hook after each
        of its tool uses."""
        closed = [str(p) for p in (closed_paths() if closed is None else closed)]
        # `/tmp` is a link to `/private/tmp`: a rule names the path as the
        # model writes it.
        tmps = sorted({f"/{tmp}/**", *_as_tmp(tmp)})
        settings: dict[str, Any] = {
            "sandbox": {
                "enabled": True,
                "failIfUnavailable": True,
                "allowUnsandboxedCommands": False,
                "autoAllowBashIfSandboxed": True,
                # no command runs outside the sandbox
                "excludedCommands": [],
                "filesystem": {
                    "denyRead": closed,
                    "allowRead": [str(work)],
                    "allowWrite": [str(work), str(tmp)],
                },
                "network": {"allowedDomains": [], "strictAllowlist": True},
            },
            "permissions": {
                "allow": [
                    "Bash",
                    "Read(./**)",
                    "Edit(./**)",
                    "Write(./**)",
                    *(
                        f"{tool}({tmp})"
                        for tmp in tmps
                        for tool in ("Read", "Edit", "Write")
                    ),
                    "Task",
                    "TodoWrite",
                    *web,
                ],
                "deny": [
                    f"{tool}(/{path}/**)"
                    for path in closed
                    for tool in ("Read", "Edit", "Write")
                ],
            },
        }
        hooks: dict[str, Any] = {}
        if on_write is not None:
            hooks["PostToolUse"] = [
                {
                    "matcher": "*",
                    "hooks": [
                        {
                            "type": "command",
                            "command": on_write,
                            "timeout": GATE_SECONDS,
                        }
                    ],
                }
            ]
        if gate is not None:
            hooks["Stop"] = [
                {
                    "hooks": [
                        {"type": "command", "command": gate, "timeout": GATE_SECONDS}
                    ]
                }
            ]
        if hooks:
            settings["hooks"] = hooks
        return settings

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
        version = done.stdout.strip().split(" ")[0]
        if done.returncode != 0 or not version:
            raise HostSetupError(
                f"`{self.executable} --version` gave no version: {done.stderr.strip()}"
            )
        self.name = f"claude-code {version}"
        shutil.rmtree(self._directory(), ignore_errors=True)

    def abort(self) -> None:
        with self._live_lock:
            live = list(self._live)
        for process in live:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(process.pid, signal.SIGKILL)

    def argv(
        self, root: Path, workspace: Workspace | None, spend_max: float | None = None
    ) -> list[str]:
        """The command; the prompt goes on stdin."""
        argv = [
            self.executable,
            "-p",
            "--model",
            self.model,
            "--no-session-persistence",
            "--setting-sources",
            "",
            "--strict-mcp-config",
            "--permission-mode",
            "dontAsk",
            "--output-format",
            "stream-json",
            "--verbose",
        ]
        if self.effort is not None:
            argv += ["--effort", self.effort]
        if spend_max is not None:
            argv += ["--max-budget-usd", f"{spend_max:.4f}"]
        if workspace is None:
            return [*argv, "--tools", ""]
        tools = _tools(self.tools, workspace)
        if tools is not None:
            argv += [
                "--tools",
                ",".join(tools),
                "--exclude-dynamic-system-prompt-sections",
            ]
        if workspace.helpers:
            argv += ["--agents", json.dumps(_agents(workspace.helpers))]
        if workspace.served is not None:
            argv += ["--mcp-config", str(root / MCP_CONFIG)]
        return [
            *argv,
            "--disallowedTools",
            *(t for t in WEB if t not in _web(workspace)),
            "--settings",
            str(root / SETTINGS),
            # a review's brief is its whole prompt, in place of the host's
            # own, which speaks of a coding agent's work
            "--system-prompt-file"
            if workspace.brief_alone
            else "--append-system-prompt-file",
            str(root / BRIEF),
        ]

    def ask(
        self,
        prompt: str,
        timeout: float,
        workspace: Workspace | None = None,
        spend_max: float | None = None,
    ) -> HostAnswer:
        root = self._directory()
        closed = closed_paths()
        if workspace is not None and workspace.served is not None:
            # its store is its tool server's to read, never the session's
            closed = (*closed, Path(workspace.served.store))
        held = contextlib.ExitStack()
        try:
            # made first: the settings name it, the one temporary place the
            # session writes
            tmp = held.enter_context(attempt_tmp())
            work = root / WORK
            if workspace is not None:
                for name, text in workspace.files.items():
                    target = (work / name).resolve()
                    if not target.is_relative_to(work):
                        raise HostSetupError(f"a workspace file lies outside: {name}")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(text, encoding="utf-8")
                (root / "tmp").mkdir()
                (root / BRIEF).write_text(workspace.instructions, encoding="utf-8")
                gate = on_write = None
                if workspace.gate is not None:
                    (root / GATE).write_text(workspace.gate.spec, encoding="utf-8")
                    # `-I`: the hook runs in the session's directory, which
                    # the model writes, and `-m` would put it first on the
                    # import path; isolated, Python reads the package alone.
                    command = [
                        sys.executable,
                        "-I",
                        "-m",
                        workspace.gate.module,
                        str(root / GATE),
                        str(work / workspace.edit),
                    ]
                    gate = shlex.join(command)
                    if workspace.gate.on_write:
                        on_write = shlex.join([*command, WRITE_MODE])
                settings = self.settings(
                    work.resolve(),
                    tmp,
                    gate,
                    on_write,
                    closed,
                    _web(workspace),
                )
                if workspace.served is not None:
                    settings["permissions"]["allow"].append(f"mcp__{SERVED_NAME}")
                (root / SETTINGS).write_text(
                    json.dumps(settings, indent=1), encoding="utf-8"
                )
            given = _tools(self.tools, workspace)
            served = None if workspace is None else workspace.served
            if served is not None:
                # its tool server's settings name the spec its check reads
                # and the work it reads: never where the session writes
                store = Path(served.store).resolve()
                if any(store.is_relative_to(p.resolve()) for p in (work, TMP)):
                    raise HostSetupError(
                        f"the store {store} lies where a session writes: a review "
                        "session's tool server keeps its settings there"
                    )
            with written_tools(
                served,
                ""
                if workspace is None or workspace.gate is None
                else workspace.gate.spec,
                work,
                root.name,
            ) as server:
                limits = {}
                if server is not None:
                    (root / MCP_CONFIG).write_text(
                        json.dumps(_mcp_config(server), indent=1), encoding="utf-8"
                    )
                    limits = {
                        "MCP_TOOL_TIMEOUT": str(round(server.call_seconds * 1000)),
                        "MCP_TIMEOUT": str(round(SERVER_START_SECONDS * 1000)),
                        "CLAUDE_CODE_PROMPT_CACHE_TTL": SERVED_CACHE_TTL,
                    }
                answer = self._run(
                    root,
                    prompt,
                    timeout,
                    self.argv(root, workspace, spend_max),
                    limits,
                    tmp,
                    None if workspace is None else work / workspace.edit,
                    lambda uses: _breach(
                        uses,
                        work,
                        workspace,
                        closed,
                        given,
                        None if server is None else server.name,
                    ),
                    spend_max,
                )
            _kept_in_claude_tmp(work)
            return answer
        finally:
            held.close()
            shutil.rmtree(root, ignore_errors=True)
            self._forget(root / WORK)

    def queue_argv(
        self, root: Path, session: QueueSession, spend_max: float | None = None
    ) -> list[str]:
        """A queue session's command: no tool but its server's, and the
        host's reads where files are laid in its folder; no MCP server but
        its own; its brief the whole system prompt, in place of the host's
        own, which speaks of a coding agent's work; the prompt goes on
        stdin."""
        argv = [
            self.executable,
            "-p",
            "--model",
            self.model,
            "--no-session-persistence",
            "--setting-sources",
            "",
            "--mcp-config",
            str(root / MCP_CONFIG),
            "--strict-mcp-config",
            "--permission-mode",
            "dontAsk",
            "--output-format",
            "stream-json",
            "--verbose",
            "--tools",
            ",".join(
                [
                    *(QUEUE_READS if session.files else ()),
                    *(QUEUE_WEB if session.web else ()),
                ]
            ),
            "--exclude-dynamic-system-prompt-sections",
        ]
        if self.effort is not None:
            argv += ["--effort", self.effort]
        if spend_max is not None:
            argv += ["--max-budget-usd", f"{spend_max:.4f}"]
        if session.context_tokens is not None:
            argv += ["--autocompact", str(session.context_tokens)]
        return [
            *argv,
            "--settings",
            str(root / SETTINGS),
            "--system-prompt-file",
            str(root / BRIEF),
        ]

    def ask_queue(
        self, session: QueueSession, timeout: float, spend_max: float | None = None
    ) -> HostAnswer:
        """A queue session: its tasks are answered through its tool server,
        which spools them; the answer is the session's last message, its
        cost and its tokens (docs/architecture.md § 2)."""
        root = self._directory()
        closed = closed_paths()
        server = session.server
        held = contextlib.ExitStack()
        try:
            # made first: the settings name it, the one temporary place the
            # session writes
            tmp = held.enter_context(attempt_tmp())
            work = root / WORK
            for name, text in session.files.items():
                target = (work / name).resolve()
                if not target.is_relative_to(work.resolve()):
                    raise HostSetupError(f"a queue session's file lies outside: {name}")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
            (root / "tmp").mkdir()
            (root / BRIEF).write_text(session.instructions, encoding="utf-8")
            settings = self.settings(
                work.resolve(),
                tmp,
                closed=closed,
                web=QUEUE_WEB if session.web else (),
            )
            settings["permissions"]["allow"].append(f"mcp__{server.name}")
            if session.files:
                settings["permissions"]["allow"].append("Grep(./**)")
            (root / SETTINGS).write_text(
                json.dumps(settings, indent=1), encoding="utf-8"
            )
            (root / MCP_CONFIG).write_text(
                json.dumps(_mcp_config(server), indent=1), encoding="utf-8"
            )
            limits = {
                "MCP_TOOL_TIMEOUT": str(round(server.call_seconds * 1000)),
                "MCP_TIMEOUT": str(round(SERVER_START_SECONDS * 1000)),
                "CLAUDE_CODE_PROMPT_CACHE_TTL": SERVED_CACHE_TTL,
            }
            answer = self._run(
                root,
                session.prompt,
                timeout,
                self.queue_argv(root, session, spend_max),
                limits,
                tmp,
                None,
                lambda uses: _queue_breach(
                    uses, server.name, work if session.files else None, session.web
                ),
                spend_max,
            )
            _kept_in_claude_tmp(work)
            return answer
        finally:
            held.close()
            shutil.rmtree(root, ignore_errors=True)
            self._forget(root / WORK)

    def _forget(self, work: Path) -> None:
        """Remove what Claude Code kept for `work`: its project entry, where
        it stores large tool outputs even with no session kept. It is named
        after `work`, a directory made for this attempt alone, so it is the
        attempt's."""
        name = re.sub(r"[^A-Za-z0-9]", "-", str(work))
        shutil.rmtree(self.projects / name, ignore_errors=True)

    def _run(
        self,
        root: Path,
        prompt: str,
        timeout: float,
        argv: Sequence[str],
        env: Mapping[str, str],
        tmp: Path,
        edited: Path | None,
        breach: Callable[[tuple[dict[str, Any], ...]], str],
        spend_max: float | None = None,
    ) -> HostAnswer:
        """The session run: `argv` with `env` added, its answer the text of
        `edited` where given, else its last message; `breach` says why its
        tool uses breached its confinement, or nothing."""
        work = root / WORK
        started = time.monotonic()
        process = subprocess.Popen(
            argv,
            cwd=work,
            # The per-user temporary directory is closed to the sandbox.
            env={
                **os.environ,
                "PWD": str(work),
                "TMPDIR": str(root / "tmp"),
                CLAUDE_TMP_ENV: str(tmp),
                **env,
            },
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
            threading.Thread(
                target=_feed, args=(process.stdin, prompt.encode("utf-8")), daemon=True
            ),
        ]
        for reader in readers:
            reader.start()
        timed_out = False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            for reader in readers:
                reader.join(DRAIN_SECONDS)
            with self._live_lock:
                self._live.discard(process)
        seconds = time.monotonic() - started
        transcript = b"".join(out).decode("utf-8", errors="replace")
        stderr = b"".join(err).decode("utf-8", errors="replace")
        events = parse(transcript)
        transcript += _gate_checks(root)

        errors = [events.error] if events.error else []
        if timed_out:
            errors.append(f"timed out after {timeout:g} s")
        exit_code = process.returncode if not timed_out else -signal.SIGKILL
        if stderr.strip() and (exit_code != 0 or errors):
            errors.append(stderr.strip()[-2000:])
        if exit_code != 0 and not errors:
            errors.append(f"claude exited with {exit_code}")
        text = events.text
        planted = None
        if edited is not None:
            # the session could make its file a link to one its sandbox
            # closes: read through none
            try:
                text = read_regular(edited) if edited.exists() else ""
            except NotRegularFile as error:
                text, planted = "", f"the session's answer file is no file: {error}"
        answer = HostAnswer(
            text=text,
            model=self.model,
            cost=events.cost or 0.0,
            tokens=events.tokens,
            tokens_by_model=events.tokens_by_model,
            seconds=seconds,
            tool_uses=events.tool_uses,
            transcript=transcript,
            exit_code=exit_code,
            error="\n".join(errors),
        )
        breached = planted or breach(events.tool_uses)
        if breached:
            raise HostBreach(breached, answer)
        if answer.error and BILLING.search(answer.error):
            raise HostOutOfCredit(answer.error, answer)
        if events.over_budget:
            raise HostOverBudget(
                f"the session reached its limit of ${spend_max or 0:.2f}, what was "
                f"left of the run's budget: {answer.error}",
                answer,
            )
        if events.cost is None:
            raise HostUncounted(
                "what the attempt cost is unknown: the events hold no result "
                "with a cost",
                answer,
            )
        return answer


def _gate_checks(root: Path) -> str:
    """The checks the gate logged (`GATE_LOG`), as transcript events of type
    `gate_check`, after the session's own."""
    path = root / GATE_LOG
    if not path.exists():
        return ""
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        with contextlib.suppress(ValueError):
            entry = json.loads(line)
            if isinstance(entry, dict):
                lines.append(json.dumps({"type": "gate_check", **entry}) + "\n")
    return "".join(lines)


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


@dataclass
class Events:
    """What a session's stream-json events say: the result's text, cost
    (`None` when no result names one), tokens and error, and each tool
    use with whether it ran (`status`: `completed`, `refused`, or `None`
    when no result came back)."""

    text: str = ""
    cost: float | None = None
    tokens: dict[str, int] = field(default_factory=dict)
    tokens_by_model: dict[str, dict[str, int]] = field(default_factory=dict)
    error: str = ""
    tool_uses: tuple[dict[str, Any], ...] = ()
    #: The session ended at its `--max-budget-usd`.
    over_budget: bool = False


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


#: `modelUsage`'s counts, by the names a record keeps them under; a cache
#: write's 1-hour part where Claude Code names it, the rest 5-minute.
_MODEL_USAGE = {
    "input": "inputTokens",
    "output": "outputTokens",
    "reasoning": "thinkingTokens",
    "cache_read": "cacheReadInputTokens",
    "cache_write": "cacheCreationInputTokens",
    "cache_write_1h": "cacheCreationEphemeral1hInputTokens",
}


def _by_model(usage: dict[str, Any]) -> dict[str, dict[str, int]]:
    """A `result` event's `modelUsage`: the session's counts per model,
    over every model and every turn it was woken for."""
    found = {}
    for model, counts in usage.items():
        counts = _dict(counts)
        found[str(model)] = {
            name: int(counts[field] or 0)
            for name, field in _MODEL_USAGE.items()
            if field in counts
        }
    return found


def _usage(usage: dict[str, Any]) -> dict[str, int]:
    """A `result` event's `usage`, where it names no `modelUsage`: the last
    turn it was woken for, on the session's own model."""
    details = _dict(usage.get("output_tokens_details"))
    found = {
        "input": int(usage.get("input_tokens") or 0),
        "output": int(usage.get("output_tokens") or 0),
        "reasoning": int(details.get("thinking_tokens") or 0),
        "cache_read": int(usage.get("cache_read_input_tokens") or 0),
        "cache_write": int(usage.get("cache_creation_input_tokens") or 0),
    }
    lifetimes = _dict(usage.get("cache_creation"))
    if "ephemeral_1h_input_tokens" in lifetimes:
        found["cache_write_1h"] = int(lifetimes["ephemeral_1h_input_tokens"] or 0)
    return found


def parse(transcript: str) -> Events:
    found = Events()
    uses: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for event in _events(transcript):
        kind = event.get("type")
        content = _list(_dict(event.get("message")).get("content"))
        if kind == "assistant":
            for item in content:
                if isinstance(item, dict) and item.get("type") == "tool_use":
                    key = str(item.get("id") or len(order))
                    uses[key] = {
                        "tool": item.get("name"),
                        "input": item.get("input"),
                        "status": None,
                    }
                    order.append(key)
        elif kind == "user":
            for item in content:
                if isinstance(item, dict) and item.get("type") == "tool_result":
                    use = uses.get(str(item.get("tool_use_id")))
                    if use is not None:
                        use["status"] = (
                            "refused" if item.get("is_error") else "completed"
                        )
        elif kind == "result":
            found.text = str(event.get("result") or "")
            cost = event.get("total_cost_usd")
            if isinstance(cost, int | float) and not isinstance(cost, bool):
                found.cost = float(cost)
            by_model = _by_model(_dict(event.get("modelUsage")))
            if by_model:
                found.tokens_by_model = by_model
                found.tokens = {
                    name: sum(t.get(name, 0) for t in by_model.values())
                    for name in dict.fromkeys(n for t in by_model.values() for n in t)
                }
            else:
                found.tokens_by_model = {}
                found.tokens = _usage(_dict(event.get("usage")))
            if event.get("is_error"):
                found.error = f"{event.get('subtype') or 'error'}: {found.text}".strip()
            found.over_budget = event.get("subtype") == OVER_BUDGET
    found.tool_uses = tuple(uses[key] for key in order)
    return found


def _tools(
    tools: tuple[str, ...] | None, workspace: Workspace | None
) -> tuple[str, ...] | None:
    """The session's tools (`_given`): the workspace's own, else the
    host's; None, Claude Code's own set."""
    if workspace is not None and workspace.tools is not None:
        return _given(workspace.tools, workspace)
    return None if tools is None else _given(tools, workspace)


def _given(tools: tuple[str, ...], workspace: Workspace | None) -> tuple[str, ...]:
    """The session's tools: `tools`, and the one that hands a task to a
    subagent where its workspace defines one, with the web tools a subagent
    is given (a subagent has only tools its session has)."""
    if workspace is not None and workspace.tools is not None:
        tools = workspace.tools
    if workspace is None or not workspace.helpers:
        return tools
    return (*tools, SUBAGENT, *_web(workspace))


def _web(workspace: Workspace | None) -> tuple[str, ...]:
    """The web tools a subagent of the workspace is given (a forager);
    none where no subagent is given one."""
    if workspace is None:
        return ()
    return tuple(t for t in WEB if any(t in h.tools for h in workspace.helpers))


def _agents(helpers: Sequence[Helper]) -> dict[str, dict[str, Any]]:
    """The subagents as `--agents` takes them."""
    return {
        h.name: {
            "description": h.description,
            "prompt": h.prompt,
            "model": h.model,
            "tools": list(h.tools),
        }
        for h in helpers
    }


def _mcp_config(server: ToolServer) -> dict[str, Any]:
    """The MCP configuration that starts `server` alone."""
    return {
        "mcpServers": {
            server.name: {
                "command": server.argv[0],
                "args": list(server.argv[1:]),
                "env": dict(server.env),
            }
        }
    }


def _queue_breach(
    tool_uses: tuple[dict[str, Any], ...],
    server: str,
    work: Path | None = None,
    web: bool = False,
) -> str:
    """Why a queue session breached its confinement, or empty: a tool that
    ran though only its server's are given, the host's reads where files
    lie in `work`, and the web search where `web`; or one of those reads
    that reached outside it."""
    for use in tool_uses:
        tool = str(use.get("tool"))
        if use.get("status") != "completed" or tool.startswith(f"mcp__{server}__"):
            continue
        if web and tool in QUEUE_WEB:
            continue
        if work is None or tool not in QUEUE_READS:
            return f"the model's {tool} ran, though it was not given"
        given = _dict(use.get("input"))
        for name in PATH_FIELDS:
            value = given.get(name)
            if not isinstance(value, str):
                continue
            path = Path(os.path.expanduser(value))
            if not path.is_absolute():
                path = work / path
            if (
                not Path(os.path.normpath(path))
                .resolve()
                .is_relative_to(work.resolve())
            ):
                return f"the model's {tool} reached {value}, outside its directory"
    return ""


def _breach(
    tool_uses: tuple[dict[str, Any], ...],
    work: Path,
    workspace: Workspace | None,
    closed: Sequence[Path],
    tools: tuple[str, ...] | None = None,
    server: str | None = None,
) -> str:
    """Why the session breached its confinement, or empty: a tool that ran
    though it was not given (the web, or one outside `tools` and its tool
    server's, `server`), or a file tool that ran on a path outside the
    directory and /tmp, or inside a path closed when it started."""
    work = work.resolve()
    closed = [p.resolve() for p in closed]
    for use in tool_uses:
        if use.get("status") != "completed":
            continue
        tool = str(use.get("tool"))
        if server is not None and tool.startswith(f"mcp__{server}__"):
            continue
        if (
            workspace is None
            or (tool in WEB and tool not in _web(workspace))
            or (tools is not None and tool not in tools)
        ):
            return f"the model's {tool} ran, though it was not given"
        given = _dict(use.get("input"))
        for name in PATH_FIELDS:
            value = given.get(name)
            if not isinstance(value, str):
                continue
            path = Path(os.path.expanduser(value))
            if not path.is_absolute():
                path = work / path
            resolved = Path(os.path.normpath(path)).resolve()
            inside = resolved.is_relative_to(work) or (
                resolved.is_relative_to(TMP.resolve())
                and not any(resolved.is_relative_to(p) for p in closed)
            )
            if not inside:
                return f"the model's {tool} reached {value}, outside its directory"
    return ""
