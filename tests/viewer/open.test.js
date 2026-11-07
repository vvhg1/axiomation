// `node --test tests/viewer/`: the open view: the report's items
// in its kinds' order, grouped, filtered and walked.
const test = require("node:test");
const assert = require("node:assert/strict");

const O = require("../../src/axiomation/viewer/page/src/open.js");
const P = require("../../src/axiomation/viewer/page/src/palette.js");

const item = (kind, block, section, status, extra = {}) => ({ kind, block, handle: block, section, top_section: section, roles: block ? "claim" : null, status, anchor: { kind: "block" }, cause: null, clears: "", since: null, ...extra });
const REPORT = {
  state: "s1",
  counts: {},
  stale: { total: 1, items: [item("stale", "b2", "§2", "stale")], lines: ["b2  ⟦stale since s1: why⟧"], more: 0 },
  unsupported: { total: 2, items: [item("unsupported", "b1", "§1", "unsupported", { reading: "held inconclusive" }), item("unsupported", "b3", "§2", "unsupported", { reading: "held verified" })], lines: ["b1  ⊢ unsupported", "b3  ⊢ unsupported"], more: 0 },
  uses_unsupported: { total: 1, items: [item("uses-unsupported", "b2", "§2", "uses unsupported")], lines: ["b2  uses unsupported b1"], more: 0 },
  pending: { total: 1, items: [item("pending", null, null, "unanswered")], lines: ["question k left unanswered"], more: 0 },
};

test("the items in the kinds' order, each with its line", () => {
  const all = O.items(REPORT);
  assert.deepEqual(all.map((i) => i.kind), ["unsupported", "unsupported", "uses-unsupported", "stale", "pending"]);
  assert.deepEqual(all.map((i) => i.line), ["b1  ⊢ unsupported", "b3  ⊢ unsupported", "b2  uses unsupported b1", "b2  ⟦stale since s1: why⟧", "question k left unanswered"]);
  assert.equal(O.counts(all).unsupported, 2);
  assert.equal(O.counts(all).finding, 0);
});

test("one group per kind, in the kinds' order; inside each, its items by section, role, reading or block, the paper's own last", () => {
  const all = O.items(REPORT);
  assert.deepEqual(O.groups(all).map((g) => [g.key, g.members]), [["unsupported", [0, 1]], ["uses-unsupported", [2]], ["stale", [3]], ["pending", [4]]]);
  // unsupported claims and lapsed judgments are read by block where nothing else groups them
  assert.deepEqual(O.groups(all).map((g) => g.parts), [[{ label: "b1", members: [0], byBlock: true }, { label: "b3", members: [1], byBlock: true }], [{ label: null, members: [2] }], [{ label: null, members: [3] }], [{ label: null, members: [4] }]]);
  const parts = (by) => O.groups(all, by).map((g) => g.parts.map((p) => [p.label, p.members]));
  assert.deepEqual(parts("section")[0], [["§1", [0]], ["§2", [1]]]);
  assert.deepEqual(parts("section")[3], [["(the paper)", [4]]]);
  assert.deepEqual(parts("reading")[0], [["held inconclusive", [0]], ["held verified", [1]]]);
  // an item that is no claim groups by its status
  assert.deepEqual(parts("reading")[2], [["stale", [3]]]);
  assert.deepEqual(parts("role")[0], [["claim", [0, 1]]]);
  assert.deepEqual(parts("block")[0], [["b1", [0]], ["b3", [1]]]);
  const mixed = O.subgroups([item("unsupported", null, null, "x"), item("unsupported", "b1", null, "x")], [0, 1], "section");
  assert.deepEqual(mixed.map((p) => p.label), ["(before any section)", "(the paper)"]);
  assert.deepEqual(O.order(O.groups(all, "reading")), [0, 1, 2, 3, 4]);
  // the families, in their order, each its kinds; the walk in the order they show
  assert.deepEqual(O.families(all).map((f) => [f.key, f.kinds.map((k) => k.key), f.members]), [["structure", ["unsupported", "uses-unsupported"], [0, 1, 2]], ["again", ["stale", "pending"], [3, 4]]]);
  assert.deepEqual(O.order(O.families(all)), [0, 1, 2, 3, 4]);
  for (const kind of O.KINDS) assert.equal(O.FAMILIES.filter((f) => f.kinds.includes(kind)).length, 1, kind);
});

test("the walk goes round the items shown, from the first or the last", () => {
  assert.equal(O.walk([3, 0, 1], null, 1), 3);
  assert.equal(O.walk([3, 0, 1], null, -1), 1);
  assert.equal(O.walk([3, 0, 1], 1, 1), 3);
  assert.equal(O.walk([3, 0, 1], 3, -1), 1);
  assert.equal(O.walk([], null, 1), null);
});

test("every kind has a colour and every item a row: its block, and a paper's item its line", () => {
  for (const kind of O.KINDS) assert.ok(P.OPEN[kind], kind);
  assert.deepEqual(Object.keys(P.OPEN), O.KINDS);
  const all = O.items(REPORT);
  const html = O.html(all, O.families(all), P, 0, "none");
  all.forEach((_, i) => assert.ok(html.includes('data-item="' + i + '"'), i));
  assert.ok(html.includes('class="open-item e-row current" data-item="0"'));
  assert.ok(html.includes('<span class="e-name">b1</span>'));
  // an item of the paper's own, no block: its line in place of a passage
  assert.ok(html.includes('<div class="e-words">question k left unanswered</div>'));
  // the head: a label, the count, one sentence
  assert.ok(html.includes('<div class="eyebrow">Open items</div><p class="e-counts"><span class="e-n" title="every item open at this state">○ 5 open</span></p>'));
  assert.ok(!html.includes("undefined"));
});

test("a cause reads as words: a mark's causes, a list of handles, a judgment's cause", () => {
  assert.equal(O.causeWords({ cause: [{ why: "asked", via: "a → uses b" }, { why: "moved" }] }), "asked (via a → uses b); moved");
  assert.equal(O.causeWords({ cause: ["thm:1", "lem:2"] }), "thm:1; lem:2");
  assert.equal(O.causeWords({ cause: { kind: "text", why: "its text changed" } }), "its text changed");
  assert.equal(O.causeWords({ cause: null }), "");
});

test("an open finding's dot is its severity's tone; a lapsed one is listed under lapsed by its line", () => {
  const report = {
    lapsed: { items: [{ kind: "lapsed", judgments: ["j1"] }, { kind: "lapsed", finding: "f2", severity: 1 }], lines: ["true · \"x\"  lapsed at s2: text", "finding f2  severity 1  well-posed  lapsed  \"p\"  (by a)"] },
    finding: { items: [{ kind: "finding", finding: "f1", severity: 3 }, { kind: "finding", finding: "f3", severity: 1 }], lines: ["finding f1 …", "finding f3 …"] },
  };
  const all = O.items(report);
  assert.deepEqual(all.map((i) => O.tone(i, P)), [P.open("lapsed").colour, P.open("lapsed").colour, P.severity(3).colour, P.severity(1).colour]);
  const html = O.html(all, O.families(all), P, null, "none");
  assert.ok(!html.includes("undefined"));
  assert.ok(all[1].line.startsWith("finding f2") && !all[0].line.startsWith("finding"));
});

test("the kept graph's four faults are listed after the findings, and the query's notes above the list", () => {
  const fault = (kind, block) => ({ ...item(kind, block, "§1", kind), anchors: [{ kind: "block", node: "n" + block }] });
  const report = {
    finding: { items: [{ kind: "finding", finding: "f1", severity: 2 }], lines: ["finding f1 …"] },
    undeclared_assumption: { items: [fault("undeclared-assumption", "b1")], lines: ['b1 "x": its J b2 rests on b3, which its statement does not'] },
    dangling_reference: { items: [fault("dangling-reference", "b2")], lines: ["b2  \\ref{lem:x}: no block holds lem:x (defined nowhere)"] },
    duplicate_label: { items: [fault("duplicate-label", "b3")], lines: ["eq:1 held by b3, b4: each reference to it reads b3"] },
    unused_premise: { items: [fault("unused-premise", "b4")], lines: ["cond:u  condition (b4): no uses edge reaches it"] },
    notes: ["the labels and environments of state s1 were not read: no such version"],
  };
  const all = O.items(report);
  assert.deepEqual(all.map((i) => i.kind), ["finding", "dangling-reference", "duplicate-label", "unused-premise", "undeclared-assumption"]);
  assert.equal(O.counts(all)["duplicate-label"], 1);
  assert.equal(O.tone(all[1], P), P.open("dangling-reference").colour);
  const html = O.html(all, O.families(all), P, null, "none", new Set(), null, report.notes);
  assert.ok(html.indexOf('class="open-note"') < html.indexOf("open-group"));
  assert.ok(html.includes("were not read: no such version"));
  for (const k of ["dangling-reference", "duplicate-label", "unused-premise", "undeclared-assumption"]) assert.ok(html.includes('data-kind="' + k + '"'), k);
  assert.ok(!O.html(all, O.families(all), P, null, "none").includes("open-note"));
});

test("each family folded unless opened or holding the current item; it and each kind in the tone of the gates that list its items", () => {
  const all = O.items(REPORT);
  const opened = (html) => [...html.matchAll(/data-family="([^"]+)" open>/g)].map((m) => m[1]);
  assert.deepEqual(opened(O.html(all, O.families(all), P, null, "none")), []);
  assert.deepEqual(opened(O.html(all, O.families(all), P, 3, "none")), ["again"]);
  assert.deepEqual(opened(O.html(all, O.families(all), P, null, "none", new Set(["structure"]))), ["structure"]);
  const tones = { unsupported: { tone: "fail", gates: ["unsupported-claims"] }, stale: { tone: "warn", gates: ["unreviewed"] } };
  const html = O.html(all, O.families(all), P, null, "none", new Set(), tones);
  // a family's head: its title, its count, the worst of its kinds' tones; its sentence under it
  assert.ok(html.includes('class="card-sec open-group check-fail" data-family="structure"'));
  assert.ok(html.includes('<span class="title">Structure</span><span class="count e-fail" title="unsupported-claims → fail">3 · fails a gate</span>'));
  assert.ok(html.includes('<p class="e-sentence">' + O.FAMILIES[0].sentence + "</p>"));
  assert.ok(html.includes('class="card-sec open-group check-warn" data-family="again"'));
  // each kind inside, its own count and tone
  assert.ok(html.includes('class="e-kindpart check-fail" data-kind="unsupported"'));
  assert.ok(html.includes('title="unsupported-claims → fail">2 · fails a gate</span>'));
  assert.ok(html.includes('class="e-kindpart check-warn" data-kind="stale"'));
  assert.ok(html.includes("1 · warns"));
  assert.ok(html.includes('class="e-kindpart check-none" data-kind="pending"'));
  assert.ok(html.includes("1 · in no gate"));
  // while the verdict is read, no gate is named
  assert.ok(!O.html(all, O.families(all), P, null, "none").includes("in no gate"));
  // grouped, each part a line with its count above its items
  const grouped = O.html(all, O.families(all, "reading"), P, null, "reading");
  assert.ok(grouped.indexOf('<li class="open-sub">held inconclusive <span class="meta">1</span></li>') < grouped.indexOf('data-item="0"'));
  assert.ok(grouped.includes("truth: held inconclusive"));
});

test("a soundness item's cause is not repeated where its line says each field", () => {
  const row = (line, cause) => O.row({ kind: "duplicate-label", line, cause, clears: "" }, 0, P);
  assert.ok(!row("eq:1 held by b3, b4: each reference to it reads b3", { label: "eq:1", holders: ["b3", "b4"] }).includes("<b>cause</b>"));
  assert.ok(!row("cond:u  condition (cond:u): no uses edge reaches it", { label: "cond:u", environment: "condition" }).includes("<b>cause</b>"));
  // an undeclared assumption's cause holds its premises and path as
  // records: its why, in the line's words, keeps them out of the row
  const undeclared = {
    cause: "left-out", why: "a premise it leaves out", claim: "x is small",
    premises: [{ via: "proof:t", premise: "ass:a", conjectured: false, through: null, narrows: null, discharged_by: [] }],
    lines: [], path: [{ source: "proof:t", hop: "uses", target: "ass:a", quote: "since x" }],
  };
  const line = 't "x is small": its J proof:t rests on ass:a, which its statement does not: a premise it leaves out\n  proof:t → uses ass:a · "since x"';
  assert.ok(!row(line, undeclared).includes("<b>cause</b>"));
  assert.ok(!row(line, undeclared).includes("[object Object]"));
  // a field the line does not say is shown
  assert.ok(row("b1  something", { judgment: "j1" }).includes("<b>cause</b> judgment j1"));
});

test("an unanswered suggestion is listed after the findings, in its own tone, and marked in the gutter", () => {
  const G = require("../../src/axiomation/viewer/page/src/gutter.js");
  const report = {
    ...REPORT,
    finding: { total: 1, items: [{ ...item("finding", "b1", "§1", "severity 2"), severity: 2 }], lines: ["finding f1  open"], more: 0 },
    suggestion: { total: 1, items: [{ ...item("suggestion", "b3", "§2", "unanswered"), cause: "the standard bound" }], lines: ["suggestion s1  open  at b3  work:doi:10.1/x"], more: 0 },
  };
  const all = O.items(report);
  const kinds = all.map((i) => i.kind);
  assert.equal(kinds.indexOf("suggestion"), kinds.indexOf("finding") + 1);
  assert.notEqual(P.open("suggestion").colour, P.open("finding").colour, "a pointer, never a problem's tone");
  const marks = G.openMarks(all);
  // b3 also holds the report's unsupported claim, marked before it
  assert.deepEqual(marks.get("b3").map((m) => m.kind), ["unsupported", "suggestion"]);
});

test("a lapsed judgment is read under its block, what clears a kind said once, and a gate's items alone", () => {
  const lapsed = (block, id, why, state) => ({ ...item("lapsed", block, "§1", "lapsed"), anchor: { kind: "part", words: "w " + id }, judgments: [id], cause: { kind: "text", state, why }, clears: "`axiomation reask` " + id + ", then a judgment or `axiomation reaffirm`" });
  const report = {
    lapsed: {
      items: [lapsed("b1", "j1", "b1's text changed (changed)", "abcdef0123456789"), lapsed("b2", "j2", "b1's text changed (changed)", "abcdef0123456789"), lapsed("b1", "j3", "the edge b1 → justifies b3 is gone", "s2"), { ...lapsed(null, "j4", "the claim ended", "s3"), handle: null }],
      lines: ["l1", "l2", "l3", "l4"],
    },
  };
  const all = O.items(report);
  const [group] = O.groups(all);
  assert.deepEqual(group.parts.map((p) => [p.label, p.members]), [["b1", [0, 2]], ["b2", [1]], ["(no longer in the paper)", [3]]]);
  const html = O.html(all, O.families(all), P, null, "none", new Set(["again"]));
  // each block named once, its readings under it without the name again;
  // a block with one reading named on its row
  assert.ok(html.includes('<summary class="open-sub e-block"><span class="chev"></span>b1 <span class="meta">2</span> <span class="e-where">§1</span></summary>'));
  assert.ok(!html.includes('<span class="e-name">b1</span>'));
  // a row that names its block names its section after it
  assert.ok(html.includes('<span class="e-name">b2</span> <span class="e-where">§1</span>') && !html.includes('e-block">b2'));
  // a block's own text: "its text changed", the bare class not repeated
  assert.ok(html.includes('its text changed <span class="e-at">at abcdef01</span>'));
  assert.ok(html.includes("b1's text changed <span"));
  // what clears each, its id aside, under the head once
  assert.ok(html.includes('<span class="row-k">Clears</span> <code>axiomation reask</code> its id, then a judgment or <code>axiomation reaffirm</code>'));
  assert.ok(!html.includes("clears: "));
  // grouped otherwise, each row names its block
  assert.ok(O.html(all, O.families(all, "section"), P, null, "section").includes('<span class="e-name">b1</span>'));
  // one gate's items, by their index in the check's, by kind: a fold
  // each with its dot, label, count and the check's sentence for it
  const gate = { gate: "unreviewed", outcome: "warn", count: 3, items: [{ ...item("unjudged", "b1", "§1", "unjudged"), anchor: { kind: "part", words: "x" }, aspect: "valid", target: "t1" }, { ...item("lapsed", "b2", "§1", "lapsed"), anchor: { kind: "part", words: "y" } }, { ...item("unjudged", "b3", "§1", "unjudged"), anchor: { kind: "part", words: "z" }, aspect: "true" }] };
  const one = O.gateHtml(gate, 5, P, 6, { toneOf: () => "#123456", sentences: { unjudged: "Never read yet." } });
  assert.ok(one.includes("<h3>Unreviewed</h3>") && one.includes("3 items · the gate warns") && one.includes("data-gate-all"));
  assert.ok(one.indexOf('data-fold="g:unreviewed:unjudged"') < one.indexOf('data-fold="g:unreviewed:lapsed"'), "the most first");
  assert.ok(one.includes('<i class="h-dot" style="background:#123456"></i><span class="e-kindtitle">Unjudged</span><span class="count">2</span>'));
  assert.ok(one.includes('<p class="e-sentence">Never read yet.</p>'));
  const lapsedLabel = P.open("lapsed").label;
  assert.ok(one.includes(">" + lapsedLabel.charAt(0).toUpperCase() + lapsedLabel.slice(1) + "</span>"));
  // folded but for the kind that holds the item clicked, or one opened
  assert.ok(one.includes('data-fold="g:unreviewed:lapsed" open') && !one.includes('data-fold="g:unreviewed:unjudged" open'));
  assert.ok(O.gateHtml(gate, 5, P, null, { folds: { opened: new Set(["g:unreviewed:unjudged"]) } }).includes('data-fold="g:unreviewed:unjudged" open'));
  assert.ok(one.includes('data-check="5"') && one.includes('class="open-item e-row current" data-check="6"') && one.includes('data-check="7"'));
  // a review's reason names its aspect; a validity its argued claim
  assert.ok(one.includes("validity: no reading yet") && one.includes("truth: no reading yet"));
  assert.ok(one.includes('argues for</span> <span class="e-ref">t1</span>'));
});

test("a row's passage: a part in its line's context, a finding by its problem, the blocks an update carried, an unused premise none", () => {
  const line = 'b1  ⊢ unsupported · "[This essay] presents the account [: in short.]"  ruled owed';
  assert.equal(O.passage({ kind: "unsupported", block: "b1", line, anchor: { kind: "part", words: "presents the account" } }),
    '<span class="e-ctx">This essay</span> presents the account <span class="e-ctx">: in short.</span>');
  // a line that quotes the words plainly: the words alone
  assert.equal(O.passage({ kind: "lapsed", block: "b1", line: 'true · "presents"  lapsed at s', anchor: { kind: "part", words: "presents" } }), "presents");
  assert.equal(O.passage({ kind: "finding", block: "b1", cause: "The step is not argued.", anchor: { kind: "span", words: "w" } }), "The step is not argued.");
  assert.equal(O.passage({ kind: "unused-premise", block: "b4", roles: "assumption", anchor: { kind: "block" } }), "");
  const carried = (since, blocks) => ({ kind: "carried-unread", block: null, anchor: { kind: "paper" }, cause: { since, blocks }, line: "update …" });
  const all = [carried("u1abcdef99", ["a", "b"]), carried("u2", ["b", "c"])];
  assert.equal(O.passage(all[0]), "2 blocks carried unread by update u1abcdef");
  assert.deepEqual(O.carried(all, [0, 1]), ["a", "b", "c"]);
  const html = O.html(all, O.families(all), P, null, "none");
  assert.ok(html.includes('<p class="e-said">3 blocks in all</p>'));
  // each update's blocks folded under its row, each a link, open as the reader left it
  assert.ok(html.includes('<details class="e-blocks" data-fold="c:u1abcdef99"><summary>the 2 blocks</summary><div><a class="go" data-ref="a">a</a> · <a class="go" data-ref="b">b</a></div></details>'));
  assert.ok(O.html(all, O.families(all), P, null, "none", new Set(), null, [], "", { opened: new Set(["c:u2"]) }).includes('data-fold="c:u2" open>'));
  assert.ok(!html.includes("e-name"));
});

test("what the reviews found is read by block, each row with its kind; a validity reading names the claim it argues for; a row links to its detail", () => {
  const report = {
    negative: { items: [{ ...item("negative", "j1", "§1", "not-shown"), anchor: { kind: "part", words: "four are needed" }, aspect: "valid", target: "t1", cause: { judgment: "x-1-4", author: "claude-code 2.1 opus" } }], lines: ["j1  valid j1 → t1 · \"four are needed\"  held: not-shown"] },
    finding: { items: [{ ...item("finding", "j1", "§1", "severity 2"), cause: "The step is not argued.", finding: "x-1-21", severity: 2 }], lines: ["finding x-1-21 …"] },
  };
  const all = O.items(report);
  const [found] = O.families(all);
  assert.equal(found.key, "readings");
  assert.deepEqual(found.parts.map((p) => [p.label, p.members]), [["j1", [0, 1]]]);
  const html = O.html(all, O.families(all), P, null, "none");
  assert.ok(html.includes('<summary class="open-sub e-block"><span class="chev"></span>j1 <span class="meta">2</span> <span class="e-where">§1</span></summary>'));
  assert.ok(html.includes(">" + P.open("negative").label + "</span>") && html.includes(">" + P.open("finding").label + "</span>"));
  assert.ok(html.includes('<span class="e-ctx">argues for</span> <span class="e-ref">t1</span>: four are needed'));
  assert.ok(html.includes('validity: not-shown, read by <span class="r-by" title="claude-code 2.1 opus">opus</span>'));
  assert.equal((html.match(/data-open-block/g) || []).length, 2);
  // a row in a list of mixed kinds carries its own kind's gate tone, its card's border
  const toned = O.html(all, O.families(all), P, null, "none", new Set(), { negative: { tone: "fail", gates: ["readings"] } });
  assert.ok(toned.includes('class="open-item e-row check-fail" data-item="0"'));
  assert.ok(toned.includes('class="open-item e-row check-none" data-item="1"'));
  assert.ok(!html.includes("e-row check-"));
  // grouped otherwise, each kind under its own head
  assert.ok(O.html(all, O.families(all, "section"), P, null, "section").includes('data-kind="negative"'));
});

test("a passage's markup reads as it is meant: bold, emphasis, a citation by its keys", () => {
  assert.equal(O.styled("a **b** \\emph{c} \\textbf{d} \\autocite[Thm.~3]{stone1948} e"), 'a <b>b</b> <i>c</i> <b>d</b> <span class="e-cite">[stone1948]</span> e');
});

test("a kind folds, open where it is its family's only one; a part with a head folds as the reader leaves it, open while it holds the current item", () => {
  const all = O.items(REPORT);
  const kindsOpen = (html) => [...html.matchAll(/data-fold="k:([^"]+)" open>/g)].map((m) => m[1]);
  // the structure holds two kinds: both folded; to read again two: folded
  assert.deepEqual(kindsOpen(O.html(all, O.families(all), P, null, "none")), []);
  assert.deepEqual(kindsOpen(O.html(all, O.families(all), P, null, "none", new Set(), null, [], "", { opened: new Set(["k:stale"]) })), ["stale"]);
  // the current item opens its kind
  assert.deepEqual(kindsOpen(O.html(all, O.families(all), P, 4, "none")), ["pending"]);
  // a family of one kind: open
  const lone = O.items({ stale: REPORT.stale });
  assert.deepEqual(kindsOpen(O.html(lone, O.families(lone), P, null, "none")), ["stale"]);
  const grouped = (folds, current = null) => O.html(all, O.families(all, "reading"), P, current, "reading", new Set(), null, [], "", folds);
  assert.ok(grouped({}).includes('data-fold="p:unsupported:held inconclusive" open>'));
  assert.ok(grouped({ shut: new Set(["p:unsupported:held inconclusive"]) }).includes('data-fold="p:unsupported:held inconclusive">'));
  assert.ok(grouped({ shut: new Set(["p:unsupported:held inconclusive"]) }, 0).includes('data-fold="p:unsupported:held inconclusive" open>'));
});

test("each kind has a sentence on what it holds, under its head", () => {
  for (const kind of O.KINDS) assert.ok(O.KIND_SENTENCES[kind], kind);
  const all = O.items(REPORT);
  const html = O.html(all, O.families(all), P, null, "none");
  assert.ok(html.includes('</summary><p class="e-sentence">' + O.KIND_SENTENCES.unsupported + "</p>"));
});
