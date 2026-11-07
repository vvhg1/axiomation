// `node --test tests/viewer/`: the card: `show`'s lines, each
// handle a link, a quote never cut into links.
const test = require("node:test");
const assert = require("node:assert/strict");

const C = require("../../src/axiomation/viewer/page/src/card.js");

// the card's words as its tests read them: each author's icon as the
// words its hover shows, [an agent: …] (its own test reads the icons)
const icons = (html) => html && html.replace(/<svg class="who w-\w+"[^>]*aria-label="([^"]*)"[\s\S]*?<\/svg>/g, "[$1]");
const card = (...args) => icons(C.html(...args));
const owedRow = (s) => icons(C.owedRow(s));

const handles = new Set(["thm:1", "lem:a", "group:proof-of-thm"]);

test("a sheet line's handles are links, its quoted excerpt is not cut", () => {
  const html = C.linked('→ uses lem:a [x] · "by lem:a we have"', handles);
  assert.equal(html, '→ uses <a class="go" data-ref="lem:a">lem:a</a> [x] · &quot;by lem:a we have&quot;');
  assert.equal(C.linked("stale since s: from thm:1, via group:proof-of-thm", handles),
    'stale since s: from <a class="go" data-ref="thm:1">thm:1</a>, via <a class="go" data-ref="group:proof-of-thm">group:proof-of-thm</a>');
});

test("the card shows its header, each section's lines with counts and its record; never the paper's words again", () => {
  const report = {
    block: { node: "n3", handle: "thm:1", block: "p.md:9:1", form: "statement", environment: "theorem", meaning: "theorem", roles: ["claim"], section: { number: "2", title: "Results" }, groups: [], standings: ["justified"] },
    text: "The <bound> holds.",
    rests_on: { total: 1, items: [{}], lines: ['→ uses lem:a · "x"'], more: 0 },
    rested_on_by: { total: 0, items: [], lines: [], more: 0 },
    standings: { total: 1, items: [{}], lines: ['⊢ justified · "The bound holds."'], more: 0 },
    history: { total: 2, items: [{ state: "aaaaaaaaaaaa", change: "built" }, { state: "bbbbbbbbbbbb", change: "changed" }], lines: ["aaaa build built", "bbbb update changed"], more: 0 },
    notes: ["1 edits on this version no state applied yet"],
  };
  const html = card(report, handles, "", { lineOf: () => "line 9" });
  assert.ok(html.includes("<h3>thm:1</h3>"));
  assert.ok(!html.includes("The &lt;bound&gt; holds."), "the paper holds its words; the card does not repeat them");
  assert.ok(html.includes('<a class="reveal" data-reveal title="scroll the paper back to it">line 9</a>'), "a link back to it in the paper");
  assert.ok(html.includes("§ 2 Results"));
  // a title that numbers itself is not numbered again
  const own = card({ ...report, block: { ...report.block, section: { number: "2.3", title: "2.3 Second Law" } } }, handles, "", { lineOf: () => "line 9" });
  assert.ok(own.includes("§ 2.3 Second Law") && !own.includes("2.3 2.3"));
  assert.ok(!html.includes("card-nav"), "no navigation where the trail holds one selection");
  assert.ok(!html.includes("rested on by"), "an empty section is left out");
  assert.ok(html.includes("note: 1 edits"));
  // the record, folded: its ids, its form, the commands; never the
  // history's lines, which `axiomation history` prints
  assert.ok(html.includes('<details class="card-sec" data-sec="record">'), "folded until opened");
  for (const id of ["n3", "p.md:9:1", "statement theorem", "axiomation show thm:1"]) assert.ok(html.includes(id), id);
  assert.ok(!html.includes("aaaa build built"));
  assert.ok(html.includes('data-copy="axiomation history thm:1"'));
  const attr = (line) => 'data-line="' + line.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;") + '"';
  for (const [name] of C.SECTIONS) if (name !== "history" && report[name] && report[name].total) for (const line of report[name].lines) assert.ok(html.includes(attr(line)), line);
});

test("the history shows the steps where something happened to the block, the newest first, each named by what happened; the rest counted", () => {
  const report = {
    block: { node: "n3", handle: "thm:1", block: "p.md:9:1", form: "prose", roles: ["claim"], groups: [] },
    history: {
      total: 4,
      items: [
        { state: "s1", change: "built", block: "p.md:9:1", text: "t1", causes: [] },
        { state: "s2", change: "same", block: "p.md:9:1", text: "t1", causes: [{ person: "k1" }] },
        { state: "s3", change: "changed", block: "p.md:9:1", text: "t2", causes: [{ delta: "weakened", entry: null, note: "now only for small n", by: "writer" }] },
        { state: "s4", change: "same", block: "p.md:9:1", text: "t2", causes: [{ moved_by_rule: [{ why: "its meaning moved by rule", from: "lem:a", via: "thm:1 → uses lem:a" }] }] },
        { state: "s5", change: "same", block: "p.md:9:1", text: "t2", causes: [{ stale: [{ why: "asked by the rule", from: "lem:a", via: null }] }] },
        { state: "s6", change: "same", block: "p.md:9:1", text: "t2", causes: [{ person: "k2" }], edges: [
          { step: "began", kind: "uses", source: "n3", target: "n7", parts: ["lem:a [K]"], was: [], by: "person" },
          { step: "ended", kind: "about", source: "n8", target: "n3", parts: ["thm:1 [T]"], was: [], by: "agent" },
          { step: "changed", kind: "uses", source: "n3", target: "n9", parts: ["lem:b [S]"], was: [], by: "agent" },
        ] },
      ],
      lines: ["s1", "s2", "s3", "s4", "s5", "s6"],
      more: 0,
    },
  };
  const names = { n7: "lem:a", n8: "rem:c", n9: "lem:b" };
  const html = card(report, new Set(["lem:a"]), "", { whenOf: (s) => "at " + s, nodeName: (n) => names[n] || n });
  assert.ok(html.indexOf('data-state="s6"') < html.indexOf('data-state="s4"') && html.indexOf('data-state="s4"') < html.indexOf('data-state="s3"') && html.indexOf('data-state="s3"') < html.indexOf('data-state="s1"'), "the newest first");
  assert.ok(!html.includes('data-state="s2"'), "a person's answers at an edit are the state's, not the block's");
  assert.ok(html.includes("1 other state: nothing changed for it"));
  assert.ok(html.includes('<span class="count">5</span>'), "the steps counted where something happened");
  // each heading names what happened, never that its text was kept
  assert.ok(html.includes(">its text changed · weakened</span>") && html.includes("now only for small n"));
  assert.ok(html.includes(">meaning moved by the rule</span>") && html.includes(">its edges changed</span>"));
  assert.ok(!html.includes("its text kept"));
  // a step that only marked it stale, its edges unchanged, folds
  assert.ok(html.includes('<details class="h-asked"><summary>1 update marked it stale; its edges did not change</summary><div class="h-row" data-state="s5">'));
  // its edges from its side: its own active, one onto it passive
  assert.ok(html.includes('<span class="h-sign">+</span> uses <a class="go" data-ref="n7">lem:a</a> <span class="parts">[K]</span> <span class="flag person">by a person</span>'));
  assert.ok(html.includes('<span class="h-sign">−</span> described by <a class="go" data-ref="n8">rem:c</a> <span class="parts">on [T]</span>'), "onto it, its own parts");
  assert.ok(html.includes('<span class="h-sign">~</span> uses <a class="go" data-ref="n9">lem:b</a> <span class="parts">[S]</span> <span class="fact-k">was no parts</span>'));
  assert.ok(html.includes('<div class="h-diff" data-from="s2" data-from-block="p.md:9:1" data-to="s3" data-to-block="p.md:9:1"></div>'), "a place for the word change where the text changed");
  assert.equal((html.match(/class="h-diff"/g) || []).length, 1, "none where it did not");
  assert.ok(html.includes('data-from="s3" data-to="s4"') && html.includes("compare in paper"));
  assert.ok(!html.includes('data-from="undefined"') && html.includes("first kept"), "the first step compares with nothing");
  assert.ok(html.includes('its meaning moved by rule (via thm:1 → uses <a class="go" data-ref="lem:a">lem:a</a>)'), "a rule's cause in words, its handles links");
  assert.ok(html.includes("at s3"), "when, as the page names a state");
});

test("an edge reads from the selection's side: active out, passive in, its parts where they are", () => {
  const out = { kind: "uses", source: "me", target: { block: "b1", entries: ["lem:a [S_n]"] }, evidence: [{ text: "by the lemma" }], mark: "ref" };
  const into = { kind: "uses", source: "b1", target: { block: "me", entries: ["thm:1 [ii]"] }, evidence: [{ text: "we use (ii)" }] };
  const proof = { kind: "justifies", source: "g", target: { block: "me" }, evidence: [] };
  const paper = { kind: "about", source: "me", target: { scope: "paper" }, evidence: [] };
  const self = { block: "me", evidence: [{ text: "\\citep{x}" }] };
  const pick = (v) => [v.verb, v.other, v.label, v.parts, v.onOwn, v.flags, v.quote];
  assert.deepEqual(pick(C.viewed(out, "me")), ["uses", "b1", "", ["S_n"], false, ["ref"], "by the lemma"]);
  assert.deepEqual(pick(C.viewed(into, "me")), ["used by", "b1", "", ["ii"], true, [], "we use (ii)"]);
  assert.deepEqual(pick(C.viewed(proof, "me")), ["justified by", "g", "", [], false, [], ""]);
  assert.deepEqual(pick(C.viewed(paper, "me")), ["about", null, "the paper", [], false, [], ""]);
  assert.deepEqual(pick(C.viewed(self, "me")), ["self-supported", null, "", [], false, [], "\\citep{x}"]);
  for (const [kind, [active, passive]] of Object.entries(C.VERBS)) assert.ok(active && passive.endsWith(" by"), kind);
});

test("the card's edges: a group per verb in the order they come, a row per edge, its other end named", () => {
  const report = {
    block: { node: "n1", handle: "def:x", block: "me", form: "statement", roles: ["definition"], groups: [], standings: [] },
    rests_on: { total: 0, items: [], lines: [], more: 0 },
    rested_on_by: {
      total: 3,
      items: [
        { kind: "uses", source: "b1", target: { block: "me" }, evidence: [{ text: "first" }] },
        { kind: "about", source: "b2", target: { block: "me" }, evidence: [{ text: "second" }] },
        { kind: "uses", source: "b3", target: { block: "me" }, evidence: [{ text: "third" }] },
      ],
      lines: ['b1 → uses def:x · "first"', 'b2 → about def:x · "second"', 'b3 → uses def:x · "third"'],
      more: 0,
    },
  };
  const ctx = { nameOf: (id) => "h-" + id, whatOf: () => "claim", lineOf: (id) => "line " + id.slice(1) };
  const html = card(report, new Set(), "", ctx);
  const heads = [...html.matchAll(/class="verb k-[a-z]+">([^<]+)</g)].map((m) => m[1]);
  assert.deepEqual(heads, ["used by", "described by"]);
  const rows = [...html.matchAll(/data-ref="(b\d)">h-b\d</g)].map((m) => m[1]);
  assert.deepEqual(rows, ["b1", "b3", "b2"]);
  assert.ok(html.includes(">line 3<") && html.includes("“third”") && html.includes('class="what">claim'));
});

test("a claim is shown once: its status, what the graph holds for it, its truth judged; where it stops a link", () => {
  const words = "every set is finite";
  const folded = { status: "rests-on", detail: "proof:x: unjudged", next: null, at: "proof:x", findings: [] };
  const report = {
    block: { block: "me", handle: "me", node: "n1", form: "prose", roles: ["claim"], groups: [] },
    standings: { total: 1, items: [{ block: "me", claim: { text: words }, status: "restated", claim_status: folded }], lines: ['⊢ restated · "every set is finite"'] },
    judgments: {
      total: 2,
      items: [
        { aspect: "true", subject: { node: "n1", handle: "me", target: null, words }, status: "unjudged" },
        { aspect: "accurate", subject: { node: "n1", handle: "me", target: null, words: null }, status: "unjudged" },
      ],
      lines: ['true · "every set is finite"  unjudged', "accurate  unjudged"],
    },
    established: {
      total: 1,
      items: [{ claim: words, standing: "restated", established: false, assumptions: [], reason: { kind: "unjudged", at: "proof:x", why: 'rests on thm:x "all": `valid` of proof:x is unjudged' } }],
      lines: ['"every set is finite" restated  not established: unjudged, rests on thm:x'],
    },
  };
  const html = card(report, new Set(["thm:x", "proof:x"]), "");
  const found = C.claims(report);
  assert.equal(found.rows.length, 1);
  assert.equal(found.rows[0].judgment.aspect, "true");
  assert.deepEqual(found.others.map((o) => o.item.aspect), ["accurate"]);
  assert.equal((html.match(/every set is finite/g) || []).length, 1 + 3, "the words shown once, the three sheet lines kept in data-line, the established one on the status");
  assert.ok(html.includes('<div class="ph"><span title="rests-on: proof:x: unjudged">waits on another claim</span><span class="side">elsewhere</span></div>'), "its open status a card, the record's words on hover, whose move");
  assert.ok(html.includes('<p>It is established once <a class="go" data-ref="proof:x">proof:x</a> is, unless it does not rely on it.</p>'), "one sentence, where it stops a link");
  assert.ok(html.includes('<div class="status-line"><span class="sg a-open">○</span><span>0/1 claims</span>'), "the block's status: a sign, its claims");
  assert.ok(html.includes('<span class="k">Relies on</span><span class="v"><a class="go" data-ref="proof:x">proof:x</a>, which is not established yet</span>'), "what it relies on, a line of its own");
  assert.ok(html.includes('<b>Settle <a class="go" data-ref="proof:x">proof:x</a> first</b> this claim waits on it</span><span class="w-takes t-elsewhere">elsewhere</span>'), "its way: the claim it waits on");
  assert.ok(!html.includes(">no<") && !html.includes("row-k\">established"), "no established row saying again what the status says");
  assert.ok(html.includes(">Judgments<") && !html.includes(">Established<"));
});

test("a generalizing about edge shows its flag on its row, from either end", () => {
  const wider = { kind: "about", source: "g", target: { block: "n" }, evidence: [], generalizes: true };
  assert.deepEqual(C.viewed(wider, "g").flags, ["generalizes"]);
  assert.deepEqual(C.viewed(wider, "n").flags, ["generalizes"]);
  assert.deepEqual(C.viewed({ ...wider, generalizes: undefined }, "g").flags, []);
});

test("an assumed uses edge shows its flag, and a claim established under it names the conjecture", () => {
  const hypothesis = { kind: "uses", source: "x", target: { block: "k" }, evidence: [], assumed: true };
  assert.deepEqual(C.viewed(hypothesis, "x").flags, ["assumed"]);
  assert.deepEqual(C.viewed({ ...hypothesis, assumed: undefined }, "x").flags, []);
  const words = "Henry is red";
  const report = {
    block: { block: "x", handle: "x", node: "n1", form: "statement", roles: ["claim"], groups: [] },
    standings: { total: 1, items: [{ block: "x", claim: { text: words }, status: "justified" }], lines: ['⊢ justified · "Henry is red"'] },
    judgments: { total: 0, items: [], lines: [] },
    established: {
      total: 1,
      items: [{ claim: words, standing: "justified", established: true, via: "p", assumptions: [], under: [{ at: "k", claim: "All elephants are red." }], reason: null }],
      lines: ['"Henry is red" justified  established by p, under conjecture k "All elephants are red."'],
    },
  };
  const html = card(report, new Set(["k", "p"]), "");
  assert.ok(html.includes('<span class="k">Under</span><span class="v">conjecture <a class="go" data-ref="k">'), html);
});

test("a justification's partial is a flag on its row, a declaration and a completeness on the claim", () => {
  const proof = { kind: "justifies", source: "g", target: { block: "me" }, evidence: [], partial: "analogy" };
  const self = { block: "me", evidence: [{ text: "\\citep{x}" }], partial: "sketch" };
  assert.deepEqual(C.viewed(proof, "me").flags, ["analogy"]);
  assert.deepEqual(C.viewed(self, "me").flags, ["sketch"]);
  const claim = (s) => ({
    block: { node: "n1", handle: "thm:1", block: "me", form: "statement", roles: ["claim"], groups: [], standings: [s.status] },
    standings: { total: 1, items: [s], lines: ["⊢ " + s.status], more: 0 },
  });
  const declared = card(claim({ status: "unsupported", declared: "omitted", claim: { text: "f holds" }, completeness: null }), handles, "");
  assert.ok(declared.includes('<span class="k">Argued</span><span class="v">no argument <span class="fact-k">· the paper says it is omitted</span>'), "no line in the graph, and what the paper declares");
  assert.ok(!declared.includes("argued as"));
  const partial = card(claim({ status: "justified", claim: { text: "f holds" }, completeness: "analogy" }), handles, "");
  assert.ok(partial.includes("argued as analogy"));
  const given = card(claim({ status: "justified", claim: { text: "f holds" }, completeness: "given" }), handles, "");
  assert.ok(!given.includes("argued as"));
});

// A block's judgments and findings as `show` gives them: a claim's
// `true` held, a cited judgment with its pages, a computed one with its
// run, a finding on two blocks.
const JUDGED = {
  block: { node: "n2", handle: "thm:1", block: "b2", form: "statement", roles: ["claim"], groups: [], standings: ["self"] },
  standings: { total: 1, items: [{ block: "b2", status: "self", claim: { text: "f is small" } }], lines: ['⊢ self · "f is small"'], more: 0 },
  judgments: {
    total: 3,
    items: [
      { aspect: "true", subject: { node: "n2", handle: "thm:1", target: null, target_handle: null, words: "f is small", work: null, file: null }, status: "lapsed", held: null,
        judgments: [{ id: "j1", author: "agent model-a", verdict: "verified", depth: "informal", state: "s1aaaaaaaaaaaa", lapsed: true, reaffirms: null, evidence: "the bound holds", pages: [], provisional: false, run: null, code: [], values: [] }],
        findings: [], cause: { kind: "text", state: "s2bbbbbbbbbbbb", why: "def:x's text changed", element: { kind: "node", name: "n1" } }, clears: "`axiomation reask` j1, then a judgment or `axiomation reaffirm`" },
      { aspect: "cited", subject: { node: "n2", handle: "thm:1", target: null, target_handle: null, words: "f is small", work: "able1998", file: null }, status: "held", held: "j2",
        judgments: [
          { id: "j2", author: "agent model-a", verdict: "verified", depth: "informal", state: "s1aaaaaaaaaaaa", lapsed: false, reaffirms: null, evidence: "the page says f is small", pages: [{ name: "able1998:12", key: "able1998", page_index: 12, read_status: "transient" }, { name: "able1998:12", key: "able1998", page_index: 12, read_status: "ok" }], provisional: true, run: null, code: [], values: [] },
        ], findings: [], cause: null, clears: null },
      { aspect: "computed", subject: { node: "n2", handle: "thm:1", target: null, target_handle: null, words: null, work: null, file: "code/run.py" }, status: "held", held: "j3",
        judgments: [{ id: "j3", author: "person", verdict: "verified", depth: "computational", state: "s1aaaaaaaaaaaa", lapsed: false, reaffirms: null, evidence: "the table's 0.42", pages: [], provisional: false,
          run: { file: "code/run.py", arguments: ["--n", "10"], runner: "python", status: "ran", output: "ffff" }, code: ["x = mean(a)"], values: [{ text: "0.42", output: "0.4200" }] }],
        findings: [], cause: null, clears: null },
    ],
    lines: ['true · "f is small"  lapsed at s2: text', 'cited able1998 · "f is small"  held: verified', "computed file:code/run.py  held: verified"],
    more: 0,
  },
  findings: {
    total: 2,
    items: [
      { id: "f1", severity: 3, aspect: "true", state: "open", problem: "false for f = 0", fix: "assume f nonzero", author: "agent model-b",
        anchors: [{ kind: "span", node: "n2", words: "f is small", work: null, file: null }, { kind: "block", node: "n5", words: null, work: null, file: null }], pages: [], answer: null, proposal: { status: "fixed", of: null, note: "now assumed nonzero", author: "agent model-a" }, cause: null, clears: "`axiomation answer-finding` f1" },
      { id: "f2", severity: 1, aspect: "presentation", state: "fixed", problem: "a typo", fix: null, author: "person", anchors: [{ kind: "block", node: "n2", words: null, work: null, file: null }], pages: [], answer: { status: "fixed", note: "done" }, cause: null, clears: null },
    ],
    lines: ['finding f1  severity 3  true  open  "false for f = 0"', 'finding f2  severity 1  presentation  fixed  "a typo"'],
    more: 0,
  },
};

test("the card's judgments: a claim's rows, its readings folded under its true status, each reading's evidence, pages and run", () => {
  const html = card(JUDGED, new Set(["thm:1", "def:x"]), "", { nodeName: (n) => ({ n1: "def:x", n5: "lem:b" })[n] || n, sameOf: (id) => (id === "f1" ? ["f9"] : []) });
  const attr = (line) => 'data-line="' + line.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;") + '"';
  for (const name of ["standings", "judgments", "findings"]) for (const line of JUDGED[name].lines) assert.ok(html.includes(attr(line)), line);
  // the claim's true, lapsed: its cause linked, what clears it, its reading folded
  // its answer lines: whether it is true, its reading unfolded on a click
  assert.ok(html.includes('<span class="k">True</span>'));
  assert.ok(!html.includes('<span class="row-k">true</span>'));
  assert.ok(html.includes('<details class="readings"><summary>lapsed<span class="rs-more">reason</span></summary>'));
  assert.ok(html.includes('<a class="go" data-ref="n1">def:x</a>'), "the element that changed");
  // each reading's evidence, its pages with their read status, provisional
  for (const text of ["the bound holds", "the page says f is small", "the table's 0.42"]) assert.ok(html.includes("“" + text + "”"), text);
  assert.ok(html.includes("able1998:12 <span class=\"rs\">transient</span>"));
  assert.ok(html.includes("able1998:12 <span class=\"rs\">ok</span>"));
  assert.ok(!html.includes("hash"), "a page read shows no hash: its file is the evidence");
  assert.ok(html.includes(">provisional</span>"));
  assert.ok(html.includes('<span class="verdict v-verified">verified</span>'));
  // a held reading's provisional shows beside its verdict, not only folded
  assert.ok(/<summary>verified <span class="fact-k">\w+<\/span> <span class="flag provisional"/.test(html));
  // a computation: its run, its values matched, its code lines
  assert.ok(html.includes("<code>code/run.py --n 10</code>") && html.includes('<span class="run-ran">ran</span>'));
  assert.ok(html.includes("“0.42”") && html.includes("0.4200") && html.includes("x = mean(a)"));
  assert.ok(html.includes('<span class="k">Cited</span><span class="v">able1998: ') && html.includes("file:code/run.py"), "a citation reading in Cites, a run's in Judgments");
});

test("a citation no reading has read: a row each in Cites, read or not, and one card for those unread", () => {
  const unread = (work) => ({ kind: "unjudged", aspect: "cited", status: "unjudged", block: "b2", handle: "thm:1", line: "thm:1  cited " + work + " · \"\\cite{" + work + "}\"  unjudged",
    clears: "a reading: `axiomation review PAPER --session citationcheck`", subject: { aspect: "cited", node: "n2", target: null, words: "\\cite{" + work + "}", work, file: null } });
  const html = card(JUDGED, new Set(["thm:1"]), "", { open: { unread: [unread("smith2001"), unread("jones2002")] } });
  assert.ok(html.includes('<span class="k">Cited</span><span class="v">able1998: '), "the one read, as before");
  for (const work of ["smith2001", "jones2002"]) assert.ok(html.includes('<span class="k">Cited</span><span class="v">' + work + ": not checked"), work);
  assert.equal((html.match(/citations? not checked/g) || []).length, 1, "one card for both");
  assert.ok(html.includes("2 citations not checked") && html.includes("--session citationcheck") && html.includes("--aspect cited --on thm:1"));
  assert.ok(!card(JUDGED, new Set(["thm:1"]), "").includes("not checked</span>"), "none unread: no row of one");
});

test("the card's findings: severity, state, problem, fix, every anchor a link, the pile's same, a proposal, what clears it", () => {
  const html = card(JUDGED, new Set(["thm:1"]), "", { nodeName: (n) => ({ n5: "lem:b" })[n] || n, sameOf: (id) => (id === "f1" ? ["f9"] : []) });
  assert.ok(html.includes('class="finding sev-3 fs-open"') && html.includes('class="finding sev-1 fs-fixed"'));
  assert.ok(html.includes('data-finding="f1"'), "its row carries its id, for a search by it");
  assert.ok(html.includes("false for f = 0") && html.includes("assume f nonzero"));
  assert.ok(html.includes('<a class="go" data-ref="n2">n2</a> <span class="q">“f is small”</span>'), "a span anchor: its block, its words");
  assert.ok(html.includes('<a class="go" data-ref="n5">lem:b</a>'), "its second anchor");
  assert.ok(html.includes('<span class="row-k">the same</span><span class="row-v">f9'));
  assert.ok(html.includes("`axiomation answer-finding` f1"));
  assert.ok(html.includes('<span class="row-k">fixed</span><span class="row-v">done'), "the answer's note");
  assert.ok(html.includes('<span class="row-k">proposed fixed</span><span class="row-v"><span class="r-by">[an agent: agent model-a]</span> now assumed nonzero'), "an agent's proposal, beside the open finding, who made it in front, its whole name on hover");
});

test("a claim's status and next step above its rows; what justifies it a link", () => {
  const report = {
    block: { node: "n2", handle: "thm:1", block: "b2", form: "statement", roles: ["claim"], groups: [], standings: ["justified"] },
    rests_on: { total: 1, items: [{ kind: "justifies", source: "g1", target: { block: "b2" }, evidence: [] }], lines: ["proof:thm:1 → justifies thm:1"], more: 0 },
    standings: { total: 1, items: [{ block: "b2", status: "justified", claim: { text: "f is small" }, claim_status: { status: "defect", detail: "`valid` of its proof not-shown, read by claude-code 2.1 opus-5", by: "claude-code 2.1 opus-5", next: "the text corrected and read again, or your judgment over it (`axiomation judge`)", at: null, findings: [] } }], lines: ['⊢ justified · "f is small"'], more: 0 },
    established: { total: 1, items: [{ claim: "f is small", standing: "justified", established: false, via: null, assumptions: [], reason: { kind: "not-shown", at: "proof:thm:1", why: "the valid is held not-shown" } }], lines: ['"f is small" justified  not established: not-shown'], more: 0 },
  };
  const html = card(report, new Set(["thm:1", "proof:thm:1"]), "", { nameOf: (id) => ({ g1: "proof:thm:1" })[id] || id });
  // whether its argument proves it: a reading says not, its argument a link
  assert.ok(html.includes('<span class="i a-no">✕</span><span class="k">Proved</span><span class="v">not-shown[an agent: claude-code 2.1 opus-5]<div class="arg">the argument the graph links: <a class="go" data-ref="g1">g1</a>'), "who read it, an icon; the passage read, named");
  assert.ok(html.includes('<div class="pcard p-bad"'), "its problem a card, in a defect's tone");
  assert.ok(html.includes('<span title="defect: `valid` of its proof not-shown, read by claude-code 2.1 opus-5">argument not shown</span><span class="side">here</span>'), "named by what is open, the record's words on hover");
  assert.ok(html.includes('<p>A reading found that <a class="go" data-ref="g1">g1</a> does not show it.</p>'));
  assert.ok(html.includes('<span class="sg a-no">✕</span>'), "the block's sign: red");
  // its ways, one line each: what to do and when, what it takes, the command
  assert.ok(html.includes('<li><span class="w-do"><b>Repair the argument</b> if the objection is right</span><span class="w-takes t-text">text edit</span><span></span></li>'));
  assert.ok(html.includes('data-copy="axiomation judge --aspect valid --on proof:thm:1 --for thm:1 --verdict … --depth … --evidence …"'), "a judgment of this argument for this claim, with its depth");
  assert.ok(html.includes("data-copy=\"axiomation change --retract proof:thm:1 '→ justifies thm:1' --standing thm:1 '⊢ unsupported · &quot;f is small&quot;' --reason …\""), "the link retracted as show prints it, the standing set with it");
});

test("whether an argument is owed comes first, then what the graph holds: no argument owed, neutral, with whose ruling and why", () => {
  const words = "kernels compose";
  const known = { status: "no-argument-owed", gap: null, ruling: "known", by_person: false, guard: null, speaks: [{ ruling: "known", author: "agent model-a", reason: "the standard associativity", id: "r1", person: false }] };
  const report = {
    block: { block: "me", handle: "me", node: "n1", form: "prose", roles: ["claim"], groups: [] },
    standings: { total: 1, items: [{ block: "me", claim: { text: words }, status: "unsupported", support: known }], lines: ['⊢ unsupported · "kernels compose"'] },
  };
  const html = card(report, new Set(), "");
  assert.ok(html.includes('<span class="i a-none">–</span><span class="k">Argued</span><span class="v">no argument needed'), "no argument, and no problem: a ruling owes none");
  assert.ok(html.indexOf('<span class="k">Argued</span>') < html.indexOf('<div class="owed">'), "whose ruling and why, under it");
  assert.ok(html.includes('<span class="owed owed-no">none owed</span>: ruled <span class="ruling">known</span> by <span class="flag">agents alone</span>'), "in words: none owed, ruled known by agents alone");
  assert.ok(html.includes('<div class="ruled">the standard associativity'), "its reason, the kind the head names not said again");
  assert.ok(html.includes("agents alone") && html.includes("the standard associativity") && html.includes('<span class="r-by">[an agent: agent model-a]</span>'));
  // unsupported: a guard, an owed ruling, a gap of its own; none where the
  // standing says all
  const row = (status, support) => owedRow({ status, support });
  assert.ok(row("unsupported", { ...known, status: "unsupported", guard: "true-not-held" }).includes("ruled known, but agents alone ruled, and no reading has shown it holds"));
  assert.ok(row("unsupported", { ...known, status: "unsupported", ruling: null, speaks: [{ ...known.speaks[0], ruling: "owed" }] }).includes("ruled owed"));
  assert.ok(row("justified", { status: "unsupported", gap: "unsupported", speaks: [] }).includes("no justifies edge reaches it"));
  assert.ok(row("justified", { status: "unsupported", gap: "circular", speaks: [] }).includes("in a circle"));
  assert.ok(row("restated", { status: "unsupported", gap: "unsupported", speaks: [] }).includes("it restates nothing"));
  assert.ok(row("restated", { status: "unsupported", gap: "unsupported", speaks: [], conjectures: ["k"] }).includes("it restates k, which the paper only conjectures"));
  assert.ok(row("unsupported", { status: "unsupported", gap: "unsupported", speaks: [] }).includes('owed-yes">owed</span>: <span class="claim-why">unruled'));
  assert.equal(row("justified", { status: "supported", speaks: [] }), null);
});

test("the card's suggestions: state, works, why, pages, and the command that answers an open one", () => {
  const report = {
    block: { block: "me", handle: "me", node: "n1", form: "prose", roles: ["claim"], groups: [] },
    suggestions: {
      total: 2,
      items: [
        { id: "s1", state: "open", works: ["doi:10.1/x"], why: "the bound a reader expects", author: "agent model-a", blocks: ["me"], pages: [{ name: "doi:10.1/x:2", read_status: "ok" }], note: null },
        { id: "s2", state: "declined", works: ["arxiv:1"], why: "another", author: "agent model-a", blocks: ["me"], pages: [], note: "not this field" },
      ],
      lines: ["suggestion s1  open  at me", "suggestion s2  declined  at me"],
    },
  };
  const html = card(report, new Set(), "");
  assert.ok(html.includes(">Suggestions<"));
  assert.ok(html.includes('class="suggestion ss-open"') && html.includes('class="suggestion ss-declined"'));
  assert.ok(html.includes("<code>doi:10.1/x</code>") && html.includes("the bound a reader expects"));
  assert.ok(html.includes("doi:10.1/x:2"), "the pages read");
  assert.ok(html.includes("answer-suggestion PAPER s1"), "an open one's answer");
  assert.ok(!html.includes("answer-suggestion PAPER s2"), "an answered one's none");
  assert.ok(html.includes("not this field"));
});

test("the support row shows the ruling that decides, once; a ruling of another kind as a note; the history folded below", () => {
  const owed = (reason, author) => ({ ruling: "owed", reason, author, id: author, person: false });
  const twice = { status: "unsupported", gap: "unsupported", ruling: null, by_person: false, guard: null,
    speaks: [owed("The paper gives no argument, and the claim needs a construction.", "agent 2.1.296"), owed("The paper gives no argument. The claim needs a separating family.", "agent 2.1.302")] };
  const row = owedRow({ status: "unsupported", support: twice });
  assert.ok(row.includes("separating family") && row.includes("[an agent: agent 2.1.302]"), "the latest of the deciding kind");
  assert.ok(!row.includes("needs a construction"), "an agreeing earlier ruling is the history's, not the row's");
  assert.ok(!row.includes("other ruling"));
  // a speaking ruling that disagrees is named in a note, and why it lost
  const split = { ...twice, speaks: [{ ruling: "known", reason: "standard", author: "agent 2.1.296", id: "k", person: false }, twice.speaks[1]] };
  const noted = owedRow({ status: "unsupported", support: split });
  assert.ok(noted.includes('1 other ruling</span> known <span class="r-by">[an agent: agent 2.1.296]</span> (outweighed: any owed wins)'));
  assert.ok(!noted.includes(">standard<") && !noted.includes(" standard "), "its reason is the history's");
  // cleared: the latest ruling of the clearing kind
  const cleared = { status: "no-argument-owed", gap: null, ruling: "known", by_person: false, guard: null,
    speaks: [{ ruling: "known", reason: "the old words", author: "a1", id: "1", person: false }, { ruling: "known", reason: "the new words", author: "a2", id: "2", person: false }] };
  const shown = owedRow({ status: "unsupported", support: cleared });
  assert.ok(shown.includes("the new words") && !shown.includes("the old words"));
  // the Rulings section, the rulings on words no claim holds now, folded
  // until opened
  assert.ok(C.FOLDED.includes("rulings"));
  const report = {
    block: { block: "me", handle: "me", node: "n1", form: "prose", roles: ["claim"], groups: [] },
    rulings: { total: 2, items: [{}, {}], lines: ["one", "two"], more: 0 },
  };
  assert.ok(card(report, new Set()).includes('<details class="card-sec" data-sec="rulings"><summary'));
});

test("a section opens where it asks something now, folds where it is looked up, its folded head saying what it holds; the reader's own fold outranks both", () => {
  const edge = (source, target) => ({ kind: "uses", source, target: { block: target, entries: [] }, evidence: [], provenance: { by: "agent" } });
  const report = {
    block: { block: "me", handle: "me", node: "n1", form: "prose", roles: ["claim"], groups: [] },
    rests_on: { total: 1, items: [edge("me", "a")], lines: ["uses a"], more: 0 },
    rested_on_by: { total: 2, items: [edge("b", "me"), edge("c", "me")], lines: ["b", "c"], more: 0 },
    marks: { total: 2, items: [
      { node: "n1", handle: "me", since: "s2", edge: "me → uses a", state: "gate", causes: [] },
      { node: "n1", handle: "me", since: "s1", edge: "me → uses a", state: "gate", causes: [], closed_by: "r1234567890", read_by: "reread", by_person: false },
    ], lines: ["stale since s2: a's text changed (via me → uses a)", "closed: x, stale since s1, read by reread r1234567890"], more: 0 },
    history: { total: 2, items: [
      { state: "s0", change: "built", causes: [], joined: [], edges: [] },
      { state: "s1", change: "same", causes: [{ stale: [{ why: "a's text changed", via: "me → uses a" }] }], joined: [], edges: [] },
    ], lines: ["s0", "s1"], more: 0 },
    findings: { total: 1, items: [{ id: "f1", state: "fixed", severity: 2, aspect: "presentation", author: "a", problem: "p", anchors: [{ kind: "block", node: "n1" }] }], lines: ["f1"], more: 0 },
  };
  const sec = (html, name) => (html.match(new RegExp('<details class="card-sec" data-sec="' + name + '"( open)?>')) || [])[1] === " open";
  const html = card(report, new Set(["a", "b", "c"]));
  assert.ok(sec(html, "rests_on"), "what it rests on: open");
  assert.ok(!sec(html, "rested_on_by"), "what rests on it: folded");
  assert.ok(html.includes('<span class="sec-brief">used by 2</span>'), "its head says what it holds");
  assert.ok(sec(html, "marks"), "an open mark: open");
  assert.ok(!sec(html, "findings"), "only a closed finding: folded");
  assert.ok(html.includes('<span class="sec-brief">1 closed</span>'));
  // the open mark a row from the block's side, counted alone; the closed
  // one on the history's step that marked it, with what read it again
  assert.ok(html.includes('<div class="who">uses <a class="go" data-ref="a">a</a></div>'));
  const marks = html.slice(html.indexOf('data-sec="marks"'), html.indexOf('data-sec="rests_on"'));
  assert.ok(marks.includes('<span class="count">1</span>') && !marks.includes("closed by"));
  const step = html.slice(html.indexOf('<div class="h-row" data-state="s1">'));
  assert.ok(step.includes('<div class="h-cause h-closed" data-line="closed: x, stale since s1, read by reread r1234567890">stale: uses <a class="go" data-ref="a">a</a> <span class="fact-k">· closed by reread r1234567</span></div>'));
  // the reader's own folds
  const mine = card(report, new Set(["a", "b", "c"]), "", { shut: new Set(["rests_on"]), opened: new Set(["rested_on_by", "record"]) });
  assert.ok(!sec(mine, "rests_on") && sec(mine, "rested_on_by") && sec(mine, "record"));
});

test("an owed ruling shows its weight, its fix and what relies on the claim; a proposed ruling is the person's, for the next review", () => {
  const weighed = { status: "unsupported", gap: "unsupported", ruling: null, by_person: false, guard: null, relied: ["n7", "n9"],
    speaks: [{ ruling: "owed", severity: 3, fix: "prove the matching lower bound", reason: "the lower bound is argued nowhere", author: "agent a", id: "r1", person: false }],
    proposal: { ruling: "known", severity: null, fix: null, reason: "a textbook bound", author: "person Ada", id: "p1" } };
  const row = owedRow({ status: "unsupported", support: weighed });
  assert.ok(row.includes('<span class="fact-k">owed 3</span> the lower bound is argued nowhere'));
  assert.ok(row.includes('<span class="fact-k">fix</span> prove the matching'));
  assert.ok(row.includes("relied on</span> by 2 blocks"));
  assert.ok(row.includes('<span class="fact-k">proposed</span> known <span class="r-by">[a person: Ada]</span>: a textbook bound (the next argument review settles it)'));
  // the lightest, relied on by nothing
  const light = { ...weighed, relied: [], proposal: null, speaks: [{ ...weighed.speaks[0], severity: 1 }] };
  const plain = owedRow({ status: "unsupported", support: light });
  assert.ok(plain.includes('<span class="fact-k">owed 1</span>') && !plain.includes("relied on"));
});

test("a refiled finding shows why it stands first, its problem as first filed, and the proposal its refiling weighed; a proposed reading is the person's", () => {
  const refiled = {
    ...JUDGED,
    findings: {
      total: 2,
      items: [
        { id: "f3", severity: 2, aspect: "valid", state: "open", problem: "the step is asserted", fix: null, author: "agent model-b",
          anchors: [{ kind: "block", node: "n2", words: null, work: null, file: null }], pages: [], answer: null, proposal: null, cause: null, clears: null,
          first: "f1", refiled_as: null, evidence: "the note reads the citation as the step", reading: true, weighed: null,
          turned_down: { status: "dismissed", of: null, note: "it is cited", author: "person" } },
        { id: "f1", severity: 2, aspect: "valid", state: "open", problem: "the step is asserted", fix: null, author: "agent model-b",
          anchors: [{ kind: "block", node: "n2", words: null, work: null, file: null }], pages: [], answer: null, proposal: null, cause: null, clears: null,
          first: "f1", refiled_as: "f3", evidence: null, reading: false, weighed: { status: "dismissed", of: null, note: "it is cited", author: "person" } },
      ],
      lines: ['finding f3  severity 2  valid  open  a disagreement of reading', 'finding f1  severity 2  valid  open  "the step is asserted"'],
      more: 0,
    },
  };
  refiled.judgments = {
    ...JUDGED.judgments,
    items: JUDGED.judgments.items.map((s, i) => (i === 0 ? { ...s, proposal: { id: "j9", author: "person", verdict: "verified", evidence: "the example attains it", lapsed: false, reask: null } } : s)),
  };
  const html = card(refiled, new Set(["thm:1"]), "", { nodeName: (n) => n, sameOf: () => [] });
  const stands = html.indexOf("a disagreement of reading, a person's to answer</span> the note reads the citation as the step");
  assert.ok(stands >= 0 && stands < html.indexOf('<span class="row-k">first filed</span><span class="row-v">the step is asserted'), "the reason now, then the problem as first filed");
  assert.ok(html.includes('<span class="row-k">proposed dismissed</span><span class="row-v"><span class="r-by">[a person]</span> it is cited <span class="fact-k">not taken</span>'), "the proposal it turned down, on the refiling shown");
  assert.ok(html.includes('class="reading proposed" data-judgment="j9"') && html.includes("asked by the next review of its kind"), "the person's proposed reading");
});

test("a block no review judges says so under its head, each of show's lines a paragraph", () => {
  const line = "a definition: no claim to judge; a review files a finding on it only when it is ill-posed (none filed)\nlast read by a review: none; no review reading rests on it";
  const report = {
    block: { node: "n1", handle: "def:x", block: "me", form: "prose", roles: ["definition"], groups: [], standings: [] },
    text: "Let x be <it>.",
    no_claim: { roles: ["definition"], findings: 0, last_read: null, line },
  };
  // a definition: its Defines, whether anything uses it and whether a
  // review read it, in place of the no-claim lines
  const html = card(report, new Set(), "");
  assert.ok(html.includes('data-sec="defines"') && !html.includes("no-claim"));
  assert.ok(html.includes('<span class="k">Used</span><span class="v">no recorded use</span>') && html.includes('<span class="k">Read</span><span class="v">not yet</span>'));
  const read = card({ ...report, no_claim: { ...report.no_claim, findings: 2, last_read: { state: "s" }, line: line.replace("none;", "2026-10-26;") } }, new Set(), "");
  assert.ok(read.includes('<span class="k">Read</span><span class="v">2 findings filed <span class="fact-k">2026-10-26</span>'), "a review's last read, its date");
  // a block of no role a section reads: the no-claim lines, one quiet line
  const plain = card({ ...report, block: { ...report.block, roles: ["motivation"] }, no_claim: { ...report.no_claim, roles: ["motivation"] } }, new Set(), "");
  assert.ok(plain.includes('<div class="no-claim"><p>A motivation: no claim to judge; a review files a finding on it only when it is ill-posed (none filed); no review has read it.</p></div>'), "one quiet line");
  assert.ok(plain.indexOf("no-claim") > plain.indexOf("card-head"), "under its head");
  assert.ok(!card({ ...report, no_claim: undefined }, new Set(), "").includes("no-claim"));
});

test("a split or a merge is named from the block's side, its word change across the nodes it joined, its edges carried", () => {
  const report = {
    block: { node: "n2", handle: "rest", block: "p.md:5:1", form: "prose", roles: ["claim"], groups: [] },
    history: {
      total: 1,
      items: [
        { state: "s2", change: "added", block: "p.md:5:1", text: "t2", joined: ["n1"], causes: [], edges: [
          { step: "carried-from", kind: "uses", source: "n9", target: "n2", parts: [], was: [], by: "agent", piece: "n1" },
        ] },
      ],
      lines: ["s2"],
      more: 0,
    },
  };
  const names = { n1: "whole", n9: "user" };
  const html = card(report, new Set(), "", { nodeName: (n) => names[n] || n, parentOf: (s) => (s === "s2" ? "s1" : null) });
  assert.ok(html.includes('<span class="h-cut" data-state="s2">split off</span>'), "begun from another: split off it");
  assert.ok(html.includes('<div class="h-cause h-joined">split off <a class="go" data-ref="n1">whole</a></div>'));
  assert.ok(html.includes('<div class="h-diff" data-node="n2" data-joined="n1" data-from="s1" data-to="s2"></div>'), "the word change across the split, from the state before");
  assert.ok(html.includes('<span class="h-sign">←</span> used by <a class="go" data-ref="n9">user</a> <span class="fact-k">carried from</span> <a class="go" data-ref="n1">whole</a>'));
});

test("one layout for a claim, a finding and a judgment: the words first, then the status, then rows each a short label; an agent by its model", () => {
  const long = "the step is not argued. ".repeat(20);
  const report = {
    block: { block: "me", handle: "me", node: "n1", form: "prose", roles: ["definition", "claim"], groups: [] },
    standings: { total: 1, items: [{ block: "me", claim: { text: "f is small" }, status: "restated", note: "a note", claim_status: { status: "established", detail: "argued", next: null, at: null, findings: [] } }], lines: ["⊢ restated"], more: 0 },
    findings: { total: 1, items: [{ id: "f1", state: "open", severity: 2, aspect: "valid", author: "claude-code 2.1.310 claude-opus-5-5", problem: long, anchors: [{ kind: "block", node: "n1" }] }], lines: ["f1"], more: 0 },
    judgments: { total: 2, items: [
      { aspect: "accurate", subject: {}, status: "unjudged", judgments: [] },
      { aspect: "valid", subject: { handle: "p", target: "me", target_handle: "me" }, status: "held", judgments: [] },
    ], lines: ["a", "v"], more: 0 },
  };
  const html = card(report, new Set());
  // a judgment of the block its aspect and status alone; of anything else, that first
  // a judgment a line, as a claim's: the question, its answer, what it is of
  assert.ok(html.includes('<span class="i a-open">○</span><span class="k">accuracy</span><span class="v">not checked</span>'));
  assert.ok(html.includes('data-sec="argues"') && html.includes('<span class="k">Proves</span><span class="v"><a class="go" data-ref="me">me</a>: held</span>'), "a validity in Argues: whether it proves the claim it argues");
  assert.ok(!html.includes(" · of "), "never \"of the block\" on the block's own card");
  // the header: its name, then what it is, each role in its colour
  assert.ok(html.includes('<h3>me</h3><div class="kind"><span class="role rl-definition">definition</span> and <span class="role rl-claim">claim</span></div>'));
  const claim = html.slice(html.indexOf('<div class="claim"'), html.indexOf("</details>", html.indexOf('<div class="claim"')));
  assert.ok(claim.includes('class="claim-words"') && !claim.includes("pcard"), "an established claim: its words and lines, no card");
  assert.ok(html.includes('<span class="k">Note</span><span class="v">a note</span>'));
  assert.ok(html.includes('<div class="f-problem clamped">') && html.includes('<button type="button" class="f-more">more</button>'), "a long problem folded to its first lines");
  assert.ok(html.includes('<div class="r-head"><span class="r-by">[an agent: claude-code 2.1.310 claude-opus-5-5]</span><span class="sev'), "who found it first, an icon, its model on hover");
});

test("a block's open items: a line of what to settle here, what it uses unsupported and what waits; each kind show holds no row of in its home", () => {
  const report = {
    block: { node: "n3", handle: "c", block: "p.md:9:1", form: "statement", roles: ["claim"], groups: [] },
    standings: { total: 1, items: [{ claim: { text: "It  holds." }, status: "justified", block: "p.md:9:1" }], lines: ["⊢ justified · \"It holds.\""], more: 0 },
    rests_on: { total: 1, items: [{ kind: "uses", source: "p.md:9:1", target: { block: "p.md:2:1" }, evidence: [] }], lines: ["→ uses u"], more: 0 },
    rested_on_by: { total: 0, items: [], lines: [], more: 0 },
  };
  const row = (k) => '<li class="open-item e-row" data-here="' + k + '">' + k + "</li>";
  const open = {
    settle: 2, settleWhat: "1 undeclared assumptions, 1 unused premises", settleColour: "#f00",
    waiting: 3, waitingWhat: "3 unjudged",
    settleKinds: [{ words: "undeclared assumption", colour: "#a5f", goto: "here:1" }, { words: "unused premise", colour: "#a5f", goto: "here:4" }],
    elsewhere: ["p.md:2:1"],
    rows: { claim: [{ words: "It holds.", html: row(1) }, { words: "gone", html: row(2) }], rests_on: [row(3)], rested_on_by: [row(4)], graph: [row(5)], waiting: [row(6)] },
    flawed: new Map([["p.md:2:1", { label: "unsupported", colour: "#f00" }]]),
  };
  const html = card(report, new Set(["u"]), "", { open, nameOf: (id) => (id === "p.md:2:1" ? "u" : id) });
  // the status under the header: a sign, its claims, the unsupported
  // claim it relies on; an open count only for the problems the card
  // shows (these rows are not drawn as one)
  assert.ok(html.includes('<div class="status-line"><span class="sg a-open">○</span><span>0/1 claims</span><span class="gsep"> · </span><span>relies on <a class="go" data-ref="p.md:2:1">an unsupported claim</a></span></div>'));
  // a claim's own under it, its words matched whatever their spacing
  const claim = html.slice(html.indexOf('<div class="claim"'), html.indexOf('data-sec="rests_on"'));
  assert.ok(claim.includes(row(1)) && !claim.includes(row(2)));
  // the graph here, before the claims: its own and a claim's whose words
  // no claim holds now
  const graph = html.slice(html.indexOf('data-sec="graph"'), html.indexOf('data-sec="standings"'));
  assert.ok(graph.includes(row(5)) && graph.includes(row(2)));
  // upstream: its row first, the edge's other end flagged unsupported
  const up = html.slice(html.indexOf('data-sec="rests_on"'), html.indexOf('data-sec="rested_on_by"'));
  assert.ok(up.includes(row(3)) && up.indexOf(row(3)) < up.indexOf("edge-row"));
  assert.ok(up.includes('<span class="flag flaw" style="color:#f00">unsupported</span>'));
  assert.ok(up.includes('<span class="count">1</span>'), "its count the edges'");
  // downstream shown for its row though no edge rests on it, and open,
  // though it starts folded
  assert.ok(html.slice(html.indexOf('data-sec="rested_on_by"')).includes(row(4)));
  assert.ok(html.includes('<details class="card-sec" data-sec="rested_on_by" open>'));
  // waiting, folded
  assert.ok(html.includes('<details class="card-sec" data-sec="waiting">') && html.includes(row(6)));
  // none: no line, no sections of its own
  const bare = card(report, new Set(), "");
  assert.ok(!bare.includes("open-line") && !bare.includes('data-sec="graph"') && !bare.includes('data-sec="rested_on_by"'));
});

test("an undeclared assumption: its lines in one sentence, the graph's own fix first where it may be wrong", () => {
  const ctx = { placeOf: (r) => "<" + r + ">", linesOf: (rs) => rs.join(", "), ordered: (rs) => rs, nameOf: (r) => r, lineOf: (r) => "line " + r.slice(1) };
  const item = {
    kind: "undeclared-assumption", handle: "c", clears: "that line naming the claim it means",
    cause: {
      cause: "unnamed-line",
      lines: [{ where: "statement", line: 'u → about w · "gives it" (names no claim)' }],
      premises: [{ via: "j", premise: "p1" }, { via: "j", premise: "p2" }, { via: "j", premise: "p1" }],
      path: [{ source: "u", hop: "about", target: "w", quote: "gives it" }, { source: "j", hop: "uses", target: "m", quote: "by m" }, { source: "p1", hop: "qualifies", target: "m", quote: "x" }],
    },
  };
  const html = C.undeclaredCard(item, [], ctx, 7);
  assert.ok(html.includes(">2 undeclared assumptions</span>"));
  assert.ok(html.includes('<p>Its argument, <j>, assumes what the claim\'s recorded conditions do not: lines <a class="go" data-ref="p1">1</a>, <a class="go" data-ref="p2">2</a>.</p>'), "each once, by its line");
  // the ways by what is wrong, each case said once, the graph's first
  const ways = [...html.matchAll(/<li class="w-case">(.*?)<\/li>|<b>(.*?)<\/b>/g)].map((m) => m[1] ? "# " + m[1] : m[2]);
  assert.deepEqual(ways, [
    "# If the graph is wrong", "Name the claim in the link from <u> to <w>", "Correct the dependency on m", "Link the claim to them",
    "# If the text is wrong", "State them in the claim", "Revise the argument so it does not need them",
  ], "the graph's fixes first, the case said once");
  assert.ok(html.includes('<span class="w-takes t-graph">graph edit</span>') && html.includes('<span class="w-takes t-text">text edit</span>'), "each keeps its coloured tag, as on every card");
  // a restating about line names its claim at its source end; any other line at its target
  assert.ok(html.includes('data-copy="axiomation change --retract u \'→ about w · &quot;gives it&quot;\' --add u \'→ about […] w · &quot;gives it&quot;\' --reason …"'));
  assert.ok(html.includes("data-copy=\"axiomation change --retract j '→ uses m · &quot;by m&quot;' --reason …\""), "the argument's own link taken off");
  // an argument that is a group: its members' links
  const group = C.undeclaredCard({ ...item, cause: { cause: "missing-edges", premises: [{ via: "g", premise: "p1" }], path: [{ source: "g", hop: "member", target: "b" }, { source: "b", hop: "uses", target: "d", quote: "q" }] } }, [], ctx);
  assert.ok(group.indexOf("Link the claim to it") < group.indexOf("Correct the dependency on d"), "no uses link at all: linking first");
  const uses = C.undeclaredCard({ ...item, cause: { ...item.cause, lines: [{ where: "ask", line: 'u → uses w · "q" (names no claim)' }] } }, [], ctx);
  assert.ok(uses.includes("--add u '→ uses w […] · &quot;q&quot;'"));
  assert.ok(html.includes('class="pcard p-open" data-here="7"') && !html.includes("open-item"), "a card as the others: a goto finds it, a click does not leave the card");
  // one assumption, no uses link: linking it first
  const one = C.undeclaredCard({ ...item, cause: { cause: "missing-edges", premises: [{ via: "j", premise: "p1" }] } }, [], ctx);
  assert.ok(one.includes(">undeclared assumption</span>") && one.includes('do not: line <a class="go" data-ref="p1">1</a>.'));
  assert.ok(one.indexOf("change --add c") < one.indexOf("State it in the claim"));
  // a long list: the first twelve, the rest counted
  const many = C.undeclaredCard({ ...item, cause: { cause: "left-out", premises: Array.from({ length: 14 }, (_, k) => ({ via: "j", premise: "q" + k })) } }, [], ctx);
  assert.equal((many.match(/data-ref="q/g) || []).length, 12);
  assert.ok(many.includes(" and 2 more."));
});

test("a claim argued in its own words: the claim points at its justification, which has its own section", () => {
  const report = {
    block: { node: "n2", handle: "c", block: "b", form: "statement", roles: ["claim", "justification"], groups: [] },
    standings: { total: 1, items: [{ block: "b", status: "self", claim: { text: "it holds" } }], lines: ['⊢ self · "it holds"'], more: 0 },
    rests_on: { total: 1, items: [{ block: "b", evidence: [{ text: "apply $x$ twice" }], claims: ["it holds"] }], lines: ['→ self · "apply $x$ twice"'], more: 0 },
  };
  const html = card(report, new Set(), "");
  assert.ok(html.includes('<span class="k">Argued</span><span class="v">by this block\'s justification, below</span>'), "the claim points at it");
  const just = html.slice(html.indexOf('data-sec="argues"'));
  assert.ok(just.includes("Justification") && just.includes('<div class="j-words e-quote">apply $x$ twice</div>'), "its own section, its words");
  assert.ok(just.includes('<span class="k">Argues</span><span class="v"><span class="e-quote">“it holds”</span>, above <span class="fact-k">· not checked: its truth reading checks it</span>'));
});

test("each status's ways: only those that apply, never a judgment of the wrong aspect, never a promise", () => {
  const at = { claim: "c", words: "", justifier: { handle: "j", source: "j", line: "→ justifies c" } };
  const ctx = { placeOf: (r) => r };
  const ways = (status, detail = "", support = {}, standing = "justified") => C.claimWays({ status, detail, at: status === "rests-on" ? "x" : null }, { status: standing, support }, at, ctx);
  const hows = (...args) => ways(...args).map((w) => w.how);
  assert.deepEqual(hows("unsupported", "unruled", {}, "unsupported"), ["graph", "review", "text", "ruling"]);
  assert.deepEqual(hows("unjudged", "`valid` of j → c is unjudged"), ["review", "judgment", "graph"]);
  // the judgment of the argument for this claim, with its depth; a review is paid
  const [review, judge, relink] = ways("unjudged", "`valid` of j → c is unjudged");
  assert.equal(judge.command, "axiomation judge --aspect valid --on j --for c --verdict … --depth … --evidence …");
  assert.ok(review.paid && judge.starter, "a review spends; a judgment takes its reader's own words");
  assert.equal(relink.command, "axiomation change --retract j '→ justifies c' --reason …");
  // its only argument: its standing set with it, which change requires
  const sole = C.claimWays({ status: "unjudged", detail: "`valid` of j → c is unjudged" }, { status: "justified" }, { ...at, claimWords: "it holds", justifier: { handle: "j", source: "j", line: '→ justifies c [k] · "so"' } }, ctx)[2];
  assert.equal(sole.command, "axiomation change --retract j '→ justifies c [k] · \"so\"' --standing c '⊢ unsupported [k] · \"it holds\"' --reason …");
  assert.ok(!ways("unjudged", "`valid` of j → c").some((w) => /--aspect true/.test(w.command || "")), "never a truth judgment for an argument");
  assert.deepEqual(hows("rests-on", "x: unjudged"), ["elsewhere", "graph"]);
  assert.deepEqual(hows("established", "argued"), []);
  // a lapsed reading resting on a run is never reaffirmed
  assert.ok(!ways("lapsed", "`computed` of c lapsed").some((w) => /reaffirm/.test(w.command || "")));
  assert.equal(C.waysFold([]), "", "nothing to resolve, no list");
});

test("who made a record, at a glance: an agent, a person of the paper, an outside reviewer", () => {
  assert.equal(C.whoKind("claude-code 2.1.310 claude-opus-5-5"), "agent");
  assert.equal(C.whoKind("agent claude-opus-5-5"), "agent");
  assert.equal(C.whoKind("person Ada"), "person");
  assert.equal(C.whoKind("person"), "person");
  assert.equal(C.whoKind("reviewer referee-2"), "reviewer");
  assert.ok(C.who("person Ada").includes('class="who w-person"') && C.who("person Ada").includes("<title>a person: Ada</title>"));
  assert.ok(C.who("reviewer referee-2").includes("<title>an outside reviewer: referee-2</title>"));
  assert.equal(C.who(""), "");
});

test("a finding's ways: a review where your proposal asks one, a person's answer always, its reaffirmation only where it lapsed", () => {
  const kinds = (next) => C.findingWaysOf("f1", next).map((w) => w.action);
  assert.deepEqual(kinds("a person's `axiomation answer-finding` f1"), ["Fix the problem", "Dismiss it", "Mark it a duplicate"]);
  assert.deepEqual(kinds("a review of its kind settling the proposal (`axiomation review`), or a person's `axiomation answer-finding` f1")[0], "Let a review weigh the proposed answer");
  assert.ok(kinds("a person's `axiomation answer-finding` f1, or `axiomation reaffirm-finding` f1").includes("Confirm it still stands"));
  // the review named by the finding's own session, which the default may not ask
  const weigh = C.findingWaysOf("f1", "a review of its kind settling the proposal (`axiomation review --session contribution`), or a person's `axiomation answer-finding` f1")[0];
  assert.equal(weigh.command, "axiomation review --session contribution --budget …");
  assert.ok(C.findingWaysOf("f1", "", true)[1].when.includes("an outside reviewer's needs them"));
});
