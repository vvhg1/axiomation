// Anchors: where a mark attaches on the text pane. An anchor is
// `{kind, node, words, work, file}`: a block or a group by its node; a part
// or a span by its node and words, found once in the block's text,
// whitespace aside; the paper; a cited work by its key, at its citation
// sites; a script's lines by its file's key, at the evidence of
// the edges onto that file, since its lines are not the text's. One
// that does not place is kept unanchored, at its block or the paper, its
// words to be shown struck through (Hypothesis's rule), never dropped.
//
// Pure: the text layer in, spans of `[file, start, end]` (UTF-16) out.
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else (root.AxView = root.AxView || {}).anchors = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const KINDS = ["block", "part", "group", "span", "paper", "work", "file"];

  // The layer's blocks, groups and citation sites by what anchors name.
  function index(layer) {
    return {
      layer,
      byNode: new Map([...layer.blocks, ...layer.groups].map((b) => [b.node, b])),
      groups: new Set(layer.groups.map((g) => g.id)),
      blocks: new Map(layer.blocks.map((b) => [b.id, b])),
      files: new Map(layer.files.map((f) => [f.path, f.text])),
    };
  }

  // `words` in a block's owned spans, exactly once, any run of whitespace
  // matching any other: its span, else null and how often it was found.
  function findOnce(at, block, words) {
    const wanted = words.trim().split(/\s+/).join(" ");
    if (!wanted) return { span: null, found: 0 };
    const hits = [];
    for (const [file, start, end] of block.owned) {
      const text = (at.files.get(file) || "").slice(start, end);
      // the text with each run of whitespace one space, and where each
      // kept character came from
      let flat = "";
      const from = [];
      for (let i = 0; i < text.length; i += 1) {
        const space = /\s/.test(text[i]);
        if (space && (flat.endsWith(" ") || flat === "")) continue;
        flat += space ? " " : text[i];
        from.push(i);
      }
      for (let k = flat.indexOf(wanted); k >= 0; k = flat.indexOf(wanted, k + 1)) {
        hits.push([file, start + from[k], start + from[k + wanted.length - 1] + 1]);
      }
    }
    return { span: hits.length === 1 ? hits[0] : null, found: hits.length };
  }

  // An anchor placed: `{placed, kind, block, spans, why}`; `block` where it
  // shows (a group its first block's; null: the paper, at the header), and
  // `why` where it is unanchored.
  function resolve(at, anchor) {
    const kind = anchor && anchor.kind;
    if (kind === "paper") return { placed: true, kind, block: null, spans: [] };
    if (kind === "work") {
      const sites = (at.layer.citations || []).filter((c) => c.keys.includes(anchor.work));
      if (!sites.length) return { placed: false, kind, block: null, spans: [], why: "no citation of " + anchor.work + " in this state" };
      return { placed: true, kind, block: null, spans: sites.map((c) => c.span) };
    }
    if (kind === "file") {
      const target = "file:" + anchor.file;
      const spans = (at.layer.edges || []).filter((e) => e.target === target).flatMap((e) => e.evidence);
      if (!spans.length) return { placed: false, kind, block: null, spans: [], why: "no edge onto " + target + " in this state" };
      return { placed: true, kind, block: null, spans };
    }
    const item = anchor && at.byNode.get(anchor.node);
    if (!item) return { placed: false, kind, block: null, spans: [], why: "node " + (anchor && anchor.node) + " is not in this state" };
    const group = at.groups.has(item.id);
    const first = group ? at.blocks.get(item.blocks[0]) : item;
    const where = first ? first.id : null;
    if (kind === "block" || kind === "group") {
      if ((kind === "group") !== group) return { placed: false, kind, block: where, spans: [], why: "node " + anchor.node + " is a " + (group ? "group" : "block") };
      const owned = group ? item.blocks.flatMap((id) => (at.blocks.get(id) || { owned: [] }).owned) : item.owned;
      return { placed: true, kind, block: where, spans: owned };
    }
    if (kind === "part" || kind === "span") {
      if (group) return { placed: false, kind, block: where, spans: [], why: "node " + anchor.node + " is a group" };
      const { span, found } = findOnce(at, item, anchor.words || "");
      if (!span) return { placed: false, kind, block: where, spans: [], why: "its words are found " + found + " times, not once" };
      return { placed: true, kind, block: where, spans: [span] };
    }
    return { placed: false, kind, block: null, spans: [], why: "no anchor kind " + kind };
  }

  // Each open item's anchors placed, those `anchorsOf(item)` gives (none:
  // the item is not placed): `places`, each `{item, spans, whole, words}`
  // (`item` its index in `items`, `whole` a block or group anchored whole,
  // `words` anchored on words: a part, a span or a work's citations), and
  // `unanchored`, item index -> why, where an anchor does not place.
  function placeItems(at, items, anchorsOf) {
    const places = [];
    const unanchored = new Map();
    items.forEach((item, i) => {
      const whys = [];
      for (const anchor of anchorsOf(item)) {
        const placed = resolve(at, anchor);
        if (!placed.placed) whys.push(placed.why);
        else if (placed.spans.length) {
          const whole = placed.kind === "block" || placed.kind === "group";
          places.push({ item: i, spans: placed.spans, whole, words: !whole });
        }
      }
      if (whys.length) unanchored.set(i, whys.join("; "));
    });
    return { places, unanchored };
  }

  // The blocks an open item stands at: those its anchors place on (a
  // finding on each block it anchors), else its own.
  function blocksOf(at, item) {
    const found = (item.anchors || []).map((a) => resolve(at, a).block).filter(Boolean);
    return found.length ? found : item.block ? [item.block] : [];
  }

  return { KINDS, index, findOnce, resolve, placeItems, blocksOf };
});
