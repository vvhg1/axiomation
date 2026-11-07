// The text pane's arithmetic, from the text layer.
//
// Pure. The layer's runs are character runs over a whole file; the pane is
// lines. These functions cut runs, and any other layer of intervals (parts,
// anchors, a standing's words, an edge's evidence), into the pieces one line
// is drawn from. Offsets are the layer's: UTF-16 code units, which is what
// a JavaScript string indexes.
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else (root.AxView = root.AxView || {}).text = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // Where each line starts; line n (1-based) starts at starts[n - 1].
  function lineStarts(text) {
    const starts = [0];
    for (let i = text.indexOf("\n"); i >= 0; i = text.indexOf("\n", i + 1)) starts.push(i + 1);
    return starts;
  }

  // The 1-based line holding an offset.
  function lineOf(starts, offset) {
    let lo = 0;
    let hi = starts.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (starts[mid] <= offset) lo = mid;
      else hi = mid - 1;
    }
    return lo + 1;
  }

  // The lines of one segment: `{line, start, end}`, `end` before the
  // newline. A segment that ends mid-line (a directive with text after it)
  // gives a partial last line, and the segment that resumes the file a
  // partial first one.
  function segmentLines(segment, starts, textLength) {
    const lines = [];
    if (segment.end <= segment.start) return lines;
    const first = lineOf(starts, segment.start);
    const last = lineOf(starts, Math.max(segment.start, segment.end - 1));
    for (let line = first; line <= last; line += 1) {
      const lineStart = starts[line - 1];
      const lineEnd = line < starts.length ? starts[line] - 1 : textLength;
      const start = Math.max(lineStart, segment.start);
      const end = Math.min(lineEnd, segment.end);
      if (end < start) continue;
      lines.push({ line, start, end });
    }
    return lines;
  }

  // The runs (sorted, disjoint) that meet [from, to): index of the first.
  function firstRun(runs, from) {
    let lo = 0;
    let hi = runs.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (runs[mid][1] <= from) lo = mid + 1;
      else hi = mid;
    }
    return lo;
  }

  function runsOn(runs, from, to) {
    const out = [];
    for (let i = firstRun(runs, from); i < runs.length && runs[i][0] < to; i += 1) out.push(runs[i]);
    return out;
  }

  // Cut [from, to) into pieces at every edge of every layer interval.
  // A layer is `{start, end, ...}`; a piece carries the layers covering it,
  // in the order given.
  function pieces(from, to, layers) {
    const cuts = new Set([from, to]);
    const inside = layers.filter((layer) => layer.end > from && layer.start < to);
    for (const layer of inside) {
      if (layer.start > from) cuts.add(layer.start);
      if (layer.end < to) cuts.add(layer.end);
    }
    const points = [...cuts].sort((a, b) => a - b);
    const out = [];
    for (let i = 0; i + 1 < points.length; i += 1) {
      const start = points[i];
      const end = points[i + 1];
      out.push({
        start,
        end,
        layers: inside.filter((layer) => layer.start < end && layer.end > start),
      });
    }
    return out;
  }

  // The rows a segment's lines are drawn as, when the formulas given
  // (sorted, each within the segment) are rendered: a line a formula
  // runs past joins the next, so a display over several source lines is
  // one row. Each row `{first, last, start, end}`, `lines` as
  // `segmentLines` gives them.
  function mergedRows(lines, formulas) {
    const rows = [];
    let k = 0;
    for (let i = 0; i < lines.length; i += 1) {
      const row = { first: lines[i].line, last: lines[i].line, start: lines[i].start, end: lines[i].end };
      while (k < formulas.length && formulas[k].start < row.start) k += 1;
      for (let j = k; j < formulas.length && formulas[j].start < row.end; j += 1) {
        while (formulas[j].end > row.end && i + 1 < lines.length) {
          i += 1;
          row.last = lines[i].line;
          row.end = lines[i].end;
        }
      }
      rows.push(row);
    }
    return rows;
  }

  // Every run owned by one block (by index), as offsets: what selecting it
  // lights.
  function blockRuns(runs, owner) {
    return runs.filter((run) => run[3] === owner).map((run) => [run[0], run[1]]);
  }

  return { lineStarts, lineOf, segmentLines, mergedRows, runsOn, pieces, blockRuns };
});
