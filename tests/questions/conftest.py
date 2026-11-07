"""The synthetic paper as a fixture (`review_paper`)."""

from __future__ import annotations

from pathlib import Path

import pytest

from questions.review_paper import Paper, make_paper


@pytest.fixture
def paper(tmp_path: Path) -> Paper:
    return make_paper(tmp_path / "paper")
