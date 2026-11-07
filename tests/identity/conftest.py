"""Two versions of a synthetic paper, each read and cut."""

from __future__ import annotations

from pathlib import Path

import pytest

from axiomation.cutter import CutRules, DisplayRule, cut
from axiomation.identity import Version
from axiomation.reader import read_paper


@pytest.fixture
def version(tmp_path: Path):
    """Write one version of a paper into its own directory and cut it;
    `extra` writes further files beside the root."""
    counter = 0

    def build(
        text: str, name: str = "paper.tex", extra: dict[str, str] | None = None
    ) -> Version:
        nonlocal counter
        counter += 1
        root = tmp_path / f"v{counter}"
        root.mkdir()
        (root / name).write_text(text, encoding="utf-8")
        for path, content in (extra or {}).items():
            (root / path).write_text(content, encoding="utf-8")
        document = read_paper(root)
        return Version(document, cut(document, CutRules(DisplayRule.OWN_BLOCK)))

    return build
