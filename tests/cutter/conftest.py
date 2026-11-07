"""Synthetic papers, cut.

Every cut the `paper` fixture builds is checked for coverage: on every
synthetic paper in the suite, no rendered character is unowned or owned
twice.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from axiomation.cutter import Block, Cut, CutRules, DisplayRule, Form, cut
from axiomation.cutter.coverage import coverage
from axiomation.reader import Document, read_paper


@dataclass(frozen=True)
class Cutting:
    """A synthetic paper's document layer and its cut."""

    document: Document
    cut: Cut

    def text(self, block: Block) -> str:
        """A block's owned text, its runs joined by a space and its
        whitespace collapsed."""
        return " ".join(
            " ".join(self.document.text(run) for run in block.owned).split()
        )

    def blocks(self, form: Form | None = None) -> list[Block]:
        return [
            block for block in self.cut.blocks if form is None or block.form is form
        ]

    def texts(self, form: Form | None = None) -> list[str]:
        return [self.text(block) for block in self.blocks(form)]

    def holding(self, fragment: str) -> Block:
        """The one block whose owned text holds `fragment`."""
        found = [block for block in self.cut.blocks if fragment in self.text(block)]
        assert len(found) == 1, [self.text(block) for block in found]
        return found[0]


@pytest.fixture
def paper(tmp_path: Path):
    """Write a paper into its own directory, read it and cut it.

    `name` is the root's file name; `extra` writes further files beside it.
    """
    counter = 0

    def build(
        text: str,
        name: str = "paper.tex",
        extra: dict[str, str] | None = None,
        displays: DisplayRule = DisplayRule.OWN_BLOCK,
    ) -> Cutting:
        nonlocal counter
        counter += 1
        root = tmp_path / f"case{counter}"
        root.mkdir()
        (root / name).write_text(text, encoding="utf-8")
        for path, content in (extra or {}).items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        document = read_paper(root)
        found = cut(document, CutRules(displays))
        covered = coverage(document, found)
        assert covered.unowned == () and covered.owned_twice == (), covered
        return Cutting(document, found)

    return build
