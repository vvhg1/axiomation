// The gutter's lanes, per line, from the text layer, with the open
// view's marks.
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else (root.AxView = root.AxView || {}).gutter = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // The group lane of one file: one column per nesting depth, and in each a
  // bracket from a group's first line in this file to its last, counted over
  // its blocks' line ranges. Each cell is `{group, start, end}` or null.
  function groupLane(groups, blocksById, path, lineCount) {
    const depth = groups.reduce((max, group) => Math.max(max, group.depth + 1), 0);
    const columns = Array.from({ length: depth }, () => new Array(lineCount).fill(null));
    groups.forEach((group, index) => {
      let first = Infinity;
      let last = -Infinity;
      for (const id of group.blocks) {
        const block = blocksById.get(id);
        if (!block) continue;
        for (const [file, from, to] of block.lines) {
          if (file !== path) continue;
          first = Math.min(first, from);
          last = Math.max(last, to);
        }
      }
      if (first > last) return;
      const column = columns[group.depth];
      for (let line = first; line <= Math.min(last, lineCount); line += 1) {
        if (column[line - 1] === null) {
          column[line - 1] = { group: index, start: line === first, end: line === last };
        }
      }
    });
    return columns;
  }

  // --- the measured lanes (measured) ---
  //
  // A block's bar runs exactly from the first to the last rendered row of
  // its text, as wrapped, so blocks that share a source line each show;
  // blocks whose rows overlap stand side by side. The page measures the
  // rows; these functions are pure.

  // One block's rows (`{top, bottom}`, in any order) as the segments its
  // bar is drawn in: rows closer than `gap` join, so a block another one
  // interrupts gets two segments.
  function segments(rows, gap = 3) {
    const sorted = [...rows].sort((a, b) => a.top - b.top);
    const out = [];
    for (const row of sorted) {
      const last = out[out.length - 1];
      if (last && row.top <= last.bottom + gap) last.bottom = Math.max(last.bottom, row.bottom);
      else out.push({ top: row.top, bottom: row.bottom });
    }
    return out;
  }

  // Every block's segments at once, from `owner -> rows`: a block's rows
  // join where they are closer than `gap`, or where no row of another
  // block lies between them (a blank line inside a block renders none of
  // its text); a block another one interrupts gets two segments. Rows are
  // as measured, unpadded: a row of another block on the same visual line
  // as either end, a pixel taller or not, is not between them.
  function blockSegments(byOwner, gap = 3) {
    const all = [];
    let tallest = 0;
    for (const [owner, rows] of byOwner) {
      for (const row of rows) {
        all.push({ owner, top: row.top, bottom: row.bottom });
        tallest = Math.max(tallest, row.bottom - row.top);
      }
    }
    all.sort((a, b) => a.top - b.top);
    function between(owner, from, to) {
      let lo = 0;
      let hi = all.length;
      while (lo < hi) {
        const mid = (lo + hi) >> 1;
        if (all[mid].top < from - tallest) lo = mid + 1;
        else hi = mid;
      }
      for (let i = lo; i < all.length && all[i].top < to - 1; i += 1) {
        if (all[i].owner !== owner && all[i].bottom > from + 1) return true;
      }
      return false;
    }
    const out = new Map();
    for (const [owner, rows] of byOwner) {
      const segs = [];
      for (const row of [...rows].sort((a, b) => a.top - b.top)) {
        const last = segs[segs.length - 1];
        if (last && (row.top <= last.bottom + gap || !between(owner, last.bottom, row.top))) last.bottom = Math.max(last.bottom, row.bottom);
        else segs.push({ top: row.top, bottom: row.bottom });
      }
      out.set(owner, segs);
    }
    return out;
  }

  // Lanes for segments (`{key, top, bottom}`): each takes the leftmost lane
  // free over its rows; a longer segment first where two start together,
  // so a block holding another keeps the inner lane. Returns key -> lane.
  function lanes(items) {
    const sorted = [...items].sort((a, b) => a.top - b.top || (b.bottom - b.top) - (a.bottom - a.top));
    const ends = [];
    const lane = new Map();
    for (const item of sorted) {
      let k = ends.findIndex((end) => end <= item.top + 0.5);
      if (k < 0) {
        k = ends.length;
        ends.push(0);
      }
      ends[k] = item.bottom;
      lane.set(item.key, k);
    }
    return lane;
  }

  // At most how many blocks touch one source line: lanes never need more
  // (two blocks whose rows overlap share a row, so a line), and the column
  // is sized before the text is measured, so its width cannot reflow it.
  function laneBound(blocks) {
    const at = new Map();
    for (const block of blocks) {
      for (const [file, from, to] of block.lines) {
        for (let line = from; line <= to; line += 1) {
          const key = file + ":" + line;
          at.set(key, (at.get(key) || 0) + 1);
        }
      }
    }
    return Math.max(1, ...at.values());
  }

  // --- the open view's marks ---

  // The open items' marks: a
  // stale tick for an open stale mark, a ring for a block that uses an
  // unsupported claim; for a lapsed subject or finding, a disputed subject,
  // an open finding and an unanswered suggestion, a mark of its kind at
  // every place it is anchored.
  // `placesOf(item, index)` names where an item marks (the page: the rows
  // a finding's words start on, else its blocks' first rows); by default
  // its block. One mark per place and kind: `{kind, index, count,
  // severity}`, `index` its first item's in `items`, `count` how many it
  // stands for, `severity` a finding's highest; in `MARKED` order. The
  // kept graph's four faults mark at every block they sit on; a later
  // rule's problem at the block its line belongs to.
  // Place -> marks.
  const MARKED = ["finding", "negative", "unsupported", "circle", "disputed", "partial", "uses-unsupported", "flag", "retracted", "publication", "unplaced", "suggestion", "dangling-reference", "duplicate-label", "unused-premise", "undeclared-assumption", "later-rule", "lapsed", "stale", "unjudged", "inconclusive", "provisional", "unchecked"];
  function openMarks(items, placesOf = (item) => (item.block ? [item.block] : [])) {
    const at = new Map();
    items.forEach((item, index) => {
      if (!MARKED.includes(item.kind)) return;
      for (const place of new Set(placesOf(item, index))) {
        if (!at.has(place)) at.set(place, []);
        const marks = at.get(place);
        const mark = marks.find((m) => m.kind === item.kind);
        if (mark) {
          mark.count += 1;
          mark.indices.push(index);
          if (item.severity > mark.severity) mark.severity = item.severity;
        } else marks.push({ kind: item.kind, index, indices: [index], count: 1, severity: item.severity || 0 });
      }
    });
    for (const marks of at.values()) marks.sort((a, b) => MARKED.indexOf(a.kind) - MARKED.indexOf(b.kind));
    return at;
  }

  // Marks at measured rows (`{top, height, rank, ...}`) as the rows they
  // stand on: those whose rows overlap stand together, each row's marks
  // by rank (two blocks or claims on one source
  // line show at their own screen rows, not both at the line's top).
  function markRows(items) {
    const rows = [];
    for (const item of [...items].sort((a, b) => a.top - b.top || a.rank - b.rank)) {
      const last = rows[rows.length - 1];
      if (last && item.top < last.top + last.height - 2) last.items.push(item);
      else rows.push({ top: item.top, height: item.height, items: [item] });
    }
    for (const row of rows) row.items.sort((a, b) => a.rank - b.rank);
    return rows;
  }

  // The marks a row shows, and those folded into a `+N` (none dropped):
  // all where they fit, else the first `room - 1` (at least one) and the
  // rest folded.
  function shownMarks(marks, room = 3) {
    if (marks.length <= room) return { shown: marks, folded: [] };
    const kept = Math.max(1, room - 1);
    return { shown: marks.slice(0, kept), folded: marks.slice(kept) };
  }

  // A row's one mark (`!`): its marks ordered worst first, by each one's
  // tone (`toneOf(mark)`: `fail` or `warn` as the check's gates give it;
  // `finding`, a finding no gate fails or warns on, drawn in its
  // severity's tone; `none`), then in `MARKED` order, the more severe
  // first; `tone` the worst's.
  const TONE_RANK = { none: 0, finding: 1, warn: 2, fail: 3 };
  function worstFirst(marks, toneOf) {
    const rank = (m) => TONE_RANK[toneOf(m)] || 0;
    const sorted = [...marks].sort((a, b) => rank(b) - rank(a) || MARKED.indexOf(a.kind) - MARKED.indexOf(b.kind) || (b.severity || 0) - (a.severity || 0));
    return { tone: sorted.length && rank(sorted[0]) ? toneOf(sorted[0]) : "none", marks: sorted };
  }

  // The blocks starting on one row, their edges summed: `[in, out]` each,
  // the row's edges in and its edges out.
  function edgeSums(counts) {
    return counts.reduce(([a, b], [i, o]) => [a + i, b + o], [0, 0]);
  }

  return { edgeSums, groupLane, segments, blockSegments, lanes, laneBound, MARKED, openMarks, markRows, shownMarks, TONE_RANK, worstFirst };
});
