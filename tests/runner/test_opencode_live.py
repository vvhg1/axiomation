"""The OpenCode adapter against the real `opencode` and a real model.
Marked `live`: the default run deselects it. It costs money and runs only
when asked for:

    uv run pytest -m live tests/runner/test_opencode_live.py

The model is `$AXIOMATION_LIVE_MODEL`, else the default.
"""

from __future__ import annotations

import os
import shutil

import pytest

from axiomation.runner.hosts import DEFAULT_MODEL
from axiomation.runner.opencode import OpenCodeHost

pytestmark = pytest.mark.live


def test_a_trivial_prompt_is_answered_without_a_tool() -> None:
    if shutil.which("opencode") is None:
        pytest.skip("opencode is not on PATH")
    host = OpenCodeHost(model=os.environ.get("AXIOMATION_LIVE_MODEL", DEFAULT_MODEL))
    host.check()

    answer = host.ask(
        'Answer with exactly this and nothing else: ```json\n{"corrections": []}\n```',
        timeout=300,
    )

    assert not answer.failed, answer.error
    assert answer.tool_uses == ()
    assert '"corrections"' in answer.text
    assert answer.cost > 0 or answer.model.startswith("opencode-go/")
    assert answer.tokens["input"] > 0
    # About 400 with nothing but the agent's system prompt; a user's
    # global AGENTS.md alone would add some 4,000.
    assert answer.tokens["input"] < 2000
