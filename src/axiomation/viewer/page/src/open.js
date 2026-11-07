// The open view: `axiomation open`'s list, one folded group per kind, as
// the check tab folds its gates, each in the tone of what the gates do
// with its items (blocks, warns, in no gate: `check.kindTones`), its items
// grouped inside it by section, role, reading or block, and walked with
// the walk's keys; an open finding in its severity's tone, a
// lapsed one under `lapsed`, its line a finding's; what the kept
// graph got wrong or cannot settle, its four kinds; what a rule added
// since the state was kept refuses in its own files; and the
// query's notes above the list. The list is the query's: a kind is added
// to `queries.open_report`, never here.
//
// Pure: the items in order, their groups, the walk and the HTML; the page
// wires them.
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else (root.AxView = root.AxView || {}).open = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // `queries.OpenKind`, in its order; a report's section is the kind with
  // `_` for `-`.
  const KINDS = ["unsupported", "circle", "uses-unsupported", "partial", "flag", "stale", "carried-unread", "pending", "lapsed", "disputed", "negative", "finding", "suggestion", "retracted", "publication", "unplaced", "unjudged", "inconclusive", "provisional", "unchecked", "unread", "dangling-reference", "duplicate-label", "unused-premise", "undeclared-assumption", "later-rule"];
  // What groups the items inside a kind or a gate (`queries.GroupBy`, and
  // the block): the item's field, `none` not at all. A claim's reading is
  // its `true`'s; an item that is no claim groups by its status.
  const GROUP_BY = {
    none: null,
    section: "top_section",
    role: "roles",
    reading: "reading",
    block: "handle",
  };
  const PAPER = "(the paper)";
  const NONE = { role: PAPER, reading: "(none)", block: PAPER };
  const GONE = "(no longer in the paper)";

  // The kinds read by block where nothing else groups them: several
  // lapsed judgments, or claims standing unsupported, on one block.
  const BY_BLOCK = ["unsupported", "lapsed"];

  // Every item of a report, in its kinds' order, each `{...data, line}`;
  // then `extra`, an overlay's open items (kind `overlay:NAME`), which the
  // product does not know and the page adds.
  const OVERLAY = "overlay:";
  function items(report, extra = []) {
    const found = [];
    for (const kind of KINDS) {
      const section = report[kind.replace(/-/g, "_")];
      if (!section) continue;
      section.items.forEach((data, i) => found.push({ ...data, line: section.lines[i] }));
    }
    return found.concat(extra);
  }

  function counts(all) {
    const found = Object.fromEntries(KINDS.map((k) => [k, 0]));
    for (const item of all) if (item.kind in found) found[item.kind] += 1;
    return found;
  }

  // An item's key under `by`: its field, else what stands for none.
  function keyOf(item, by) {
    const field = GROUP_BY[by];
    if (!field) return null;
    const value = item[field] || (by === "reading" ? item.status : null);
    if (value) return String(value);
    if (by === "section") return item.block ? "(before any section)" : PAPER;
    // a judgment whose subject the state no longer holds
    if (by === "block" && item.kind === "lapsed") return GONE;
    return NONE[by];
  }

  // `members` (indices into `all`) grouped by `by`, each group `{label,
  // members}` in the order its first item comes, the paper's own last;
  // `none`, one group with no label.
  function subgroups(all, members, by) {
    if (!GROUP_BY[by]) return [{ label: null, members: members.slice() }];
    const found = new Map();
    for (const i of members) {
      const key = keyOf(all[i], by);
      if (!found.has(key)) found.set(key, []);
      found.get(key).push(i);
    }
    const last = (key) => (key === PAPER || key === NONE[by] || key === GONE ? 1 : 0);
    return [...found.entries()]
      .sort(([a], [b]) => last(a) - last(b))
      .map(([label, list]) => ({ label, members: list }));
  }

  // The groups: one per kind with items, in the kinds' order, then an
  // overlay's own; each `{key, label, members, parts}`, `parts` its
  // members grouped by `by`.
  function groups(all, by = "none") {
    const found = new Map();
    all.forEach((item, i) => {
      if (!found.has(item.kind)) found.set(item.kind, []);
      found.get(item.kind).push(i);
    });
    const rank = (key) => (KINDS.includes(key) ? KINDS.indexOf(key) : KINDS.length);
    return [...found.entries()]
      .sort(([a], [b]) => rank(a) - rank(b) || String(a).localeCompare(String(b)))
      .map(([key, members]) => ({
        key,
        label: String(key).startsWith(OVERLAY) ? String(key).slice(OVERLAY.length) + " (overlay)" : key,
        members,
        // a lapsed judgment is read with the others on its block
        parts: by === "none" && BY_BLOCK.includes(key) ? subgroups(all, members, "block").map((p) => ({ ...p, byBlock: true })) : subgroups(all, members, by),
      }));
  }

  // The kinds in three families, by where an item comes from, each with
  // what it holds in a sentence: what the graph's structure shows the
  // argument lacks (the text or the graph may be what is wrong: an
  // undeclared premise is the statement's gap or an edge missed), what a
  // reading or a review found in the text, and what a change left to read
  // again. The readings are read by block where nothing else groups them:
  // a negative reading and the finding that says why sit together.
  // `quiet`: a family whose sentence says nothing in it says the paper is
  // wrong; its items mark the gutter grey, whatever their gate does.
  const FAMILIES = [
    { key: "structure", title: "Structure", sentence: "What the graph shows the argument lacks: a claim without an argument, a premise unnamed or unused, a reference with no target. The text or the graph may be what needs fixing.", kinds: ["unsupported", "circle", "uses-unsupported", "partial", "undeclared-assumption", "unused-premise", "dangling-reference", "duplicate-label", "flag", "later-rule", "unread"] },
    { key: "readings", title: "Readings", sentence: "What a reading or a review found in the text, and works it suggests citing.", kinds: ["negative", "disputed", "finding", "suggestion"], byBlock: true },
    { key: "cited", title: "Cited works", sentence: "What the paper cites that a referee would question, and citations or quotations no one block holds.", kinds: ["retracted", "publication", "unplaced"] },
    { key: "again", quiet: true, title: "To read again", sentence: "Readings and lines a change left behind; the next review or update reads them again. Nothing here says the paper is wrong.", kinds: ["lapsed", "stale", "carried-unread", "pending"] },
    { key: "unreviewed", quiet: true, title: "Not yet reviewed", sentence: "What no review has settled: never read, read without a conclusion, or read off a copy that is not the version of record. The next review reads them; nothing here says the paper is wrong.", kinds: ["unjudged", "inconclusive", "provisional", "unchecked"] },
  ];

  // What each kind holds, in a sentence under its head; the help says
  // more.
  const KIND_SENTENCES = {
    unsupported: "Claims with no argument in the graph and no ruling that the paper owes none.",
    circle: "Claims that each rest on the others, so none is argued from outside the circle.",
    "uses-unsupported": "Blocks that use an unsupported claim directly: what rests on a gap.",
    partial: "Claims the paper argues only as a sketch or by analogy.",
    "undeclared-assumption": "Claims whose justification rests on a premise their statement does not name.",
    "unused-premise": "Assumptions nothing uses and that qualify nothing.",
    "dangling-reference": "References to a label no block holds.",
    "duplicate-label": "Labels two or more blocks hold: every reference reads the first.",
    flag: "Doubts a stage or a rule raised on the graph that no correction decided.",
    "later-rule": "Lines written before a check was added, which that check now refuses.",
    negative: "Readings that found a claim or a step not shown, or contradicted.",
    disputed: "Readings by different authors that differ, or a severe finding against a held reading.",
    finding: "Problems a review found in the text, not yet answered.",
    suggestion: "Works a review suggests citing here, not yet taken or declined.",
    lapsed: "Readings whose basis changed since they were made; the next review asks them again.",
    stale: "Lines an update marked because their far end changed, unread since.",
    "carried-unread": "Blocks an update moved by the rule without reading them again.",
    pending: "An edit no state applied yet, or a question a run left unanswered.",
    retracted: "Retracted works the paper cites, on every block that cites them.",
    publication: "Cited works a referee would question as ground: preprints, theses, reports, works with an erratum.",
    unplaced: "Citations or quotations whose words stand in no one block.",
    unjudged: "What a review reads that no reading has judged yet; the next review reads it.",
    inconclusive: "Readings that could not tell; the works they could not read are named.",
    provisional: "Readings that hold, but read off a copy that is not the version of record.",
    unchecked: "Claims ruled positioning that no contribution check has read yet.",
    unread: "Kept files today's reader could not read, so their labels or quotations went unchecked.",
  };

  // The families with items, in their order, each `{key, title, sentence,
  // members, kinds, parts}`: `kinds` its kinds' groups (`groups`), `parts`
  // its items by block across its kinds where it is read so, else null;
  // then an overlay's group each, a family of its own.
  function families(all, by = "none") {
    const kinds = groups(all, by);
    const out = [];
    for (const f of FAMILIES) {
      const held = kinds.filter((g) => f.kinds.includes(g.key)).sort((a, b) => f.kinds.indexOf(a.key) - f.kinds.indexOf(b.key));
      if (!held.length) continue;
      const members = held.flatMap((g) => g.members);
      const parts = f.byBlock && by === "none" ? subgroups(all, members, "block").map((p) => ({ ...p, byBlock: true })) : null;
      out.push({ key: f.key, title: f.title, sentence: f.sentence, members, kinds: held, parts });
    }
    for (const g of kinds) if (!FAMILIES.some((f) => f.kinds.includes(g.key))) out.push({ key: g.key, title: g.label, sentence: "", members: g.members, kinds: [g], parts: null });
    return out;
  }

  // The items in the order the list shows them: what the walk's keys walk;
  // the families' or the kinds' (`groups`).
  function order(shown) {
    return shown.flatMap((g) => (g.kinds ? (g.parts || g.kinds.flatMap((k) => k.parts)) : g.parts).flatMap((p) => p.members));
  }

  // The walk: the next (`step` 1) or previous (-1) item shown after `at`,
  // around the end; the first or last where nothing is current.
  function walk(shown, at, step) {
    if (!shown.length) return null;
    const here = shown.indexOf(at);
    if (here < 0) return step > 0 ? shown[0] : shown[shown.length - 1];
    return shown[(here + step + shown.length) % shown.length];
  }

  function escape(text) {
    return String(text).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  }

  function causeWords(item) {
    const cause = item.cause;
    if (cause === null || cause === undefined || cause === "") return "";
    if (Array.isArray(cause)) {
      return cause.map((c) => (typeof c === "string" ? c : c.why + (c.via ? " (via " + c.via + ")" : ""))).join("; ");
    }
    if (typeof cause === "object") {
      if (cause.why) return cause.why;
      // a cause without a why in words, field by field: never as JSON
      return Object.entries(cause).filter(([, v]) => v !== null && v !== undefined && v !== "" && v !== false)
        .map(([k, v]) => k + " " + (Array.isArray(v) ? v.join(", ") : String(v))).join("; ");
    }
    return String(cause);
  }

  // An item's dot: an overlay's own colour; an open finding's its
  // severity's tone; else its kind's.
  function tone(item, P) {
    if (item.colour) return item.colour;
    if (item.kind === "finding" && item.severity) return P.severity(item.severity).colour;
    return P.open(item.kind).colour;
  }

  function said(item, cause) {
    const line = String(item.line);
    if (line.includes(cause)) return true;
    const c = item.cause;
    if (!c || typeof c !== "object" || Array.isArray(c) || c.why) return false;
    const values = Object.values(c).flat().filter((v) => v !== null && v !== undefined && v !== "" && typeof v !== "boolean");
    return values.every((v) => line.includes(String(v)));
  }

  // An item's row: its dot, its line, then its cause, what clears it and
  // since when. `attr` names the index's attribute (the check view's rows
  // are not the open view's); `colour` and `cause` override the item's own
  // tone and cause words. A cause the line already says is not repeated: as
  // a whole, or field by field where it has no why (a duplicate label's
  // label and holders are its line's own words).
  function row(item, i, P, { current = false, attr = "data-item", colour = tone(item, P), cause = causeWords(item) } = {}) {
    return '<li class="open-item' + (current ? " current" : "") + '" ' + attr + '="' + i + '">' +
      '<i class="h-dot" style="background:' + colour + '" title="' + escape(item.kind + (item.severity ? ", severity " + item.severity : "")) + '"></i> ' +
      '<code class="sheet' + (item.unanchored ? " unanchored" : "") + '">' + escape(item.line) + "</code>" +
      (item.unanchored ? '<div class="why"><b>unanchored</b> ' + escape(item.unanchored) + "</div>" : "") +
      (cause && !said(item, cause) ? '<div class="why"><b>cause</b> ' + escape(cause) + "</div>" : "") +
      (item.clears ? '<div class="why"><b>clears</b> ' + escape(item.clears) + "</div>" : "") +
      (item.since ? '<div class="why"><b>since</b> ' + escape(item.since) + "</div>" : "") + "</li>";
  }

  // The "group by" control's options, shared by the open view and the
  // check tab.
  function byOptions(by) {
    return Object.keys(GROUP_BY).map((k) => '<option value="' + k + '"' + (k === by ? " selected" : "") + ">" + k + "</option>").join("");
  }

  // A group's items, under a line per part where they are grouped.
  function parts(all, groupParts, draw) {
    const out = [];
    for (const part of groupParts) {
      if (part.label !== null) out.push('<li class="open-sub">' + escape(part.label) + ' <span class="meta">' + part.members.length + "</span></li>");
      for (const i of part.members) out.push(draw(all[i], i));
    }
    return out.join("");
  }

  // What a kind's tone says: the gates that list its items and their
  // outcome, or none.
  function toneWords(t) {
    if (!t) return "";
    return t.gates.length ? " · " + t.gates.join(", ") + " → " + t.tone : " · in no gate";
  }

  // A text in words, its `code` spans set as code.
  function prose(text) {
    return escape(text).replace(/`([^`]+)`/g, "<code>$1</code>");
  }

  // An author by the first word of its name, the whole on hover.
  // An author as the page names it: a person by name (`person` unnamed),
  // an agent by its model, its host and the host's version on hover (an
  // agent's name is `host version model`).
  function shortName(whole) {
    const name = String(whole);
    if (name === "person") return "a person";
    if (name.startsWith("person ")) return name.slice("person ".length);
    const words = name.split(" ");
    return words[words.length - 1];
  }

  function author(name) {
    const whole = String(name);
    return '<span class="r-by" title="' + escape(whole) + '">' + escape(shortName(whole)) + "</span>";
  }

  // An aspect in words, as the Block tab names it.
  const ASPECTS = { valid: "validity", true: "truth", accurate: "accuracy", cited: "citation", "well-posed": "well-posedness", computed: "computation", presentation: "presentation", significance: "significance" };

  // A passage's markup as it reads: bold, emphasis, a citation by its keys;
  // the formulas are the page's to render (`maths.withMaths`).
  function styled(html) {
    return html
      .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
      .replace(/\\(?:emph|textit)\{([^{}]*)\}/g, "<i>$1</i>")
      .replace(/\\textbf\{([^{}]*)\}/g, "<b>$1</b>")
      .replace(/\\[a-zA-Z]*cite[a-zA-Z]*\*?(?:\[[^\]]*\])*\{([^{}]*)\}/g, '<span class="e-cite">[$1]</span>');
  }

  // The passage an item is about, its markup styled: its anchor's words,
  // else what its line says in place of them.
  function passage(item) {
    const words = passageWords(item);
    // a justification's validity is read as arguing another block's claim:
    // the words are that claim's
    if (item.aspect === "valid" && item.target && words) return '<span class="e-ctx">argues for</span> <span class="e-ref">' + escape(item.target) + "</span>: " + styled(words);
    return styled(words);
  }

  function passageWords(item) {
    // a finding is read by its problem, not the span it is on
    if (item.kind === "finding" && typeof item.cause === "string") return escape(item.cause);
    if (item.kind === "carried-unread" && item.cause && Array.isArray(item.cause.blocks)) {
      return item.cause.blocks.length + " blocks carried unread by update " + escape(String(item.cause.since || "").slice(0, 8));
    }
    const words = item.anchor && item.anchor.words;
    if (words) return inContext(item.line, words) || escape(words);
    if (item.kind === "uses-unsupported" && Array.isArray(item.cause)) return "uses " + item.cause.map((h) => '<span class="e-ref">' + escape(h) + "</span>").join(", ");
    // an unused premise is its block alone: its kind's head says the rest
    if (item.kind === "unused-premise") return "";
    if (!item.block || !item.anchor || item.anchor.kind === "paper") return escape(String(item.line).split("\n")[0]);
    return "";
  }

  // A part's words as its line quotes them, the words around them in
  // brackets (`"[This essay] presents … [: …]"`): the context set grey;
  // null where the line quotes them otherwise.
  const CONTEXT = 60;
  function inContext(line, words) {
    const first = String(line || "").split("\n")[0];
    const open = first.indexOf(' "[') >= 0 ? first.indexOf(' "[') + 1 : first.indexOf('· "') >= 0 ? first.indexOf('· "') + 2 : -1;
    if (open < 0) return null;
    // the quote ends right after the words or after the context that
    // follows them: the words may hold a quotation mark, the line goes on
    const found = first.indexOf(words, open + 1);
    if (found < 0) return null;
    let end = found + words.length;
    if (first.startsWith(" [", end)) end = first.indexOf(']"', end) + 1;
    if (end <= 0 || first[end] !== '"') return null;
    const quoted = first.slice(open + 1, end);
    if (!quoted.includes("[")) return null;
    // the context before the words kept to its end, after them to its start
    const at = quoted.indexOf(words);
    // cut at a space, so no word is broken
    const cut = (text, keepEnd) => {
      if (text.length <= CONTEXT) return text;
      const part = keepEnd ? text.slice(-CONTEXT) : text.slice(0, CONTEXT);
      const space = keepEnd ? part.indexOf(" ") : part.lastIndexOf(" ");
      const whole = space < 0 ? part : keepEnd ? part.slice(space + 1) : part.slice(0, space);
      // a command the cut left open (`\\autocite{csi`) is dropped whole
      return keepEnd ? "…" + whole.replace(/^[^{\\]*\}/, "") : whole.replace(/\\[a-zA-Z]*\*?(?:\[[^\]]*\]?)*(?:\{[^}]*)?$/, "").trimEnd() + "…";
    };
    // a bold mark the cut left without its pair is dropped
    const paired = (text) => (text.split("**").length % 2 === 0 ? text.replace(/\*\*/g, "") : text);
    const ctx = (text, keepEnd) => text.replace(/\[([^\]]*)\]/g, (_, inner) => "\u0000" + paired(cut(inner, keepEnd)) + "\u0001");
    const shown = ctx(quoted.slice(0, at), true) + quoted.slice(at, at + words.length) + ctx(quoted.slice(at + words.length), false);
    return escape(shown).replace(/\u0000/g, '<span class="e-ctx">').replace(/\u0001/g, "</span>");
  }

  // One line of why: what the item's status is, in the words its kind
  // needs; nothing where the passage says it.
  const aspectOf = (item) => (item.aspect ? escape(ASPECTS[item.aspect] || item.aspect) + ": " : "");

  function reason(item) {
    const c = item.cause;
    switch (item.kind) {
      case "unsupported":
        return item.reading ? "truth: " + escape(item.reading) : "";
      case "lapsed": {
        if (!c || typeof c !== "object" || !c.why) return "";
        // a block's own text changing is "its text changed"; the identity
        // class is said where it is more than a change
        const own = item.handle ? String(c.why).replace(item.handle + "'s ", "its ") : String(c.why);
        return escape(own.replace(/ \(changed\)$/, "")) + (c.state ? ' <span class="e-at">at ' + escape(String(c.state).slice(0, 8)) + "</span>" : "");
      }
      case "negative":
        return aspectOf(item) + escape(item.status || "") + (c && c.author ? ", read by " + author(c.author) : "");
      case "disputed":
        return aspectOf(item) + "readings differ";
      // a gate's reasons for what a review has not settled, as the check
      // tab's link lists them
      case "not-shown":
      case "contradicted":
        return aspectOf(item) + escape(item.kind) + (c && c.author ? ", read by " + author(c.author) : "");
      case "unjudged":
        return aspectOf(item) + "no reading yet";
      case "inconclusive":
        return aspectOf(item) + "inconclusive" + (c && c.unread && c.unread.length ? "; could not read " + c.unread.map((w) => escape(w.key)).join(", ") : "");
      case "provisional":
        return aspectOf(item) + "verified off a copy that is not the version of record";
      case "unchecked":
        return "no contribution check reads it yet";
      case "finding":
        return escape(item.status || "") + (item.proposal ? " · proposed " + escape(item.proposal.status) : "");
      case "undeclared-assumption":
        return c && c.why ? escape(c.why) : "";
      case "unused-premise":
      case "uses-unsupported":
      case "carried-unread":
        return "";
      default:
        return escape(causeWords(item));
    }
  }

  // What clears an item, its own ids said as "its id", so one sentence
  // reads for every item of a kind.
  function clearsOf(item) {
    let text = String(item.clears || "");
    for (const id of [...(item.judgments || []), item.finding].filter(Boolean)) text = text.split(id).join("its id");
    return text;
  }

  // The sentence most of `members` share, and how many share it.
  function shared(all, members) {
    const found = new Map();
    for (const i of members) {
      const text = clearsOf(all[i]);
      if (text) found.set(text, (found.get(text) || 0) + 1);
    }
    let best = null;
    for (const [text, n] of found) if (!best || n > best.n) best = { text, n };
    return best && best.n > 1 ? best : null;
  }

  // An item's row: its block (left out where its group is the block),
  // its kind where the list mixes kinds, its passage and one line of why,
  // and what clears it where its group's head does not say so. `step`,
  // where given, is the item's next step, said first in place of what
  // clears it (empty: its group's head says it).
  // `gate`, where given, is the tone of what the gates do with its kind:
  // its card's border, where its list mixes kinds.
  function entry(item, i, P, { current = false, attr = "data-item", name = true, kind = false, said = null, colour = tone(item, P), label = item.kind in P.OPEN ? P.open(item.kind).label : item.kind, opened = new Set(), because = null, step = null, blockLink = true, gate = null } = {}) {
    const why = because === null ? reason(item) : because;
    const words = passage(item);
    const clears = step === null ? clearsOf(item) : "";
    const lead = [
      kind ? '<span class="e-kind" style="color:' + colour + '">' + escape(label) + "</span>" : "",
      name && item.handle ? '<span class="e-name">' + escape(item.handle) + "</span>" : "",
      name && item.section ? '<span class="e-where">' + escape(item.section) + "</span>" : "",
    ].filter(Boolean).join(" ");
    return '<li class="open-item e-row' + (current ? " current" : "") + (gate ? " check-" + gate : "") + '" ' + attr + '="' + i + '">' +
      (item.block && blockLink ? '<a class="e-open" data-open-block title="select it and open its detail on the Block tab">Block ›</a>' : "") +
      (lead ? '<div class="e-lead">' + lead + "</div>" : "") +
      (step ? '<div class="e-why e-next">next: ' + prose(step) + "</div>" : "") +
      (words ? '<div class="e-words' + (item.unanchored ? " unanchored" : "") + '">' + words + "</div>" : "") +
      (item.unanchored ? '<div class="e-why">unanchored: ' + escape(item.unanchored) + "</div>" : "") +
      (why ? '<div class="e-why">' + why + "</div>" : "") +
      (clears && clears !== said ? '<div class="e-why e-clears">clears: ' + prose(clears) + "</div>" : "") +
      (item.kind === "carried-unread" ? carriedBlocks(item, opened.has("c:" + String((item.cause && item.cause.since) || ""))) : "") + "</li>";
  }

  // The blocks the updates carried unread, each once.
  function carried(all, members) {
    return [...new Set(members.flatMap((i) => (all[i].cause && all[i].cause.blocks) || []))];
  }
  function carriedCount(all, members) {
    const n = carried(all, members).length;
    return members.length > 1 && n ? '<p class="e-said">' + n + " blocks in all</p>" : "";
  }

  // The blocks an update carried unread, folded under its row, each a
  // link that selects it; open where the reader opened it.
  function carriedBlocks(item, open) {
    const blocks = (item.cause && item.cause.blocks) || [];
    if (!blocks.length) return "";
    return '<details class="e-blocks" data-fold="c:' + escape(String(item.cause.since || "")) + '"' + (open ? " open" : "") + "><summary>the " + blocks.length + " blocks</summary><div>" +
      blocks.map((h) => '<a class="go" data-ref="' + escape(h) + '">' + escape(h) + "</a>").join(" · ") + "</div></details>";
  }

  // What a kind's head says on the right: its count and what the gates do
  // with its items, the gates by name on hover; nothing of the gates while
  // the verdict is read.
  function outcome(count, t) {
    const words = !t ? "" : t.tone === "fail" ? " · fails a gate" : t.tone === "warn" ? " · warns" : " · in no gate";
    const title = t && t.gates.length ? ' title="' + escape(t.gates.join(", ") + " → " + t.tone) + '"' : "";
    return '<span class="count e-' + (t ? t.tone : "none") + '"' + title + ">" + count + escape(words) + "</span>";
  }

  // A list of items under its head: grouped as `parts` say; where the
  // parts are blocks, each block's name heads its items once.
  function listed(all, groupParts, by, draw, { scope = "", shut = new Set(), current = null } = {}) {
    const blocks = by === "block" || (groupParts.length && groupParts.every((p) => p.byBlock));
    const out = [];
    for (const part of groupParts) {
      // a block with one item: named on its row, no head of its own
      const alone = blocks && part.members.length === 1 && part.label !== GONE;
      const rows = part.members.map((i) => draw(all[i], i, alone || !blocks || part.label === null)).join("");
      if (part.label === null || alone) {
        out.push(rows);
        continue;
      }
      // a part with a head folds; open unless the reader folded it, or
      // while it holds the current item
      const key = "p:" + scope + ":" + part.label;
      const open = !shut.has(key) || part.members.includes(current);
      const where = blocks && all[part.members[0]].section ? ' <span class="e-where">' + escape(all[part.members[0]].section) + "</span>" : "";
      out.push('<li class="e-part"><details data-fold="' + escape(key) + '"' + (open ? " open" : "") + '><summary class="open-sub' + (blocks ? " e-block" : "") + '"><span class="chev"></span>' + escape(part.label) + ' <span class="meta">' + part.members.length + "</span>" + where + "</summary><ul>" + rows + "</ul></details></li>");
    }
    return out.join("");
  }

  // The tab's head: a small label, its count, one sentence.
  function head(label, title, sentence) {
    return '<div class="tab-head"><div class="eyebrow">' + escape(label) + "</div><h3>" + escape(title) + "</h3>" +
      (sentence ? '<p class="tab-lede">' + sentence + "</p>" : "") + "</div>";
  }

  const RANK = { none: 0, warn: 1, fail: 2 };
  // The worst of the kinds' tones, and their gates.
  function worst(kindGroups, tones) {
    if (!tones) return null;
    const out = { tone: "none", gates: [] };
    for (const g of kindGroups) {
      const t = tones[g.key] || { tone: "none", gates: [] };
      if (RANK[t.tone] > RANK[out.tone]) out.tone = t.tone;
      for (const gate of t.gates) if (!out.gates.includes(gate)) out.gates.push(gate);
    }
    return out;
  }

  const capital = (text) => text.charAt(0).toUpperCase() + text.slice(1);
  const kindLabel = (key, group, P) => (String(key).startsWith(OVERLAY) ? group.label : P.open(key).label);

  // What clears most of `members`, said once.
  function clearsHead(all, members) {
    const common = shared(all, members);
    return common ? { text: common.text, html: '<p class="e-said"><span class="row-k">Clears' + (common.n < members.length ? " (" + common.n + " of " + members.length + ")" : "") + "</span> " + prose(common.text) + "</p>" } : null;
  }

  // A kind inside its family: a small head, its count and tone, what
  // clears most of its items, then its items.
  function kindPart(all, group, P, current, by, tones, open, shut, opened) {
    const t = tones && (tones[group.key] || { tone: "none", gates: [] });
    const dot = String(group.key).startsWith(OVERLAY) ? tone(all[group.members[0]], P) : P.open(group.key).colour;
    const common = clearsHead(all, group.members);
    return '<details class="e-kindpart check-' + (t ? t.tone : "none") + '" data-kind="' + escape(group.key) + '" data-fold="k:' + escape(group.key) + '"' + (open ? " open" : "") + '><summary class="e-kindhead" title="kind `' + escape(group.key) + '`"><span class="chev"></span><i class="h-dot" style="background:' + dot + '"></i><span class="e-kindtitle">' + escape(capital(kindLabel(group.key, group, P))) + "</span>" + outcome(group.members.length, t) + "</summary>" +
      (KIND_SENTENCES[group.key] ? '<p class="e-sentence">' + escape(KIND_SENTENCES[group.key]) + "</p>" : "") +
      (group.key === "carried-unread" ? carriedCount(all, group.members) : "") + (common ? common.html : "") +
      "<ul>" + listed(all, group.parts, by, (item, i, name) => entry(item, i, P, { current: i === current, name, said: common && common.text, opened }), { scope: group.key, shut, current }) + "</ul></details>";
  }

  // The view: its head, `lead` (the walks), the group-by, the query's
  // notes (where it could not read what a kind needs), then a folded
  // section per family (`families`), open where `opened` names it or it
  // holds the current item, its head its title, count and the worst of
  // what the gates do with its kinds (`tones`, from the verdict: null while
  // it is read), its sentence under it; inside, each kind with its count
  // and tone, what clears most of its items, then its items; a family read
  // by block lists its items so, each with its kind.
  function html(all, shown, P, current, by, opened = new Set(), tones = null, notes = [], lead = "", folds = {}) {
    const kindsOpen = folds.opened || new Set();
    const shut = folds.shut || new Set();
    const out = ['<div class="tab-head"><div class="eyebrow">Open items</div><p class="e-counts"><span class="e-n" title="every item open at this state">○ ' + all.length + " open</span>" + (lead ? " · " + lead : "") + "</p>" +
      '<p class="tab-lede">Everything not yet settled at this state. Select one to read it beside the paper; Block › opens its detail.</p></div>',
      ""];
    for (const note of notes) out.push('<p class="open-note"><b>note</b> ' + escape(note) + "</p>");
    if (!shown.length) out.push('<p class="meta">Nothing is open.</p>');
    for (const family of shown) {
      const t = worst(family.kinds, tones);
      const holds = family.members.includes(current);
      out.push('<details class="card-sec open-group check-' + (t ? t.tone : "none") + '" data-family="' + escape(family.key) + '"' + (opened.has(family.key) || holds ? " open" : "") + ">" +
        '<summary class="sec-head"><span class="chev"></span><span class="title">' + escape(capital(family.title)) + "</span>" + outcome(family.members.length, t) + "</summary>" +
        (family.sentence ? '<p class="e-sentence">' + escape(family.sentence) + "</p>" : "") + '<div class="sec-body">');
      if (family.parts) {
        out.push('<ul class="e-mixed">' + listed(all, family.parts, by, (item, i, name) => entry(item, i, P, { current: i === current, name, kind: true, gate: tones ? (tones[item.kind] || { tone: "none" }).tone : null }), { scope: family.key, shut, current }) + "</ul>");
      } else {
        // a kind folds; open where it is its family's only one, or the
        // reader opened it, or it holds the current item
        for (const group of family.kinds) {
          const key = "k:" + group.key;
          const open = group.members.includes(current) || kindsOpen.has(key) || (!shut.has(key) && family.kinds.length === 1);
          out.push(kindPart(all, group, P, current, by, tones, open, shut, kindsOpen));
        }
      }
      out.push("</div></details>");
    }
    return out.join("");
  }

  // One gate's items alone, as the check tab's link shows them: its head
  // names the gate and the way back to the whole list; its items in the
  // check's order (`at`, their index there), by kind as Open draws a
  // kind: a fold each, its dot, label, count and a sentence (`sentences`,
  // by kind, the check's), open where it is the gate's only kind, the
  // reader opened it, or it holds the current item.
  function gateHtml(gate, at, P, current, { toneOf = (item) => tone(item, P), sentences = {}, folds = {} } = {}) {
    const opened = folds.opened || new Set();
    const shut = folds.shut || new Set();
    const all = [];
    gate.items.forEach((item, i) => { all[at + i] = item; });
    const kinds = new Map();
    gate.items.forEach((item, i) => {
      const key = item.lapsed && item.kind !== "lapsed" ? "lapsed " + item.kind : item.kind;
      if (!kinds.has(key)) kinds.set(key, []);
      kinds.get(key).push(at + i);
    });
    const t = gate.outcome === "fail" ? "fails" : gate.outcome === "warn" ? "warns" : gate.outcome;
    const out = [head("Open items · one gate", capital(gate.gate.replace(/-/g, " ")), escape(gate.count + " " + (gate.count === 1 ? "item" : "items") + " · the gate " + t) + ' · <a class="e-all" data-gate-all>show all open items</a>'),
      ""];
    const ordered = [...kinds].sort((x, y) => y[1].length - x[1].length);
    for (const [key, members] of ordered) {
      const first = all[members[0]];
      const fold = "g:" + gate.gate + ":" + key;
      const open = members.includes(current) || opened.has(fold) || (!shut.has(fold) && ordered.length === 1);
      const label = first.kind in P.OPEN ? P.open(first.kind).label : key;
      const sentence = sentences[first.kind] || KIND_SENTENCES[first.kind] || "";
      const common = clearsHead(all, members);
      out.push('<details class="e-kindpart check-' + escape(gate.outcome) + '" data-fold="' + escape(fold) + '"' + (open ? " open" : "") + '><summary class="e-kindhead" title="' + escape("cause `" + key + "`") + '"><span class="chev"></span><i class="h-dot" style="background:' + toneOf(first) + '"></i><span class="e-kindtitle">' + escape(capital(label)) + '</span><span class="count">' + members.length + "</span></summary>" +
        (sentence ? '<p class="e-sentence">' + escape(sentence) + "</p>" : "") + (common ? common.html : "") +
        "<ul>" + members.map((i) => entry(all[i], i, P, { current: i === current, attr: "data-check", said: common && common.text })).join("") + "</ul></details>");
    }
    return out.join("");
  }

  return { KINDS, GROUP_BY, author, shortName, OVERLAY, items, counts, keyOf, subgroups, groups, order, walk, escape, causeWords, tone, row, byOptions, parts, families, FAMILIES, KIND_SENTENCES, styled, prose, passage, inContext, reason, clearsOf, carried, carriedBlocks, shared, entry, html, gateHtml };
});
