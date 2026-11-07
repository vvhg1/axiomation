// The verdict: `axiomation check --json` over the state shown,
// as the header's chip and the check tab. The chip says whether the paper
// passes and how many gates fail or warn, each gate's count in its title.
// The tab says whether the paper passes and what stops it: a row per gate
// that fails or warns (its count, what it counts, its split by kind),
// each a link to its items in the open tab, which lists them; the gates
// that pass in one line; then the audit, what no gate counts because a
// ruling, a reading or the author's word cleared it, as counts, each list
// folded. Nothing here decides a gate: the verdict is the command's, and
// so is the tone of each open kind (`kindTones`).
//
// Pure: the chip's words, the items in order, the HTML; the page wires
// them.
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else (root.AxView = root.AxView || {}).check = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // `check.Gate`, in its order.
  const GATES = ["unsupported-claims", "defects", "reviews", "unreviewed", "cited-publications", "dangling-references", "duplicate-labels", "unused-premises", "undeclared-assumptions"];

  const counted = (gates) => gates.map((g) => g.gate + " " + g.count).join(", ");

  // The gates the chip and the tab show: every gate but `reviews` where no
  // outside review was received (its `received` 0), which a paper no
  // reviewer read has nothing to answer in (`check.GateResult.shown`).
  const shownGates = (verdict) => verdict.gates.filter((g) => g.gate !== "reviews" || (g.received || 0) > 0);

  // The chip: `{tone, text, title}`, `tone` pass, warn, fail, or error
  // where the verdict could not be read (its words in the title); while it
  // is asked (`verdict` null), a grey "check …".
  function chip(verdict) {
    if (verdict === null || verdict === undefined) return { tone: "error", text: "check …", title: "axiomation check is reading this state's gates" };
    if (verdict.error) {
      return { tone: "error", text: "check: not read", title: "check could not be read: " + verdict.error };
    }
    const failing = verdict.gates.filter((g) => g.outcome === "fail");
    const warning = verdict.gates.filter((g) => g.outcome === "warn");
    const off = shownGates(verdict).filter((g) => g.outcome === "off").map((g) => g.gate);
    const gates = (n) => n + (n === 1 ? " gate" : " gates");
    const text = (verdict.passes ? "✓ passes" : "✗ fails on " + gates(failing.length)) + (warning.length ? " · " + warning.length + " warn" : "");
    const title = "axiomation check at this state: " + (verdict.passes ? "the paper passes, no gate fails" : "the paper fails on " + counted(failing)) +
      (warning.length ? "; warns: " + counted(warning) : "") + (off.length ? "; off: " + off.join(", ") : "") + ". Click: the check tab, each gate's items";
    return { tone: verdict.passes ? (warning.length ? "warn" : "pass") : "fail", text, title };
  }

  // How many items stand in the gates that fail: the tab's count.
  function failing(verdict) {
    if (!verdict || verdict.error) return null;
    return verdict.gates.filter((g) => g.outcome === "fail").reduce((n, g) => n + g.count, 0);
  }

  // Every item of the verdict, gate by gate in its order, each `{...data}`
  // (its `line` among them); then each claim a ruling cleared; then each
  // the author leaves unargued; then each cited work the author accepts
  // resting on; then each established under a conjecture its statement
  // assumes.
  function items(verdict) {
    if (!verdict || verdict.error) return [];
    return verdict.gates.flatMap((g) => g.items)
      .concat(verdict.gates.flatMap((g) => g.cleared || []))
      .concat(verdict.gates.flatMap((g) => g.left || []))
      .concat(verdict.gates.flatMap((g) => g.accepted || []))
      .concat(verdict.conditional || []);
  }

  // The open kind a gate's item is, by its cause: the cause where it is an
  // open kind's word, else the kind that lists the same thing; a defect
  // standing on what lapsed is `lapsed`'s, as `open` lists it.
  const CAUSE_KIND = { circular: "circle", "not-shown": "negative", contradicted: "negative" };
  const RANK = { none: 0, warn: 1, fail: 2 };

  // Each open kind's tone, from what the gates do with its items: `{tone,
  // gates}`, `fail` where a gate that fails lists an item of that kind,
  // else `warn` where one that warns does, else `none`, in no gate; null
  // while the verdict is read or where it could not be.
  function kindTones(verdict) {
    if (!verdict || verdict.error) return null;
    const found = {};
    for (const g of verdict.gates) {
      if (g.outcome !== "fail" && g.outcome !== "warn") continue;
      for (const item of g.items) {
        const kind = item.lapsed ? "lapsed" : CAUSE_KIND[item.kind] || item.kind;
        const t = found[kind] || (found[kind] = { tone: "none", gates: [] });
        if (!t.gates.includes(g.gate)) t.gates.push(g.gate);
        if (RANK[g.outcome] > RANK[t.tone]) t.tone = g.outcome;
      }
    }
    return found;
  }

  // Each open finding's own tone, by its id: `fail` or `warn` where a gate
  // that does so lists it (a severity-3 finding in `defects`, an outside
  // reviewer's in `reviews`), a finding no such gate lists absent; a
  // kind's tone would give every finding the worst one's. Null as
  // `kindTones`.
  function findingTones(verdict) {
    if (!verdict || verdict.error) return null;
    const found = {};
    for (const g of verdict.gates) {
      if (g.outcome !== "fail" && g.outcome !== "warn") continue;
      for (const item of g.items) if (item.finding && RANK[g.outcome] > RANK[found[item.finding] || "none"]) found[item.finding] = g.outcome;
    }
    return found;
  }

  // A conditional result's conjectures, and who wrote the flags.
  function assumedBy(item) {
    return "under " + (item.under || []).map((u) => u.at + (u.by_person ? "" : " (an agent's flag)")).join(", ");
  }

  // Who spoke for a claim a ruling cleared, and why.
  function speakers(item) {
    const ruling = (item.cause && item.cause.ruling) || { speaks: [] };
    return ruling.speaks.map((s) => s.ruling + " by " + s.author + ": " + s.reason).join("; ");
  }

  // An item's cause in words: a gap's why, the reading a defect stands on,
  // what an inconclusive reading could not read, a retracted work; else the
  // open view's words. A defect standing on what lapsed says so first.
  function causeWords(item, O) {
    const words = ownWords(item, O);
    return item.lapsed ? "lapsed, unresolved: " + words : words;
  }

  function ownWords(item, O) {
    const c = item.cause;
    if (c && typeof c === "object" && !Array.isArray(c)) {
      const parts = [];
      if (c.why) parts.push(c.why);
      if (c.judgment) parts.push("judgment " + c.judgment + (c.author ? " by " + c.author : ""));
      if (c.unread && c.unread.length) parts.push("could not read " + c.unread.map((w) => w.key + " (" + w.read_status + ")").join(", "));
      if (c.work) parts.push("work " + c.work + (c.doi ? " (doi " + c.doi + ")" : ""));
      if (parts.length) return parts.join("; ");
    }
    return O.causeWords(item);
  }

  // An item's dot: an open finding's severity's tone, else its cause's.
  function tone(item, P) {
    if (item.kind === "finding" && item.severity) return P.severity(item.severity).colour;
    return P.cause(item.kind).colour;
  }

  // What each gate counts, in one sentence (`docs/model.md` § 11).
  const GATE_SENTENCES = {
    "unsupported-claims": "Claims the paper asserts with no argument in the graph and no ruling that the paper owes none.",
    defects: "Negative readings, open severe findings and retracted works the paper cites.",
    reviews: "An outside reviewer's findings not yet answered.",
    unreviewed: "What a review reads and no verified reading settles: unjudged, lapsed, disputed, inconclusive or unchecked.",
    "cited-publications": "Cited works that are preprints, theses, reports or errata.",
    "dangling-references": "References whose target the paper does not have.",
    "duplicate-labels": "Labels the paper gives to more than one block.",
    "unused-premises": "Premises no claim uses.",
    "undeclared-assumptions": "Claims whose justification rests on a premise their statement does not name.",
  };

  // Why a claim stands in `unsupported-claims`, by its standing.
  const GAP_WORDS = { unsupported: "standing unsupported", justified: "justified with no justifies edge onto it", restated: "restated restating nothing or a conjecture" };

  const tally = (words) => {
    const found = new Map();
    for (const w of words) found.set(w, (found.get(w) || 0) + 1);
    return [...found].sort((x, y) => y[1] - x[1]);
  };

  // A gate's count split by what its items are, `[[words, n]]`, most
  // first: an unsupported claim by its standing and the circles apart,
  // any other item by its kind, a lapsed one as lapsed.
  function split(gate) {
    if (gate.gate === "unsupported-claims") {
      return tally(gate.items.map((i) => i.kind === "circular" ? "circles" : GAP_WORDS[i.cause && i.cause.standing] || (i.cause && i.cause.standing) || i.kind));
    }
    return tally(gate.items.map((i) => (i.lapsed ? "lapsed " : "") + i.kind));
  }

  // The ruling a cleared claim stands under: its first speaker's.
  const rulingOf = (item) => {
    const speaks = (item.cause && item.cause.ruling && item.cause.ruling.speaks) || [];
    return speaks.length ? speaks[0].ruling : "ruled";
  };

  const plural = (n, one, many) => n + " " + (n === 1 ? one : many);

  // What each cause a gate lists that is no open kind holds, in one
  // sentence: the head of its part when Open lists one gate's items.
  const CAUSE_SENTENCES = {
    circular: "Claims whose support runs in a circle back to them.",
    "not-shown": "Readings that found the paper does not show what it claims.",
    contradicted: "Readings that found a claim false.",
  };

  // A gate's name as a heading: its words, its first capital.
  const title = (gate) => gate.charAt(0).toUpperCase() + gate.slice(1).replace(/-/g, " ");

  // A gate as a row, drawn as Open draws a kind's head: its dot, its name,
  // its count; under it what it counts and its count by kind. A gate with
  // items is a link to them in Open, its "in Open ›" shown on hover.
  function gateRow(g, O, P) {
    const parts = split(g);
    const also = [g.to_defects ? g.to_defects + " in defects" : "", g.to_unreviewed ? g.to_unreviewed + " disputed, in unreviewed" : ""].filter(Boolean);
    const mode = g.mode === "block" ? "a block gate: it fails the check" : g.mode === "warn" ? "a warn gate: it warns, never fails" : "off";
    const linked = g.items.length > 0;
    const notes = [];
    if (parts.length > 1) notes.push(parts.map(([w, n]) => n + " " + O.escape(w)).join(" · "));
    if (also.length) notes.push("Also standing unsupported, counted elsewhere: " + O.escape(also.join(", ")));
    if (g.unlooked) notes.push(plural(g.unlooked, "cited work", "cited works") + " never looked up, so not counted (<code>axiomation sources publications</code>)");
    return '<li class="k-gate check-' + g.outcome + (linked ? " k-linked" : "") + '"' + (linked ? ' data-gate-link="' + O.escape(g.gate) + '"' : "") + ' title="' + O.escape(g.gate + ": " + mode + (linked ? ". Click: its items in Open" : "")) + '">' +
      '<div class="k-head"><i class="h-dot" style="background:' + P.CHECK[g.outcome === "off" ? "error" : g.outcome].colour + '"></i><span class="k-title">' + O.escape(title(g.gate)) + "</span>" +
      (linked ? '<span class="k-go">in Open ›</span>' : "") + '<span class="count">' + (g.outcome === "off" ? "off" : g.count) + "</span></div>" +
      (GATE_SENTENCES[g.gate] ? '<p class="e-sentence">' + O.escape(GATE_SENTENCES[g.gate]) + "</p>" : "") +
      notes.map((n) => '<p class="e-sentence k-note">' + n + "</p>").join("") + "</li>";
  }

  // A section, drawn as Open draws a family: a fold, its head's title and
  // its count in its tone, a sentence under it, its body.
  function section(key, tone, heading, count, sentence, body, open) {
    return '<details class="card-sec open-group check-' + tone + '" data-fold="' + key + '"' + (open ? " open" : "") + '><summary class="sec-head"><span class="chev"></span><span class="title">' + heading + '</span><span class="count">' + count + "</span></summary>" +
      (sentence ? '<p class="e-sentence">' + sentence + "</p>" : "") + '<div class="sec-body">' + body + "</div></details>";
  }

  // A part of the audit, drawn as Open draws a kind: a fold, its title and
  // count, a sentence, its body; open where the reader opened it or it
  // holds the current item.
  function part(key, heading, count, sentence, body, folds, holds) {
    const open = holds || folds.opened.has(key);
    return '<details class="e-kindpart check-none" data-fold="' + key + '"' + (open ? " open" : "") + '><summary class="e-kindhead"><span class="chev"></span><span class="e-kindtitle">' + heading + '</span><span class="count">' + count + "</span></summary>" +
      (sentence ? '<p class="e-sentence">' + sentence + "</p>" : "") + body + "</details>";
  }

  // A list inside a part, its rows under a head of their own: a fold, as
  // Open folds a block's items.
  function sub(key, heading, n, rows, folds, holds) {
    const open = holds || folds.opened.has(key);
    return '<li class="e-part"><details data-fold="' + key + '"' + (open ? " open" : "") + '><summary class="open-sub"><span class="chev"></span>' + heading + ' <span class="meta">' + n + "</span></summary><ul>" + rows + "</ul></details></li>";
  }

  // The tab: the verdict at its state in one line; the gates that fail,
  // then those that warn, a row each linking to its items in Open; the
  // gates that pass, folded; then the audit, folded: what no gate counts
  // because a ruling, a reading or the author's word cleared it, as
  // counts, each list folded. No gate's items are listed here: Open lists
  // them. `current` is the item clicked, by its index in `items`; `folds`,
  // the sections and parts the reader opened or shut; `ahead`, the
  // paper's files changed since this state.
  function html(verdict, O, P, current, folds = {}, { ahead = false } = {}) {
    if (!verdict) return '<p class="meta">Reading the gates…</p>';
    if (verdict.error) return '<p class="meta">check could not be read: ' + O.escape(verdict.error) + "</p>";
    folds = { opened: folds.opened || new Set(), shut: folds.shut || new Set() };
    const by = (outcome) => shownGates(verdict).filter((g) => g.outcome === outcome);
    const [fails, warns, passes, off] = ["fail", "warn", "pass", "off"].map(by);
    const tone = chip(verdict).tone;
    const head = [verdict.passes ? "✓ passes" : "✗ fails on " + plural(fails.length, "gate", "gates")];
    if (warns.length) head.push(warns.length + " warn");
    if (passes.length) head.push(passes.length + " pass");
    if (off.length) head.push(off.length + " off");
    const out = ['<div class="tab-head"><div class="eyebrow">Check</div><p class="e-counts"><span class="k-v check-' + tone + '">' + O.escape(head[0]) + "</span>" +
      (head.length > 1 ? " · " + O.escape(head.slice(1).join(" · ")) : "") + "</p>" +
      '<p class="tab-lede">Whether the paper passes its gates at state ' + O.escape(String(verdict.state).slice(0, 8)) + ". Select a gate to list its items in Open.</p>" +
      (ahead ? '<p class="open-note"><b>note</b> The paper\'s files changed since this state, and no update has run: the check does not read the edit.</p>' : "") + "</div>"];
    // a section open unless the reader shut it, or shut unless opened
    const shown = (key, byDefault) => (byDefault ? !folds.shut.has(key) : folds.opened.has(key));
    const items = (gates) => gates.reduce((n, g) => n + g.count, 0);
    const rows = (gates) => '<ul class="k-gates">' + gates.map((g) => gateRow(g, O, P)).join("") + "</ul>";
    if (fails.length) out.push(section("s:fail", "fail", "Fails", plural(items(fails), "item", "items") + " · " + plural(fails.length, "gate", "gates"), "Block gates with items: the paper does not pass until each is cleared.", rows(fails), shown("s:fail", true)));
    if (warns.length) out.push(section("s:warn", "warn", "Warns", plural(items(warns), "item", "items") + " · " + plural(warns.length, "gate", "gates"), "Warn gates with items: worth a look; they never fail the check.", rows(warns), shown("s:warn", true)));
    if (passes.length || off.length) out.push(section("s:pass", "pass", "Passes", plural(passes.length, "gate", "gates") + (off.length ? " · " + off.length + " off" : ""), "Gates with no items, and those the paper's settings turn off.", rows(passes.concat(off)), shown("s:pass", false)));
    out.push(audit(verdict, O, P, current, folds));
    return out.join("");
  }

  // The audit: what no gate counts because someone's word cleared it, by
  // who and how; where an agent alone cleared it, the check trusts that
  // agent. Each list folded under its count, its rows by their index in
  // `items` (after every gate's own).
  function audit(verdict, O, P, current, folds) {
    let at = verdict.gates.reduce((n, g) => n + g.items.length, 0);
    const parts = [];
    const holds = (from, n) => current !== null && current !== undefined && current >= from && current < from + n;
    const drawn = (list, because) => {
      const from = at;
      const html = list.map((item, k) => O.entry(item, from + k, P, { current: from + k === current, attr: "data-check", because: because(item) })).join("");
      at += list.length;
      return { html, from };
    };
    const cleared = verdict.gates.flatMap((g) => g.cleared || []);
    // what the head counts: the claims and works a word cleared, and the marks
    let total = 0;
    let lines = 0;
    if (cleared.length) {
      total += cleared.length;
      const alone = cleared.filter((i) => !(i.cause && i.cause.ruling && i.cause.ruling.by_person)).length;
      // by ruling, most first; each claim keeps its index in `items`
      const index = new Map(cleared.map((item, k) => [item, at + k]));
      const lists = tally(cleared.map(rulingOf)).map(([ruling, n]) => {
        const members = cleared.filter((i) => rulingOf(i) === ruling);
        const html = members.map((item) => {
          const i = index.get(item);
          const because = item.cause.ruling.speaks.map((s) => O.escape(s.ruling) + ", read by " + O.author(s.author) + (s.reason ? ": " + O.escape(s.reason) : "")).join("; ");
          return O.entry(item, i, P, { current: i === current, attr: "data-check", because });
        }).join("");
        return sub("cleared:" + ruling, O.escape(ruling), n, html, folds, members.some((m) => index.get(m) === current));
      });
      const inside = holds(at, cleared.length);
      at += cleared.length;
      parts.push(part("a:cleared", "No argument owed", cleared.length + " · " + (alone === cleared.length ? "all" : alone) + " by agents alone",
        "Claims a ruling says the paper owes no argument for; unsupported-claims does not count them.", '<ul class="e-mixed">' + lists.join("") + "</ul>", folds, inside));
    }
    const listPart = (key, heading, list, count, sentence, because) => {
      if (!list.length) return;
      total += list.length;
      const inside = holds(at, list.length);
      const { html } = drawn(list, because);
      parts.push(part(key, heading, count, sentence, '<ul class="e-mixed">' + html + "</ul>", folds, inside));
    };
    const left = verdict.gates.flatMap((g) => g.left || []);
    listPart("a:left", "Left unargued", left, String(left.length), "Claims you leave unargued, each with your reason.", (item) => O.escape(speakers(item)));
    const accepted = verdict.gates.flatMap((g) => g.accepted || []);
    listPart("a:accepted", "Accepted works", accepted, String(accepted.length), "Cited works you accept resting on as they are; cited-publications does not count them.", (item) => {
      const word = (item.cause && item.cause.accepted) || { by: "", reason: "" };
      return "accepted by " + O.author(word.by) + (word.reason ? ": " + O.escape(word.reason) : "");
    });
    const conditional = verdict.conditional || [];
    const flagged = conditional.filter((i) => !i.by_person).length;
    listPart("a:conditional", "Established under a conjecture", conditional, conditional.length + " · " + flagged + " by an agent's flag alone", "Claims established only under a conjecture their statement assumes.", (item) => O.escape(assumedBy(item)));
    // the stale marks cleared: counts only, as the command says them
    const marks = [["reread", "by rereads"], ["read", "by readings"]].map(([key, how]) => {
      const found = verdict[key] || [];
      if (!found.length) return null;
      const alone = found.filter((m) => !m.by_person).length;
      return { n: found.length, line: found.length + " " + how + " (" + (alone === found.length ? "all" : alone) + " by agents alone)" };
    }).filter(Boolean);
    if (marks.length) {
      const n = marks.reduce((k, m) => k + m.n, 0);
      lines = n;
      parts.push('<div class="e-kindpart k-flat"><div class="e-kindhead"><span class="chev k-none"></span><span class="e-kindtitle">Stale marks cleared</span><span class="count">' + n + "</span></div>" +
        '<p class="e-sentence">Lines a change marked, cleared without a new review: ' + O.escape(marks.map((m) => m.line).join(", ")) + ". <code>axiomation check --json</code> lists them.</p></div>");
    }
    if (!parts.length) return "";
    const open = folds.opened.has("s:audit") || (current !== null && current !== undefined && current >= verdict.gates.reduce((n, g) => n + g.items.length, 0));
    return section("s:audit", "none", "The audit", [total ? total + " cleared" : "", lines ? plural(lines, "stale mark", "stale marks") : ""].filter(Boolean).join(" · "), "What no gate counts because a ruling, a reading or your word cleared it. Where an agent alone cleared it, the check trusts that agent.", parts.join(""), open);
  }

  return { GATES, GATE_SENTENCES, CAUSE_SENTENCES, CAUSE_KIND, chip, split, failing, items, kindTones, findingTones, speakers, assumedBy, causeWords, tone, html };
});
