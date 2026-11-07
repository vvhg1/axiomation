// `node --test tests/viewer/`: the local graph: the
// neighbourhood to a depth over collapsed groups, which way each edge runs
// as the selection sees it, the `uses` reduction, and ELK's layout with a
// block below what it rests on.
const test = require("node:test");
const assert = require("node:assert/strict");

const M = require("../../src/axiomation/viewer/page/src/graph.js");
const ELK = require("../../src/axiomation/viewer/page/vendor/elk.bundled.js");

const edge = (source, kind, target, by = "agent") => ({ source, kind, target, by });
// d a definition; t a theorem resting on d and justified by the proof P
// (p1 uses the lemma l, p2 uses d); c uses t and d; r is about t and the
// paper; k uses t and r
const LAYER = {
  rested_on_by_source: ["justifies", "qualifies"],
  blocks: ["d", "t", "l", "p1", "p2", "c", "r", "k"].map((id) => ({ id })),
  groups: [{ id: "P", blocks: ["p1", "p2"], depth: 0 }],
  standings: [
    { block: "t", status: "justified" },
    { block: "c", status: "justified" },
    { block: "c", status: "unsupported" },
  ],
  edges: [
    edge("t", "uses", "d"),
    edge("l", "uses", "d"),
    edge("p1", "uses", "l"),
    edge("p2", "uses", "d"),
    edge("P", "justifies", "t"),
    edge("c", "uses", "t"),
    edge("c", "uses", "d", "person"),
    edge("r", "about", "t"),
    edge("r", "about", "scope:paper"),
    edge("k", "uses", "t"),
    edge("k", "uses", "r"),
  ],
};
const ids = (model) => model.nodes.map((n) => n.id);
const sides = (model) => Object.fromEntries(model.nodes.map((n) => [n.id, n.side]));
const streams = (model) => Object.fromEntries(model.edges.map((e) => [e.from + ">" + e.to, e.stream]));

test("depth 1: what the centre rests on and what rests on it, a group one node carrying its members' edges", () => {
  const model = M.neighbourhood(LAYER, "t");
  assert.deepEqual(ids(model), ["d", "t", "P", "c", "r", "k"]);
  assert.deepEqual(sides(model), { d: "up", t: "centre", P: "up", c: "down", r: "down", k: "down" });
  assert.equal(model.nodes.find((n) => n.id === "P").type, "group");
  // P justifies t: t rests on P; P carries p2's use of d
  assert.deepEqual(streams(model), {
    "t>d": "up", "P>d": "up", "t>P": "up",
    "c>t": "down", "r>t": "down", "k>t": "down", "k>r": "down",
  });
});

test("an edge between two neighbours, on neither of the selection's ways, only when asked for", () => {
  const model = M.neighbourhood(LAYER, "t");
  // c uses d: c rests on t, t on d; c's own use of d is beside the way
  assert.equal(model.aside, 1);
  assert.ok(!model.edges.some((e) => e.from === "c" && e.to === "d"));
  const all = M.neighbourhood(LAYER, "t", { aside: true, reduce: false });
  const beside = all.edges.find((e) => e.from === "c" && e.to === "d");
  assert.equal(beside.stream, "aside");
  assert.equal(beside.by, "person");
  // k uses t is kept: k uses r, but r is only about t
  assert.ok(model.edges.some((e) => e.from === "k" && e.to === "t"));
});

test("the reduction hides a use another chain implies, never the selection's own", () => {
  const chain = {
    rested_on_by_source: ["justifies", "qualifies"],
    blocks: ["z", "y", "x", "w"].map((id) => ({ id })),
    groups: [],
    standings: [],
    edges: [edge("y", "uses", "z"), edge("x", "uses", "y"), edge("x", "uses", "z"), edge("w", "uses", "x")],
  };
  const own = M.neighbourhood(chain, "x");
  assert.equal(own.hidden, 0);
  assert.ok(own.edges.some((e) => e.from === "x" && e.to === "z"));
  const deeper = M.neighbourhood(chain, "w", { depth: 3 });
  assert.equal(deeper.hidden, 1);
  assert.ok(!deeper.edges.some((e) => e.from === "x" && e.to === "z"));
});

test("a hub shows its nearest neighbours a side, the rest one node, unless uncapped", () => {
  const users = Array.from({ length: 10 }, (_, i) => "u" + i);
  const hub = {
    rested_on_by_source: ["justifies", "qualifies"],
    blocks: [...users.slice(0, 5), "h", ...users.slice(5)].map((id) => ({ id })),
    groups: [],
    standings: [],
    edges: [...users.map((u) => edge(u, "uses", "h")), edge("u0", "uses", "u9")],
  };
  const model = M.neighbourhood(hub, "h", { cap: 3, depth: 2 });
  assert.deepEqual(model.capped, { up: 0, down: 7 });
  assert.deepEqual(ids(model).filter((id) => id.startsWith("u")).sort(), ["u3", "u4", "u5"]);
  const more = model.nodes.find((n) => n.type === "more");
  assert.deepEqual([more.id, more.side, more.count], [M.MORE + "down", "down", 7]);
  assert.ok(model.edges.some((e) => e.from === more.id && e.to === "h" && e.stream === "down"));
  const whole = M.neighbourhood(hub, "h", { cap: 3, uncapped: new Set(["down"]) });
  assert.equal(whole.nodes.length, 11);
  assert.deepEqual(whole.capped, { up: 0, down: 0 });
});

test("the reduction keeps what reaches what, in a cycle too", () => {
  const e = (from, to) => ({ id: from + to, from, to, kind: "uses" });
  const all = [e("a", "b"), e("b", "c"), e("c", "a"), e("a", "c"), { id: "x", from: "a", to: "c", kind: "about" }];
  const kept = M.reduced(all);
  assert.deepEqual(kept.map((k) => k.id), ["ab", "bc", "ca", "x"]);
});

test("deeper: the second step up through the group, a file or the paper an end", () => {
  const model = M.neighbourhood(LAYER, "t", { depth: 2 });
  assert.equal(sides(model).l, "up");
  const r = M.neighbourhood(LAYER, "r", { depth: 3 });
  assert.equal(r.nodes.find((n) => n.id === "scope:paper").type, "external");
  assert.deepEqual(sides(r)["d"], "up");
});

test("a centre inside a group opens it: the group a container, the centre within", () => {
  const model = M.neighbourhood(LAYER, "p1");
  const byId = Object.fromEntries(model.nodes.map((n) => [n.id, n]));
  assert.equal(byId.P.type, "open");
  assert.equal(byId.p1.parent, "P");
  assert.equal(byId.l.side, "up");
  // p2 is not reached, so not drawn
  assert.equal(byId.p2, undefined);
});

test("a group opened by a click shows all its members, on its side", () => {
  const model = M.neighbourhood(LAYER, "t", { expanded: new Set(["P"]) });
  const byId = Object.fromEntries(model.nodes.map((n) => [n.id, n]));
  assert.equal(byId.P.type, "open");
  assert.equal(byId.p1.parent, "P");
  assert.equal(byId.p2.side, "up");
  assert.equal(streams(model)["p2>d"], "up");
  assert.equal(streams(model)["t>P"], "up");
});

test("the kinds narrow what is walked and drawn", () => {
  const model = M.neighbourhood(LAYER, "t", { kinds: ["about"] });
  assert.deepEqual(ids(model), ["t", "r"]);
});

test("a block's fill is the standing most in need among its claims", () => {
  const found = M.standingOf(LAYER);
  assert.equal(found.get("c"), "unsupported");
  assert.equal(found.get("t"), "justified");
  // read by the page's key, its tone: a claim no argument is owed for
  // needs less than a conjecture, more than a justified one
  const ruled = { standings: [{ block: "b", status: "unsupported", support: "no-argument-owed" }, { block: "b", status: "justified", support: "supported" }] };
  assert.equal(M.standingOf(ruled, (s) => (s.support === "no-argument-owed" ? s.support : s.status)).get("b"), "no-argument-owed");
});

test("ELK lays a block below what it rests on, the group's members inside it", async () => {
  const model = M.neighbourhood(LAYER, "t", { depth: 2, expanded: new Set(["P"]) });
  const sizes = new Map(model.nodes.map((n) => [n.id, M.sizeOf(n.id)]));
  const input = M.elkGraph(model, sizes);
  assert.deepEqual(input.children.find((c) => c.id === "P").children.map((c) => c.id), ["p1", "p2"]);
  const laid = await new ELK().layout(input);
  const { at, boxes } = M.centres(laid);
  const top = (id) => (at.has(id) ? at.get(id).y : boxes.get(id).y + boxes.get(id).height / 2);
  for (const e of model.edges) assert.ok(top(e.from) > top(e.to), e.id + " runs upward");
  const box = boxes.get("P");
  for (const member of ["p1", "p2"]) {
    const { x, y } = at.get(member);
    assert.ok(x > box.x && x < box.x + box.width && y > box.y && y < box.y + box.height, member + " inside P");
  }
});

test("a proof about the claim it justifies stands above the claim: the description yields", async () => {
  const layer = {
    rested_on_by_source: ["justifies", "qualifies"],
    blocks: ["c", "q1"].map((id) => ({ id })),
    groups: [{ id: "Q", blocks: ["q1"], depth: 0 }],
    standings: [],
    edges: [edge("q1", "about", "c"), edge("Q", "justifies", "c")],
  };
  const model = M.neighbourhood(layer, "c");
  assert.equal(model.edges.length, 2);
  const input = M.elkGraph(model, new Map(model.nodes.map((n) => [n.id, M.sizeOf(n.id)])));
  assert.deepEqual(input.edges.map((e) => e.sources[0] + ">" + e.targets[0]), ["c>Q"]);
  const { at } = M.centres(await new ELK().layout(input));
  assert.ok(at.get("c").y > at.get("Q").y);
});

test("the description yields with the group open too: its member's edge against the group's", async () => {
  const layer = {
    rested_on_by_source: ["justifies", "qualifies"],
    blocks: ["c", "q1", "q2"].map((id) => ({ id })),
    groups: [{ id: "Q", blocks: ["q1", "q2"], depth: 0 }],
    standings: [],
    edges: [edge("q1", "about", "c"), edge("Q", "justifies", "c"), edge("q2", "uses", "q1")],
  };
  const model = M.neighbourhood(layer, "c", { expanded: new Set(["Q"]) });
  const input = M.elkGraph(model, new Map(model.nodes.map((n) => [n.id, M.sizeOf(n.id)])));
  assert.ok(!input.edges.some((e) => e.sources[0] === "q1" && e.targets[0] === "c"));
  const { at, boxes } = M.centres(await new ELK().layout(input));
  assert.ok(at.get("c").y > boxes.get("Q").y + boxes.get("Q").height);
});

test("positions held: a layout seeded with an earlier one moves no unchanged node by more than a cell", async () => {
  const before = M.neighbourhood(LAYER, "t", { depth: 2 });
  const sizes = new Map(before.nodes.map((n) => [n.id, M.sizeOf(n.id)]));
  const first = M.centres(await new ELK().layout(M.elkGraph(before, sizes)));
  const after = {
    ...before,
    nodes: [...before.nodes, { id: "new", type: "block", side: "down", parent: null, order: 3.5 }],
    edges: [...before.edges, { id: "new>t", from: "new", to: "t", kind: "uses", stream: "down", ids: [], by: "agent" }],
  };
  sizes.set("new", M.sizeOf("new"));
  const second = M.centres(await new ELK().layout(M.seeded(M.elkGraph(after, sizes), first.at)));
  const cell = 60;
  for (const [id, p] of first.at) {
    const q = second.at.get(id);
    assert.ok(Math.hypot(q.x - p.x, q.y - p.y) <= cell, id + " moved " + Math.hypot(q.x - p.x, q.y - p.y).toFixed(1));
  }
  assert.ok(second.at.has("new"));
});

test("a block's glyphs: ✓ exactly for the established set, ⚠ for the open view's users of an unsupported claim", () => {
  const established = new Set(["b1", "b3"]);
  const usesUnsupported = new Set(["b3", "b4"]);
  const ids = ["b1", "b2", "b3", "b4", "g1"];
  assert.deepEqual(ids.map((id) => M.glyphs(id, { established, usesUnsupported })), [["established"], [], ["established", "uses-unsupported"], ["uses-unsupported"], []]);
  assert.deepEqual(ids.filter((id) => M.glyphs(id, { established }).includes("established")), [...established]);
  assert.deepEqual(M.glyphs("b1"), []);
  assert.deepEqual(M.GLYPHS, ["established", "uses-unsupported"]);
});
