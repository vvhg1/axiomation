// `node --test tests/viewer/`: the inbox: `axiomation inbox`'s
// decisions first, a section per kind, each item a card with its next
// step; then what waits on a review, folded; one line on the lapsed
// judgments.
const test = require("node:test");
const assert = require("node:assert/strict");

const I = require("../../src/axiomation/viewer/page/src/inbox.js");
const O = require("../../src/axiomation/viewer/page/src/open.js");
const P = require("../../src/axiomation/viewer/page/src/palette.js");

const RULE = "a ruling: a review's, or yours (`axiomation rule`)";
const unsupported = (handle, next, speaks = []) => ({ kind: "unsupported", block: handle, handle, route: "ruling", next, reading: "held inconclusive", support: { speaks, relied: [] }, anchor: { words: handle + " holds" } });
const REPORT = {
  state: "s1abcdef99",
  counts: {},
  to_judge: { items: [{ kind: "disputed", block: "b1", handle: "b1", aspect: "true", clears: "your judgment (`axiomation judge`)" }], lines: ["b1  true  disputed"] },
  awaiting_review: {
    items: [
      { kind: "negative", status: "proposed", block: "w1", handle: "w1", proposed: "ruling", proposal: { ruling: "known", author: "person", reason: "a textbook fact" }, waits: "the support search reads it first", clears: "the next argument review" },
      { kind: "finding", block: "w2", handle: "w2", status: "severity 2", cause: "a gap", finding: "f1", proposal: { status: "fixed", note: "reworded", author: "claude-code 2.1 claude-opus-5-5" }, clears: "a review of its kind" },
    ],
    lines: ["w1  proposed ruling known by person", "finding f1"],
  },
  unsupported_by_a_ruling: {
    items: [
      unsupported("b2", RULE, [{ ruling: "owed", author: "claude-code 2.1 claude-opus-5-5", reason: "a referee would ask" }]),
      unsupported("b3", RULE),
      unsupported("b4", "your ruling `known`, which needs no verified `true`"),
    ],
    lines: ["b2", "b3", "b4"],
  },
};

test("the items in the sections' order, each with its line and section; the decisions counted without what waits", () => {
  const all = I.items(REPORT);
  assert.deepEqual(all.map((i) => [i.inbox, i.handle]), [["to_judge", "b1"], ["awaiting_review", "w1"], ["awaiting_review", "w2"], ["unsupported_by_a_ruling", "b2"], ["unsupported_by_a_ruling", "b3"], ["unsupported_by_a_ruling", "b4"]]);
  assert.equal(I.decisions(all), 4);
  assert.deepEqual(I.items(null), []);
});

test("the head counts the decisions and what waits apart; a section per decision, open; what waits folded", () => {
  const all = I.items(REPORT);
  const html = I.html(REPORT, all, O, P, null);
  assert.ok(html.includes("4 decisions</span> · 2 waiting on a review"));
  assert.ok(html.includes("at state s1abcdef."));
  const open = (key, h = html) => new RegExp('data-fold="' + key + '" open>').test(h);
  assert.ok(open("d:to_judge") && open("d:unsupported_by_a_ruling"));
  assert.ok(!open("s:waiting"));
  assert.ok(html.includes(O.escape(I.SECTIONS.unsupported_by_a_ruling.title)));
  // decisions come before what waits
  assert.ok(html.indexOf('data-fold="d:unsupported_by_a_ruling"') < html.indexOf('data-fold="s:waiting"'));
  // folds kept: a decision shut, what waits opened; or holding the item clicked
  const kept = I.html(REPORT, all, O, P, null, { opened: new Set(["s:waiting"]), shut: new Set(["d:to_judge"]) });
  assert.ok(open("s:waiting", kept) && !open("d:to_judge", kept));
  assert.ok(open("s:waiting", I.html(REPORT, all, O, P, 1)));
  assert.ok(open("d:to_judge", I.html(REPORT, all, O, P, 0, { shut: new Set(["d:to_judge"]) })));
});

test("a decision is a card: its label line, its words, what the records say and its next step", () => {
  const all = I.items(REPORT);
  const html = I.html(REPORT, all, O, P, null);
  const card = (i) => html.slice(html.indexOf('data-inbox="' + i + '"'), html.indexOf("</li>", html.indexOf('data-inbox="' + i + '"')));
  assert.ok(card(3).includes(">unsupported</span>") && card(3).includes(">truth held inconclusive</span>"));
  assert.ok(card(3).includes("b2 holds"));
  assert.ok(card(3).includes('<span class="row-k">Ruled owed</span><span class="row-v">a referee would ask <span class="i-by">by '));
  assert.ok(card(4).includes('<span class="row-k">Ruled</span><span class="row-v">unruled</span>'));
  assert.ok(card(3).includes('<span class="row-k">Next</span><span class="row-v">A ruling: a review\'s, or yours (<code>axiomation rule</code>)'));
  assert.ok(card(5).includes("Your ruling <code>known</code>"));
  assert.ok(card(0).includes(">readings differ</span>") && card(0).includes(">truth</span>"));
  assert.ok(card(3).includes('<span class="row-k">At</span><span class="row-v"><span class="e-name">b2</span>'));
});

test("what waits says what was proposed, by whom, and what it waits for", () => {
  const all = I.items(REPORT);
  const html = I.html(REPORT, all, O, P, null);
  assert.ok(html.includes("Your proposed rulings") && html.includes("Findings with a proposal"));
  assert.ok(html.includes('<span class="row-k">Proposed known</span><span class="row-v">a textbook fact <span class="i-by">by you</span>'));
  assert.ok(html.includes('<span class="row-k">Waits</span><span class="row-v">The support search reads it first'));
  assert.ok(html.includes('<span class="row-k">Proposed fixed</span><span class="row-v">reworded') && html.includes('title="claude-code 2.1 claude-opus-5-5"'));
});

test("the lapsed readings: one line linking to Open, none where none lapsed", () => {
  const all = I.items(REPORT);
  const html = I.html(REPORT, all, O, P, null, {}, { lapsed: 32 });
  assert.ok(/data-open-kind="lapsed"[^]*Lapsed judgments[^]*in Open ›[^]*>32</.test(html));
  assert.ok(!I.html(REPORT, all, O, P, null).includes("data-open-kind"));
});

test("empty, loading and failed reads say so", () => {
  const empty = { state: "s1", counts: {} };
  assert.ok(I.html(empty, [], O, P, null).includes("Nothing for you to decide"));
  assert.ok(I.html(null, [], O, P, null).includes("Reading the inbox"));
  assert.ok(I.html({ error: "boom" }, [], O, P, null).includes("could not be read: boom"));
});
