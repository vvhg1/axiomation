"""The Claude Code adapter against the real `claude` and a real model.
Marked `live`: the default run deselects it. It costs money (about $0.002
on Haiku) and runs only when asked for, again whenever Claude
Code's version changes:

    uv run pytest -m live tests/runner/test_claude_code_live.py

The model is `$AXIOMATION_LIVE_MODEL`, else Haiku.
"""

from __future__ import annotations

import os
import shutil

import pytest

from axiomation.questions import Workspace
from axiomation.runner.claude_code import READING_TOOLS, TMP, ClaudeCodeHost

pytestmark = pytest.mark.live


def test_the_session_s_bash_keeps_its_files_in_the_attempt_s_directory() -> None:
    """Claude Code reads the attempt's own directory for its temporary
    files: its Bash's `TMPDIR` lies there, Bash runs with Claude's directory
    under /tmp closed, and the directory goes with the attempt."""
    if shutil.which("claude") is None:
        pytest.skip("claude is not on PATH")
    host = ClaudeCodeHost(
        model=os.environ.get("AXIOMATION_LIVE_MODEL", "haiku"), tools=READING_TOOLS
    )
    host.check()
    before = set(TMP.glob("ax-*"))

    answer = host.ask(
        'Run `echo $TMPDIR` with Bash. Then run `echo hi > "$TMPDIR/probe.txt"` '
        "with Bash. Then replace the whole of note.md with the TMPDIR value alone.",
        300,
        Workspace(files={"note.md": "probe\n"}, edit="note.md", instructions=""),
    )

    assert not answer.failed, answer.error
    assert answer.text.strip().startswith(f"{TMP}/ax-")
    assert [u["status"] for u in answer.tool_uses if u["tool"] == "Bash"] == [
        "completed",
        "completed",
    ]
    assert set(TMP.glob("ax-*")) == before
