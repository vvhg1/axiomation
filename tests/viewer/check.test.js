// `node --test tests/viewer/`: the verdict: `check --json` as
// the header's chip and the check tab: a row per gate that fails or
// warns, each a link to its items in the open tab; the passing gates in
// one line; the audit as counts, its lists folded.
const test = require("node:test");
const assert = require("node:assert/strict");

const K = require("../../src/axiomation/viewer/page/src/check.js");
const O = require("../../src/axiomation/viewer/page/src/open.js");
const P = require("../../src/axiomation/viewer/page/src/palette.js");
const help = require("../../src/axiomation/viewer/page/src/help.js");

const item = (gate, kind, block, line, extra = {}) => ({ gate, kind, block, handle: block, line, anchor: { kind: "block" }, anchors: [{ kind: "block" }], status: kind, cause: null, clears: "", since: null, ...extra });
const gate = (name, mode, outcome, items) => ({ gate: name, mode, outcome, count: items.length, items });
const VERDICT = {
  state: "s1",
  passes: false,
  gates: [
    gate("unsupported-claims", "block", "fail", [
      item("unsupported-claims", "unsupported", "b1", "b1  ⊢ unsupported · \"x\"", { cause: { standing: "unsupported", why: "nothing in the paper supports it" } }),
      item("unsupported-claims", "circular", "b2", "b2  ⊢ justified · \"y\"", { cause: { standing: "justified", why: "its support runs back to it" } }),
    ]),
    gate("defects", "block", "fail", [item("defects", "contradicted", "b3", "b3  true · \"z\"  held contradicted", { cause: { judgment: "j1", author: "model-a" } })]),
    gate("unreviewed", "warn", "warn", [
      item("unreviewed", "inconclusive", "b4", "b4  cited · w1  held inconclusive", { cause: { judgment: "j2", unread: [{ key: "w1", read_status: "absent" }] } }),
      item("unreviewed", "stale", "b4", "b4  ⟦stale since s0: asked⟧", { cause: [{ why: "asked", via: "b4 → uses b5" }] }),
    ]),
    gate("dangling-references", "block", "pass", []),
    gate("duplicate-labels", "block", "pass", []),
    gate("unused-premises", "off", "off", []),
    gate("undeclared-assumptions", "warn", "pass", []),
  ],
};

test("the chip: the gates that fail with their counts, those that warn after them, the rest in its title", () => {
  const chip = K.chip(VERDICT);
  assert.equal(chip.tone, "fail");
  assert.equal(chip.text, "✗ fails on 2 gates · 1 warn");
  assert.ok(chip.title.includes("fails on unsupported-claims 2, defects 1; warns: unreviewed 2") && chip.title.includes("off: unused-premises"));
  const passing = { ...VERDICT, passes: true, gates: VERDICT.gates.slice(2) };
  assert.deepEqual([K.chip(passing).tone, K.chip(passing).text], ["warn", "✓ passes · 1 warn"]);
  const clean = { ...VERDICT, passes: true, gates: VERDICT.gates.slice(3) };
  assert.deepEqual([K.chip(clean).tone, K.chip(clean).text], ["pass", "✓ passes"]);
  const refused = K.chip({ error: "axiomation.toml: [gates] names no gate 'x'" });
  assert.equal(refused.tone, "error");
  assert.ok(refused.title.includes("names no gate"));
  assert.deepEqual([K.chip(null).tone, K.chip(null).text], ["error", "check …"]);
  assert.ok(K.html(null, O, P, null).includes("Reading the gates"));
  // the tab's head takes the chip's tone: a pass with a warning is yellow
  assert.ok(K.html(passing, O, P, null).includes('<span class="k-v check-warn">✓ passes</span> · 1 warn · 3 pass · 1 off'));
  assert.equal(K.failing(VERDICT), 3);
  assert.equal(K.failing({ error: "x" }), null);
});

test("a gate that fails or warns is a row linking to its items in Open, never listing them; the sections fold as Open's families", () => {
  const html = K.html(VERDICT, O, P, null);
  assert.ok(html.includes('<span class="k-v check-fail">✗ fails on 2 gates</span> · 1 warn · 3 pass · 1 off'));
  assert.ok(html.includes("at state s1"));
  const at = (s) => html.indexOf(s);
  assert.ok(at('data-fold="s:fail" open') < at('data-gate-link="unsupported-claims"') && at('data-gate-link="defects"') < at('data-fold="s:warn" open') && at('data-fold="s:warn"') < at('data-gate-link="unreviewed"'));
  assert.ok(html.includes("3 items · 2 gates"), "a section's head counts its items and gates");
  assert.ok(html.includes('<li class="k-gate check-fail k-linked" data-gate-link="unsupported-claims"'));
  assert.ok(html.includes('<span class="k-title">Unsupported claims</span><span class="k-go">in Open ›</span><span class="count">2</span>'));
  assert.ok(html.includes(K.GATE_SENTENCES["unsupported-claims"]));
  // a split where the items are of more than one kind
  assert.ok(html.includes("1 standing unsupported · 1 circles"));
  assert.ok(html.includes("1 inconclusive · 1 stale"));
  assert.ok(!html.includes("data-check="), "no gate's items here: Open lists them");
  // the passing and the off gates folded, rows with no link
  assert.ok(html.includes('data-fold="s:pass">') && html.includes('<li class="k-gate check-pass" title="dangling-references'));
  assert.ok(html.includes('<span class="k-title">Unused premises</span><span class="count">off</span>'));
  assert.ok(!html.includes('data-gate-link="dangling-references"'));
  assert.ok(K.html(VERDICT, O, P, null, { opened: new Set(["s:pass"]) }).includes('data-fold="s:pass" open'));
  assert.ok(K.html(VERDICT, O, P, null, { shut: new Set(["s:fail"]) }).includes('data-fold="s:fail">'));
  assert.ok(!html.includes(">The audit<"), "nothing cleared, no audit");
  assert.ok(!html.includes('id="check-by"'), "no grouping: the open tab groups");
  assert.ok(K.html(VERDICT, O, P, null, {}, { ahead: true }).includes("files changed since this state"));
  assert.ok(K.html({ error: "refused" }, O, P, null).includes("refused"));
});

test("a pass that left works unlooked says so; a gate that counts claims elsewhere says where", () => {
  const verdict = { ...VERDICT, gates: VERDICT.gates.map((g, i) => (i === 0 ? { ...g, to_defects: 2 } : i === 3 ? { ...g, gate: "cited-publications", unlooked: 62 } : g)) };
  const html = K.html(verdict, O, P, null);
  assert.ok(html.includes("62 cited works never looked up"));
  assert.ok(html.includes("counted elsewhere: 2 in defects"));
});

test("an open kind's tone is the worst outcome of the gates that list an item of it, by its cause", () => {
  const tones = K.kindTones(VERDICT);
  assert.deepEqual(tones.unsupported, { tone: "fail", gates: ["unsupported-claims"] });
  assert.deepEqual(tones.circle, { tone: "fail", gates: ["unsupported-claims"] });
  assert.deepEqual(tones.negative, { tone: "fail", gates: ["defects"] });
  assert.deepEqual(tones.stale, { tone: "warn", gates: ["unreviewed"] });
  assert.equal(tones.finding, undefined);
  assert.equal(K.kindTones(null), null);
  const lapsed = { ...VERDICT, gates: [gate("defects", "block", "fail", [item("defects", "not-shown", "b3", "b3  lapsed", { lapsed: true })])] };
  assert.deepEqual(K.kindTones(lapsed), { lapsed: { tone: "fail", gates: ["defects"] } });
  assert.equal(K.kindTones({ error: "no" }), null);
});

test("`reviews` shows only once an outside review was received: its row, the passing gates' count, the chip's off list", () => {
  const verdict = (received, outcome, mode = "block") => ({ ...VERDICT, gates: [gate("defects", "block", "fail", []), { ...gate("reviews", mode, outcome, []), received }, gate("dangling-references", "block", "pass", [])] });
  const none = K.html(verdict(0, "pass"), O, P, null);
  assert.ok(none.includes("1 pass") && !none.includes("reviews") && !none.includes("outside reviewer"));
  const received = K.html(verdict(1, "pass"), O, P, null);
  assert.ok(received.includes("2 pass") && received.includes("reviews"));
  assert.ok(!K.chip(verdict(0, "off", "off")).title.includes("reviews"));
  assert.ok(K.chip(verdict(1, "off", "off")).title.includes("off: reviews"));
});

test("a finding's tone is its own: the gates that list it by its id, none where none does", () => {
  const found = (id, severity) => item("defects", "finding", "b1", "b1  " + id, { finding: id, severity });
  const verdict = { ...VERDICT, gates: [gate("defects", "block", "fail", [found("f3", 3)]), gate("reviews", "block", "warn", [found("r1", 1)])] };
  assert.deepEqual(K.findingTones(verdict), { f3: "fail", r1: "warn" });
  assert.equal(K.findingTones(null), null);
});

test("a cause reads as words, never as JSON: a gap's why, the reading a defect stands on, what could not be read", () => {
  const [unsupported, circular, contradicted, inconclusive, stale] = K.items(VERDICT);
  assert.equal(K.causeWords(unsupported, O), "nothing in the paper supports it");
  assert.equal(K.causeWords(circular, O), "its support runs back to it");
  assert.equal(K.causeWords(contradicted, O), "judgment j1 by model-a");
  assert.equal(K.causeWords({ ...contradicted, lapsed: true }, O), "lapsed, unresolved: judgment j1 by model-a");
  assert.equal(K.causeWords(inconclusive, O), "judgment j2; could not read w1 (absent)");
  assert.equal(K.causeWords(stale, O), "asked (via b4 → uses b5)");
  assert.equal(K.causeWords({ cause: { work: "w9", doi: "10.1/x" } }, O), "work w9 (doi 10.1/x)");
  assert.equal(O.causeWords({ cause: { completeness: "sketch", by: ["proof:a", "proof:b"] } }), "completeness sketch; by proof:a, proof:b");
  assert.ok(!K.html(VERDICT, O, P, null).includes("{&quot;"));
});

test("an item's dot: an open kind's tone where its cause is one, a verdict's, pending's grey for what no one read", () => {
  const [unsupported, circular, contradicted, inconclusive, stale] = K.items(VERDICT);
  assert.equal(K.tone(unsupported, P), P.open("unsupported").colour);
  assert.equal(K.tone(stale, P), P.open("stale").colour);
  assert.equal(K.tone(circular, P), P.standing("unsupported").colour);
  assert.equal(K.tone(contradicted, P), P.VERDICT.contradicted.colour);
  assert.equal(K.tone(inconclusive, P), P.VERDICT.inconclusive.colour);
  assert.equal(K.tone({ kind: "unjudged" }, P), P.open("pending").colour);
  assert.equal(K.tone({ kind: "finding", severity: 3 }, P), P.severity(3).colour);
});

test("every gate, cause tone and chip tone has its words in the help", () => {
  assert.deepEqual(Object.keys(help.GATE_TEXT), K.GATES);
  assert.deepEqual(Object.keys(help.CAUSE_TEXT).sort(), Object.keys(P.CAUSE_TONE).sort());
  const terms = help.sections(P, { open: O.KINDS, lenses: [] }).flatMap((s) => s.items.map((i) => s.id + ": " + i.term));
  for (const c of Object.values(P.CHECK)) assert.ok(terms.includes("check: Verdict: " + c.label), c.label);
  assert.ok(terms.includes("overview: The verdict"));
});

test("the audit: the claims rulings cleared, counted by ruling, each ruling's list folded, each claim with who ruled and why", () => {
  const ruled = (block, ruling, by_person = false) => item("unsupported-claims", "unsupported", block, block + '  "z" unsupported  no argument owed: ruled ' + ruling, {
    cause: { standing: "unsupported", ruling: { speaks: [{ ruling, author: by_person ? "person" : "claude-code 2.1 model-a", reason: "a textbook fact", id: "r1" }], by_person } },
  });
  const cleared = [ruled("b9", "known"), ruled("b10", "immediate"), ruled("b11", "known", true)];
  const verdict = { ...VERDICT, gates: VERDICT.gates.map((g, i) => (i === 0 ? { ...g, cleared } : g)) };
  const all = K.items(verdict);
  assert.deepEqual(all.slice(-3), cleared, "after every gate's items, so a click's index finds them");
  const html = K.html(verdict, O, P, null);
  assert.ok(html.indexOf(">The audit<") > html.indexOf('data-fold="s:pass"'));
  assert.ok(html.includes('data-fold="s:audit">'), "folded until opened");
  assert.ok(html.includes("3 cleared"));
  assert.ok(html.includes('<span class="e-kindtitle">No argument owed</span><span class="count">3 · 2 by agents alone</span>'));
  // by ruling, most first, each folded until opened
  assert.ok(html.indexOf('data-fold="cleared:known"') < html.indexOf('data-fold="cleared:immediate"'));
  assert.ok(html.includes('<details data-fold="cleared:known">'));
  assert.ok(html.includes("known, read by " + O.author("claude-code 2.1 model-a") + ": a textbook fact"));
  // each claim keeps its index in `items`
  const first = all.length - 3;
  assert.ok(html.includes('data-check="' + first + '"') && html.includes('data-check="' + (first + 2) + '"'));
  const holding = K.html(verdict, O, P, first + 2);
  assert.ok(holding.includes('data-fold="s:audit" open') && holding.includes('data-fold="a:cleared" open') && holding.includes('data-fold="cleared:known" open'), "open where it holds the item clicked");
  assert.ok(K.html(verdict, O, P, null, { opened: new Set(["cleared:immediate"]) }).includes('data-fold="cleared:immediate" open'));
});

test("the audit: the claims the author leaves unargued, then those established under a conjecture, then the stale marks cleared, as counts", () => {
  const left = item("unsupported-claims", "unsupported", "b8", 'b8  "w" unsupported  left unargued by the author', { cause: { standing: "unsupported", ruling: { speaks: [{ ruling: "left-unargued", author: "person", reason: "a routine step", id: "r2" }], by_person: true } } });
  const under = (by_person) => ({ kind: "conditional", line: 'x  "y" under k "K", by ' + (by_person ? "a person" : "an agent alone"), anchors: [{ kind: "part" }], claim: "y", under: [{ at: "k", claim: "K", by_person }], by_person });
  const flagged = [under(true), under(false)];
  const marks = [{ by_person: false }, { by_person: true }];
  const verdict = { ...VERDICT, gates: VERDICT.gates.map((g, i) => (i === 0 ? { ...g, left: [left] } : g)), conditional: flagged, reread: marks };
  const all = K.items(verdict);
  assert.deepEqual(all.slice(-3), [left, ...flagged], "after those cleared, so a click's index finds them");
  const html = K.html(verdict, O, P, null);
  assert.ok(html.indexOf(">Left unargued<") < html.indexOf(">Established under a conjecture<"));
  assert.ok(html.includes("left-unargued by person: a routine step"));
  assert.ok(html.includes("2 · 1 by an agent's flag alone") && html.includes("under k (an agent's flag)"));
  assert.ok(html.includes("2 by rereads (1 by agents alone)"));
  assert.ok(html.includes("3 cleared · 2 stale marks"));
  assert.ok(K.html(verdict, O, P, all.length - 1).includes('data-fold="a:conditional" open'), "open where it holds the item clicked");
  assert.ok(K.html(verdict, O, P, all.length - 3).includes('data-fold="a:left" open'));
});
