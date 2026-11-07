// The inbox: `axiomation inbox`'s list. A section per kind of decision
// only the author makes (`inbox.Section`, in its order), headed by the
// decision; each row the claim or the finding, what the records say of
// it, and its next step. Then, folded and apart, what waits on a review:
// the author's proposals and the findings carrying one, which the next
// review of their kind settles. Last, one line counting the lapsed
// judgments, the next review's, which links to them in Open. Nothing
// here decides what is the author's: the list is the command's, and the
// page writes nothing.
//
// Pure: the items in order and the HTML; the page wires them.
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else (root.AxView = root.AxView || {}).inbox = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // `inbox.Section`, in its order: each section's head, the decision it
  // asks, and a sentence on it.
  const SECTIONS = {
    to_judge: { title: "Readings to judge", asks: "Readings by different authors differ: your judgment decides which holds." },
    read_negative: { title: "Read negative", asks: "A reading found a claim or a step not shown, or contradicted: correct the text, or judge over the reading." },
    reading: { title: "Disagreements of reading", asks: "A review read these findings otherwise than your proposal, with no case either way: your answer closes each." },
    stood_again: { title: "Findings filed again", asks: "Findings filed again after an answer, each with the reason it stands now." },
    to_answer: { title: "Findings to answer", asks: "Open findings with no answer and no proposal." },
    awaiting_review: { title: "Waiting on a review", asks: "Your proposals, and findings carrying a proposal. The next review of their kind settles each; until then nothing is asked of you." },
    unsupported_by_a_line: { title: "Unsupported: a line missing", asks: "The graph lacks one line for each claim: an edge, an about line, a circle's end narrowed." },
    unsupported_by_a_ruling: { title: "Unsupported: does the paper owe an argument?", asks: "A ruling decides whether the paper owes each claim an argument: yours, or a review's." },
    unsupported_by_an_argument: { title: "Unsupported: argue it", asks: "The text must argue each claim, or cut or soften it; or your ruling over the agents' owed." },
  };
  const WAITING = "awaiting_review";

  // What waits on a review, by what was proposed, in this order.
  const WAITS = [
    { key: "ruling", title: "Your proposed rulings", sentence: "The next argument review rules each claim with your proposal in view (<code>axiomation sync</code>); or rule it yourself (<code>axiomation rule</code>)." },
    { key: "reading", title: "Your proposed readings", sentence: "The next review of their aspect reads each with your proposal in view (<code>axiomation sync</code>); or judge it yourself (<code>axiomation judge</code>)." },
    { key: "finding", title: "Findings with a proposal", sentence: "The next review of their kind settles each proposed answer (each names its session: <code>axiomation review --session …</code>); or answer it yourself (<code>axiomation answer-finding</code>)." },
  ];
  const waitOf = (item) => item.proposed || "finding";

  // Every item of the report, section by section in its order, each
  // `{...data, line, inbox}`.
  function items(report) {
    if (!report) return [];
    const found = [];
    for (const key of Object.keys(SECTIONS)) {
      const section = report[key];
      if (!section) continue;
      section.items.forEach((data, i) => found.push({ ...data, line: section.lines[i], inbox: key }));
    }
    return found;
  }

  // The decisions, what waits on a review left out: the tab's count.
  const decisions = (all) => all.filter((item) => item.inbox !== WAITING).length;

  const plural = (n, one, many) => n + " " + (n === 1 ? one : many);
  const capital = (text) => text.charAt(0).toUpperCase() + text.slice(1);
  const isPerson = (name) => name === "person" || String(name).startsWith("person ");

  // An item as a card, drawn as the Block tab draws a finding: a label
  // line (what it is, its aspect, its state), the claim's or finding's
  // words, labelled rows (where it is, what the records say, its next
  // step), and the ids it rests on.
  const ASPECTS = { valid: "validity", true: "truth", accurate: "accuracy", cited: "citation", "well-posed": "well-posedness", computed: "computation", presentation: "presentation", significance: "significance" };
  const escape = (text) => String(text).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const label = (text, colour) => (text ? '<span class="i-k"' + (colour ? ' style="color:' + colour + '"' : "") + ">" + escape(text) + "</span>" : "");
  const fact = (key, value) => (value ? '<div class="claim-row"><span class="row-k">' + key + '</span><span class="row-v">' + value + "</span></div>" : "");

  function card(item, i, O, P, current, { head, rows, ids }) {
    const passage = O.passage(item);
    const at = item.handle ? '<span class="e-name">' + O.escape(item.handle) + "</span>" + (item.section ? ' <span class="e-where">' + O.escape(item.section) + "</span>" : "") : "";
    return '<li class="open-item i-card' + (current ? " current" : "") + '" data-inbox="' + i + '">' +
      (item.block ? '<a class="e-open" data-open-block title="select it and open its detail on the Block tab">Block ›</a>' : "") +
      '<div class="r-head">' + head.join("") + "</div>" +
      (passage ? '<div class="i-words">' + passage + "</div>" : "") +
      '<div class="claim-facts">' + fact("At", at) + rows.join("") + "</div>" +
      (ids.length ? '<div class="f-by">' + ids.map((id) => '<span class="r-id">' + O.escape(id) + "</span>").join(" · ") + "</div>" : "") + "</li>";
  }

  const who = (name, O) => (isPerson(name) ? "you" : O.author(name));
  const aspectOf = (item) => ASPECTS[item.aspect] || item.aspect || "";

  // A decision's card: an unsupported claim with its rulings, its `true`
  // reading, what relies on it; a reading or a finding with its state;
  // each with its next step.
  function decisionCard(item, i, O, P, current) {
    const step = item.next || O.clearsOf(item);
    const next = fact("Next", O.prose(capital(step)));
    const c = item.cause && typeof item.cause === "object" ? item.cause : {};
    if (item.kind === "unsupported") {
      const support = item.support || {};
      const speaks = support.speaks || [];
      const ruled = speaks.length
        ? speaks.map((s) => fact("Ruled " + O.escape(s.ruling), (s.reason ? O.escape(s.reason) + " " : "") + '<span class="i-by">' + (isPerson(s.author) ? "by you" : "by " + O.author(s.author)) + "</span>")).join("")
        : fact("Ruled", item.rulings_lapsed ? "each ruling lapsed" : "unruled");
      const relied = (support.relied || []).map((h) => '<span class="e-ref">' + O.escape(h) + "</span>").join(", ");
      return card(item, i, O, P, current, {
        head: [label("unsupported", O.tone(item, P)), item.reading ? label("truth " + item.reading) : ""],
        rows: [ruled, fact("Relied on by", relied), next],
        ids: speaks.map((s) => s.id).filter(Boolean),
      });
    }
    if (item.kind === "finding") {
      return card(item, i, O, P, current, {
        head: [label("severity " + item.severity, P.severity(item.severity).colour), label(aspectOf(item) || item.status || ""), label(item.status && item.status.startsWith("severity") ? "open" : item.status || "")],
        rows: [next],
        ids: [item.finding].filter(Boolean),
      });
    }
    return card(item, i, O, P, current, {
      head: [label(item.kind === "disputed" ? "readings differ" : "read " + (item.status || item.kind), O.tone(item, P)), item.aspect ? label(aspectOf(item)) : ""],
      rows: [c.author ? fact("Read by", O.author(c.author)) : "", next],
      ids: [c.judgment, ...(item.judgments || [])].filter(Boolean),
    });
  }

  // What waits on a review: what was proposed, by whom and why, and what
  // it waits for first.
  function waitingCard(item, i, O, P, current) {
    const p = item.proposal || {};
    const waits = item.waits ? fact("Waits", O.prose(capital(item.waits))) : "";
    const by = (name) => ' <span class="i-by">' + (isPerson(name) ? "by you" : "by " + O.author(name)) + "</span>";
    if (item.proposed === "ruling") {
      return card(item, i, O, P, current, { head: [label("proposed ruling", P.open("unsupported").colour), label(p.ruling || "")], rows: [fact("Proposed " + O.escape(p.ruling || ""), O.escape(p.reason || "") + by(p.author)), waits], ids: [p.id].filter(Boolean) });
    }
    if (item.proposed === "reading") {
      return card(item, i, O, P, current, { head: [label("proposed reading", O.tone(item, P)), label(aspectOf(item)), label(p.verdict || "")], rows: [fact("Proposed " + O.escape(p.verdict || ""), O.escape(p.evidence || "") + by(p.author)), waits], ids: [p.id].filter(Boolean) });
    }
    return card(item, i, O, P, current, {
      head: [label("severity " + item.severity, P.severity(item.severity).colour), label(aspectOf(item)), label("open")],
      rows: [fact("Proposed " + O.escape(p.status || ""), O.escape(p.note || "") + by(p.author)), waits],
      ids: [item.finding].filter(Boolean),
    });
  }

  // A section, drawn as Open draws a family: its dot, its head and its
  // count; a sentence; its body.
  function section(key, dot, heading, count, sentence, body, open) {
    return '<details class="card-sec open-group check-none" data-fold="' + key + '"' + (open ? " open" : "") + '><summary class="sec-head"><span class="chev"></span>' + (dot ? '<i class="h-dot" style="background:' + dot + '"></i>' : "") + '<span class="title">' + heading + '</span><span class="count">' + count + "</span></summary>" +
      (sentence ? '<p class="e-sentence">' + sentence + "</p>" : "") + '<div class="sec-body">' + body + "</div></details>";
  }

  // A part of what waits, drawn as Open draws a kind.
  function part(key, heading, members, sentence, rows, open) {
    return '<details class="e-kindpart check-none" data-fold="' + key + '"' + (open ? " open" : "") + '><summary class="e-kindhead"><span class="chev"></span><span class="e-kindtitle">' + heading + '</span><span class="count">' + members.length + "</span></summary>" +
      (sentence ? '<p class="e-sentence">' + sentence + "</p>" : "") + '<ul class="i-cards">' + rows + "</ul></details>";
  }

  // The tab. `current` is the item clicked, by its index in `items`;
  // `folds`, the sections and parts the reader opened or shut; `lapsed`,
  // how many lapsed judgments Open lists.
  function html(report, all, O, P, current, folds = {}, { lapsed = null } = {}) {
    if (!report) return '<p class="meta">Reading the inbox…</p>';
    if (report.error) return '<p class="meta">the inbox could not be read: ' + O.escape(report.error) + "</p>";
    const opened = folds.opened || new Set();
    const shut = folds.shut || new Set();
    const of = (key) => all.map((item, i) => (item.inbox === key ? i : -1)).filter((i) => i >= 0);
    const waiting = of(WAITING);
    const n = all.length - waiting.length;
    const out = ['<div class="tab-head"><div class="eyebrow">Inbox</div><p class="e-counts"><span class="e-n">' + O.escape(plural(n, "decision", "decisions")) + "</span>" +
      (waiting.length ? " · " + waiting.length + " waiting on a review" : "") + "</p>" +
      '<p class="tab-lede">What only you can decide at state ' + O.escape(String(report.state || "").slice(0, 8)) + ". Each card ends with its next step; select one to read it beside the paper.</p></div>"];
    const holds = (members) => members.includes(current);

    // a section per kind of decision, open unless shut
    for (const [key, words] of Object.entries(SECTIONS)) {
      if (key === WAITING) continue;
      const members = of(key);
      if (!members.length) continue;
      const rows = members.map((i) => decisionCard(all[i], i, O, P, i === current)).join("");
      const fold = "d:" + key;
      out.push(section(fold, O.tone(all[members[0]], P), O.escape(words.title), members.length, O.escape(words.asks), '<ul class="i-cards">' + rows + "</ul>", holds(members) || !shut.has(fold)));
    }
    if (!n) out.push('<p class="meta">Nothing for you to decide at this state.</p>');

    // what waits on a review: folded, by what was proposed
    if (waiting.length) {
      const kinds = WAITS.filter((w) => waiting.some((i) => waitOf(all[i]) === w.key));
      const parts = kinds.map((w) => {
        const members = waiting.filter((i) => waitOf(all[i]) === w.key);
        const fold = "w:" + w.key;
        const rows = members.map((i) => waitingCard(all[i], i, O, P, i === current)).join("");
        return part(fold, O.escape(w.title), members, w.sentence, rows, holds(members) || opened.has(fold) || (kinds.length === 1 && !shut.has(fold)));
      }).join("");
      out.push(section("s:waiting", "", O.escape(SECTIONS[WAITING].title), waiting.length, O.escape(SECTIONS[WAITING].asks), parts, holds(waiting) || opened.has("s:waiting")));
    }

    // the lapsed judgments: the next review's, counted, a link to Open
    if (lapsed) {
      out.push('<ul class="k-gates i-lapsed"><li class="k-gate k-linked" data-open-kind="lapsed" title="Click: the lapsed judgments in Open">' +
        '<div class="k-head"><i class="h-dot" style="background:' + P.open("lapsed").colour + '"></i><span class="k-title">' + O.escape(capital(P.open("lapsed").label)) + '</span><span class="k-go">in Open ›</span><span class="count">' + lapsed + "</span></div>" +
        '<p class="e-sentence">Readings whose basis changed since they were made. The next review asks them again; none is yours to decide.</p></li></ul>');
    }
    return out.join("");
  }

  return { SECTIONS, WAITS, items, decisions, html };
});
