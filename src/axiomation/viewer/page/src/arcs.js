// The arcs: the selection's edges drawn as
// brackets along the right edge of the text pane, each from the line its
// evidence stands on to the line of what it points at. Colour is the kind,
// the line style which way reliance runs (solid upstream, dotted
// downstream), an arrowhead at the edge's target.
//
// Pure: which edges, their ends as `[file, line]`, and an arc's path; the
// page finds the screen line each end's words stand on and draws. The
// lenses are the graph's too.
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else (root.AxView = root.AxView || {}).arcs = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // A lens: the kinds it draws.
  const LENSES = {
    all: { label: "all edges", kinds: null },
    reliance: { label: "reliance (uses, qualifies)", kinds: ["uses", "qualifies"] },
    support: { label: "support (justifies)", kinds: ["justifies"] },
    description: { label: "description (about)", kinds: ["about"] },
  };

  function inLens(edge, lens) {
    const kinds = (LENSES[lens] || LENSES.all).kinds;
    return !kinds || kinds.includes(edge.kind);
  }

  // Where a block or a group stands in the text: its first line, a group's
  // its first block's. `layer` is the text layer; `index` from `indexOf`.
  function indexOf(layer) {
    return {
      blocks: new Map(layer.blocks.map((b) => [b.id, b])),
      groups: new Map(layer.groups.map((g) => [g.id, g])),
    };
  }
  function rowOf(index, ident) {
    const group = index.groups.get(ident);
    const block = index.blocks.get(group ? group.blocks[0] : ident);
    const first = block && block.lines[0];
    return first ? [first[0], first[1]] : null;
  }
  function lineOfSpan(starts, span) {
    // `starts` per file path: line starts of that file's text
    const own = starts.get(span[0]);
    if (!own) return null;
    let lo = 0;
    let hi = own.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (own[mid] <= span[1]) lo = mid;
      else hi = mid - 1;
    }
    return [span[0], lo + 1];
  }

  // Which way reliance runs along an edge, as the selection sees it: "up"
  // where the selection rests on the other end, "down" where the other
  // end rests on it. The rule is the query's, from the layer: a kind in
  // `rested_on_by_source` has its target rest on its source, any other its
  // source on its target.
  function stream(layer, edge, out) {
    const reversed = (layer.rested_on_by_source || []).includes(edge.kind);
    return out !== reversed ? "up" : "down";
  }

  // The selection's edges in the lens, each `{edge, dir, stream, near, far, other}`:
  // `near` the row at the selection's side (an edge out: where its
  // evidence stands; in: the selection's own row, or its anchor's), `far`
  // the other end's, `other` the block or group there. An end with no row
  // in the text (a file, the paper) is left out.
  function selected(layer, index, starts, ident, lens) {
    const found = [];
    layer.edges.forEach((edge, i) => {
      if (!inLens(edge, lens)) return;
      const out = edge.source === ident;
      const into = edge.target === ident;
      if (!out && !into) return;
      const ends = endsOf(edge, index, starts);
      if (!ends) return;
      found.push({ edge: i, kind: edge.kind, by: edge.by, dir: out ? "out" : "in", stream: stream(layer, edge, out), near: out ? ends.from : ends.to, far: out ? ends.to : ends.from, other: out ? edge.target : edge.source });
    });
    return found;
  }

  // An edge's two rows: from its evidence (else its source's first line) to
  // its target's anchor (else its target's first line); null where an end
  // has none.
  function endsOf(edge, index, starts) {
    const from = (edge.evidence.length && lineOfSpan(starts, edge.evidence[0])) || rowOf(index, edge.source);
    const to = (edge.anchors.length && lineOfSpan(starts, edge.anchors[0])) || rowOf(index, edge.target);
    return from && to ? { from, to } : null;
  }

  // Every edge in the lens, for the faint texture.
  function every(layer, index, starts, lens) {
    const found = [];
    layer.edges.forEach((edge, i) => {
      if (!inLens(edge, lens)) return;
      const ends = endsOf(edge, index, starts);
      if (ends) found.push({ edge: i, kind: edge.kind, near: ends.from, far: ends.to });
    });
    return found;
  }

  // An arc from (x, y1) to (x, y2), bulging right by `bulge`; without one,
  // by how far apart its ends are, within `width`.
  function path(x, y1, y2, width, bulge) {
    const b = bulge === undefined ? Math.min(width - 6, 10 + Math.abs(y2 - y1) * 0.06) : bulge;
    return "M" + x + " " + y1 + " C" + (x + b) + " " + y1 + " " + (x + b) + " " + y2 + " " + x + " " + y2;
  }

  // A lane per arc (`[{y1, y2}]`): its distance right of the column's edge,
  // the nearest end innermost, the furthest outermost, `step` apart and
  // squeezed to fit `[least, most]`. Two arcs whose spans overlap never
  // share a lane, whichever way each runs: an edge out leaves from its
  // evidence, which may lie below the selection's own row where an edge in
  // ends. Arcs apart, or meeting at one end only, share one. Equal spans
  // keep their given order.
  function fan(spans, least, most, step = 7) {
    const order = spans.map((s, i) => ({ i, lo: Math.min(s.y1, s.y2), hi: Math.max(s.y1, s.y2) }));
    order.sort((a, b) => a.hi - a.lo - (b.hi - b.lo) || a.i - b.i);
    const taken = []; // per lane, the spans in it
    const index = new Array(spans.length);
    for (const s of order) {
      let k = 0;
      while (taken[k] && taken[k].some((t) => s.lo < t.hi && t.lo < s.hi)) k += 1;
      (taken[k] = taken[k] || []).push(s);
      index[s.i] = k;
    }
    const gap = taken.length > 1 ? Math.min(step, (most - least) / (taken.length - 1)) : 0;
    return index.map((k) => least + gap * k);
  }

  // An edge as a bracket (curves from one point
  // stay bundled over a long span): out from (x, y1) to its lane at
  // `lane`, along it, and back to (x2, y2), its corners rounded by `r`.
  // Two brackets in two lanes never cross along their length. An end at an
  // arrowhead stops at the head's base, so the line does not run through it.
  function bracket(x, y1, y2, lane, r = 5, x2 = x) {
    const dy = y2 - y1;
    if (Math.abs(dy) < 1) return "M" + x + " " + y1 + " H" + lane;
    const turn = Math.min(r, Math.abs(dy) / 2, lane - Math.max(x, x2));
    const dir = dy > 0 ? 1 : -1;
    return "M" + x + " " + y1 + " H" + (lane - turn) + " Q" + lane + " " + y1 + " " + lane + " " + (y1 + dir * turn) +
      " V" + (y2 - dir * turn) + " Q" + lane + " " + y2 + " " + (lane - turn) + " " + y2 + " H" + x2;
  }

  // An arrowhead at (x, y) pointing left, into the text: its tip at x,
  // `size` long.
  function arrowhead(x, y, size = 6) {
    const half = size * 0.6;
    return "M" + x + " " + y + " L" + (x + size) + " " + (y - half) + " L" + (x + size) + " " + (y + half) + " Z";
  }

  // The outline around an edge's end, its words as they wrap, the way a
  // text selection is drawn: the rects the words stand in, each screen
  // row's merged; rows closer than `gap` one outline, its first row from
  // its words to the widest row's right, its last from the widest row's
  // left to its words, the rows between that width (a centred formula, a
  // short line inside it no step of its own). A blank rect (an indent's
  // spaces) holds its row's height and no width. Corners on the half
  // pixel, so that a 1px line falls on one pixel and stays sharp.
  function outline(rects, pad = 1, gap = 8) {
    const rows = [];
    for (const r of [...rects].filter((r) => r.right > r.left && r.bottom > r.top).sort((a, b) => a.top - b.top || a.left - b.left)) {
      const last = rows[rows.length - 1];
      const row = last && r.top < last.bottom - 2 ? last : null;
      if (!row) rows.push({ left: r.blank ? Infinity : r.left, right: r.blank ? -Infinity : r.right, top: r.top, bottom: r.bottom });
      else {
        if (!r.blank) {
          row.left = Math.min(row.left, r.left);
          row.right = Math.max(row.right, r.right);
        }
        row.top = Math.min(row.top, r.top);
        row.bottom = Math.max(row.bottom, r.bottom);
      }
    }
    const runs = [];
    for (const row of rows) {
      const run = runs[runs.length - 1];
      if (run && row.top - run[run.length - 1].bottom < gap) run.push(row);
      else runs.push([row]);
    }
    const at = (v) => Math.round(v) + 0.5;
    const box = (l, r, t, b) => "M" + l + " " + t + " L" + r + " " + t + " L" + r + " " + b + " L" + l + " " + b + " Z";
    const shape = (all) => {
      // blank rows at either end are no part of the words
      const run = all.slice(all.findIndex((row) => row.right > row.left), all.length - [...all].reverse().findIndex((row) => row.right > row.left));
      const n = run.length;
      if (!n || run[0].right <= run[0].left) return [];
      const top = at(run[0].top - pad);
      const bottom = at(run[n - 1].bottom + pad);
      if (n === 1) return [box(at(run[0].left - pad), at(run[0].right + pad), top, bottom)];
      const left = at(Math.min(...run.map((row) => row.left)) - pad);
      const right = at(Math.max(...run.map((row) => row.right)) + pad);
      const firstLeft = at(run[0].left - pad);
      const lastRight = at(run[n - 1].right + pad);
      const below = at((run[0].bottom + run[1].top) / 2); // under the first row
      const above = at((run[n - 2].bottom + run[n - 1].top) / 2); // over the last
      // two rows that do not meet (the second ends before the first starts):
      // two boxes, as a selection draws them
      if (n === 2 && lastRight <= firstLeft) return [box(firstLeft, right, top, below), box(left, lastRight, above, bottom)];
      const points = [[firstLeft, top], [right, top], [right, above], [lastRight, above], [lastRight, bottom], [left, bottom], [left, below], [firstLeft, below]];
      const kept = points.filter(([x, y], k) => {
        const [px, py] = points[(k + points.length - 1) % points.length];
        const [nx, ny] = points[(k + 1) % points.length];
        return !((px === x && x === nx) || (py === y && y === ny)); // a point on a straight run
      });
      return ["M" + kept.map(([x, y]) => x + " " + y).join(" L") + " Z"];
    };
    return runs.flatMap(shape);
  }

  return { LENSES, inLens, indexOf, rowOf, stream, selected, endsOf, every, path, fan, bracket, arrowhead, outline };
});
