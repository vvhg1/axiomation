// `node --test tests/viewer/`: anchors: each kind placed on the text, and one that
// does not place kept unanchored at its block or the paper.
const test = require("node:test");
const assert = require("node:assert/strict");

const N = require("../../src/axiomation/viewer/page/src/anchors.js");

const TEXT = "First block says   alpha\nbeta here.\nSecond block cites \\cite{k1} and alpha beta.\n";
//            0 · the second block from 36, its citation 55 to 64
const LAYER = {
  files: [{ path: "p.md", text: TEXT }],
  blocks: [
    { id: "b1", node: "n1", owned: [["p.md", 0, 36]] },
    { id: "b2", node: "n2", owned: [["p.md", 36, TEXT.length]] },
  ],
  groups: [{ id: "g", node: "g1", blocks: ["b1", "b2"] }],
  citations: [{ keys: ["k1"], command: "cite", span: ["p.md", 55, 64] }],
  edges: [{ id: "e1", source: "b2", target: "file:checks/c.py", kind: "uses", evidence: [["p.md", 69, 79]] }],
};
const at = N.index(LAYER);
const slice = ([, s, e]) => TEXT.slice(s, e);

test("every kind of anchor places", () => {
  assert.deepEqual(N.resolve(at, { kind: "block", node: "n2" }).spans, [["p.md", 36, TEXT.length]]);
  assert.equal(N.resolve(at, { kind: "group", node: "g1" }).spans.length, 2);
  assert.equal(N.resolve(at, { kind: "group", node: "g1" }).block, "b1");
  const part = N.resolve(at, { kind: "part", node: "n1", words: "alpha beta" });
  assert.equal(part.placed, true);
  assert.equal(slice(part.spans[0]), "alpha\nbeta", "whitespace aside");
  assert.equal(slice(N.resolve(at, { kind: "span", node: "n2", words: "\\cite{k1}" }).spans[0]), "\\cite{k1}");
  assert.deepEqual(N.resolve(at, { kind: "paper" }), { placed: true, kind: "paper", block: null, spans: [] });
  assert.equal(slice(N.resolve(at, { kind: "work", work: "k1" }).spans[0]), "\\cite{k1}");
  assert.equal(slice(N.resolve(at, { kind: "file", file: "checks/c.py", words: "x = 1" }).spans[0]), "alpha beta", "at the evidence of its edges");
  assert.deepEqual(N.KINDS, ["block", "part", "group", "span", "paper", "work", "file"]);
});

test("an anchor that does not place is kept, unanchored, where it can show", () => {
  const gone = N.resolve(at, { kind: "part", node: "n1", words: "gamma" });
  assert.deepEqual([gone.placed, gone.block], [false, "b1"]);
  assert.match(gone.why, /0 times/);
  assert.equal(N.resolve(at, { kind: "span", node: "n2", words: "a" }).placed, false, "found more than once");
  assert.deepEqual([N.resolve(at, { kind: "block", node: "n9" }).placed, N.resolve(at, { kind: "block", node: "n9" }).block], [false, null]);
  assert.equal(N.resolve(at, { kind: "work", work: "k2" }).placed, false);
  assert.match(N.resolve(at, { kind: "file", file: "other.py" }).why, /no edge onto file:other.py/);
  assert.equal(N.resolve(at, { kind: "block", node: "g1" }).placed, false, "a group anchored as a block");
});

test("an open item's anchors placed: those it is given, on words or whole, one that does not place named", () => {
  const items = [
    { kind: "stale", block: "b1" },
    { kind: "finding", finding: "f1", severity: 3, anchors: [{ kind: "span", node: "n2", words: "Second block" }, { kind: "block", node: "n1" }] },
    { kind: "lapsed", finding: "f2", anchors: [{ kind: "part", node: "n1", words: "no such words" }, { kind: "span", node: "n1", words: "beta here" }] },
    { kind: "lapsed", judgments: ["j1"], block: "b2" },
    { kind: "unsupported", block: "b1", anchor: { kind: "part", node: "n1", words: "beta here" } },
    { kind: "retracted", anchor: { kind: "work", work: "k1" } },
    { kind: "uses-unsupported", block: "b2", anchor: { kind: "block", node: "n2" } },
  ];
  // the caller's choice: a block using an unsupported claim places nothing
  const anchorsOf = (item) => (item.kind === "uses-unsupported" ? [] : item.anchors || (item.anchor ? [item.anchor] : []));
  const { places, unanchored } = N.placeItems(at, items, anchorsOf);
  assert.deepEqual(places.map((p) => [p.item, p.whole, p.words, p.spans.map(slice)]), [
    [1, false, true, ["Second block"]],
    [1, true, false, ["First block says   alpha\nbeta here.\n"]],
    [2, false, true, ["beta here"]],
    [4, false, true, ["beta here"]],
    [5, false, true, ["\\cite{k1}"]],
  ]);
  assert.deepEqual([...unanchored], [[2, "its words are found 0 times, not once"]]);
  assert.deepEqual(items.map((item) => N.blocksOf(at, item)), [["b1"], ["b2", "b1"], ["b1", "b1"], ["b2"], ["b1"], [], ["b2"]]);
});
