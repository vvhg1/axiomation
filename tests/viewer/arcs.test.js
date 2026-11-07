// `node --test tests/viewer/`: the arcs: which edges a selection
// draws through a lens, where their ends stand, and their path.
const test = require("node:test");
const assert = require("node:assert/strict");

const R = require("../../src/axiomation/viewer/page/src/arcs.js");
const T = require("../../src/axiomation/viewer/page/src/text.js");

const TEXT = "line one\nline two\nline three\nline four\nline five\n";
//            0        9        18         29        39
const starts = new Map([["p.md", T.lineStarts(TEXT)]]);
const block = (id, first, last) => ({ id, lines: [["p.md", first, last]] });
const LAYER = {
  rested_on_by_source: ["justifies", "qualifies"],
  blocks: [block("a", 1, 1), block("b", 2, 3), block("c", 4, 4), block("d", 5, 5)],
  groups: [{ id: "g", blocks: ["c", "d"] }],
  edges: [
    // a's evidence on line 1, onto b's part on line 3
    { source: "a", target: "b", kind: "uses", by: "agent", evidence: [["p.md", 2, 5]], anchors: [["p.md", 20, 24]] },
    // b about a, no evidence placed: from b's first line
    { source: "b", target: "a", kind: "about", by: "person", evidence: [], anchors: [] },
    // the group justifies b: from its first block's line
    { source: "g", target: "b", kind: "justifies", by: "agent", evidence: [], anchors: [] },
    // onto the paper: no row, never drawn
    { source: "a", target: "scope:paper", kind: "about", by: "agent", evidence: [], anchors: [] },
  ],
};
const index = R.indexOf(LAYER);

test("a selection's edges out and in, each end at its evidence's or anchor's row", () => {
  const arcs = R.selected(LAYER, index, starts, "a", "all");
  assert.deepEqual(arcs.map((a) => [a.edge, a.dir, a.near, a.far, a.other]), [
    [0, "out", ["p.md", 1], ["p.md", 3], "b"],
    [1, "in", ["p.md", 1], ["p.md", 2], "b"],
  ]);
});

test("a group's edge stands at its first block; a group selected draws its own", () => {
  const into = R.selected(LAYER, index, starts, "b", "support");
  assert.deepEqual(into.map((a) => [a.dir, a.near, a.far, a.other]), [["in", ["p.md", 2], ["p.md", 4], "g"]]);
  assert.deepEqual(R.selected(LAYER, index, starts, "g", "all").map((a) => a.dir), ["out"]);
  assert.deepEqual(R.rowOf(index, "g"), ["p.md", 4]);
});

test("a lens draws its kinds only", () => {
  assert.deepEqual(R.selected(LAYER, index, starts, "b", "reliance").map((a) => a.kind), ["uses"]);
  assert.deepEqual(R.selected(LAYER, index, starts, "b", "description").map((a) => a.kind), ["about"]);
  assert.deepEqual(Object.keys(R.LENSES), ["all", "reliance", "support", "description"]);
  assert.equal(R.every(LAYER, index, starts, "all").length, 3);
});

test("an arc bulges right by how far apart its ends are, within its column", () => {
  assert.equal(R.path(6, 10, 30, 90), "M6 10 C17.2 10 17.2 30 6 30");
  assert.ok(R.path(6, 0, 100000, 90).includes("C90 0 90 100000"));
});

test("the selection's arcs take lanes: nearest innermost, furthest outermost, overlapping spans apart", () => {
  const spans = [
    { y1: 100, y2: 400 }, // down, far
    { y1: 100, y2: 140 }, // down, near
    { y1: 100, y2: 250 }, // down, between
    { y1: 100, y2: 60 }, // up, the only one
  ];
  assert.deepEqual(R.fan(spans, 10, 138), [24, 10, 17, 10]);
  // an edge out from evidence below the selection's row runs up over an
  // edge in running down from it: the two take lanes of their own
  const crossed = [
    { y1: 923, y2: 869 }, // out, up from the evidence
    { y1: 859, y2: 985 }, // in, down from the selection's row
  ];
  assert.deepEqual(R.fan(crossed, 10, 138), [10, 17]);
  // spans apart share a lane
  assert.deepEqual(R.fan([{ y1: 0, y2: 50 }, { y1: 60, y2: 100 }], 10, 138), [10, 10]);
  // many squeeze into the column
  const many = Array.from({ length: 33 }, (_, i) => ({ y1: 0, y2: 10 + i }));
  const lanes = R.fan(many, 10, 138);
  assert.equal(Math.max(...lanes), 138);
  assert.equal(R.fan([], 10, 138).length, 0);
});

test("an edge is a bracket: out to its lane, along it, back", () => {
  assert.equal(R.bracket(6, 100, 300, 30), "M6 100 H25 Q30 100 30 105 V295 Q30 300 25 300 H6");
  assert.equal(R.bracket(6, 300, 100, 30), "M6 300 H25 Q30 300 30 295 V105 Q30 100 25 100 H6");
  assert.equal(R.bracket(6, 100, 100.4, 30), "M6 100 H30");
  // an end at an arrowhead stops at the head's base
  assert.equal(R.bracket(6, 100, 300, 30, 5, 12), "M6 100 H25 Q30 100 30 105 V295 Q30 300 25 300 H12");
});

test("which way reliance runs is the layer's rule: up where the selection rests on the other end", () => {
  // a uses b: a rests on b; g justifies b: b rests on g
  assert.deepEqual(R.selected(LAYER, index, starts, "a", "all").map((a) => [a.other, a.stream]), [["b", "up"], ["b", "down"]]);
  assert.deepEqual(R.selected(LAYER, index, starts, "b", "all").map((a) => [a.other, a.stream]), [["a", "down"], ["a", "up"], ["g", "up"]]);
  assert.deepEqual(R.selected(LAYER, index, starts, "g", "all").map((a) => [a.other, a.stream]), [["b", "down"]]);
});

test("an edge's target end is an arrowhead pointing into the text, its tip on the end", () => {
  assert.equal(R.arrowhead(6, 100), "M6 100 L12 96.4 L12 103.6 Z");
  assert.equal(R.arrowhead(6, 100, 10), "M6 100 L16 94 L16 106 Z");
});

test("an edge's end boxed as its words wrap, as a selection is drawn, on the half pixel", () => {
  // one row: its pieces one box, padded a pixel
  assert.deepEqual(R.outline([{ left: 10, right: 30, top: 0, bottom: 10 }, { left: 30, right: 50.3, top: 0, bottom: 12 }]), ["M9.5 -0.5 L51.5 -0.5 L51.5 13.5 L9.5 13.5 Z"]);
  // starting mid-row and wrapping: first row to the widest's right, last from its left
  assert.deepEqual(R.outline([{ left: 30, right: 100, top: 0, bottom: 10 }, { left: 0, right: 60, top: 12, bottom: 22 }]),
    ["M29.5 -0.5 L101.5 -0.5 L101.5 11.5 L61.5 11.5 L61.5 23.5 L-0.5 23.5 L-0.5 11.5 L29.5 11.5 Z"]);
  // two rows that do not meet: two boxes
  assert.equal(R.outline([{ left: 30, right: 100, top: 0, bottom: 10 }, { left: 0, right: 20, top: 12, bottom: 22 }]).length, 2);
  // a centred formula between two rows, an indent's blank row: no step of their own
  const rows = [
    { left: 30, right: 80, top: 0, bottom: 10 },
    { left: 0, right: 30, top: 12, bottom: 22, blank: true },
    { left: 60, right: 110, top: 24, bottom: 50 },
    { left: 0, right: 30, top: 52, bottom: 62, blank: true },
    { left: 30, right: 112, top: 52, bottom: 62 },
  ];
  assert.deepEqual(R.outline(rows), ["M29.5 -0.5 L113.5 -0.5 L113.5 63.5 L29.5 63.5 Z"]);
  // a row far below: its own outline; an empty rect none
  assert.equal(R.outline([{ left: 0, right: 20, top: 0, bottom: 10 }, { left: 0, right: 40, top: 100, bottom: 110 }]).length, 2);
  assert.deepEqual(R.outline([{ left: 5, right: 5, top: 0, bottom: 10 }]), []);
});
