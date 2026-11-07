"""Overlays: marks a layer the product
does not know puts on the page, each item naming an anchor or an edge, a
channel, a value and its words. `axiomation view --overlay FILE` reads each
file once, checks it here and serves it at `/api/overlays`; the page draws
it. The product knows nothing of what an overlay means (an answer key, a
reviewer's marks): it checks the shape and passes it on.
"""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Any

from axiomation.records import AnchorKind


class Channel(StrEnum):
    """An overlay item's channel: each its own style slot and legend entry
    on the page."""

    KEY = "key"
    JUDGMENT = "judgment"
    FINDING = "finding"
    CUSTOM = "custom"


class OverlayError(Exception):
    """An overlay file the viewer cannot read: the file, the item, what is
    wrong."""


ANCHOR_WORDS = {AnchorKind.PART, AnchorKind.SPAN}
ANCHOR_NODE = {AnchorKind.BLOCK, AnchorKind.GROUP, AnchorKind.PART, AnchorKind.SPAN}


def _anchor(raw: Any, where: str) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("kind") not in set(AnchorKind):
        kinds = ", ".join(k.value for k in AnchorKind)
        raise OverlayError(
            f"{where}: an anchor is an object whose kind is one of {kinds}"
        )
    kind = AnchorKind(raw["kind"])
    if kind in ANCHOR_NODE and not isinstance(raw.get("node"), str):
        raise OverlayError(f"{where}: a {kind.value} anchor names its node")
    if kind in ANCHOR_WORDS and not isinstance(raw.get("words"), str):
        raise OverlayError(f"{where}: a {kind.value} anchor names its words")
    if kind is AnchorKind.WORK and not isinstance(raw.get("work"), str):
        raise OverlayError(f"{where}: a work anchor names the cited work's key")
    if kind is AnchorKind.FILE and not isinstance(raw.get("file"), str):
        raise OverlayError(f"{where}: a file anchor names the file's key")
    return {
        "kind": kind.value,
        "node": raw.get("node"),
        "words": raw.get("words"),
        "work": raw.get("work"),
        "file": raw.get("file"),
    }


def read_overlay(path: Path) -> dict[str, Any]:
    """An overlay file checked: `{"name", "items": [{"anchor" | "edge",
    "channel", "value", "label", "note", "open"}]}`, an anchor in the
    `anchors.js` shape, an edge its id at the state shown."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise OverlayError(
            f"{path}: not a JSON file the viewer can read: {error}"
        ) from None
    if (
        not isinstance(raw, dict)
        or not isinstance(raw.get("name"), str)
        or not raw["name"]
    ):
        raise OverlayError(f"{path}: an overlay is an object with a name")
    if not isinstance(raw.get("items"), list):
        raise OverlayError(f"{path}: an overlay lists its items")
    items = []
    for i, item in enumerate(raw["items"]):
        where = f"{path}, item {i}"
        if not isinstance(item, dict):
            raise OverlayError(f"{where}: an item is an object")
        if ("anchor" in item) == ("edge" in item):
            raise OverlayError(
                f"{where}: an item names an anchor or an edge, one of them"
            )
        if item.get("channel") not in set(Channel):
            channels = ", ".join(c.value for c in Channel)
            raise OverlayError(f"{where}: its channel is one of {channels}")
        if not isinstance(item.get("label"), str) or not item["label"]:
            raise OverlayError(f"{where}: an item has a label, its words")
        if "edge" in item and not isinstance(item["edge"], str):
            raise OverlayError(f"{where}: an edge is named by its id")
        items.append(
            {
                "anchor": _anchor(item["anchor"], where) if "anchor" in item else None,
                "edge": item.get("edge"),
                "channel": item["channel"],
                "value": str(item.get("value", "")),
                "label": item["label"],
                "note": str(item.get("note", "")),
                "open": bool(item.get("open", False)),
            }
        )
    return {"name": raw["name"], "items": items}
