// `node --test tests/viewer/`: the text pane's arithmetic.
const test = require("node:test");
const assert = require("node:assert/strict");

const T = require("../../src/axiomation/viewer/page/src/text.js");

const TEXT = "first line\nsecond line here\nthird\n";
//            0         10 11              27 28   33

test("line starts and the line of an offset", () => {
  const starts = T.lineStarts(TEXT);
  assert.deepEqual(starts, [0, 11, 28, 34]);
  assert.equal(T.lineOf(starts, 0), 1);
  assert.equal(T.lineOf(starts, 10), 1);
  assert.equal(T.lineOf(starts, 11), 2);
  assert.equal(T.lineOf(starts, 33), 3);
});

test("a segment's lines end before their newline; a partial line is cut", () => {
  const starts = T.lineStarts(TEXT);
  // a trailing newline ends the last line; no empty line follows it
  assert.deepEqual(T.segmentLines({ start: 0, end: TEXT.length }, starts, TEXT.length), [
    { line: 1, start: 0, end: 10 },
    { line: 2, start: 11, end: 27 },
    { line: 3, start: 28, end: 33 },
  ]);
  // a segment resuming mid-line, and one closing mid-line
  assert.deepEqual(T.segmentLines({ start: 18, end: 30 }, starts, TEXT.length), [
    { line: 2, start: 18, end: 27 },
    { line: 3, start: 28, end: 30 },
  ]);
});

// runs: [start, end, class, owner]
const RUNS = [
  [0, 5, "owned", 0],
  [5, 6, "space", -1],
  [6, 10, "owned", 1],
  [10, 11, "space", -1],
  [11, 27, "owned", 1], // a run crossing into the next line's text
  [27, 28, "space", -1],
  [28, 33, "uncovered", -1],
];

test("the runs that meet a line, a run crossing a line break included", () => {
  assert.deepEqual(T.runsOn(RUNS, 11, 27), [[11, 27, "owned", 1]]);
  assert.deepEqual(
    T.runsOn(RUNS, 0, 10).map((run) => run[3]),
    [0, -1, 1]
  );
  assert.deepEqual(T.runsOn(RUNS, 28, 33), [[28, 33, "uncovered", -1]]);
});

test("pieces cut at every layer edge and carry the layers covering them", () => {
  const part = { start: 2, end: 4, cls: "part" };
  const site = { start: 3, end: 8, cls: "cand" };
  const out = T.pieces(0, 10, [part, site]);
  assert.deepEqual(out.map((p) => [p.start, p.end]), [[0, 2], [2, 3], [3, 4], [4, 8], [8, 10]]);
  assert.deepEqual(out.map((p) => p.layers.map((l) => l.cls)), [[], ["part"], ["part", "cand"], ["cand"], []]);
});

test("the selected block's runs", () => {
  assert.deepEqual(T.blockRuns(RUNS, 1), [[6, 10], [11, 27]]);
  assert.deepEqual(T.blockRuns(RUNS, 7), []);
});

test("rows: a line a rendered formula runs past joins the next", () => {
  const text = "a $x$ b\n$$\ny = 1\n$$ then\nlast\n";
  const starts = T.lineStarts(text);
  const lines = T.segmentLines({ start: 0, end: text.length }, starts, text.length);
  const shape = (rows) => rows.map((r) => [r.first, r.last, text.slice(r.start, r.end)]);
  assert.deepEqual(shape(T.mergedRows(lines, [])), [[1, 1, "a $x$ b"], [2, 2, "$$"], [3, 3, "y = 1"], [4, 4, "$$ then"], [5, 5, "last"]]);
  // the inline formula stays on its line; the display joins lines 2 to 4
  const formulas = [{ start: 2, end: 5 }, { start: 8, end: 19 }];
  assert.deepEqual(shape(T.mergedRows(lines, formulas)), [[1, 1, "a $x$ b"], [2, 4, "$$\ny = 1\n$$ then"], [5, 5, "last"]]);
  // a formula starting on a joined line joins further
  const chained = [{ start: 8, end: 16 }, { start: 15, end: 22 }];
  assert.deepEqual(shape(T.mergedRows(lines, chained)).slice(1, 2), [[2, 4, "$$\ny = 1\n$$ then"]]);
});
