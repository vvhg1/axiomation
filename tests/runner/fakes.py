"""Fakes for the runner's tests: a host, and an `opencode` executable
Nothing here calls a model."""

from __future__ import annotations

import json
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from axiomation.questions import (
    QueueSession,
    Stage,
    Workspace,
    front_lines,
    review_blocks,
)
from axiomation.questions import edge_sheet as es
from axiomation.questions.cut_file import CUT_FILE
from axiomation.questions.edges_file import (
    CHANGES,
    EDGES_FILE,
    LINES_FILE,
    LINES_HEADER,
    as_changes,
    written_lines,
)
from axiomation.runner.hosts import (
    HostAnswer,
    HostBreach,
    HostOutOfCredit,
    HostUncounted,
)

#: The file a front stage's session edits where it does not answer as
#: lines, by stage.
STAGE_FILES = {
    Stage.BOUNDARIES: CUT_FILE,
    Stage.ROLES: "roles.md",
    Stage.GROUPS: "groups.md",
}


def _spec(workspace: Workspace) -> object:
    return json.loads(workspace.gate.spec) if workspace.gate is not None else {}


def shown(workspace: Workspace) -> str:
    """The file a session is shown: a front stage that answers as lines,
    its file as `paper.md`; else the file it edits."""
    return workspace.files.get(front_lines.PAPER_FILE, workspace.files[workspace.edit])


def _editable(workspace: Workspace) -> str:
    """The editable file as the fake edits it: a review that answers by
    block, the sheet its answer is read as; a front stage that answers as
    lines, its file, which its check reads as it is; else the file."""
    spec = _spec(workspace)
    if isinstance(spec, dict) and review_blocks.SUBJECTS in spec:
        return str(spec["reference"])
    if isinstance(spec, dict) and front_lines.STAGE in spec:
        # a re-ask starts from the refused answer, the fake's own file
        return workspace.files[workspace.edit] or str(spec[front_lines.SHOWN])
    return workspace.files[workspace.edit]


def stage_file(workspace: Workspace) -> str:
    """The name the fake's `edits` know a session's file by: a front stage
    that answers as lines by its file's."""
    spec = _spec(workspace)
    if isinstance(spec, dict) and front_lines.STAGE in spec:
        return STAGE_FILES[Stage(spec[front_lines.STAGE])]
    return workspace.edit


class FakeHost:
    """A host that answers by a callable of the prompt. The callable returns the
    answer's text, or a `HostAnswer`, or raises. `cost` and `tool_uses` are given to
    every text answer; `calls` counts prompts. A session on a workspace answers with
    `edit` of its editable file, where `edit` is given, or with `edits[<file>]`
    where that names the file; an edges pass that writes `lines.md`, with its
    sheet's edit written as lines by owner (`as_lines`), or a later pass's as
    changes (`as_changes`), where no edit names `lines.md`; a review that answers by
    block with the edit of the sheet its answer is read as, and a front stage that
    answers as lines with the edit of its file, by that file's name, which each
    check reads as it is (answers by block and as lines are tested on their own);
    `workspaces` are those it was given."""

    name = "fake"
    effort: str | None = None

    def __init__(
        self,
        answer: Callable[[str], str | HostAnswer],
        cost: float = 0.01,
        tool_uses: tuple[dict[str, Any], ...] = (),
        model: str = "fake/model",
        edit: Callable[[str], str] | None = None,
        edits: dict[str, Callable[[str], str]] | None = None,
    ) -> None:
        self.answer = answer
        self.edit = edit
        self.edits = edits or {}
        self.workspaces: list[Workspace] = []
        self.cost = cost
        self.tool_uses = tool_uses
        self.model = model
        self.prompts: list[str] = []
        #: each attempt's spending limit, as the runner gave it
        self.spend_maxes: list[float | None] = []
        self._lock = threading.Lock()

    aborted = 0

    def abort(self) -> None:
        self.aborted += 1

    @property
    def calls(self) -> int:
        return len(self.prompts)

    def ask(
        self,
        prompt: str,
        timeout: float,
        workspace: Workspace | None = None,
        spend_max: float | None = None,
    ) -> HostAnswer:
        with self._lock:
            self.prompts.append(prompt)
            self.spend_maxes.append(spend_max)
            if workspace is not None:
                self.workspaces.append(workspace)
        edit = None
        if workspace is not None:
            edit = self.edits.get(stage_file(workspace), self.edit)
        if (
            workspace is not None
            and workspace.edit == LINES_FILE
            and LINES_FILE not in self.edits
            and EDGES_FILE in self.edits
        ):
            sheet = workspace.files[EDGES_FILE]
            spec = _spec(workspace)
            edited = self.edits[EDGES_FILE](sheet)
            found: str | HostAnswer = (
                as_changes(sheet, edited)
                if isinstance(spec, dict) and CHANGES in spec
                else as_lines(edited)
            )
        elif workspace is not None and edit is not None:
            found = edit(_editable(workspace))
        else:
            found = self.answer(prompt)
        if isinstance(found, HostAnswer):
            return found
        return HostAnswer(
            text=found,
            model=self.model,
            cost=self.cost,
            tokens={"input": len(prompt) // 3, "output": 100},
            seconds=0.1,
            tool_uses=self.tool_uses,
            transcript=json.dumps({"type": "text", "part": {"text": found}}) + "\n",
        )


def as_lines(sheet: str) -> str:
    """A written edges sheet as a first pass writes it in `lines.md`: the
    header's legend and `Pervasive:` lines under `@@ the header`, then each
    block's and group's own lines under its heading, an owner not done
    left out."""
    head = sheet.split("\n---\n", 1)[0].split("\n")
    out = [f"@@ {LINES_HEADER}"]
    out += [line for line in head if line.startswith(("  ", "Pervasive:"))]
    rows = sheet.split("\n")
    done = {m.group(1) for line in rows if (m := es.DONE.match(line))}
    owners = [
        m.group(1) for line in rows if (m := es.BLOCK.match(line) or es.END.match(line))
    ]
    written = written_lines(sheet, flags=True)
    for owner in owners:
        if owner in done:
            own = written.get(owner, [])
            out += [f"@@ {owner}"] + [line for line in own if not es.DONE.match(line)]
    return "\n".join(out)


def breach(prompt: str) -> str:
    raise HostBreach(
        "the model's read named /etc/passwd, outside its directory",
        HostAnswer(text="", model="fake/model", cost=0.02),
    )


def out_of_credit(prompt: str) -> str:
    raise HostOutOfCredit(
        '{"name": "APIError", "data": {"message": "Insufficient balance"}}',
        HostAnswer(
            text="",
            model="fake/model",
            error="Insufficient balance",
            transcript='{"type": "error"}\n',
        ),
    )


def uncounted(prompt: str) -> str:
    raise HostUncounted(
        "what the attempt cost is unknown: `session export` exited with 1",
        HostAnswer(text='{"corrections": []}', model="fake/model"),
    )


def fenced(answer: dict[str, Any]) -> str:
    return "Here is my review.\n```json\n" + json.dumps(answer) + "\n```\n"


FAKE_OPENCODE = """\
#!{python}
import json, os, pathlib, sys, time

if sys.argv[1:] == ["--version"]:
    print("2.0.16-fake")
    sys.exit(0)
if sys.argv[1:4] == ["session", "export", "--standalone"]:
    session = sys.argv[4]
    exports = os.environ.get("FAKE_OPENCODE_EXPORT_LOG")
    if exports:
        with open(exports, "a") as handle:
            handle.write(json.dumps({{
                "session": session,
                "cwd": os.getcwd(),
                "global_config": os.environ.get("OPENCODE_CONFIG_DIR"),
            }}) + "\\n")
    code = int(os.environ.get("FAKE_OPENCODE_EXPORT_EXIT", "0"))
    if code:
        sys.stderr.write(f"Session not found: {{session}}\\n")
        sys.exit(code)
    given = os.environ.get("FAKE_OPENCODE_EXPORT")
    if given:
        sys.stdout.write(pathlib.Path(given).read_text())
        sys.exit(0)
    # The record OpenCode keeps of what the events show: the text parts,
    # the tools, the steps' cost and tokens summed; over every run of the
    # session, where they are kept.
    events_path = os.environ.get("FAKE_OPENCODE_EVENTS")
    sessions = os.environ.get("FAKE_OPENCODE_SESSIONS")
    if sessions and (pathlib.Path(sessions) / f"{{session}}.jsonl").exists():
        events_path = str(pathlib.Path(sessions) / f"{{session}}.jsonl")
    lines = pathlib.Path(events_path).read_text().splitlines() if events_path else []
    played = [json.loads(line) for line in lines if line.strip()]
    if not any(e.get("sessionID") == session for e in played):
        sys.stderr.write(f"Session not found: {{session}}\\n")
        sys.exit(1)
    content, cost = [], 0.0
    tokens = {{"input": 0, "output": 0, "reasoning": 0}}
    tokens["cache"] = {{"read": 0, "write": 0}}
    for event in played:
        part = event.get("part") or {{}}
        if event.get("type") == "step_finish":
            cost += part.get("cost") or 0.0
            for name in ("input", "output", "reasoning"):
                tokens[name] += part["tokens"].get(name) or 0
            for name in ("read", "write"):
                tokens["cache"][name] += part["tokens"]["cache"].get(name) or 0
        elif event.get("type") in ("text", "tool_use"):
            content.append(part)
    record = {{
        "info": {{"id": session, "cost": cost, "tokens": tokens}},
        "messages": [
            {{"type": "user", "text": "..."}},
            {{"type": "assistant", "cost": cost, "tokens": tokens, "content": content}},
        ],
    }}
    sys.stdout.write(json.dumps(record))
    sys.exit(0)
log = pathlib.Path(os.environ["FAKE_OPENCODE_LOG"])
config = pathlib.Path(".opencode/opencode.jsonc")
global_config = os.environ.get("OPENCODE_CONFIG_DIR")
record = {{
    "argv": sys.argv[1:],
    "stdin": sys.stdin.read(),
    "cwd": os.getcwd(),
    "pwd": os.environ.get("PWD"),
    "global_config": global_config,
    "global_listing": (
        sorted(os.listdir(global_config)) if global_config else None
    ),
    "database": os.environ.get("OPENCODE_DB"),
    "config": config.read_text() if config.exists() else None,
    "listing": sorted(str(p) for p in pathlib.Path(".").rglob("*")),
}}
brief = pathlib.Path(global_config or ".", "AGENTS.md")
if global_config and brief.exists():
    record["brief"] = brief.read_text()
with log.open("a") as handle:
    handle.write(json.dumps(record) + "\\n")
if os.environ.get("FAKE_OPENCODE_CHILD"):
    import subprocess
    subprocess.Popen(["sleep", os.environ["FAKE_OPENCODE_CHILD"]])
events = os.environ.get("FAKE_OPENCODE_EVENTS")
played = pathlib.Path(events).read_text() if events else ""
session = next(
    (json.loads(line).get("sessionID") for line in played.splitlines() if line.strip()),
    None,
)
# A session's edit of its answer file, as an edit tool that ran: the
# first run makes FAKE_OPENCODE_EDIT's, a run going on (`--session`)
# FAKE_OPENCODE_FIX's.
answer_file = next(
    (p for p in map(pathlib.Path, ("roles.md", "groups.md")) if p.exists()),
    pathlib.Path("cut.md"),
)
going_on = "--session" in sys.argv
# A front stage that answers as lines is shown its file as `paper.md`: a
# first run writes that file whole as its answer, which the check reads as
# it is, and edits it there.
lines_file = next(
    (p for p in map(pathlib.Path, ("cuts.md", "roles.md", "groups.md")) if p.exists()),
    None,
)
if pathlib.Path("paper.md").exists() and lines_file is not None and not going_on:
    answer_file = lines_file
    answer_file.write_text(pathlib.Path("paper.md").read_text())
change = os.environ.get("FAKE_OPENCODE_FIX" if going_on else "FAKE_OPENCODE_EDIT")
if change and answer_file.exists() and "proposed" not in answer_file.read_text():
    old, new = change.split("|||")
    if os.environ.get("FAKE_OPENCODE_EDIT_RE"):
        import re
        answer_file.write_text(
            re.sub(old, new, answer_file.read_text(), flags=re.MULTILINE)
        )
    else:
        answer_file.write_text(answer_file.read_text().replace(old, new))
    edit = {{"type": "tool_use", "sessionID": session, "part": {{"type": "tool",
        "tool": "edit", "state": {{"status": "completed", "input": {{
            "path": str(answer_file.resolve()), "oldString": old,
            "newString": new}}}}}}}}
    played = json.dumps(edit) + "\\n" + played
sessions = os.environ.get("FAKE_OPENCODE_SESSIONS")
if sessions and session:
    with open(pathlib.Path(sessions) / f"{{session}}.jsonl", "a") as handle:
        handle.write(played)
sys.stdout.write(played)
sys.stdout.flush()
if os.environ.get("FAKE_OPENCODE_PRINTED"):
    pathlib.Path(os.environ["FAKE_OPENCODE_PRINTED"]).write_text("printed")
if os.environ.get("FAKE_OPENCODE_SLEEP"):
    time.sleep(float(os.environ["FAKE_OPENCODE_SLEEP"]))
sys.stderr.write(os.environ.get("FAKE_OPENCODE_STDERR", ""))
sys.exit(int(os.environ.get("FAKE_OPENCODE_EXIT", "0")))
"""


def fake_opencode(directory: Path) -> Path:
    """An `opencode` in `directory`, run by this Python, that logs its argv,
    stdin, working directory, `PWD`, the global config directory and
    database it is given, if any, the config it finds and what else is
    there, to `$FAKE_OPENCODE_LOG`, then prints `$FAKE_OPENCODE_EVENTS`.

    A session's run edits its answer file (`roles.md`, `groups.md`, else
    `cut.md`; one that answers as lines, `paper.md` written into its
    lines file) with `$FAKE_OPENCODE_EDIT` (old and new text split by `|||`,
    a regular expression with `$FAKE_OPENCODE_EDIT_RE`), and a run going
    on (`--session`) with `$FAKE_OPENCODE_FIX`, each reported as an edit
    that ran. With `$FAKE_OPENCODE_SESSIONS`, a directory, every run's
    events are kept there per session.

    `session export` prints `$FAKE_OPENCODE_EXPORT`, else the record the
    events show, over every run of the session where they are kept; it
    fails with `$FAKE_OPENCODE_EXPORT_EXIT`, or when no event names the
    session, and logs to `$FAKE_OPENCODE_EXPORT_LOG`."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "opencode"
    path.write_text(FAKE_OPENCODE.format(python=sys.executable), encoding="utf-8")
    path.chmod(0o755)
    return path


SESSION = "ses_fake"


#: Events in the shape `opencode run --format json` prints, trimmed to the
#: fields the adapter reads. Each names the
#: session, unless `session` is None.
def events(*items: dict[str, Any], session: str | None = SESSION) -> str:
    named = [{"sessionID": session, **item} if session else item for item in items]
    return "".join(json.dumps(item) + "\n" for item in named)


def step_start() -> dict[str, Any]:
    return {"type": "step_start", "part": {"type": "step-start"}}


def text(value: str) -> dict[str, Any]:
    return {"type": "text", "part": {"type": "text", "text": value}}


def step_finish(cost: float, input: int, output: int, reasoning: int = 0,
                read: int = 0, write: int = 0) -> dict[str, Any]:  # fmt: skip
    return {
        "type": "step_finish",
        "part": {
            "type": "step-finish",
            "reason": "stop",
            "cost": cost,
            "tokens": {
                "input": input,
                "output": output,
                "reasoning": reasoning,
                "cache": {"read": read, "write": write},
            },
        },
    }


def tool_use(tool: str, input: dict[str, Any], status: str = "error") -> dict[str, Any]:
    return {
        "type": "tool_use",
        "part": {
            "type": "tool",
            "tool": tool,
            "state": {"status": status, "input": input, "error": "Permission denied"},
        },
    }


def error(message: str) -> dict[str, Any]:
    return {
        "type": "error",
        "error": {"name": "APIError", "data": {"message": message}},
    }


FAKE_CLAUDE = """\
#!{python}
import json, os, pathlib, sys, time

if sys.argv[1:] == ["--version"]:
    print("2.1.285 (Claude Code)")
    sys.exit(0)
argv = sys.argv[1:]
record = {{
    "argv": argv,
    "stdin": sys.stdin.read(),
    "cwd": os.getcwd(),
    "pwd": os.environ.get("PWD"),
    "listing": sorted(str(p) for p in pathlib.Path(".").rglob("*")),
    "claude_tmp": os.environ.get("CLAUDE_CODE_TMPDIR"),
    "cache_ttl": os.environ.get("CLAUDE_CODE_PROMPT_CACHE_TTL"),
    "gate_at": os.environ.get("AXIOMATION_GATE"),
}}
for flag in ("--append-system-prompt-file", "--system-prompt-file"):
    if flag in argv:
        brief = pathlib.Path(argv[argv.index(flag) + 1])
        record["brief"] = brief.read_text()
        record["brief_flag"] = flag
if "--mcp-config" in argv:
    given = pathlib.Path(argv[argv.index("--mcp-config") + 1])
    record["mcp"] = json.loads(given.read_text())
    record["served"] = [
        json.loads(pathlib.Path(s["args"][-1]).read_text())
        for s in record["mcp"]["mcpServers"].values()
        if pathlib.Path(s["args"][-1]).is_file()
    ]
    record["mcp_limits"] = [
        os.environ.get("MCP_TOOL_TIMEOUT"), os.environ.get("MCP_TIMEOUT")
    ]
if "--settings" in argv:
    given = pathlib.Path(argv[argv.index("--settings") + 1])
    record["allowed"] = json.loads(given.read_text())["permissions"]["allow"]
    record["excluded"] = json.loads(given.read_text())["sandbox"]["excludedCommands"]
    sandbox = json.loads(given.read_text())["sandbox"]
    record["closed"] = sandbox["filesystem"]["denyRead"]
    record["written"] = sandbox["filesystem"]["allowWrite"]
    record["root"] = str(given.parent)
    record["claude_tmp"] = os.environ.get("CLAUDE_CODE_TMPDIR")
with open(os.environ["FAKE_CLAUDE_LOG"], "a") as handle:
    handle.write(json.dumps(record) + "\\n")
# A project entry, as Claude Code makes one for its directory.
projects = os.environ.get("FAKE_CLAUDE_PROJECTS")
if projects:
    import re
    entry = pathlib.Path(projects) / re.sub(r"[^A-Za-z0-9]", "-", os.getcwd())
    (entry / "memory").mkdir(parents=True, exist_ok=True)
    (entry / "session" / "tool-results").mkdir(parents=True, exist_ok=True)
    (entry / "session" / "tool-results" / "out.txt").write_text("output")

def say(event):
    sys.stdout.write(json.dumps(event) + "\\n")

say({{"type": "system", "subtype": "init", "cwd": os.getcwd()}})
edit = os.environ.get("FAKE_CLAUDE_EDIT")
# An author's file only: in a judge's (its edits flagged `proposed`) the
# fake keeps everything, as a judge may change no marker. A roles
# session's file is `roles.md`, a groups session's `groups.md`. A front
# stage that answers as lines is shown its file as `paper.md`: the fake
# writes that file, edited or not, whole as its answer, which the check
# reads as it is.
path = next(
    (
        p
        for p in map(pathlib.Path, ("roles.md", "groups.md", "edges.md"))
        if p.exists()
    ),
    pathlib.Path("cut.md"),
)
source = path
lines_file = next(
    (p for p in map(pathlib.Path, ("cuts.md", "roles.md", "groups.md")) if p.exists()),
    None,
)
if pathlib.Path("paper.md").exists() and lines_file is not None:
    source, path = pathlib.Path("paper.md"), lines_file
    path.write_text(source.read_text())
if edit and path.exists() and "proposed" not in source.read_text():
    old, new = edit.split("|||")
    if os.environ.get("FAKE_CLAUDE_EDIT_RE"):
        import re
        text = re.sub(old, new, source.read_text(), flags=re.MULTILINE)
    else:
        text = source.read_text().replace(old, new)
    # An edges pass reads the sheet and writes in `lines.md`: a first pass
    # the sheet as edited by owner; a later pass, shown a sheet written
    # already (its done marks), the changes that edit it.
    lines = pathlib.Path("lines.md")
    if path.name == "edges.md" and lines.exists() and "⟦done " in source.read_text():
        from axiomation.questions.edges_file import as_changes
        path = lines
        text = as_changes(source.read_text(), text)
    elif path.name == "edges.md" and lines.exists():
        from axiomation.questions import edge_sheet as es
        from axiomation.questions.edges_file import LINES_HEADER, written_lines
        head = text.split("\\n---\\n", 1)[0].split("\\n")
        out = ["@@ " + LINES_HEADER] + [
            x for x in head if x.startswith(("  ", "Pervasive:"))
        ]
        rows = text.split("\\n")
        done = {{m.group(1) for m in map(es.DONE.match, rows) if m}}
        written = written_lines(text, flags=True)
        for row in rows:
            m = es.BLOCK.match(row) or es.END.match(row)
            if m and m.group(1) in done:
                own = written.get(m.group(1), [])
                out += ["@@ " + m.group(1)] + [x for x in own if not es.DONE.match(x)]
        path = lines
        text = "\\n".join(out)
    path.write_text(text)
    say({{"type": "assistant", "message": {{"content": [{{"type": "tool_use",
        "id": "t1", "name": "Edit", "input": {{"file_path": str(path.resolve()),
        "old_string": old, "new_string": new}}}}]}}}})
    say({{"type": "user", "message": {{"content": [{{"type": "tool_result",
        "tool_use_id": "t1", "content": "ok"}}]}}}})
extra = os.environ.get("FAKE_CLAUDE_TOOL")
if extra:
    tool = json.loads(extra)
    say({{"type": "assistant", "message": {{"content": [{{"type": "tool_use",
        "id": "t2", "name": tool["name"], "input": tool["input"]}}]}}}})
    say({{"type": "user", "message": {{"content": [{{"type": "tool_result",
        "tool_use_id": "t2", "is_error": tool.get("refused", True),
        "content": "denied" if tool.get("refused", True) else "ok"}}]}}}})
# A scratch file in Claude Code's directory, where its Bash's `TMPDIR`
# lies, as a model writes one there.
scratch = os.environ.get("FAKE_CLAUDE_SCRATCH")
if scratch and os.environ.get("CLAUDE_CODE_TMPDIR"):
    within = pathlib.Path(os.environ["CLAUDE_CODE_TMPDIR"]) / f"claude-{{os.getuid()}}"
    within.mkdir(parents=True, exist_ok=True)
    (within / scratch).write_text("a scratch")
# A Claude Code that reads no CLAUDE_CODE_TMPDIR: its folder for its
# directory in Claude's directory under /tmp, given here.
ignored = os.environ.get("FAKE_CLAUDE_IGNORES_TMPDIR")
if ignored:
    import re
    folder = pathlib.Path(ignored) / re.sub(r"[^A-Za-z0-9]", "-", os.getcwd())
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "shell-state").write_text("state")
# A file the session plants in its directory (`path|||text`), as a model
# may.
plant = os.environ.get("FAKE_CLAUDE_PLANT")
if plant:
    where, body = plant.split("|||")
    pathlib.Path(where).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(where).write_text(body)
# The answer file made a link (`file|||target`), as a session may try.
link = os.environ.get("FAKE_CLAUDE_LINK")
if link:
    where, target = link.split("|||")
    pathlib.Path(where).unlink(missing_ok=True)
    pathlib.Path(where).symlink_to(target)
# The PostToolUse hooks the settings name, run once after the
# edit as Claude Code runs them; their outputs go to FAKE_CLAUDE_WRITES.
if "--settings" in argv and os.environ.get("FAKE_CLAUDE_WRITES"):
    import shlex, subprocess
    settings = json.loads(pathlib.Path(argv[argv.index("--settings") + 1]).read_text())
    writes = []
    for entry in settings.get("hooks", {{}}).get("PostToolUse", []):
        for hook in entry["hooks"]:
            done = subprocess.run(
                shlex.split(hook["command"]),
                input=json.dumps({{"hook_event_name": "PostToolUse",
                    "tool_name": "Edit"}}),
                capture_output=True, text=True,
            )
            writes.append({{"stdout": done.stdout, "stderr": done.stderr,
                "code": done.returncode, "matcher": entry.get("matcher")}})
    pathlib.Path(os.environ["FAKE_CLAUDE_WRITES"]).write_text(json.dumps(writes))
# The Stop hooks the settings name, run as Claude Code runs them: while one
# blocks, the fake makes FAKE_CLAUDE_FIX's edit, if any, and tries again.
hooks = []
if "--settings" in argv:
    settings = json.loads(pathlib.Path(argv[argv.index("--settings") + 1]).read_text())
    hooks = [h for e in settings.get("hooks", {{}}).get("Stop", []) for h in e["hooks"]]
stops = []
fix = os.environ.get("FAKE_CLAUDE_FIX")
for round_ in range(6):
    blocked = False
    for hook in hooks:
        import shlex, subprocess
        done = subprocess.run(
            shlex.split(hook["command"]),
            input=json.dumps(
                {{"hook_event_name": "Stop", "stop_hook_active": round_ > 0}}
            ),
            capture_output=True, text=True,
        )
        stops.append({{"stdout": done.stdout, "stderr": done.stderr,
            "code": done.returncode}})
        if done.stdout.strip() and json.loads(done.stdout).get("decision") == "block":
            blocked = True
    if not blocked:
        break
    if fix:
        old, new = fix.split("|||")
        path.write_text(path.read_text().replace(old, new))
if os.environ.get("FAKE_CLAUDE_STOPS"):
    pathlib.Path(os.environ["FAKE_CLAUDE_STOPS"]).write_text(json.dumps(stops))
if os.environ.get("FAKE_CLAUDE_SLEEP"):
    time.sleep(float(os.environ["FAKE_CLAUDE_SLEEP"]))
error = os.environ.get("FAKE_CLAUDE_ERROR")
result = {{"type": "result",
    "subtype": os.environ.get("FAKE_CLAUDE_SUBTYPE")
        or ("error_during_execution" if error else "success"),
    "is_error": bool(error),
    "result": error or os.environ.get("FAKE_CLAUDE_TEXT", "Done."),
    "usage": {{"input_tokens": 10, "output_tokens": 200,
        "cache_read_input_tokens": 3000, "cache_creation_input_tokens": 500,
        "output_tokens_details": {{"thinking_tokens": 50}}}}}}
if not os.environ.get("FAKE_CLAUDE_NO_COST"):
    result["total_cost_usd"] = float(os.environ.get("FAKE_CLAUDE_COST", "0.01"))
say(result)
sys.exit(int(os.environ.get("FAKE_CLAUDE_EXIT", "0")))
"""


def fake_claude(directory: Path) -> Path:
    """A `claude` in `directory`, run by this Python. It logs its argv,
    stdin, working directory, `PWD`, what is there and the brief it was
    pointed to, to `$FAKE_CLAUDE_LOG`; applies `$FAKE_CLAUDE_EDIT` (old
    and new text, split by `|||`) to an author's `cut.md` as an Edit, and
    keeps a judge's as it is; reports
    `$FAKE_CLAUDE_TOOL` (a JSON tool use, refused unless `"refused":
    false`); makes a project entry under `$FAKE_CLAUDE_PROJECTS`; and ends
    with a result costing `$FAKE_CLAUDE_COST`, none with
    `$FAKE_CLAUDE_NO_COST`, an error with `$FAKE_CLAUDE_ERROR`."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "claude"
    path.write_text(FAKE_CLAUDE.format(python=sys.executable), encoding="utf-8")
    path.chmod(0o755)
    return path


FAKE_CLAUDE_ENV = (
    "FAKE_CLAUDE_EDIT",
    "FAKE_CLAUDE_LINK",
    "FAKE_CLAUDE_FIX",
    "FAKE_CLAUDE_STOPS",
    "FAKE_CLAUDE_WRITES",
    "FAKE_CLAUDE_PLANT",
    "FAKE_CLAUDE_SCRATCH",
    "FAKE_CLAUDE_IGNORES_TMPDIR",
    "FAKE_CLAUDE_EDIT_RE",
    "FAKE_CLAUDE_TOOL",
    "FAKE_CLAUDE_SLEEP",
    "FAKE_CLAUDE_ERROR",
    "FAKE_CLAUDE_SUBTYPE",
    "FAKE_CLAUDE_TEXT",
    "FAKE_CLAUDE_NO_COST",
    "FAKE_CLAUDE_COST",
    "FAKE_CLAUDE_EXIT",
    "FAKE_CLAUDE_PROJECTS",
)


class FakeQueueHost:
    """A host whose queue sessions are scripted: `script(call)` works the
    session through its tool server, served in this process, where
    `call(name, args)` gives the tool's text and whether it was refused;
    it returns the session's last message. Each session costs `cost`;
    with `ends`, it then raises that host exception (`HostOverBudget`,
    …) with its answer. Its tool server starts unless `server_error`
    says why not."""

    name = "fake-queue"
    effort: str | None = None

    def __init__(
        self,
        script: Callable[[Callable[[str, dict], tuple[str, bool]]], str],
        cost: float = 0.01,
        model: str = "fake/queue",
        ends: type[Exception] | None = None,
        server_error: str | None = None,
    ) -> None:
        self.script = script
        self.server_error = server_error
        self.cost = cost
        self.model = model
        self.ends = ends
        self.sessions: list[QueueSession] = []
        self.spend_maxes: list[float | None] = []
        self.aborted = 0

    def abort(self) -> None:
        self.aborted += 1

    def ask(self, *_: Any, **__: Any) -> HostAnswer:
        raise AssertionError("a queue host asks no question")

    def tool_server_error(self, server: Any) -> str | None:
        """Its server is served in this process: it starts, unless the test
        says why not."""
        return self.server_error

    def ask_queue(
        self, session: QueueSession, timeout: float, spend_max: float | None = None
    ) -> HostAnswer:
        from axiomation.queue.server import served

        self.sessions.append(session)
        self.spend_maxes.append(spend_max)
        with served(Path(session.server.argv[-1])) as serving:
            text = self.script(serving.call)
        answer = HostAnswer(
            text, self.model, cost=self.cost, tokens={"input": 100, "output": 10}
        )
        if self.ends is not None:
            raise self.ends("the fake ended", answer)
        return answer


def working_through(
    answer: Callable[[str], tuple[str, dict]], stop_after: int | None = None
) -> Callable[[Callable[[str, dict], tuple[str, bool]]], str]:
    """A session's script: `next`, then each task served answered by
    `answer(served) -> (tool, args)` until the queue says to stop, calling
    `next` again where it says so, or its share is done; with
    `stop_after`, it ends after that many accepted answers. A refusal
    fails the test."""

    def script(call: Callable[[str, dict], tuple[str, bool]]) -> str:
        from axiomation.queue import AGAIN, EMPTY, ENDED, SHARE_DONE

        accepted = 0
        text, _ = call("next", {})
        while text not in (EMPTY, ENDED, SHARE_DONE):
            if text == AGAIN:
                text, _ = call("next", {})
                continue
            tool, args = answer(text)
            reply, refused = call(tool, args)
            assert not refused, reply
            accepted += 1
            if stop_after is not None and accepted >= stop_after:
                return "stopped"
            text = reply.split("\n\n", 1)[1] if reply.startswith("accepted") else reply
        return "done"

    return script
