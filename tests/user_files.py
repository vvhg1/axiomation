"""A person's user file written for a test (`user_settings.py`)."""

from __future__ import annotations

import json
from pathlib import Path


def write_user_file(path: Path, **values: str | float) -> Path:
    """A user file holding `values`, readable by its owner alone."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(f"{key} = {json.dumps(value)}\n" for key, value in values.items()),
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path
