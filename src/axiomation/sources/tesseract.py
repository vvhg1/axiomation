"""Whether Tesseract's engine is on this machine.

The package brings Tesseract's Python wrapper, never the engine: the
`tesseract` binary is a system install. Every ingest needs it (the default
backend reads with it; under any other it confirms a layer that may spell no
words), and `sources status` says whether it is here. Both halves read this,
so it imports neither.
"""

from __future__ import annotations

import shutil
import sys

#: The command that installs the engine, by platform.
INSTALL = {
    "darwin": "brew install tesseract",
    "linux": (
        "sudo apt install tesseract-ocr (Debian, Ubuntu) "
        "or sudo dnf install tesseract (Fedora)"
    ),
    "win32": "winget install UB-Mannheim.TesseractOCR",
}


def missing() -> str:
    """Why Tesseract cannot run here and the command that installs it, or
    an empty string when it can."""
    if shutil.which("tesseract") is not None:
        return ""
    command = INSTALL.get(sys.platform, INSTALL["linux"])
    return (
        "the `tesseract` binary is not on PATH: axiomation brings "
        f"its Python wrapper, not the engine. Install it: {command}."
    )
