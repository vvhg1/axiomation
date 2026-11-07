// `node --test tests/viewer/`: the gutter's lanes, with the open view's marks.
const test = require("node:test");
const assert = require("node:assert/strict");

const G = require("../../src/axiomation/viewer/page/src/gutter.js");

function block(id, lines, size, extra = {}) {
  return {
    id,
    owned: [["a.tex", 0, size]],
    lines: lines.map(([first, last]) => ["a.tex", first, last]),
    edges_in: 0,
    edges_out: 0,
    ...extra,
  };
}

test("group brackets at three depths", () => {
  const blocks = [block("b1", [[1, 1]], 10), block("b2", [[2, 3]], 10), block("b3", [[4, 4]], 10)];
  const byId = new Map(blocks.map((b) => [b.id, b]));
  const groups = [
    { id: "run", kind: "step-run", blocks: ["b1", "b2", "b3"], depth: 0 },
    { id: "step", kind: "step", blocks: ["b2", "b3"], depth: 1 },
    { id: "proof", kind: "proof", blocks: ["b3"], depth: 2 },
  ];
  const columns = G.groupLane(groups, byId, "a.tex", 5);
  assert.equal(columns.length, 3);
  const at = (column) => columns[column].map((c) => (c ? c.group : null));
  assert.deepEqual(at(0), [0, 0, 0, 0, null]);
  assert.deepEqual(at(1), [null, 1, 1, 1, null]);
  assert.deepEqual(at(2), [null, null, null, 2, null]);
  assert.deepEqual([columns[1][1].start, columns[1][3].end, columns[1][2].start], [true, true, false]);
});

test("the blocks starting on one row: their edges in summed, and their edges out", () => {
  assert.deepEqual(G.edgeSums([[2, 5], [0, 1], [3, 0]]), [5, 6]);
  assert.deepEqual(G.edgeSums([[4, 1]]), [4, 1]);
  assert.deepEqual(G.edgeSums([]), [0, 0]);
});

test("a block's rendered rows become segments; an interrupted block gets two", () => {
  const rows = [{ top: 36, bottom: 54 }, { top: 0, bottom: 18 }, { top: 18, bottom: 36 }, { top: 90, bottom: 108 }];
  assert.deepEqual(G.segments(rows), [{ top: 0, bottom: 54 }, { top: 90, bottom: 108 }]);
});

test("blocks sharing rows stand side by side; one that follows takes a free lane again", () => {
  // an example ends on its second row, the claim runs from the second to
  // the fifth, the next block from the fifth on
  const lane = G.lanes([
    { key: "exa", top: 0, bottom: 108 },
    { key: "claim", top: 90, bottom: 180 },
    { key: "next", top: 162, bottom: 300 },
    { key: "later", top: 300, bottom: 320 },
  ]);
  assert.equal(lane.get("exa"), 0);
  assert.equal(lane.get("claim"), 1);
  assert.equal(lane.get("next"), 0);
  assert.equal(lane.get("later"), 0);
  // a block holding another keeps the inner lane
  const nested = G.lanes([{ key: "note", top: 18, bottom: 36 }, { key: "para", top: 18, bottom: 90 }]);
  assert.deepEqual([nested.get("para"), nested.get("note")], [0, 1]);
});

test("the lane column is sized by the most blocks one source line holds", () => {
  const blocks = [block("a", [[1, 5]], 10), block("b", [[5, 5]], 5), block("c", [[5, 9]], 9), block("d", [[7, 8]], 3)];
  assert.equal(G.laneBound(blocks), 3);
});

test("a blank line inside a block keeps one bar; another block's row between splits it", () => {
  // rows 18 px apart: "para" on rows 0 and 2 (row 1 blank), "note" on row 5,
  // "para" again on row 7, interrupted by the note
  const rows = new Map([
    ["para", [{ top: 0, bottom: 15 }, { top: 36, bottom: 51 }, { top: 126, bottom: 141 }]],
    ["note", [{ top: 90, bottom: 105 }]],
  ]);
  const segs = G.blockSegments(rows, 4);
  assert.deepEqual(segs.get("para"), [{ top: 0, bottom: 51 }, { top: 126, bottom: 141 }]);
  assert.deepEqual(segs.get("note"), [{ top: 90, bottom: 105 }]);
  // another block on the same visual line as the last row, a pixel taller,
  // is not between them
  const shared = new Map([
    ["a", [{ top: 0, bottom: 15 }, { top: 36, bottom: 51 }]],
    ["b", [{ top: 0, bottom: 16 }]],
  ]);
  assert.deepEqual(G.blockSegments(shared, 4).get("a"), [{ top: 0, bottom: 51 }]);
});

test("the open view's marks are by place, by default the item's block, in their order; other kinds and the paper's items have none", () => {
  const blocks = new Map([
    ["b1", block("b1", [[3, 5]], 10)],
    ["b2", block("b2", [[3, 3], [8, 9]], 5)],
  ]);
  const items = [
    { kind: "stale", block: "b1" },
    { kind: "unsupported", block: "b1" },
    { kind: "uses-unsupported", block: "b2" },
    { kind: "pending", block: null },
    { kind: "stale", block: "gone" },
  ];
  const at = G.openMarks(items, (item) => (blocks.has(item.block) ? [item.block] : []));
  assert.deepEqual([...at.entries()], [
    ["b1", [{ kind: "unsupported", index: 1, indices: [1], count: 1, severity: 0 }, { kind: "stale", index: 0, indices: [0], count: 1, severity: 0 }]],
    ["b2", [{ kind: "uses-unsupported", index: 2, indices: [2], count: 1, severity: 0 }]],
  ]);
});

test("a finding marks every place it anchors, once a kind a place, in its highest severity", () => {
  const blocks = new Map([
    ["b1", block("b1", [[3, 5]], 10)],
    ["b2", block("b2", [[8, 9]], 5)],
  ]);
  const items = [
    { kind: "stale", block: "b1" },
    { kind: "finding", block: "b1", severity: 1, anchors: ["b1", "b2"] },
    { kind: "finding", block: "b2", severity: 3, anchors: ["b2"] },
    { kind: "lapsed", block: "b2", anchors: ["b2", "gone"] },
    { kind: "disputed", block: "b1" },
    { kind: "carried-unread", block: "b1" },
  ];
  const at = G.openMarks(items, (item) => (item.anchors || [item.block]).filter((b) => blocks.has(b)));
  assert.deepEqual([...at.entries()], [
    ["b1", [{ kind: "finding", index: 1, indices: [1], count: 1, severity: 1 }, { kind: "disputed", index: 4, indices: [4], count: 1, severity: 0 }, { kind: "stale", index: 0, indices: [0], count: 1, severity: 0 }]],
    ["b2", [{ kind: "finding", index: 1, indices: [1, 2], count: 2, severity: 3 }, { kind: "lapsed", index: 3, indices: [3], count: 1, severity: 0 }]],
  ]);
  const four = [1, 2, 3, 4].map((index) => ({ kind: "k", index }));
  assert.deepEqual(G.shownMarks(four.slice(0, 3)), { shown: four.slice(0, 3), folded: [] });
  assert.deepEqual(G.shownMarks(four), { shown: four.slice(0, 2), folded: four.slice(2) });
  // the counts column holds one: the first, the rest folded, none lost
  assert.deepEqual(G.shownMarks(four.slice(0, 2), 1), { shown: four.slice(0, 1), folded: four.slice(1, 2) });
  assert.deepEqual(G.shownMarks(four.slice(0, 1), 1), { shown: four.slice(0, 1), folded: [] });
  // a row's one mark: the worst gate tone first, then the marks' order
  const tones = { lapsed: "warn", unsupported: "fail" };
  const toneOf = (mark) => tones[mark.kind] || "none";
  const row = [{ kind: "stale", index: 0 }, { kind: "lapsed", index: 1 }, { kind: "unsupported", index: 2 }, { kind: "finding", index: 3 }];
  assert.deepEqual(G.worstFirst(row, toneOf), { tone: "fail", marks: [row[2], row[1], row[3], row[0]] });
  assert.equal(G.worstFirst([row[0]], toneOf).tone, "none");
  assert.equal(G.worstFirst([row[0], row[1]], () => undefined).tone, "none");
  // a finding no gate fails or warns on outranks grey, not amber or red;
  // two such, the more severe first
  const own = (mark) => (mark.kind === "finding" ? "finding" : tones[mark.kind] || "none");
  const minor = { kind: "finding", index: 4, severity: 1 };
  const worse = { kind: "finding", index: 5, severity: 2 };
  assert.deepEqual(G.worstFirst([row[0], minor, worse], own), { tone: "finding", marks: [worse, minor, row[0]] });
  assert.deepEqual(G.worstFirst([worse, row[1]], own), { tone: "warn", marks: [row[1], worse] });
});

test("the kept graph's four faults mark every block they sit on, before what is only to read again", () => {
  const items = [
    { kind: "duplicate-label", block: "b1", anchors: ["b1", "b2"] },
    { kind: "undeclared-assumption", block: "b3", anchors: ["b3", "b2", "b1"] },
    { kind: "stale", block: "b1" },
    { kind: "dangling-reference", block: "b2", anchors: ["b2"] },
    { kind: "unused-premise", block: "b3", anchors: ["b3"] },
  ];
  const at = G.openMarks(items, (item) => item.anchors || [item.block]);
  const kinds = (place) => at.get(place).map((m) => m.kind);
  assert.deepEqual(kinds("b1"), ["duplicate-label", "undeclared-assumption", "stale"]);
  assert.deepEqual(kinds("b2"), ["dangling-reference", "duplicate-label", "undeclared-assumption"]);
  assert.deepEqual(kinds("b3"), ["unused-premise", "undeclared-assumption"]);
});

test("marks stand at their own measured rows: two blocks on one source line apart, overlapping rows together, by rank", () => {
  // one source line, two blocks and three claims, wrapped over fourteen
  // screen rows
  const items = [
    { top: 0, height: 15, rank: 20, name: "claim 1" },
    { top: 0, height: 15, rank: 0, name: "finding on block 1" },
    { top: 18, height: 15, rank: 20, name: "claim 2" },
    { top: 54, height: 15, rank: 0, name: "finding on block 2" },
    { top: 54.5, height: 15, rank: 20, name: "claim of block 2" },
    { top: 216, height: 15, rank: 20, name: "claim 3" },
  ];
  const rows = G.markRows(items);
  assert.deepEqual(rows.map((r) => [r.top, r.items.map((i) => i.name)]), [
    [0, ["finding on block 1", "claim 1"]],
    [18, ["claim 2"]],
    [54, ["finding on block 2", "claim of block 2"]],
    [216, ["claim 3"]],
  ]);
  assert.deepEqual(G.markRows([]), []);
});
