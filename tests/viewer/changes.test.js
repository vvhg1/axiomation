// `node --test tests/viewer/`: change mode: the word
// diff, the changed blocks by node, and the update's tiers, each the
// query's answer regrouped.
const test = require("node:test");
const assert = require("node:assert/strict");

const X = require("../../src/axiomation/viewer/page/src/changes.js");

const join = (runs, op) => runs.filter((r) => r.op === op).map((r) => r.text).join("");

test("the word diff keeps what both say and marks what each alone says", () => {
  const runs = X.words("the basic gain model is unrealistic", "the basic model is unrealistic");
  assert.equal(join(runs, "same") + "", "the basic model is unrealistic");
  assert.equal(join(runs, "del").trim(), "gain");
  assert.equal(join(runs, "add"), "");
  // the old text and the new text come back whole
  assert.equal(runs.filter((r) => r.op !== "add").map((r) => r.text).join(""), "the basic gain model is unrealistic");
  assert.equal(runs.filter((r) => r.op !== "del").map((r) => r.text).join(""), "the basic model is unrealistic");
});

test("a block added or deleted is all one run; past the bound, old and new whole", () => {
  assert.deepEqual(X.words(null, "new words"), [{ op: "add", text: "new words" }]);
  assert.deepEqual(X.words("old words", null), [{ op: "del", text: "old words" }]);
  assert.deepEqual(X.words("a b", "a c", 1).map((r) => r.op), ["del", "add"]);
});

const REPORT = {
  text: {
    items: [
      { node: "n1", handle: "a", classes: ["math"], joined: [], old: { block: "o1", text: "x" }, new: { block: "b1", text: "y" } },
      { node: "n2", handle: "b", classes: ["moved", "split"], joined: ["n9"], old: { block: "o2", text: "p q" }, new: { block: "b2", text: "p" } },
      { node: "n3", handle: "c", classes: ["deleted"], joined: [], old: { block: "o3", text: "gone" }, new: null },
    ],
    lines: ["n1  a  math", "n2  b  moved, split", "n3  c  deleted"],
  },
  deltas: { items: [{ node: "n1", block: "b1", handle: "a", class: "reworded", note: "n", by: "writer" }], lines: ["a  Δ reworded — n  (by writer)"] },
  reopened: { items: [{ node: "n4", block: "b4", handle: "d", why: "its edge onto a", from: "a" }], lines: ["d  its edge onto a  (from a)"] },
  edges: { items: [{ change: "ended", edge: {} }], lines: ["- d → uses a"] },
  standings: { items: [], lines: [] },
};

test("changed blocks by node, each badged by its first class", () => {
  const found = X.byNode(REPORT);
  assert.deepEqual([...found.keys()], ["n1", "n2", "n3"]);
  assert.equal(found.get("n2").badge, "split");
  assert.equal(found.get("n3").new, null);
  assert.equal(found.get("n1").line, "n1  a  math");
});

test("the update's tiers: changed, reached, handled, open", () => {
  const open = [
    { kind: "stale", node: "n4", block: "b4", handle: "d", line: "d stale" },
    { kind: "unsupported", node: "n5", block: "b5", handle: "e", line: "e unsupported" },
  ];
  const t = X.tiers(REPORT, open);
  assert.deepEqual(t.changed.map((r) => [r.kind, r.node, r.block]), [["text", "n1", "b1"], ["text", "n2", "b2"], ["text", "n3", null], ["delta", "n1", "b1"]]);
  assert.deepEqual(t.reached.map((r) => [r.node, r.block, r.line]), [["n4", "b4", "d  its edge onto a  (from a)"]]);
  assert.deepEqual(t.handled.map((r) => r.line), ["- d → uses a"]);
  assert.deepEqual(t.open.map((r) => r.kind), ["stale"]);
});

test("the changes tab holds every line of every tier, and a diff marks what changed", () => {
  const t = X.tiers(REPORT, [{ kind: "stale", node: "n4", block: "b4", handle: "d", line: "d stale" }]);
  const page = X.html({ ...REPORT, from: "s1", to: "s2" }, t, { name: (s) => s });
  const attr = (l) => l.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  for (const tier of Object.values(t)) for (const row of tier) assert.ok(page.includes('data-line="' + attr(row.line) + '"'), row.line);
  assert.ok(page.includes("<del>gone</del>"));
  assert.ok(page.includes('<span class="gone">c</span>'), "a deleted block is named, not linked");
  assert.equal(X.diffHtml(X.words("a b", "a c")), "a <del>b</del><ins>c</ins>");
});

test("a diff in the tab keeps a few words either side of each change", () => {
  const before = Array.from({ length: 30 }, (_, i) => "w" + i).join(" ");
  const after = before.replace("w15", "changed");
  const shown = X.diffHtml(X.words(before, after), 3);
  assert.ok(shown.startsWith("… w12 w13 w14 <del>w15 </del><ins>changed </ins>w16 w17 w18 …"), shown);
  assert.ok(!shown.includes("w2 "));
});

test("a split's pieces name each other, and a cut diff keeps its whole beside it", () => {
  const long = Array.from({ length: 40 }, (_, i) => "w" + i).join(" ");
  const report = {
    from: "s1",
    to: "s2",
    text: {
      items: [{ node: "n2", handle: "b", classes: ["changed"], joined: ["n9"], old: { block: "o2", text: long }, new: { block: "b2", text: long.replace("w20", "x") } }],
      lines: ["n2  b  changed"],
    },
  };
  const page = X.html(report, X.tiers(report, []), { name: (s) => s, byNode: (n) => (n === "n9" ? { block: "b9", handle: "piece" } : null) });
  assert.ok(page.includes('with <a class="go" data-ref="b9">piece</a>'));
  assert.ok(page.includes('class="cx-diff cut"') && page.includes('class="cx-diff whole" hidden'));
  assert.ok(page.split('class="cx-diff whole" hidden>')[1].includes("w0 "), "the whole keeps every word");
});

test("the lapsed tier lists every item of the section, its cause linked to the block that changed", () => {
  const report = {
    from: "s1",
    to: "s2",
    lapsed: {
      total: 2,
      items: [
        { kind: "lapsed", node: "n3", block: "b3", handle: "thm:1", judgments: ["j1"], cause: { kind: "text", state: "s2", why: "def:x's text changed", element: { kind: "node", name: "n1" } }, clears: "`axiomation reask` j1, then a judgment or `axiomation reaffirm`" },
        { kind: "lapsed", node: null, block: null, handle: null, finding: "f1", severity: 2, cause: { kind: "deleted", state: "s2", why: "gone", element: { kind: "node", name: "n9" } }, clears: "`axiomation answer-finding` f1, or the problem filed again" },
      ],
      lines: ["true · \"x\"  lapsed at s2: text, def:x's text changed", "finding f1  severity 2  clear  lapsed  \"p\"  (by a)"],
      more: 0,
    },
  };
  const t = X.tiers(report, []);
  assert.deepEqual(t.lapsed.map((r) => [r.kind, r.block, r.line]), report.lapsed.items.map((item, i) => ["lapsed", item.block, report.lapsed.lines[i]]));
  const html = X.html(report, t, { name: (id) => id, byNode: (node) => (node === "n1" ? { block: "b1", handle: "def:x" } : null) });
  assert.ok(html.includes('data-sec="lapsed"'));
  assert.ok(html.includes('<a class="go" data-ref="b1">def:x</a>'), "the element that changed, linked");
  assert.ok(html.includes("cx-badge cx-lapsed\">finding") && html.includes("cx-badge cx-lapsed\">judgment"));
  for (const line of report.lapsed.lines) assert.ok(html.includes(line.replace(/"/g, "&quot;")), line);
  assert.deepEqual(X.tiers({}, []).lapsed, []);
});

test("the edit tab lists the files' edit as `changes` answers it: old beside new, what it would ask", () => {
  // `changes PAPER --json` with no state named, the paper's files ahead
  const report = {
    from: "s2",
    version: "abcdef0123456789",
    pending: true,
    text: {
      total: 2,
      items: [
        { class: "changed", old: [{ block: "o1", node: "n1", handle: "def:x", text: "a field over the reals" }], new: [{ block: "f1", handle: "def:x", text: "a field over the rationals" }] },
        { class: "added", old: [], new: [{ block: "f2", handle: "a-new-remark", text: "a new remark" }] },
      ],
      lines: ["def:x  changed\n  was: a field over the reals\n  now: a field over the rationals", "a-new-remark  added\n  now: a new remark"],
      more: 0,
    },
    reopened: { total: 1, items: [{ handle: "thm:1", why: "uses def:x, whose meaning moved", from: "def:x" }], lines: ["thm:1  uses def:x, whose meaning moved  (from def:x)"], more: 0 },
    on_condition: { total: 1, items: [{ handle: "rem:2", conditions: ["if thm:1's conclusion changes"] }], lines: ["rem:2\n  if thm:1's conclusion changes"], more: 0 },
  };
  const html = X.pendingHtml(report, { name: (id) => "state " + id, byNode: (node) => (node === "n1" ? { block: "b1", handle: "def:x" } : null) });
  assert.ok(html.includes("abcdef012345") && html.includes("state s2"), "the head names the files' version and the state");
  assert.deepEqual(X.PENDING.map(([name]) => name), ["text", "reopened", "on_condition", "corrections_lost", "groups_lost"]);
  for (const [name] of X.PENDING.slice(0, 3)) assert.ok(html.includes('data-sec="' + name + '"'), name);
  assert.ok(!html.includes('data-sec="corrections_lost"'), "no lost correction, no section");
  assert.ok(!html.includes('data-sec="groups_lost"'), "no lost group, no section");
  assert.ok(html.includes('<a class="go" data-ref="b1">def:x</a>'), "the kept block, linked by its node");
  assert.ok(html.includes("cx-badge cx-changed") && html.includes("cx-badge cx-added"));
  assert.ok(html.includes("a field over the <del>reals</del><ins>rationals</ins>"), "the old words struck, the new marked");
  assert.ok(html.includes("a-new-remark") && html.includes("<ins>a new remark</ins>"), "an added block named by its handle, its words new");
  assert.ok(html.includes("uses def:x, whose meaning moved · from def:x"));
  assert.ok(html.includes("if thm:1's conclusion changes"));
  const none = X.pendingHtml({ from: "s2", version: "v", pending: true }, { name: (id) => id });
  assert.equal(none.split('class="cx-none"').length - 1, 3, "an empty section says none");
  const lost = X.pendingHtml(
    {
      ...report,
      corrections_lost: { total: 1, items: [{ block: "o1", node: "n1", handle: "def:x", op: "split", words: "Taking the", why: "words", said: "its words changed" }], lines: ['def:x  its split at "Taking the" not carried: its words changed'], more: 0 },
    },
    { name: (id) => id, byNode: (node) => (node === "n1" ? { block: "b1", handle: "def:x" } : null) },
  );
  assert.ok(lost.includes('data-sec="corrections_lost"'));
  assert.ok(lost.includes("its split at “Taking the”: its words changed"), "the correction, its words and why");
  const dissolved = X.pendingHtml(
    {
      ...report,
      groups_lost: { total: 1, items: [{ group: "proof@o2", node: "g1", handle: "proof-of-x", why: "split", said: "a member was split" }], lines: ["proof-of-x  not carried: a member was split"], more: 0 },
    },
    { name: (id) => id, byNode: (node) => (node === "g1" ? { block: "proof@b2", handle: "proof-of-x" } : null) },
  );
  assert.ok(dissolved.includes('data-sec="groups_lost"'));
  assert.ok(dissolved.includes('<a class="go" data-ref="proof@b2">proof-of-x</a>') && dissolved.includes("a member was split"), "the group, linked, and why");
});

// The word diff laid on the file: its words found among the block's own,
// in order; what the kept text elides passed over.
test("added words become spans of the file and deleted words ghosts before the next word", () => {
  const text = "Let a finite network carry   a\npositive weight. We ask how.";
  const found = X.tokens([["p.md", 0, text.length]], () => text);
  const runs = X.words("Let a network carry a weight. We ask why.", "Let a finite network carry a positive weight. We ask how.");
  const { added, ghosts } = X.place(runs, found);
  assert.deepEqual(added.map(([path, start, end]) => [path, text.slice(start, end)]), [["p.md", "finite"], ["p.md", "positive"], ["p.md", "how."]]);
  assert.deepEqual(ghosts, [["p.md", text.indexOf("how."), "why. "]]);
});

test("adjacent added words are one span; words deleted at the end stand after the last", () => {
  const text = "one two three four";
  const found = X.tokens([["p", 0, text.length]], () => text);
  const { added, ghosts } = X.place(X.words("one four five", "one two three four"), found);
  assert.deepEqual(added, [["p", 4, 13]]);
  assert.deepEqual(ghosts, [["p", 18, " five"]]);
});

test("a block's spans in order, and what the kept text elides passed over", () => {
  const text = "A % hidden note\nB C";
  // the kept text says the comment as one elided mark
  const found = X.tokens([["p", 0, 1], ["p", 16, 19]], () => text);
  assert.deepEqual(found.map((t) => t.word), ["A", "B", "C"]);
  const { added } = X.place(X.words("A " + X.ELIDED + " B", "A " + X.ELIDED + " B C"), found);
  assert.deepEqual(added.map(([, s, e]) => text.slice(s, e)), ["C"]);
});

test("a word the file does not hold marks nothing rather than the wrong words", () => {
  const text = "alpha beta gamma";
  const found = X.tokens([["p", 0, text.length]], () => text);
  const { added, ghosts } = X.place(X.words("alpha gamma", "alpha delta gamma"), found);
  assert.deepEqual(added, []);
  assert.deepEqual(ghosts, []);
});

test("a split's words that went on into the other piece are carried, never deleted and added", () => {
  const runs = X.carried(
    [{ node: "a", text: "First half here. Second half there." }],
    [{ node: "a", text: "First half here." }, { node: "b", text: "Second half there, now." }],
  );
  assert.deepEqual(runs.get("a"), [{ op: "same", text: "First half here." }, { op: "to", text: "", other: "b" }]);
  assert.deepEqual(runs.get("b"), [{ op: "carried", text: "Second half ", other: "a" }, { op: "del", text: "there." }, { op: "add", text: "there, now." }]);
});

test("a merge's words taken in from the block that ended are carried", () => {
  const runs = X.carried(
    [{ node: "a", text: "One two. " }, { node: "c", text: "Three four." }],
    [{ node: "a", text: "One two. Three four." }],
  );
  assert.deepEqual(runs.get("a"), [{ op: "same", text: "One two. " }, { op: "carried", text: "Three four.", other: "c" }]);
  assert.ok(!runs.has("c"), "the block that ended has no runs of its own");
  const html = X.diffHtml(runs.get("a"), null, (n) => "name-" + n);
  assert.ok(html.includes('<span class="carried" title="from name-c">Three four.</span>'));
  assert.ok(X.diffHtml([{ op: "to", text: "", other: "b" }], null, (n) => "name-" + n).includes("→ continues in name-b"));
});

test("carried words laid on the file: a span from their node, and where words went on", () => {
  const text = "First half here.";
  const found = X.tokens([["p.md", 0, text.length]], () => text);
  const placed = X.place([{ op: "same", text: "First half here." }, { op: "to", text: "", other: "b" }], found);
  assert.deepEqual(placed.tos, [["p.md", 16, "b"]]);
  const other = "Second half there.";
  const there = X.place([{ op: "carried", text: "Second half ", other: "a" }, { op: "same", text: "there." }], X.tokens([["p.md", 0, other.length]], () => other));
  assert.deepEqual(there.carried, [["p.md", 0, 11, "a"]]);
  assert.deepEqual(there.added, []);
});
