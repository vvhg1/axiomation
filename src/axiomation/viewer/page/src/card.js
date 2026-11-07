// The card: `axiomation show`'s answer for
// the selection: its header (on a block a review judges nothing on, what
// a review does with it and when one last read it; never its words, which
// the paper beside it holds), then each section of the report
// with its count, one that folds: each claim under its status
// (`claim_status`), its rows, the findings on its words and its history
// folded; its edges as rows read from the selection's side, the block's
// own judgments and findings as rows of their facts, the others as the
// query prints its lines, every handle a link. The history is the steps
// where something happened to it; the record, last, its ids and commands.
//
// Pure: `html` from the report and the state's handles.
(function (root, factory) {
  const node = typeof module === "object" && module.exports;
  const api = factory(node ? require("./markup.js") : root.AxView.markup);
  if (node) module.exports = api;
  else (root.AxView = root.AxView || {}).card = api;
})(typeof self !== "undefined" ? self : this, function (MK) {
  "use strict";

  // The report's sections, in the card's order: each title, and what it
  // holds in a few words beside it.
  // The review's sections first, then the graph's (a reviewer
  // scrolled past the edges on every block).
  // A section opens where it asks something of the reader now; one read
  // only to look something up folds, its head saying what it holds. Folded
  // always: the rulings on words the block no longer holds (a claim's own
  // are in its history), what rests on it, its history and the record.
  // Open only while one is open: the findings, the suggestions, the stale
  // marks, and the judgments while one is anything but unjudged or held.
  // The reader's own fold or unfold of a section outranks both.
  const FOLDED = ["rulings", "rested_on_by", "history", "record"];
  const WHILE_OPEN = ["findings", "suggestions", "judgments", "marks"];

  const GRAPH_HOLDS = "what the graph's own checks raise here: a label other blocks hold too, a doubt a stage raised that no correction decided, a line a newer rule refuses";
  const WAITING_HOLDS = "what a run left to read again and no card section holds: the next review or update reads it";
  const NO_OPEN = { settle: 0, waiting: 0, elsewhere: [], rows: {}, flawed: new Map(), unread: [] };

  // A block a review judges nothing on, said plainly: what it is, that a
  // review files a finding on it only on a problem, how many, and when a
  // review last read it (the record's line, where one did). A block a
  // group's readings judge keeps the record's words.
  function noClaim(nc) {
    const lines = String(nc.line || "").split("\n");
    if (nc.judged_with) return lines;
    const role = (nc.roles || [])[0] || "block";
    const a = /^[aeiou]/.test(role) ? "An " : "A ";
    const when = (/only when (.+?) \(/.exec(lines[0]) || [])[1] || "it finds a problem in it";
    return [
      a + role + ": no claim to judge; a review files a finding on it only when " + when + " (" + (nc.findings ? nc.findings + " filed" : "none filed") + ")" +
        (nc.last_read ? "." : "; no review has read it."),
      ...(nc.last_read ? [lines[lines.length - 1]] : []),
    ];
  }
  // The block's status: a sign, and a hint where something is open: how
  // many of its claims are established, else its first problem's name;
  // the unsupported claims it relies on; how many items are open, a link
  // to the first card. A tick alone where
  // everything holds and is current.
  function statusLine(found, items, roles, ctx, open = 0) {
    const n = found.rows.length;
    const est = found.rows.filter((r) => (r.standing.claim_status || {}).status === "established").length;
    const bad = found.rows.some((r) => ["defect", "unsupported"].includes((r.standing.claim_status || {}).status)) || roles.bad;
    const sign = bad ? "no" : open || est < n || roles.open || (items.elsewhere || []).length ? "open" : "yes";
    const hints = [];
    if (n && sign !== "yes") hints.push(est + "/" + n + " claims");
    else if (!n && roles.first && sign !== "yes") hints.push(escape(roles.first));
    // the unsupported claims it uses, where their own problem is: a link
    // each (hover: its block), never a line that may be its own
    const used = items.elsewhere || [];
    if (used.length) hints.push("relies on " + (used.length === 1 ? go(used[0], "an unsupported claim") : used.length + " unsupported claims: " + used.map((id, k) => go(id, String(k + 1))).join(" ")));
    if (open) hints.push('<a class="ol-go" data-goto="first" title="' + escape([items.settleWhat, items.waitingWhat].filter(Boolean).join("; ")) + '">' + open + " open</a>");
    return '<div class="status-line"><span class="sg a-' + sign + '">' + SIGN[sign] + "</span>" + hints.map((h) => "<span>" + h + "</span>").join('<span class="gsep"> · </span>') + "</div>";
  }
  // What a block does beside its claims, a section each, and whether one
  // of them is open (`open`), or reads against it (`bad`), and the name of
  // its first problem.
  function roleSectionsOf({ report, found, argues, cites, premise, items, ctx }) {
    const out = [];
    const state = { open: false, bad: false, first: "" };
    const section = (key, title, body, count) => out.push(sectionHead(key, title, "", count, !ctx.shut.has(key), "") + body + "</div></details>");
    const judged = (o, label, what) => {
      const j = o.item;
      const sign = j.status === "unjudged" ? "open" : signOf(j);
      if (sign !== "yes") state.open = true;
      if (sign === "no") state.bad = true;
      const list = judgmentWays(j, ctx);
      const name = sign === "no" ? (j.aspect === "valid" ? "argument not shown" : "reading finds against it") : j.status === "lapsed" ? "reading lapsed" : j.aspect === "valid" ? "argument not checked" : "not checked";
      if (sign !== "yes" && !state.first) state.first = name;
      const value = (j.status === "unjudged" ? "not checked" : readingValue(j, ctx));
      const card = list.length ? problemCard(name, j.status === "unjudged" || j.status === "lapsed" ? "needs a reading" : "here",
        sign === "no" ? "A reading found this argument does not show " + what + "." + ((report.findings || {}).total ? " The findings below say where." : "") : j.status === "lapsed" ? "Its reading lapsed: what it read changed since." : "No reading says whether it does.",
        list, sign === "no" ? "bad" : "open", { record: j.clears || "" }) : "";
      return '<div class="judgment" data-line="' + escape(o.line || "") + '">' + answer(sign, label, what + ": " + value) + card + "</div>";
    };
    // its own words that argue a claim of it: the words, the claim, and
    // the truth reading that checks both
    const own = ((found && found.rows) || []).filter((r) => r.argued);
    if (argues.length || own.length) {
      section("argues", "Justification", own.map((r) => {
        const j = r.judgment;
        const read = j && j.status !== "unjudged";
        return '<div class="judgment"><div class="j-words e-quote">' + escape(r.argued) + "</div>" +
          answer(read ? signOf(j) : "open", "Argues", '<span class="e-quote">“' + escape(r.words) + "”</span>, above" +
            ' <span class="fact-k">· ' + (read ? "checked with its truth reading" : "not checked: its truth reading checks it") + "</span>") + "</div>";
      }).join("") + argues.map((o) => {
        const sub = o.item.subject;
        return judged(o, "Proves", ctx.placeOf(sub.target_handle || sub.target, false) + (sub.words ? ' <span class="e-quote">“' + escape(sub.words) + "”</span>" : ""));
      }).join(""), argues.length + own.length);
    }
    if (premise) {
      const uses = ((report.rested_on_by || {}).items || []).filter((e) => e.kind === "uses" || e.kind === "qualifies").length;
      const nc = report.no_claim || {};
      const read = /(\d{4}-\d{2}-\d{2})/.exec(String(nc.line || "").split("\n").pop() || "");
      const rows = items.rows.rested_on_by || [];
      if (!uses && premise === "assumes") { state.open = true; state.first = state.first || "no recorded use"; }
      section(premise, premise === "defines" ? "Definition" : "Assumption",
        answer(uses ? "yes" : premise === "assumes" ? "open" : "none", "Used", uses ? "by " + uses + (uses === 1 ? " block" : " blocks") : "no recorded use") +
        answer(nc.last_read && !nc.findings ? "yes" : "none", "Read", nc.last_read ? (nc.findings ? nc.findings + " findings filed" : "no problem found") + (read ? ' <span class="fact-k">' + read[1] + "</span>" : "") : "not yet") +
        (rows.length ? rowList(rows) : ""), null);
    }
    // the citations no reading has read: a row each, their one card after
    // them (the same ways for each)
    const unread = cites.filter((o) => o.item.status === "unjudged");
    if (cites.length) {
      const work = (o) => escape((o.item.subject || {}).work || "a work");
      let body = cites.filter((o) => !unread.includes(o)).map((o) => judged(o, "Cited", work(o))).join("");
      if (unread.length) {
        state.open = true;
        state.first = state.first || "citation not checked";
        const n = unread.length;
        body += unread.map((o) => '<div class="judgment" data-line="' + escape(o.line || "") + '">' + answer("open", "Cited", work(o) + ": not checked") + "</div>").join("") +
          problemCard(n === 1 ? "citation not checked" : n + " citations not checked", "needs a reading",
            "No reading says whether " + (n === 1 ? "the work says" : "these works say") + " what this passage cites " + (n === 1 ? "it" : "them") + " for.",
            judgmentWays(unread[0].item, ctx), "open", { record: unread[0].item.clears || "" });
      }
      section("cites", "Cites", body, cites.length);
    }
    return { html: out.join(""), ...state };
  }
  // The open items' cards, in a section or under a claim.
  function rowList(rows) {
    return '<div class="open-cards">' + rows.join("") + "</div>";
  }
  // The open items on a claim, or on the graph here: an undeclared
  // assumption drawn here, where its claim's argument is known; the
  // others as the viewer drew them.
  function openRows(rows, mine, ctx) {
    return rowList(rows.map((r) => (typeof r === "string" ? r : r.item && r.item.kind === "undeclared-assumption" ? undeclaredCard(r.item, mine, ctx, r.index) : r.html)));
  }
  function norm(text) {
    return String(text || "").replace(/\s+/g, " ").trim();
  }
  const SECTIONS = [
    ["standings", "Claims", "each one's status, its issues and its history"],
    ["findings", "Findings", "on the block, not on one claim's words"],
    ["suggestions", "Suggestions", "works the paper may want to cite here"],
    ["judgments", "Judgments", "of the block itself and its edges"],
    ["established", "Established", "and what is missing"],
    ["rulings", "Earlier rulings", "on words the block no longer holds"],
    ["marks", "Stale marks", "the lines an update asked again, unread since; those a reread or a reading closed are in its history"],
    ["rests_on", "Upstream", "what it rests on"],
    ["rested_on_by", "Downstream", "what rests on it"],
    ["history", "History", "along the kept states"],
  ];

  function escape(text) {
    return String(text).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  }

  // A labelled row of an item (a claim, a finding, a judgment): its label,
  // its value; `line` the sheet line it keeps.
  function fact(label, value, line, labelled) {
    return '<div class="claim-row"' + (line ? ' data-line="' + escape(line) + '"' : "") + ">" + (labelled || '<span class="row-k">' + escape(label) + "</span>") + '<span class="row-v">' + value + "</span></div>";
  }

  // Who wrote it: its first word (the host, or person), the whole of it,
  // the host's version and model, on hover.
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

  function by(author, bare) {
    const whole = String(author);
    return '<span class="r-by">' + who(whole) + "</span>";
  }

  function go(ref, label) {
    return '<a class="go" data-ref="' + escape(ref) + '">' + escape(label || ref) + "</a>";
  }

  // A sheet line with each handle in it a link: words before the first
  // quoted excerpt only, so a quote is never cut into links.
  function linked(line, handles) {
    const quote = line.indexOf(' · "');
    const head = quote < 0 ? line : line.slice(0, quote);
    const rest = quote < 0 ? "" : line.slice(quote);
    const words = head.split(/(\s+|,\s*)/);
    return words.map((word) => {
      const bare = word.replace(/[.;:)]+$/, "");
      if (bare && handles.has(bare)) return go(bare) + escape(word.slice(bare.length));
      return escape(word);
    }).join("") + escape(rest);
  }

  // An edge as the selection sees it ("uses" read
  // both ways felt backwards): the kind's verb, active where the selection
  // is the edge's source, passive where it is the target, so every line
  // reads "<selection> <verb> <other end>".
  const VERBS = {
    uses: ["uses", "used by"],
    about: ["about", "described by"],
    justifies: ["justifies", "justified by"],
    qualifies: ["qualifies", "qualified by"],
  };
  const SELF = "self-supported";

  function entryLabel(id) {
    const at = id.indexOf(" [");
    return at < 0 ? id : id.slice(at + 2, -1);
  }

  // One edge of `rests_on` or `rested_on_by` as the selection sees it:
  // its verb (active where the edge is the selection's own, passive where
  // it points at it), its other end (null: a file or the paper, `label`
  // its name), the parts it names (the other end's, or `onOwn` the
  // selection's), how the text points and its flags, its quote and note.
  function viewed(item, selection) {
    const quote = item.evidence && item.evidence.length ? item.evidence.map((e) => e.text).join(" … ") : "";
    if (!item.kind) return { verb: SELF, kind: "self", other: null, label: "", parts: [], onOwn: false, flags: item.partial ? [item.partial] : [], quote, note: item.note || "" };
    const own = item.source === selection;
    const t = item.target;
    const other = own ? t.block || t.group || null : item.source;
    const label = other ? "" : t.file ? "file:" + t.file : "the paper";
    const parts = (t.entries || []).map(entryLabel);
    const verb = (VERBS[item.kind] || [item.kind, item.kind + " by"])[own ? 0 : 1];
    const flags = [item.mark, item.where_used ? "where-used" : null, item.generalizes ? "generalizes" : null, item.assumed ? "assumed" : null, item.partial].filter(Boolean);
    const by = (item.provenance && item.provenance.by) || "agent";
    return { verb, kind: item.kind, other, label, parts, onOwn: !own && parts.length > 0, flags, quote, note: item.note || "", by };
  }

  // An edge section: one group per verb in the order the edges come (the
  // paper's order of their other ends), each row two lines: the other end
  // (its name, what it is, the parts, the flags) and its line, then the
  // words quoted. `ctx` names a block or group: `nameOf`, `whatOf`, `lineOf`.
  function edgeSection(name, section, selection, ctx) {
    const groups = new Map();
    section.items.forEach((item, i) => {
      const seen = viewed(item, selection);
      if (!groups.has(seen.verb)) groups.set(seen.verb, { kind: seen.kind, rows: [] });
      groups.get(seen.verb).rows.push({ seen, i });
    });
    const out = [];
    for (const [verb, group] of groups) {
      out.push('<div class="verb-group k-' + escape(group.kind) + '"><div class="verb-head"><span class="verb k-' + escape(group.kind) + '">' + escape(verb) +
        '</span> <span class="n">' + group.rows.length + '</span></div><div class="rail k-' + escape(group.kind) + '">');
      for (const { seen, i } of group.rows) {
        const who = seen.other ? go(seen.other, ctx.nameOf(seen.other)) : '<span class="who-plain">' + escape(seen.label || "its own words") + "</span>";
        const what = seen.other ? ctx.whatOf(seen.other) : "";
        const at = seen.other ? ctx.lineOf(seen.other) : "";
        const parts = seen.parts.length ? '<span class="parts">' + (seen.onOwn ? "on " : "") + "[" + seen.parts.map(escape).join(", ") + "]</span>" : "";
        const flaw = seen.other && ctx.open && ctx.open.flawed ? ctx.open.flawed.get(seen.other) : null;
        const flags = (flaw ? '<span class="flag flaw" style="color:' + flaw.colour + '">' + escape(flaw.label) + "</span>" : "") +
          seen.flags.map((f) => '<span class="flag">' + escape(f) + "</span>").join("") +
          (seen.by === "person" ? '<span class="flag person">person</span>' : "");
        out.push('<div class="edge-row" data-sec="' + name + '" data-i="' + i + '" data-line="' + escape(section.lines[i]) + '">' +
          '<div class="who">' + who + (what ? ' <span class="what">' + escape(what) + "</span>" : "") + parts + flags + "</div>" +
          (at && seen.other ? '<a class="go where" data-ref="' + escape(seen.other) + '">' + escape(at) + "</a>" : "<span></span>") +
          (seen.quote ? '<div class="quote">“' + escape(seen.quote) + "”</div>" : "") +
          (seen.note ? '<div class="note-line">' + escape(seen.note) + "</div>" : "") + "</div>");
      }
      out.push("</div></div>");
    }
    return out.join("");
  }

  // The card's head: its name, what it is and where, and a link that
  // brings the paper back to it (the paper holds its words; the card does
  // not repeat them). Its ids and form are the record's, at the card's end.
  function header(head, handles, ctx) {
    const at = ctx.lineOf(head.block || head.group);
    const reveal = at ? '<a class="reveal" data-reveal title="scroll the paper back to it">' + escape(at) + "</a>" : "";
    if (head.group) {
      return '<div class="card-head"><h3>' + escape(head.handle) + '</h3><div class="kind">group <span class="gk">' + escape(head.kind) + '</span></div><div class="meta">' +
        [head.blocks.length + " blocks", reveal].filter(Boolean).join(" · ") + "</div>" +
        '<div class="meta">' + head.blocks.map((b) => go(b, ctx.nameOf(b))).join(", ") + "</div></div>";
    }
    // what it is, under its name, each role in its colour; a block
    // without one by its form
    const kind = head.roles.length
      ? head.roles.map((r) => '<span class="role rl-' + escape(r) + '">' + escape(r) + "</span>").join(" and ")
      : '<span class="role">' + escape(head.form || "no role") + "</span>";
    // where it is above its name, as a small label; what it is under it;
    // its groups and its line last
    // a title that numbers itself (`2.3 Second Law`) is not numbered again
    const numbered = head.section && head.section.number && !MK.numbersItself(head.section.title);
    const where = head.section ? "§ " + (numbered ? head.section.number + " " : "") + head.section.title : "";
    const bits = [];
    if (head.groups.length) bits.push("in " + head.groups.map((g) => (handles.has(g) ? go(g) : escape(g))).join(", "));
    if (reveal) bits.push(reveal);
    return '<div class="card-head">' + (where ? '<div class="where" title="' + escape(where) + '">' + escape(where) + "</div>" : "") + "<h3>" + escape(head.handle) + "</h3>" +
      '<div class="kind">' + kind + (bits.length ? '<span class="meta"> · ' + bits.join(" · ") + "</span>" : "") + "</div></div>";
  }

  // The record: the ids, the form, the commands that print the card and
  // its history, each copied with a click.
  function record(report) {
    const head = report.block;
    const rows = [["node", head.node]];
    if (head.group) rows.push(["group", head.group]);
    else {
      rows.push(["block", head.block]);
      rows.push(["form", head.environment ? head.form + " " + head.environment + (head.meaning && head.meaning !== head.environment ? " (" + head.meaning + ")" : "") : head.form]);
    }
    const commands = ["axiomation show " + head.handle, "axiomation history " + head.handle];
    return rows.map(([k, v]) => '<div class="rec-row"><span class="row-k">' + k + "</span><code>" + escape(v) + "</code></div>").join("") +
      commands.map((c) => '<div class="rec-row"><code class="command">' + escape(c) + '</code><button type="button" class="copy" data-copy="' + escape(c) + '">copy</button></div>').join("");
  }

  // What justifies the selection: the sources of the justifies edges onto
  // it, as `show`'s upstream lists them.
  function justifiersOf(report, selection) {
    return ((report.rests_on || {}).items || [])
      .filter((e) => e.kind === "justifies" && e.target && (e.target.block === selection || e.target.group === selection))
      .map((e) => {
        const i = report.rests_on.items.indexOf(e);
        const full = (report.rests_on.lines || [])[i] || "";
        const at = full.indexOf(" → ");
        return { source: e.source, claims: e.target.claims || null, handle: at > 0 ? full.slice(0, at) : e.source, line: at > 0 ? full.slice(at + 1) : "" };
      });
  }

  // The block's claims, each once (the standings,
  // judgments and established sections read as raw): a standing, the
  // `true` judgment of its words and whether it is established, matched by
  // the claim's words; the judgments of anything else apart (`others`).
  function claims(report) {
    const empty = { items: [], lines: [] };
    const st = report.standings || empty;
    const jd = report.judgments || empty;
    const es = report.established || empty;
    const usedJ = new Set();
    const usedE = new Set();
    // the block's own words that argue a claim of it (a self edge: no
    // kind, its claims named where it holds several)
    const selfs = ((report.rests_on || {}).items || []).filter((x) => !x.kind && x.block);
    const rows = st.items.map((s, i) => {
      const words = s.claim ? s.claim.text : "";
      const own = s.status === "self" ? selfs.find((x) => (x.claims ? x.claims.includes(words) : st.items.length === 1)) : null;
      const j = jd.items.findIndex((x, k) => !usedJ.has(k) && x.aspect === "true" && !x.subject.target && x.subject.words === words);
      if (j >= 0) usedJ.add(j);
      const e = es.items.findIndex((x, k) => !usedE.has(k) && x.claim === words);
      if (e >= 0) usedE.add(e);
      return {
        words,
        standing: s,
        lines: [st.lines[i], j >= 0 ? jd.lines[j] : null, e >= 0 ? es.lines[e] : null],
        judgment: j >= 0 ? jd.items[j] : null,
        established: e >= 0 ? es.items[e] : null,
        argued: own ? ((own.evidence || [])[0] || {}).text || "" : "",
      };
    });
    const others = jd.items.map((x, k) => ({ item: x, line: jd.lines[k] })).filter((_, k) => !usedJ.has(k));
    const unmatched = es.items.map((x, k) => ({ item: x, line: es.lines[k] })).filter((_, k) => !usedE.has(k));
    return { rows, others, unmatched };
  }

  // The verdict of the reading that holds a subject, beside its status,
  // and provisional where that reading is: not left folded away.
  function heldVerdict(item) {
    const held = item.held && (item.judgments || []).find((r) => r.id === item.held);
    if (!held) return "";
    return ' <span class="verdict v-' + escape(held.verdict) + '">' + escape(held.verdict) + "</span>" +
      (held.provisional ? ' <span class="flag provisional" title="read off a copy that is not the version of record">provisional</span>' : "");
  }

  // Why a clearing ruling that speaks clears nothing (`rulings.Guard`).
  const GUARDS = {
    promised: "the paper promises its argument",
    "true-disputed": "agents alone ruled, and readers differ on whether it holds",
    "true-not-held": "agents alone ruled, and no reading has shown it holds",
    "no-result-used": "its block uses no result it could be immediate from",
    "relied-on": "something relies on it, and what was ruled is no premise",
  };
  // Why a claim is unsupported by its own reason (`established.Missing`), by
  // its standing.
  const GAPS = {
    justified: "no justifies edge reaches it",
    restated: "it restates nothing",
  };

  // The speaking ruling that decides a claim's support status: the latest
  // of the kind that decides it (the clearing kind, or the refused one, or
  // `owed`, which wins); and the speaking rulings of another kind, which
  // disagree. An agreeing ruling, earlier or of another agent, adds
  // nothing here: the Rulings section holds every ruling.
  function deciding(sp) {
    const speaks = sp.speaks || [];
    const kind = sp.ruling || (speaks.some((r) => r.ruling === "owed") ? "owed" : null);
    const of = speaks.filter((r) => r.ruling === kind);
    return { decides: of.length ? of[of.length - 1] : null, others: speaks.filter((r) => r.ruling !== kind) };
  }

  // A ruling's kind, an owed one's severity beside it (`owed 3`): its weight,
  // which sorts the owed claims and changes no gate.
  function rulingWords(r) {
    if (r.ruling !== "owed") return r.ruling;
    return "owed " + r.severity;
  }

  // Whether the paper owes a claim an argument, the row that says it
  // (`rulings.Support`): no, by which ruling, whose and why; yes, left
  // unargued by the author; or yes, and why it stands so (unruled, ruled
  // owed, a guard, its own gap). Only the ruling that decides it, and a
  // note of those that disagree. Null for a claim the paper argues, or a
  // conjecture.
  function owedRow(s) {
    const sp = s.support;
    if (!sp || sp.status === "supported") return null;
    const { decides, others } = deciding(sp);
    const outweighed = sp.status === "unsupported" && decides && decides.ruling === "owed" ? " (outweighed: any owed wins)" : "";
    // the deciding ruling's reason, its kind named where the head does not
    // (an owed one's weight)
    const said = (decides ? '<div class="ruled">' + (decides.ruling === "owed" ? '<span class="fact-k">' + escape(rulingWords(decides)) + "</span> " : "") + escape(decides.reason) +
      " " + by(decides.author) + "</div>" +
      (decides.fix ? '<div class="ruled"><span class="fact-k">fix</span> ' + escape(decides.fix) + "</div>" : "") +
      (decides.ruling === "owed" && (sp.relied || []).length ? '<div class="ruled"><span class="fact-k">relied on</span> by ' + sp.relied.length + (sp.relied.length === 1 ? " block" : " blocks") + "</div>" : "") : "") +
      (sp.proposal ? '<div class="ruled other"><span class="fact-k">proposed</span> ' + escape(rulingWords(sp.proposal)) + " " + by(sp.proposal.author) + ": " + escape(sp.proposal.reason) +
        (sp.proposal.fix ? "; fix: " + escape(sp.proposal.fix) : "") + " (the next argument review settles it)</div>" : "") +
      (others.length ? '<div class="ruled other"><span class="fact-k">' + others.length + (others.length === 1 ? " other ruling" : " other rulings") + "</span> " +
        others.map((r) => escape(r.ruling) + " " + by(r.author)).join("; ") + escape(outweighed) + "</div>" : "");
    if (sp.status === "no-argument-owed") {
      // an agent's word apart from a person's, as `check`'s audit counts them
      const whose = '<span class="flag' + (sp.by_person ? " person" : "") + '">' + (sp.by_person ? "a person" : "agents alone") + "</span>";
      return '<span class="owed owed-no">none owed</span>: ruled <span class="ruling">' + escape(sp.ruling) + "</span> by " + whose + said;
    }
    if (sp.status === "left-unargued") return '<span class="owed owed-yes">owed</span>, <span class="claim-why">left unargued by the author</span>' + said;
    let why;
    if (sp.gap === "circular") why = "its support runs in a circle back to it";
    else if ((sp.conjectures || []).length) why = "it restates " + sp.conjectures.join(", ") + ", which the paper only conjectures: restating it asserts it";
    else if (s.status !== "unsupported") why = GAPS[s.status] || "its own reason is a gap";
    else if (sp.guard) why = "ruled " + sp.ruling + ", but " + GUARDS[sp.guard];
    else if ((sp.speaks || []).length) why = "ruled owed";
    else why = "unruled";
    return '<span class="owed owed-yes">owed</span>: <span class="claim-why">' + escape(why) + "</span>" + said;
  }

  // What the graph holds for a claim (its standing), in words: no line
  // (unsupported, with what the paper declares), the J that justifies
  // it, its own argument, what it restates, or a conjecture.
  const IN_GRAPH = { unsupported: "none", justified: "justified", self: "self", restated: "restated", conjectured: "conjectured" };

  // Each aspect (`records.Aspect`) as the question it names, a noun, so a
  // label never reads as its answer ("accurate · unjudged"); the record's
  // term, which the commands take (`--aspect`), on hover.
  const ASPECTS = {
    valid: "validity",
    true: "truth",
    accurate: "accuracy",
    cited: "citation",
    "well-posed": "well-posedness",
    computed: "computation",
    presentation: "presentation",
    significance: "significance",
    novelty: "novelty",
    literature: "literature",
  };
  function aspect(term, cls = "aspect") {
    return '<span class="' + cls + '" title="aspect `' + escape(term) + '`">' + escape(ASPECTS[term] || term) + "</span>";
  }

  function status(words) {
    return '<span class="j-status j-' + escape(String(words).split(" ")[0]) + '">' + escape(words) + "</span>";
  }

  // Whose move a problem is: settled here, at another claim, or by a
  // reading not yet made.
  const WHERE = { "rests-on": "elsewhere", unjudged: "needs a reading", inconclusive: "needs a reading", lapsed: "needs a new reading", stale: "needs a reread" };
  // An open problem as a card, as a finding is: its name (the record's
  // status on its hover), whose move, one sentence, its ways inside.
  function problemCard(name, side, said, list, tone, { title = "", line = "", record = "", here = null } = {}) {
    return '<div class="pcard p-' + tone + '"' + (line ? ' data-line="' + escape(line) + '"' : "") + (here === null ? "" : ' data-here="' + escape(here) + '"') + '><div class="ph"><span' + (title ? ' title="' + escape(title) + '"' : "") + ">" + escape(name) + '</span><span class="side">' + escape(side) + "</span></div>" +
      '<p>' + said + "</p>" + waysFold(list, false, record) + "</div>";
  }
  // A claim's open status as its card: named by what is open (its
  // argument, its truth, what it waits on), what holds said with it.
  const CARD_NAMES = { defect: "does not hold", disputed: "readings disagree", unsupported: "no argument", lapsed: "reading lapsed", stale: "a link changed", inconclusive: "could not tell", "rests-on": "waits on another claim", "finding-open": "open finding" };
  function claimCard(cs, s, row, mine, line, list, ctx) {
    if (!CARD_NAMES[cs.status] && cs.status !== "unjudged") return "";
    const a = (/^`([a-z-]+)` of /.exec(cs.detail || "") || [])[1];
    const j = row.judgment;
    const held = j && j.held && (j.judgments || []).find((r) => r.id === j.held);
    const trueRead = held && held.verdict === "verified";
    const arg = mine.length ? ctx.placeOf(mine[0].source, false) : "its argument";
    let name = CARD_NAMES[cs.status];
    let said = plainStatus(cs, ctx, trueRead ? "verified" : null);
    if (cs.status === "unjudged" && a === "valid") { name = "argument not checked"; said = (trueRead ? "Read as true, but not yet established: " : "Not yet established: ") + "no reading says " + arg + " proves it."; }
    else if (cs.status === "unjudged") { name = "claim not checked"; said = "Not yet established: no reading says whether it is true."; }
    else if (cs.status === "rests-on" && cs.at) said = "It is established once " + ctx.placeOf(cs.at, false) + " is, unless it does not rely on it.";
    else if (cs.status === "defect" && a === "valid") { name = "argument not shown"; said = "A reading found that " + arg + " does not show it."; }
    const tone = cs.status === "defect" || cs.status === "unsupported" ? "bad" : "open";
    return problemCard(name, WHERE[cs.status] || "here", said, list, tone, { title: cs.status + ": " + cs.detail, line, record: cs.next || "" });
  }

  // A claim's status said plainly, for its author: what holds, what is
  // missing, and whose move it is; the record's own words (`detail`) on
  // the chip's hover, the status word kept as the record names it.
  function plainStatus(cs, ctx = PLAIN, truth = null) {
    // the reader the detail ends with, named short
    let detail = nouned(cs.detail || "");
    const reader = cs.by ? ", read by " + escape(cs.by) : null;
    if (reader && detail.endsWith(reader)) detail = detail.slice(0, -reader.length) + ", read " + by(cs.by);
    const aspectOf = /^`([a-z-]+)` of /.exec(cs.detail || "");
    switch (cs.status) {
      case "rests-on": {
        const kind = cs.at && (cs.detail || "").startsWith(cs.at + ": ") ? cs.detail.slice(cs.at.length + 2) : "";
        return "Its own support holds, but it relies on " + (cs.at ? ctx.placeOf(cs.at) : "another claim") + (kind ? ", which is " + escape(kind) : "") + ": the first thing it waits on.";
      }
      case "unjudged": {
        const a = aspectOf && aspectOf[1];
        return a === "valid" ? (truth === "verified" ? "The claim has been read as true. Its argument has no current validity reading, so it is not established." : "Its argument has no current validity reading, so it is not established.") : a === "true" ? "No current reading says whether it is true." :
          a ? "No current reading judges its " + escape(ASPECTS[a] || a) + "." : "No current reading judges it: " + detail + ".";
      }
      case "defect": return "A review found that it does not hold: " + detail + ".";
      case "disputed": return "Readers disagree on it: " + detail + ".";
      case "conjectured": return "The paper does not assert it: it states it as a conjecture.";
      case "unsupported": return "The paper gives no argument for it, and no ruling says it needs none: " + detail + ".";
      case "lapsed": return "A reading of it no longer counts: what it read changed since. " + detail + ".";
      case "stale": return "Something it rests on changed, and no one has read the link again since: " + detail + ".";
      case "inconclusive": return "A review read it and could not tell: " + detail + ".";
      case "finding-open": return "A review found a problem in it that is not answered yet: " + detail + ".";
      case "left-unargued": return "Its author ruled it left unargued.";
      case "established": return cs.detail === "argued" ? "Established: its route holds, and everything it rests on." : "Established: " + detail + ".";
      default: return detail;
    }
  }

  // A next step for its reader: its words first, the commands it names
  // after them, each a chip to copy.
  function step(next) {
    const commands = [];
    const words = String(next).replace(/\s*\(`(axiomation [^`]+)`\)/g, (whole, command) => (commands.push(command), "")).replace(/`(axiomation [^`]+)`/g, (whole, command) => (commands.push(command), "that command"));
    return '<div class="next"><span class="fact-k">next</span> ' + nouned(words) +
      commands.map((c) => ' <button type="button" class="copy cmd" data-copy="' + escape(c) + '" title="copy it">' + escape(c) + "</button>").join("") + "</div>";
  }

  // The fold's words, escaped, each aspect it names as a term (`` `true` ``)
  // given as its noun, as the rows name it; the command line keeps the term.
  function nouned(text) {
    return escape(text).replace(/`([a-z-]+)`/g, (whole, term) => (Object.hasOwn(ASPECTS, term) ? aspect(term, "aspect-noun") : whole));
  }

  // Whether a finding is on a claim's words: a part anchor on them exactly,
  // as the status counts it; a span on the claim whose words hold it, or
  // that it holds.
  function onWords(f, node, words) {
    return (f.anchors || []).some((a) => a.node === node && a.words &&
      (a.kind === "part" ? a.words === words : a.kind === "span" && (words.includes(a.words) || a.words.includes(words))));
  }

  // What a finding is now: open, the author's to act on (live, unanswered
  // or postponed, not refiled), or history (lapsed, answered, refiled).
  function current(f) {
    return !f.refiled_as && (f.state === "open" || f.state === "postponed");
  }

  // A claim: its status and next step, its words, then labelled rows, each
  // marker a control: whether the paper owes it an argument, what the
  // graph holds for it (the standing; a link to what justifies it where
  // the block holds one claim), whether it is true as judged (its status
  // and the holding verdict; a click unfolds the readings; a lapse's cause
  // and what clears it), how it is established where that says more than
  // the status (via, assuming, under a conjecture); the open findings on
  // its words; and its history, folded. Each row keeps its sheet line.
  function claimSection(found, placed, handles, ctx, justifiers, rulings, self = "") {
    return found.rows.map((row, i) => {
      const s = row.standing;
      const cs = s.claim_status;
      const [sLine, jLine, eLine] = row.lines;
      // what argues it: the J whose justifies line names its words, or names
      // none where the block holds it alone
      const mine = justifiers.filter((j) => (j.claims ? j.claims.includes(row.words) : found.rows.length === 1));
      const valid = found.others.find((o) => o.item.aspect === "valid" && o.item.subject && o.item.subject.words === row.words);
      const lines = answers(row, s, cs, mine, valid ? valid.item : null, ctx);
      const placedHere = placed.claims[i];
      const issues = placedHere.filter((x) => current(x.item));
      // its history: the rulings that do not decide, the findings no
      // longer current
      const decides = s.support ? deciding(s.support).decides : null;
      const past = rulings.filter((r) => r.item.claim === row.words && !(decides && r.item.id === decides.id));
      const gone = placedHere.filter((x) => !current(x.item));
      const history = past.length + gone.length
        ? '<details class="claim-history"><summary>earlier · ' + (past.length + gone.length) + " " +
          past.map((r) => who(r.item.author)).join("") + "</summary>" +
          past.map((r) => '<div class="ruling-line" data-line="' + escape(r.line) + '"><code class="sheet">' + escape(r.line) + "</code></div>").join("") +
          findingRows(gone, ctx) + "</details>"
        : "";
      // its words; what is known of it, a line a question; where it stands;
      // the ways to resolve it, folded; then what else is open on it
      return '<div class="claim" data-sec="standings" data-i="' + i + '"' + (sLine ? ' data-line="' + escape(sLine) + '"' : "") + '><div class="claim-words">' + escape(row.words) + "</div>" +
        // the sheet lines it keeps, each on the row that shows it: a truth
        // reading not yet made on its answers
        '<div class="answers"' + (jLine && !lines.some((l) => l.includes('data-line="' + escape(jLine) + '"')) ? ' data-line="' + escape(jLine) + '"' : "") + ">" + lines.join("") + "</div>" +
        (cs ? claimCard(cs, s, row, mine, eLine, claimWays(cs, s, { claim: self, words: found.rows.length > 1 ? row.words : "", claimWords: row.words, justifier: mine[0] || null, sole: mine.length === 1 }, ctx), ctx) : "") +
        (row.open && row.open.length ? openRows(row.open, mine, ctx) : "") +
        (issues.length ? '<div class="claim-issues">' + findingRows(issues, ctx) + "</div>" : "") + history + "</div>";
    }).join("") + found.unmatched.map((u) => '<div class="claim" data-line="' + escape(u.line) + '"><code class="sheet">' + escape(u.line) + "</code></div>").join("");
  }

  // Who made a record, as an icon: an agent (a review session or an agent
  // by hand), a person of the paper, or an outside reviewer; the whole
  // name on hover. The record's name starts with its host.
  const ICONS = {
    agent: '<rect x="3" y="5" width="10" height="8" rx="2.2"/><path d="M8 2.6V5"/><circle cx="6.3" cy="9" r=".7" fill="currentColor"/><circle cx="9.7" cy="9" r=".7" fill="currentColor"/>',
    person: '<circle cx="8" cy="5.2" r="2.5"/><path d="M3.4 13.6c.8-2.7 2.5-4 4.6-4s3.8 1.3 4.6 4"/>',
    reviewer: '<circle cx="6.6" cy="5.2" r="2.4"/><path d="M2.4 13.6c.7-2.6 2.3-3.9 4.2-3.9 1 0 1.9.3 2.6.9"/><path d="M10 12.3l1.5 1.5 3-3.3"/>',
  };
  function whoKind(author) {
    const host = String(author || "").split(" ")[0];
    return host === "person" ? "person" : host === "reviewer" ? "reviewer" : "agent";
  }
  function who(author) {
    if (!author) return "";
    const kind = whoKind(author);
    const name = kind === "agent" ? "an agent: " + author : kind === "reviewer" ? "an outside reviewer: " + String(author).slice("reviewer ".length) : shortName(author) === "a person" ? "a person" : "a person: " + shortName(author);
    return '<svg class="who w-' + kind + '" viewBox="0 0 16 16" role="img" aria-label="' + escape(name) + '"><title>' + escape(name) + "</title>" + ICONS[kind] + "</svg>";
  }

  // A reading's answer, its sign and words, and who holds it.
  const SIGN = { yes: "✓", open: "○", no: "✕", none: "–", split: "≠" };
  function answer(sign, label, value, line) {
    return '<div class="ans"' + (line ? ' data-line="' + escape(line) + '"' : "") + '><span class="i a-' + sign + '">' + SIGN[sign] + '</span><span class="k">' + label + '</span><span class="v">' + value + "</span></div>";
  }
  // A judgment as its answer line's value: what it found, who, and its
  // readings unfolded on a click.
  function readingValue(j, ctx) {
    const held = j.held && (j.judgments || []).find((r) => r.id === j.held);
    const words = held ? escape(held.verdict) + (held.depth ? ' <span class="fact-k">' + escape(held.depth) + "</span>" : "") +
      (held.provisional ? ' <span class="flag provisional" title="read off a copy that is not the version of record">provisional</span>' : "") : escape(j.status);
    const head = words + (held ? who(held.author) : "");
    const n = (j.judgments || []).length;
    return (n ? '<details class="readings"><summary>' + head + '<span class="rs-more">reason</span></summary>' + readingsOf(j, false) + "</details>" : head) + causeLine(j.cause, ctx);
  }
  function signOf(j) {
    if (!j) return "open";
    if (j.status === "disputed") return "split";
    if (j.status === "unjudged" || j.status === "lapsed") return "open";
    const held = j.held && (j.judgments || []).find((r) => r.id === j.held);
    if (!held) return "open";
    return held.verdict === "verified" ? "yes" : held.verdict === "inconclusive" ? "open" : "no";
  }
  // What is known of a claim, by its route: whether it is true; whether
  // the argument the graph links proves it (a justified claim); how it is
  // argued where it is not; what it relies on where that is open.
  function answers(row, s, cs, mine, valid, ctx) {
    const out = [];
    const j = row.judgment;
    const a = cs ? (/^`([a-z-]+)` of /.exec(cs.detail || "") || [])[1] : null;
    const status = cs ? cs.status : "";
    if (s.status === "self") {
      // its argument is its own words, which its truth reading reads
      const read = j && j.status !== "unjudged";
      out.push(answer(read ? signOf(j) : "open", "True", read ? readingValue(j, ctx) : "not checked", j ? row.lines[1] : null));
      out.push(answer(read ? signOf(j) : "open", "Argued", row.argued ? "by this block's justification, below" : "in its own words"));
    } else if (j && j.status !== "unjudged") out.push(answer(signOf(j), "True", readingValue(j, ctx), row.lines[1]));
    const part = s.completeness && s.completeness !== "given" ? ' <span class="fact-k">argued as ' + escape(s.completeness) + "</span>" : "";
    if (s.status === "justified" && !(mine.length || valid) && status !== "unsupported") {
      out.push(answer("yes", "Argued", "justified" + part));
    } else if (s.status === "justified") {
      // the passage the graph links as its argument: what a review reads,
      // and what to unlink where it is not the argument
      const where = mine.length ? '<div class="arg">the argument the graph links: ' + mine.map((x) => ctx.placeOf(x.source)).join("; ") + "</div>" : "";
      let value;
      let sign;
      if (valid && valid.status !== "unjudged") { value = readingValue(valid, ctx); sign = signOf(valid); }
      else if (row.established && row.established.established) { value = "read as valid"; sign = "yes"; }
      else if (a === "valid" && status === "unjudged") { value = "not checked"; sign = "open"; }
      else if (a === "valid" && status === "inconclusive") { value = "could not tell"; sign = "open"; }
      else if (a === "valid" && status === "lapsed") { value = "its reading lapsed"; sign = "open"; }
      else if (a === "valid" && status === "disputed") { value = "readers disagree"; sign = "split"; }
      else if (a === "valid" && status === "defect") { value = ((/(not-shown|contradicted)/.exec(cs.detail || "") || [])[1] || "a reading finds against it") + (/\(lapsed\)/.test(cs.detail || "") ? " (lapsed)" : "") + (cs.by ? who(cs.by) : ""); sign = "no"; }
      if (value) out.push(answer(sign, "Proved", value + where + part));
    } else if (s.status === "unsupported" || (s.status === "justified" && !mine.length && status === "unsupported")) {
      const owed = owedRow(s);
      const declared = s.declared ? ' <span class="fact-k">· the paper says it is ' + escape(s.declared) + "</span>" : "";
      // a ruling that none is owed, or the author's leaving it unargued:
      // no argument, and no problem
      const sp = s.support || {};
      const neutral = sp.status === "no-argument-owed" || sp.status === "left-unargued";
      const words = sp.status === "no-argument-owed" ? "no argument needed" : sp.status === "left-unargued" ? "left unargued" : "no argument";
      out.push(answer(neutral ? "none" : "no", "Argued", words + declared + (owed ? '<div class="owed">' + owed + "</div>" : ' <span class="fact-k">· and no ruling that none is needed</span>')));
    } else if (s.status === "restated") {
      out.push(answer(status === "unsupported" ? "no" : "yes", "Argued", "it restates another claim"));
    } else if (s.status === "conjectured") {
      out.push(answer("none", "Argued", "stated as a conjecture, not argued"));
    }
    if (status === "rests-on" && cs.at) out.push(answer("open", "Relies on", ctx.placeOf(cs.at) + ", which is not established yet"));
    const e = row.established;
    if (e && e.established && (e.assumptions || []).length) out.push(answer("none", "Assumes", assuming(e.assumptions, (h) => ctx.placeOf(h, false))));
    if (e && e.established && (e.under || []).length) out.push(answer("none", "Under", "conjecture " + e.under.map((h) => ctx.placeOf(h.at, false)).join(", ")));
    if (s.note) out.push(answer("none", "Note", escape(s.note)));
    return out;
  }

  // The ways to resolve, folded: one line each, what to do and when, what
  // it takes, and a copy of its command where one does it.
  const TAKES = { text: "text edit", graph: "graph edit", ruling: "ruling", review: "review", judgment: "judgment", reread: "reread", elsewhere: "elsewhere", inspect: "read only", evidence: "evidence", none: "" };
  // A way's `case`, where the ways split by what is wrong ("If the graph
  // is wrong"), heads each run of ways it holds, said once.
  function waysFold(list, open = false, record = "") {
    if (!list.length) return "";
    return '<div class="ways-list"><div class="w-head"' + (record ? ' title="' + escape(record) + '"' : "") + '>Ways to resolve</div><ul class="ways1">' + list.map((w, k) =>
      (w.case && w.case !== (list[k - 1] || {}).case ? '<li class="w-case">' + escape(w.case) + "</li>" : "") +
      '<li><span class="w-do"><b>' + w.action + "</b>" + (w.when ? " " + w.when : "") + "</span>" +
      '<span class="w-takes t-' + escape(w.how) + '">' + escape(TAKES[w.how] || w.how) + (w.paid ? " · paid" : "") + "</span>" +
      (w.command ? '<button type="button" class="copy w-copy" data-copy="' + escape(w.command) + '" title="' + escape("copy: " + w.command + (w.starter ? " (fill in " + w.starter + ")" : "") + (w.paid ? "; add --dry-run to price it first" : "")) + '">⧉</button>' : "<span></span>") +
      "</li>").join("") + "</ul></div>";
  }

  // a command's argument, quoted for the shell
  const q = (text) => "'" + String(text).replace(/'/g, "'\\''") + "'";
  // Correcting the link from a claim's argument, where it is not its
  // argument: its justifies line taken off, and where it was the claim's
  // only argument, the claim's standing set to unsupported with it (a
  // justified standing no justifies line reaches is refused), so its
  // rulings speak again. `claim` its block's handle, `words` its words.
  function relinkOf(j, ctx, claim = "", words = "", sole = true) {
    if (!j || !j.line) return null;
    const label = (/^→ justifies \S+ (\[[^\]]*\])/.exec(j.line) || [])[1];
    const standing = sole && claim && words ? " --standing " + claim + " " + q("⊢ unsupported " + (label ? label + " " : "") + '· "' + words + '"') : "";
    return { action: "Correct the argument link", when: "if " + ctx.placeOf(j.source, false) + " is not its argument", how: "graph", command: "axiomation change --retract " + j.handle + " " + q(j.line) + standing + " --reason …", starter: "the reason" };
  }

  // An undeclared assumption on a claim, as a problem card. Its point: a
  // claim's stated conditions cover what its argument needs (`docs/model.md`
  // § 10). The record knows only that the graph does not find them among
  // the claim's conditions: the graph may be wrong, so its ways say when
  // a graph edit fits, the link its cause names first.
  function undeclaredCard(item, mine, ctx, here = null) {
    const c = item.cause || {};
    const premises = ctx.ordered([...new Set((c.premises || []).map((p) => p.premise))]);
    const n = premises.length;
    const them = n === 1 ? "it" : "them";
    const via = ((c.premises || [])[0] || {}).via || (mine[0] || {}).handle;
    // each by its line, its block on hover; the first few, the rest counted
    const MOST = 12;
    const number = (h) => String(ctx.lineOf(h) || ctx.nameOf(h)).replace(/^line /, "");
    const lines = (n === 1 ? "line " : "lines ") + premises.slice(0, MOST).map((h) => go(h, number(h))).join(", ") + (n > MOST ? " and " + (n - MOST) + " more" : "");
    const said = "Its argument" + (via ? ", " + ctx.placeOf(via, false) + "," : "") + " assumes what the claim's recorded conditions do not: " + lines + ".";
    const claim = item.handle || "";
    const GRAPH = "If the graph is wrong";
    const TEXT = "If the text is wrong";
    const linked = { case: GRAPH, action: "Link the claim to " + them, how: "graph", command: "axiomation change --add " + claim + " " + q("→ uses " + (premises.length ? ctx.nameOf(premises[0]) : "…") + ' · "…"') + " --reason …", starter: "the claim's words that assume it" + (n > 1 ? ", an --add for each" : "") };
    const stated = { case: TEXT, action: "State " + them + " in the claim", how: "text" };
    const revised = { case: TEXT, action: "Revise the argument so it does not need " + them, how: "text" };
    // the argument's own links that bring them in, as the item's path
    // walks them: from the argument, or from a member of it where it is a
    // group; the graph may be wrong that it relies on them
    const path = c.path || [];
    const own = new Set([via, ...path.filter((st) => st.source === via && st.hop === "member").map((st) => st.target)]);
    const deps = path.filter((st) => own.has(st.source) && st.hop === "uses");
    const dep = deps.length ? { case: GRAPH, action: "Correct the dependency on " + ctx.linesOf(deps.map((st) => st.target)), how: "graph",
      command: "axiomation change --retract " + deps[0].source + " " + q("→ uses " + deps[0].target + (deps[0].quote ? ' · "' + deps[0].quote + '"' : "")) + " --reason …", starter: "the reason" + (deps.length > 1 ? ", a --retract for each" : "") } : null;
    let ways = [dep, linked, stated, revised];
    // a claim with no uses link at all: the graph most likely missed them
    if (c.cause === "missing-edges") ways = [linked, dep, stated, revised];
    else if (c.cause === "unnamed-line") {
      const m = /^(\S+) → (\S+) (\S+)((?: \[[^\]]*\])?) · "(.*)" \(names no claim\)$/.exec(((c.lines || [])[0] || {}).line || "");
      if (m) {
        const [, from, hop, to, , quote] = m;
        // a restating about line names its claim at its source end, any
        // other line at its target (`docs/guide.md`)
        const about = hop === "about";
        const line = (bracket) => q(about ? "→ about " + bracket + to + ' · "' + quote + '"' : "→ " + hop + " " + to + (bracket ? " " + bracket.trim() : "") + ' · "' + quote + '"');
        ways = [{ case: GRAPH, action: "Name the claim in the link from " + ctx.placeOf(from, false) + " to " + ctx.placeOf(to, false), how: "graph",
          command: "axiomation change --retract " + from + " " + line("") + " --add " + from + " " + line("[…] ") + " --reason …",
          starter: "in the brackets, the label of the claim " + (about ? "of " + ctx.nameOf(from) + " that restates it" : "of " + ctx.nameOf(to) + " it means") }, ...ways];
      }
    } else if (c.cause === "narrows") {
      const wider = [...new Set((c.premises || []).map((p) => p.narrows || p.case).filter(Boolean))];
      if (wider.length) linked.when = "the claim states " + ctx.linesOf(wider) + " in another form";
    } else if (c.cause === "discharged-elsewhere") {
      const by = [...new Set((c.premises || []).flatMap((p) => p.discharged_by || []))];
      ways = [dep, { case: GRAPH, action: "Correct where the graph settles " + them, when: "settled from " + (by.length ? ctx.linesOf(by) : "another block") + ", outside the argument", how: "graph", command: "axiomation sheet --stage edges -o edges.md", starter: "edit the sheet, then axiomation edit --stage edges --file edges.md" }, stated, revised];
    }
    return problemCard(n === 1 ? "undeclared assumption" : n + " undeclared assumptions", "here", said, ways.filter(Boolean), "open", { record: item.clears || "", here });
  }

  // A claim's ways to resolve, by its status and what makes it so: only
  // the ways that apply to this record, each what to do, when, what it
  // takes and its command; a placeholder `…` where the command needs the
  // reader's own words. A way addresses the item: it never promises the
  // claim established (a review may find against it; another blocker may
  // follow). The paper is the command's directory, left out.
  function claimWays(cs, s, at, ctx) {
    const aspectOf = /^`([a-z-]+)` of (\S+)/.exec(cs.detail || "");
    const aspect = aspectOf ? aspectOf[1] : null;
    const j = at.justifier;
    const J = j ? j.handle : aspectOf && aspect === "valid" ? aspectOf[2] : null;
    const T = at.claim;
    const words = at.words ? " --claim " + q(at.words) : "";
    const judgeValid = J ? "axiomation judge --aspect valid --on " + J + " --for " + T + words + " --verdict … --depth … --evidence …" : null;
    const judgeTrue = "axiomation judge --aspect true --on " + T + words + " --verdict … --depth … --evidence …";
    const judgeOf = (asp) => (asp === "valid" ? judgeValid : asp === "true" || !asp ? judgeTrue : "axiomation judge --aspect " + asp + " --on " + T + " --verdict … --depth … --evidence …");
    const fill = "the verdict, depth and evidence";
    const sessionOf = (asp) => (asp === "cited" ? "citationcheck" : asp === "computed" ? "methods" : "argument");
    const review = (action, asp, more = "") => ({ action, how: "review", paid: true, command: "axiomation review --session " + sessionOf(asp) + more + " --budget …", starter: "a budget" });
    const relink = relinkOf(j, ctx, T, at.claimWords, at.sole !== false);
    const edges = { how: "graph", command: "axiomation sheet --stage edges -o edges.md", starter: "edit the sheet, then axiomation edit --stage edges --file edges.md" };
    const sp = s.support || {};
    switch (cs.status) {
      case "unjudged":
        if (aspect === "valid") return [review("Request an argument review", "valid"), { action: "Judge this argument", when: "once checked that it proves the claim", how: "judgment", command: judgeValid, starter: fill }, relink].filter(Boolean);
        if (aspect === "true" || !aspect) return [review("Request a claim review", "true"), { action: "Judge this claim", when: "once the statement itself is checked", how: "judgment", command: judgeTrue, starter: fill }];
        if (aspect === "cited") return [review("Request a citation review", "cited"), { action: "Judge this citation", when: "having read the cited pages", how: "judgment", command: "axiomation judge --aspect cited --on " + T + " --span … --work … --page … --verdict … --depth … --evidence …", starter: "the site, work, page and reading" }];
        if (aspect === "computed") return [review("Request a methods review", "computed"), { action: "Check the computation", when: "having run it and matched its output", how: "judgment", command: "axiomation judge --aspect computed --on " + T + " --file … --run … --value … … --verdict … --depth computational --evidence …", starter: "the run and its values" }];
        return [{ action: "Judge it", how: "judgment", command: judgeOf(aspect), starter: fill }];
      case "inconclusive":
        return [{ action: "Supply what it could not read", when: "the works or files the reading names", how: "evidence" },
          review("Request a full rereading", aspect, " --all-again"), { action: "Judge it", how: "judgment", command: judgeOf(aspect), starter: fill }];
      case "defect": {
        if (/^finding /.test(cs.detail || "")) return findingWays(cs);
        if (/\(lapsed\)/.test(cs.detail || "")) return [{ action: "Read what changed", how: "inspect", command: "axiomation reask " + ((/reask` (\S+)/.exec(cs.next || "") || [])[1] || "JUDGMENT") }, review("Request a fresh reading", aspect)];
        const fix = aspect === "valid" ? { action: "Repair the argument", when: "if the objection is right", how: "text" } : { action: "Correct or narrow the claim", when: "if the objection is right", how: "text" };
        return [fix, { action: "Record a judgment", when: "if it holds, with reasons", how: "judgment", command: judgeOf(aspect), starter: fill }, aspect === "valid" ? relink : null].filter(Boolean);
      }
      case "disputed": {
        const f = (/finding (\S+?)(?: answered|,|$)/.exec(cs.next || "") || [])[1];
        return [{ action: "Decide between the readings", when: "after reading their evidence", how: "judgment", command: judgeOf(aspect), starter: fill },
          f ? { action: "Answer the finding", when: "that disputes it", how: "judgment", command: "axiomation answer-finding " + f + " --as … --note …", starter: "the answer and its reason" } : null].filter(Boolean);
      }
      case "lapsed": {
        const id = (/reask` (\S+)/.exec(cs.next || "") || [])[1];
        return [review("Request a fresh reading", aspect),
          { action: "Read what changed", how: "inspect", command: "axiomation reask " + (id || "JUDGMENT") },
          aspect === "computed" ? null : { action: "Reaffirm the reading", when: "if it still holds on rereading", how: "judgment", command: "axiomation reaffirm " + (id || "JUDGMENT") + " --depth … --evidence …", starter: "the depth and evidence" }].filter(Boolean);
      }
      case "stale":
        return [{ action: "Confirm the lines still hold", when: "having read them against what changed", how: "reread", command: "axiomation reread " + T + " --reason …", starter: "what was read" },
          { action: "Request a reread", how: "reread", paid: true, command: "axiomation reread " + T + " --ask --budget …", starter: "a budget" },
          { ...edges, action: "Correct the marked lines", when: "in the edges sheet, which shows the marks" }];
      case "rests-on":
        return cs.at ? [{ action: "Settle " + ctx.placeOf(cs.at, false) + " first", when: "this claim waits on it", how: "elsewhere" },
          { ...edges, action: "Correct the dependency", when: "if it does not rely on it" }] : [];
      case "finding-open":
        return findingWays(cs);
      case "unsupported":
        return unsupportedWays(sp, s, at, { review, judgeTrue, fill, q, edges });
      default:
        return [];
    }
  }
  // An open finding's ways, by what its answer asks: a person's answer
  // where a review handed it back, a review where your proposal asks one,
  // its reaffirmation where it lapsed (which keeps the problem).
  function findingWays(cs) {
    const id = (/finding (\S+)/.exec(cs.detail || "") || [])[1] || (/answer-finding` (\S+)/.exec(cs.next || "") || [])[1] || "FINDING";
    return findingWaysOf(id, cs.next || "");
  }
  function findingWaysOf(id, next, reviewer = false) {
    const ways = [{ action: "Fix the problem", when: "then mark it fixed", how: "text", command: "axiomation answer-finding " + id + " --as fixed --note …", starter: "what changed" },
      { action: "Dismiss it", when: "if it is not a problem, with reasons" + (reviewer ? " (an outside reviewer's needs them)" : ""), how: "judgment", command: "axiomation answer-finding " + id + " --as dismissed --note …", starter: "the reasons" },
      { action: "Mark it a duplicate", when: "of the same problem filed again", how: "judgment", command: "axiomation answer-finding " + id + " --as duplicate --of … --note …", starter: "the other finding and why" }];
    if (/settling the proposal/.test(next)) ways.unshift({ action: "Let a review weigh the proposed answer", how: "review", paid: true, command: ((/settling the proposal \(`([^`]+)`\)/.exec(next) || [])[1] || "axiomation review") + " --budget …", starter: "a budget" });
    if (/reaffirm-finding/.test(next)) ways.push({ action: "Confirm it still stands", when: "it lapsed; the problem stays open", how: "judgment", command: "axiomation reaffirm-finding " + id + " --evidence …", starter: "why it stands" });
    return ways;
  }
  function unsupportedWays(sp, s, at, x) {
    const T = at.claim;
    const words = at.words ? " --claim " + x.q(at.words) : "";
    const rule = (action, when, kind = "…") => ({ action, when, how: "ruling", command: "axiomation rule --on " + T + words + " --ruling " + kind + " --reason …", starter: "the ruling and its reason" });
    const link = { action: "Link its argument", when: "if the paper gives one", how: "graph", command: "axiomation change --add BLOCK '→ justifies " + T + " · \"…\"'", starter: "the argument's block and its words" };
    const write = { action: "Add the argument", when: "if the paper owes it", how: "text" };
    if (sp.gap === "circular") return [{ ...x.edges, action: "Name the exact claims relied on", when: "where coarse links make the circle" },
      { action: "Ask an agent to inspect the circle", how: "review", paid: true, command: "axiomation circles --ask --budget …", starter: "a budget" },
      { action: "Give an independent argument", when: "if the circle is real", how: "text" }];
    if ((sp.conjectures || []).length) return [{ action: "Argue and assert the conjecture", when: "if the paper means it as a result", how: "text" },
      { action: "Stop asserting it", when: "if the sentence should report a conjecture", how: "text" }, { ...x.edges, action: "Name the claim it restates", when: "if the link chose the wrong one" }];
    if (s.declared === "promised" || sp.guard === "promised") return [{ action: "Give the promised argument", how: "text" }, { action: "Take the promise out", how: "text" }, { ...x.edges, action: "Correct the declaration", when: "if the paper promises nothing" }];
    if (sp.proposal) return [x.review("Request an argument review", "valid"), rule("Decide the ruling", "in place of the proposal")];
    if (sp.guard === "true-not-held") return [x.review("Have the claim checked", "true"), { action: "Judge the claim", how: "judgment", command: x.judgeTrue, starter: x.fill }, rule("Record a ruling", "a person's ruling decides it")];
    if (sp.guard === "true-disputed") return [{ action: "Decide the truth reading", how: "judgment", command: x.judgeTrue, starter: x.fill }, rule("Record a ruling", "a person's ruling decides it")];
    if (sp.guard === "no-result-used") return [{ action: "Link the result it follows from", how: "graph", command: "axiomation change --add " + T + " '→ uses RESULT · \"…\"'", starter: "the result and its words" }, { action: "Give an argument", when: "if it is not immediate", how: "text" }, rule("Correct the ruling", "if another kind is right")];
    if (sp.guard === "relied-on") return [{ action: "Argue the claim relied on", how: "text" }, { ...x.edges, action: "Correct the reliance", when: "if the wrong claim is relied on" }];
    if (s.status === "justified") return [link, { ...x.edges, action: "Correct its standing", when: "if it is not justified" }, write];
    if (s.status === "restated") return [{ ...x.edges, action: "Name the claim it restates" }, { ...x.edges, action: "Correct its standing", when: "if it is no restatement" }];
    if (sp.ruling === "owed") return [link, write, sp.by_person ? rule("Revise the ruling", "if the assessment changed") : rule("Record a ruling", "if it owes none")];
    return [link, { action: "Search the paper for its support", how: "review", paid: true, command: "axiomation support --ask --budget …", starter: "a budget" }, write,
      rule("Record why it needs no argument", "known, immediate, interpretation or positioning")];
  }

  // What an established claim assumes: a few named, more folded under
  // their count.
  function assuming(assumed, link) {
    if (assumed.length <= 3) return assumed.map(link).join(", ");
    return '<details class="assuming"><summary>' + assumed.length + "</summary>" + assumed.map(link).join(", ") + "</details>";
  }

  // A judgment's value on its row: what it is of where that is not the
  // block (a justification → its claim, a work, a file), its status, the
  // verdict that holds it,
  // its readings folded under their count (a person's proposed one counted
  // apart); a lapse's cause and what clears it.
  function judged(j, ctx, subject) {
    const n = (j.judgments || []).length;
    const head = (subject ? '<span class="of">' + subject + "</span>: " : "") + status(j.status) + heldVerdict(j) + (n ? ' <span class="fact-k">· ' + n + (n === 1 ? " reading" : " readings") + "</span>" : "") +
      (j.proposal ? ' <span class="fact-k">· 1 proposed</span>' : "");
    return (n || j.proposal ? '<details class="readings"><summary>' + head + "</summary>" + readingsOf(j, false) + "</details>" : head) +
      causeLine(j.cause, ctx) + clearsLine(j.clears);
  }

  // --- the judgments' layer ---

  // Each page read: its name and how it was read; provisional where any
  // was read off a copy that is not the version of record.
  function pageChips(pages, provisional) {
    if (!pages || !pages.length) return "";
    return '<div class="r-pages"><span class="fact-k">pages</span> ' + pageList(pages, provisional) + "</div>";
  }
  function pageList(pages, provisional) {
    return pages.map((p) =>
      '<span class="page-chip rs-' + escape(p.read_status) + '" title="' + escape(p.name + ": read " + p.read_status) + '">' + escape(p.name) +
      ' <span class="rs">' + escape(p.read_status) + "</span></span>").join(" ") +
      (provisional ? ' <span class="flag provisional" title="read off a copy that is not the version of record">provisional</span>' : "");
  }

  // One reading of a subject: its verdict, depth, author, the state it was
  // judged on, live or lapsed, what it reaffirms; its evidence quoted as an
  // edge's is; its pages; a computation's run, its values and code lines.
  function reading(r) {
    const head = '<span class="verdict v-' + escape(r.verdict) + '">' + escape(r.verdict) + "</span>" +
      '<span class="fact-k">' + escape(r.depth) + "</span>" +
      by(r.author) +
      '<span class="fact-k" title="' + escape(r.state) + '">on ' + escape(String(r.state).slice(0, 8)) + "</span>" +
      (r.lapsed ? '<span class="r-lapsed">lapsed</span>' : "") +
      (r.reaffirms ? '<span class="fact-k">reaffirms ' + escape(r.reaffirms) + "</span>" : "") +
      '<span class="r-id">' + escape(r.id) + "</span>";
    const run = r.run ? '<div class="r-run"><span class="fact-k">run</span> <code>' + escape([r.run.file, ...(r.run.arguments || [])].join(" ")) + "</code> " +
      '<span class="run-' + escape(r.run.status) + '">' + escape(r.run.status) + "</span></div>" : "";
    const values = (r.values || []).map((v) => '<div class="r-value"><span class="q">“' + escape(v.text) + '”</span> <span class="fact-k">↔</span> <span class="q">' + escape(v.output) + "</span></div>").join("");
    const code = (r.code || []).length ? '<pre class="r-code">' + r.code.map(escape).join("\n") + "</pre>" : "";
    return '<div class="reading' + (r.lapsed ? " lapsed" : "") + '" data-judgment="' + escape(r.id) + '"><div class="r-head">' + head + "</div>" +
      (r.evidence ? '<div class="quote">“' + escape(r.evidence) + "”</div>" : "") +
      pageChips(r.pages, r.provisional) + run + values + code + "</div>";
  }

  // A person's proposed reading: its verdict, author and evidence; the
  // reading that weighed it since, or that the next review asks it.
  function proposed(p) {
    if (!p) return "";
    const weighed = p.reask
      ? '<div class="f-note"><span class="fact-k">weighed by ' + escape(p.reask.author) + "</span> " + '<span class="verdict v-' + escape(p.reask.verdict) + '">' + escape(p.reask.verdict) + "</span> " +
        escape(p.reask.verdict === p.verdict ? "taken" : "not taken") + ' <span class="r-id">' + escape(p.reask.id) + "</span></div>"
      : '<div class="f-note"><span class="fact-k">' + escape(p.lapsed ? "lapsed" : "asked by the next review of its kind") + "</span></div>";
    return '<div class="reading proposed" data-judgment="' + escape(p.id) + '"><div class="r-head"><span class="fact-k">proposed</span>' +
      '<span class="verdict v-' + escape(p.verdict) + '">' + escape(p.verdict) + "</span>" +
      by(p.author) + '<span class="r-id">' + escape(p.id) + "</span></div>" +
      (p.evidence ? '<div class="quote">“' + escape(p.evidence) + "”</div>" : "") + weighed + "</div>";
  }

  // A lapse's cause: its kind, the state it is named at, why, the element
  // that changed a link where it is a block's (a node, an edge's source).
  function causeLine(cause, ctx) {
    return cause ? '<div class="lapse"><span class="r-lapsed">lapsed</span> ' + lapseWords(cause, ctx) + "</div>" : "";
  }
  function lapseWords(cause, ctx) {
    const e = cause.element || {};
    const at = e.kind === "node" || e.kind === "edge" ? " · " + go(e.name, ctx.nodeName(e.name)) : "";
    return 'at <span title="' + escape(cause.state) + '">' + escape(String(cause.state).slice(0, 8)) + "</span>: " + escape(cause.kind) + ", " + escape(cause.why) + at;
  }

  function clearsLine(clears) {
    return clears ? '<div class="clears"><span class="fact-k">clears</span> <code>' + escape(clears) + "</code></div>" : "";
  }

  // A subject's readings, folded on a claim's row under its `true` chip.
  function readingsOf(item, folded) {
    const rows = (item.judgments || []).map((r) => reading(r)).join("") + proposed(item.proposal);
    if (!rows) return "";
    const n = (item.judgments || []).length;
    const counted = (n === 1 ? "1 reading" : n + " readings") + (item.proposal ? (n ? ", 1 proposed" : "") : "");
    return folded ? '<details class="readings"><summary>' + (n ? counted : "1 proposed") + "</summary>" + rows + "</details>" : rows;
  }

  // What a judgment is of: a `valid` its J → T, a `cited` its work, a
  // `computed` its file, else the block.
  function subjectOf(sub) {
    if (sub.target) return go(sub.handle) + " → " + go(sub.target_handle || sub.target);
    if (sub.work) return "work " + escape(sub.work);
    if (sub.file) return "file:" + escape(sub.file);
    return "the block";
  }

  // A judgment of something other than one of the block's claims: its
  // aspect, its subject, its words, its status; a lapsed or disputed one's
  // cause and what clears it; then each reading.
  function otherJudgments(others, ctx) {
    return '<div class="judgments">' + others.map(({ item, line }) => {
      const sub = item.subject || {};
      // the question it answers, as a claim's lines read: whether this
      // argument proves the claim it argues, else its aspect of what it is
      // of; its answer and who holds it; where it is open, its ways
      const of = sub.target || sub.work || sub.file ? subjectOf(sub) : "";
      const label = item.aspect === "valid" ? "Proves" : escape(ASPECTS[item.aspect] || item.aspect);
      const what = item.aspect === "valid" && sub.target ? " " + (sub.target_handle ? ctx.placeOf(sub.target_handle, false) : of) + (sub.words ? ' <span class="e-quote">“' + escape(sub.words) + "”</span>" : "")
        : of ? " · of " + of : sub.words ? ' <span class="e-quote">“' + escape(sub.words) + "”</span>" : "";
      const value = (item.status === "unjudged" ? "not checked" : readingValue(item, ctx)) + what;
      return '<div class="judgment"' + (line ? ' data-line="' + escape(line) + '"' : "") + ">" + answer(signOf(item), label, value) + waysFold(judgmentWays(item, ctx), false, item.clears || "") + "</div>";
    }).join("") + "</div>";
  }
  // A judgment's ways, as a claim's for the same status: its subject the
  // claim it argues (a `valid`'s target) or what it is of.
  function judgmentWays(item, ctx) {
    const held = item.held && (item.judgments || []).find((r) => r.id === item.held);
    const status = item.status === "held" && held ? (held.verdict === "verified" ? null : held.verdict === "inconclusive" ? "inconclusive" : "defect")
      : item.status === "held" ? null : item.status;
    if (!status) return [];
    const sub = item.subject || {};
    const cs = { status, detail: "`" + item.aspect + "` of " + (sub.handle || "") + " →", next: item.clears || "" };
    return claimWays(cs, { status: "justified", support: {} }, { claim: sub.target_handle || sub.handle || "", words: sub.words || "", justifier: item.aspect === "valid" ? { handle: sub.handle, source: sub.node, line: "" } : null }, ctx);
  }

  // Where a finding is anchored: each anchor, a block or a group or a
  // part's or span's words a link to it, a work (one the paper cites
  // nowhere said so), a file's lines, the paper.
  function anchorOf(a, ctx) {
    const words = a.words ? ' <span class="q">“' + escape(a.words) + "”</span>" : "";
    if (a.kind === "work") return "work " + escape(a.work) + (a.cited_nowhere ? " (cited nowhere in the paper)" : "");
    if (a.kind === "file") return "file:" + escape(a.file) + words;
    if (a.kind === "paper" || !a.node) return "the paper";
    return go(a.node, ctx.nodeName(a.node)) + words;
  }

  // A finding: its severity, aspect, state (open, lapsed, or its answer),
  // author, the id it was first filed under and, refiled, the filing it
  // became; its problem and fix; every anchor; its pages; the answer's
  // note; a refiled one's reason it stands now first (a disagreement of
  // reading, the person's to answer), its problem as first filed behind
  // it, and the proposal it turned down; a proposal, an agent's or a
  // person's; the findings the open view
  // piles with it; a lapse's cause; what clears it. `entries` are the
  // report's findings placed (`place`), each with its index and line.
  function findingRows(entries, ctx) {
    return entries.map(({ item: f, line, i }) => {
      const same = ctx.sameOf(f.id);
      const first = f.first && f.first !== f.id ? f.first : f.id;
      const refiled = f.first && f.first !== f.id ? '<span class="f-state">refiled as ' + escape(f.id) + "</span>"
        : f.refiled_as ? '<span class="f-state">refiled as ' + escape(f.refiled_as) + "</span>" : "";
      const proposal = (p, taken) => by(p.author) + " " + escape(p.note || "") + (taken ? "" : ' <span class="fact-k">not taken</span>');
      const proposalKind = (p) => "proposed " + p.status + (p.of ? " of " + p.of : "");
      // a problem long enough to tire folds to its first lines, a click
      // unfolding it
      const long = (text) => String(text).length > 320;
      const problem = (text, label) => '<div class="f-problem' + (long(text) ? " clamped" : "") + '">' + (label ? '<span class="fact-k">' + escape(label) + "</span> " : "") + escape(text) + "</div>" +
        (long(text) ? '<button type="button" class="f-more">more</button>' : "");
      const restated = f.evidence && f.first && f.first !== f.id;
      const rows = [
        restated ? fact("first filed", escape(f.problem)) : "",
        f.fix ? fact("fix", escape(f.fix)) : "",
        fact("at", (f.anchors || []).map((a) => anchorOf(a, ctx)).join('<span class="gsep"> · </span>')),
        f.pages && f.pages.length ? fact("pages", pageList(f.pages, false)) : "",
        f.answer && f.answer.note ? fact(f.answer.status, escape(f.answer.note)) : "",
        f.turned_down ? fact(proposalKind(f.turned_down), proposal(f.turned_down, false)) : "",
        f.proposal ? fact(proposalKind(f.proposal), proposal(f.proposal, true)) : "",
        same.length ? fact("the same", same.map(escape).join(", ")) : "",
        f.cause ? fact("lapsed", lapseWords(f.cause, ctx)) : "",
      ].join("");
      const open = f.clears && !f.refiled_as;
      const ways = open ? waysFold(findingWaysOf(f.id, f.clears, whoKind(f.author) === "reviewer"), false, f.clears) : "";
      return '<div class="finding sev-' + escape(f.severity) + " fs-" + escape(f.state) + (f.refiled_as ? " fs-refiled" : "") + '" data-finding="' + escape(f.id) + '" data-sec="findings" data-i="' + i + '" data-line="' + escape(line) + '">' +
        '<div class="r-head">' + by(f.author) + '<span class="sev sev-' + escape(f.severity) + '">severity ' + escape(f.severity) + "</span>" + aspect(f.aspect) +
        '<span class="f-state">' + escape(f.refiled_as ? "refiled" : f.state) + "</span>" + refiled + "</div>" +
        (restated ? problem(f.evidence, f.reading ? "a disagreement of reading, a person's to answer" : "stands") : problem(f.problem)) +
        '<div class="claim-facts">' + rows + "</div>" + ways +
        '<div class="f-by"><span class="r-id">' + escape(first) + "</span></div></div>";
    }).join("");
  }

  // Where each of the report's findings and rulings shows: a finding on a
  // claim's words under that claim (`claims[i]`), the others the block's
  // (`block`), a filing refiled since never beside the filing it became,
  // which names it; a ruling on a claim's words in that claim's history,
  // the others (on words the block no longer holds) in the Rulings section.
  function place(report, rows) {
    const node = report.block.node;
    const fs = report.findings || { items: [], lines: [] };
    const claims = rows.map(() => []);
    const block = [];
    const ids = new Set(fs.items.map((f) => f.id));
    fs.items.forEach((item, i) => {
      if (item.refiled_as && ids.has(item.refiled_as)) return;
      const entry = { item, line: fs.lines[i], i };
      const at = rows.findIndex((r) => onWords(item, node, r.words));
      (at >= 0 ? claims[at] : block).push(entry);
    });
    const rs = report.rulings || { items: [], lines: [] };
    const words = new Set(rows.map((r) => r.words));
    const rulings = [];
    const earlier = [];
    rs.items.forEach((item, i) => (words.has(item.claim) ? rulings : earlier).push({ item, line: rs.lines[i], i }));
    return { claims, block, rulings, earlier };
  }

  // A suggestion: its state (open, lapsed, or its answer), author, the
  // works it offers (one the paper cites nowhere said so), why, its pages,
  // the answer's note; what answers it.
  function suggestionRows(section) {
    return section.items.map((s, i) =>
      '<div class="suggestion ss-' + escape(s.state) + '" data-sec="suggestions" data-i="' + i + '" data-line="' + escape(section.lines[i]) + '">' +
        '<div class="r-head"><span class="f-state">' + escape(s.state) + '</span>' + by(s.author) + '<span class="r-id">' + escape(s.id) + "</span></div>" +
        '<div class="s-works"><span class="fact-k">cite</span> ' + (s.works || []).map((w) => "<code>" + escape(w) + "</code>" + ((s.cited_nowhere || []).includes(w) ? " (cited nowhere in the paper)" : "")).join(", ") + "</div>" +
        '<div class="f-problem">' + escape(s.why) + "</div>" +
        pageChips(s.pages, false) +
        (s.note ? '<div class="f-note"><span class="fact-k">' + escape(s.state) + "</span> " + escape(s.note) + "</div>" : "") +
        (s.state === "open" || s.state === "lapsed" ? clearsLine("axiomation answer-suggestion PAPER " + s.id + " --as taken (or declined)") : "") + "</div>").join("");
  }

  // The line a stale mark is on, from the block's side: its edge, the
  // block's own end left off, else its own line.
  function markedLine(m, handles, own) {
    const edge = m.edge && m.edge.startsWith(own + " → ") ? m.edge.slice(own.length + 3) : m.edge;
    return edge ? linked(edge, handles) : '<span class="who-plain">its own line</span>';
  }

  // The open stale marks, each a row: the line an update asked again,
  // when, why. Those a reread or a reading closed are the history's.
  function markRows(section, handles, ctx, own) {
    return section.items.map((m, i) => {
      if (m.closed_by) return "";
      const why = section.lines[i].slice(section.lines[i].indexOf(": ") + 2).replace(/ {2}\(a description: no gate asks it\)$/, "");
      const when = "marked stale " + ctx.whenOf(m.since).split(" · ")[0] + (m.state === "description" ? " · a description: no gate asks it" : "");
      return '<div class="mark-row" data-sec="marks" data-i="' + i + '" data-line="' + escape(section.lines[i]) + '">' +
        '<div class="who">' + markedLine(m, handles, own) + "</div>" + '<div class="m-when">' + escape(when) + "</div>" +
        (why ? '<div class="m-why">' + linked(why, handles) + "</div>" : "") + "</div>";
    }).join("");
  }

  // A closed mark on the history's step that marked it: its line, and
  // what read it again.
  function closedMark({ m, line }, handles, own) {
    return '<div class="h-cause h-closed" data-line="' + escape(line) + '">stale: ' + markedLine(m, handles, own) +
      ' <span class="fact-k">· closed by ' + escape(m.read_by || "a reading") + " " + escape(String(m.closed_by).slice(0, 8)) + (m.by_person ? " (a person)" : "") + "</span></div>";
  }

  // A step's class (`versions.Step.change`) in words.
  const CHANGES = {
    built: "first kept",
    added: "added",
    changed: "its text changed",
    moved: "moved",
    split: "split",
    merged: "merged",
    deleted: "deleted",
    recut: "cut again",
  };

  // A cause the run recorded for the block: what happened (`kind`, the
  // row's heading names it) and its words, its handles links: a Δ, a
  // reopening, a block left unasked, a move by the rule, a stale mark. A
  // person's answers kept at an edit are the state's, not the block's:
  // null.
  function causeOf(c, handles) {
    const reasons = (rs) => rs.map((r) => linked(r.why + (r.via ? " (via " + r.via + ")" : ""), handles)).join("; ");
    if ("delta" in c) return { kind: c.delta, words: escape(c.note || "") + (c.by ? ' <span class="r-by">by ' + escape(c.by) + "</span>" : "") };
    if ("reopened" in c) return { kind: "reopened", words: linked(c.reopened, handles) };
    if ("not_asked" in c) return { kind: "not asked", words: linked(c.not_asked, handles) };
    if ("moved_by_rule" in c) return { kind: "meaning moved by the rule", words: reasons(c.moved_by_rule) };
    if ("stale" in c) return { kind: "marked stale", words: reasons(c.stale) };
    return null;
  }

  // An edge a step began (+), ended (−), changed (~) or carried across a
  // split or a merge (→ to, ← from the other piece), from the block's
  // side: its verb, the other end a link, the parts it names (and those
  // it named before), the piece it was carried to or from, by a person
  // noted.
  const EDGE_SIGNS = { began: "+", ended: "−", changed: "~", "carried-to": "→", "carried-from": "←" };
  function edgeLine(e, node, ctx) {
    const own = e.source === node;
    const verb = e.kind === "self" ? SELF : VERBS[e.kind] ? VERBS[e.kind][own ? 0 : 1] : e.kind + (own ? "" : " (onto it)");
    const end = own ? e.target : e.source;
    const other = e.kind === "self" || !end ? "" : " " + go(end, ctx.nodeName(end));
    // an entry names the target's parts (`handle [K]`): onto the block,
    // its own (on [K]); from it, the other's ([K])
    const parts = (ps) => ps.map((x) => (/^[^\s“]+ \[.*\]$/.test(x) ? (own ? "" : "on ") + "[" + entryLabel(x) + "]" : x)).map(escape).join(", ");
    return '<div class="h-edge s-' + escape(e.step) + '"><span class="h-sign">' + (EDGE_SIGNS[e.step] || "") + "</span> " + escape(verb) + other +
      (e.parts.length ? ' <span class="parts">' + parts(e.parts) + "</span>" : "") +
      (e.step === "changed" ? ' <span class="fact-k">was ' + (e.was.length ? parts(e.was) : "no parts") + "</span>" : "") +
      (e.piece ? ' <span class="fact-k">' + (e.step === "carried-to" ? "carried to" : "carried from") + "</span> " + go(e.piece, ctx.nodeName(e.piece)) : "") +
      (e.by === "person" ? ' <span class="flag person">by a person</span>' : "") + "</div>";
  }

  // The steps of the node's chain where something happened to it, the
  // newest first: a heading that names what happened (its text changed,
  // what the update did to it, its edges changed), when and by which
  // command; the causes' words; its edges begun, ended or changed; the
  // nodes a split or merge joined it with; where its text changed, a
  // place the page fills with the word change (`h-diff`); from the
  // second step, a comparison of the two states in the paper. A step that
  // only marked it stale, its edges unchanged, folds with the others like
  // it; the steps where nothing happened are counted.
  function historyRows(section, handles, ctx, node, closed = [], own = "") {
    const steps = section.items;
    const placed = new Set();
    const rows = [];
    const asked = [];
    let quiet = 0;
    let latest = "";
    steps.forEach((step, i) => {
      const before = i > 0 ? steps[i - 1] : null;
      const causes = (step.causes || []).map((c) => causeOf(c, handles)).filter(Boolean);
      const joined = step.joined || [];
      const edges = step.edges || [];
      const text = before && step.text && before.text && step.text !== before.text;
      // a split or a merge, named by the node's own side of it: split off
      // another where it began, merged into another where it ended; where
      // it held on, the page names which once it has read the others
      const cut = joined.length ? (!before ? "split off" : !step.block ? "merged into" : "split or merged") : null;
      const shut = closed.filter((c) => c.m.since === step.state);
      shut.forEach((c) => placed.add(c));
      const happened = [
        ...(cut ? [] : step.change !== "same" ? [CHANGES[step.change] || step.change] : []),
        ...new Set(causes.map((c) => c.kind)),
        ...(edges.length ? ["its edges changed"] : []),
      ];
      if (!happened.length && !cut) {
        quiet++;
        return;
      }
      const heading = (cut ? '<span class="h-cut" data-state="' + escape(step.state) + '">' + escape(cut) + "</span>" + (happened.length ? " · " : "") : "") + escape(happened.join(" · "));
      const row = '<div class="h-row" data-state="' + escape(step.state) + '"><div class="h-head"><span class="h-change c-' + escape(step.change) + '">' +
        heading + '</span><span class="h-when" title="' + escape(step.state) + '">' + escape(ctx.whenOf(step.state)) + "</span>" +
        (before ? '<button type="button" class="h-compare" data-from="' + escape(before.state) + '" data-to="' + escape(step.state) + '" title="compare the two states in the paper">compare in paper</button>' : "") + "</div>" +
        causes.filter((c) => c.words).map((c) => '<div class="h-cause">' + c.words + "</div>").join("") +
        edges.map((e) => edgeLine(e, node, ctx)).join("") +
        shut.map((c) => closedMark(c, handles, own)).join("") +
        (joined.length ? '<div class="h-cause h-joined">' + (!before ? "split off " : !step.block ? "its words went on into " : "with ") + joined.map((n) => go(n, ctx.nodeName(n))).join(", ") + "</div>" : "") +
        // the word change: across a split or a merge, of every node it
        // joined (the page reads each one's texts), else of its own
        (joined.length && step.block
          ? '<div class="h-diff" data-node="' + escape(node) + '" data-joined="' + escape(joined.join(" ")) + '" data-from="' + escape(before ? before.state : ctx.parentOf(step.state) || "") + '" data-to="' + escape(step.state) + '"></div>'
          : text ? '<div class="h-diff" data-from="' + escape(before.state) + '" data-from-block="' + escape(before.block) + '" data-to="' + escape(step.state) + '" data-to-block="' + escape(step.block) + '"></div>' : "") +
        "</div>";
      // the folded head names the newest step a row shows
      const stale = happened.length === 1 && happened[0] === "marked stale" && !joined.length;
      if (!stale) latest = heading + ", " + escape(ctx.whenOf(step.state).split(" · ")[0]);
      (stale ? asked : rows).push(row);
    });
    rows.reverse();
    asked.reverse();
    if (asked.length) rows.push('<details class="h-asked"><summary>' + (asked.length === 1 ? "1 update" : asked.length + " updates") + " marked it stale; its edges did not change</summary>" + asked.join("") + "</details>");
    // a closed mark whose marking step the history does not show
    const left = closed.filter((c) => !placed.has(c));
    if (left.length) rows.push('<div class="h-row">' + left.map((c) => closedMark(c, handles, own)).join("") + "</div>");
    if (quiet) rows.push('<p class="h-quiet">' + quiet + (quiet === 1 ? " other state" : " other states") + ": nothing changed for it</p>");
    return { html: rows.join(""), count: steps.length - quiet, latest };
  }

  // The card: `report` is `show`'s JSON (`--whole --history --limit 0`),
  // `handles` the state's handles (blocks and groups), `ctx` how the page
  // names a block or group (`nameOf`, `whatOf`, `lineOf`) and a node
  // (`nodeName`), the findings the open view piles with a finding
  // (`sameOf`), and the sections folded (`shut`). Its edges read from the selection's side
  // (`edgeSection`), its standings as status and words, each sheet line
  // kept in `data-line`; every other section its query's lines.
  const EDGES = ["rests_on", "rested_on_by"];
  const PLAIN = { placeOf: (ref) => go(ref), linesOf: (refs) => refs.map((r) => go(r)).join(", "), ordered: (refs) => refs, nameOf: (id) => id, whatOf: () => "", lineOf: () => "", nodeName: (node) => node, whenOf: (state) => state.slice(0, 8), parentOf: () => null, sameOf: () => [], shut: new Set(), opened: new Set() };
  function html(report, handles, back, ctx) {
    const selection = report.block.block || report.block.group;
    const named = { ...PLAIN, ...(ctx || {}) };
    const out = [];
    if (back) out.push('<div class="card-nav">' + back + "</div>");
    out.push(header(report.block, handles, named));
    const items = { ...NO_OPEN, ...(named.open || {}) };
    const found = claims(report);
    // what it does beside its claims, a section each: what it argues (a
    // J's validity readings), defines or assumes (its use, its reading),
    // cites (its citation readings); the readings left, Judgments
    const roles = report.block.roles || [];
    const argues = found.others.filter((o) => o.item.aspect === "valid" && o.item.subject && o.item.subject.target);
    const cites = found.others.filter((o) => o.item.aspect === "cited");
    // a citation no reading has read: `show` lists what was read, the
    // gates' items (`ctx.open.unread`) what is owed a reading
    for (const u of items.unread) cites.push({ item: { aspect: "cited", status: "unjudged", subject: { ...u.subject, handle: u.handle }, clears: u.clears }, line: u.line });
    found.others = found.others.filter((o) => !argues.includes(o) && !cites.includes(o));
    const premise = roles.includes("definition") ? "defines" : roles.includes("assumption") ? "assumes" : null;
    const roleSections = roleSectionsOf({ report, found, argues, cites, premise, items, ctx: named });
    // the status, once the card is drawn: its open count the problems it shows
    const statusAt = out.push("") - 1;
    // a block a review judges nothing on says so, and when one last read
    // it, where its sections would be, unless its role's section says it
    if (report.no_claim && !premise) out.push('<div class="no-claim">' + noClaim(report.no_claim).map((l) => "<p>" + escape(l) + "</p>").join("") + "</div>");
    const placed = place(report, found.rows);
    // the stale marks: the open ones their section's, the closed ones the
    // history's, on the step that marked them
    const ms = report.marks || { items: [], lines: [] };
    const marked = ms.items.map((m, i) => ({ m, line: ms.lines[i] }));
    const marks = { open: marked.filter((x) => !x.m.closed_by), closed: marked.filter((x) => x.m.closed_by) };
    // the open items on it (`ctx.open`): a line of what there is to
    // settle here, what stands in its way from elsewhere and what waits for
    // a run; then each kind `show` holds no row of in its home
    const claimRows = items.rows.claim || [];
    const matched = new Set();
    const onClaim = (words) => claimRows.filter((r, k) => norm(r.words) === norm(words) && matched.add(k));
    found.rows.forEach((row) => (row.open = onClaim(row.words)));
    const graphRows = [...(items.rows.graph || []), ...claimRows.filter((r, k) => !matched.has(k))];
    if (graphRows.length) {
      const shown = named.opened.has("graph") || !named.shut.has("graph");
      out.push(sectionHead("graph", "On the graph here", GRAPH_HOLDS, graphRows.length, shown, "") + openRows(graphRows, [], named) + "</div></details>");
    }
    let rolesShown = false;
    for (const [name, title, holds] of SECTIONS) {
      if (!rolesShown && name !== "standings") {
        out.push(roleSections.html);
        rolesShown = true;
      }
      const section = report[name] || { total: 0, items: [], lines: [] };
      // an unused premise's row in its Assumes, not in what rests on it
      const extra = name === "rested_on_by" && premise ? [] : items.rows[name] || [];
      if (!section.total && !extra.length) continue;
      // the claims hold their judgments, whether they are established, the
      // findings on their words and their rulings
      if (name === "established") continue;
      if (name === "judgments" && !found.others.length) continue;
      if (name === "findings" && !placed.block.length) continue;
      if (name === "rulings" && !placed.earlier.length) continue;
      if (name === "marks" && !marks.open.length) continue;
      const steps = name === "history" ? historyRows(section, handles, named, report.block.node, marks.closed, report.block.handle) : null;
      if (steps && !steps.count) continue;
      const total = name === "judgments" ? found.others.length : name === "findings" ? placed.block.length : name === "rulings" ? placed.earlier.length : name === "marks" ? marks.open.length : steps ? steps.count : section.total;
      const { brief, busy } = summary(name, section, found, placed, steps, selection);
      // a section holding an open item's row asks something now: open
      const open = named.opened.has(name) || (!named.shut.has(name) && (extra.length > 0 || (!FOLDED.includes(name) && (!WHILE_OPEN.includes(name) || busy))));
      // a section that folds, its head a band that stays in view while
      // the section scrolls, so where a section starts and ends is clear;
      // what it holds on hover, and folded, what is in it
      out.push(sectionHead(name, title, holds, total, open, brief));
      if (steps) out.push(steps.html);
      if (EDGES.includes(name)) out.push((extra.length ? rowList(extra) : "") + (section.items.length ? edgeSection(name, section, selection, named) : ""));
      else if (name === "standings") out.push(claimSection(found, placed, handles, named, justifiersOf(report, selection), placed.rulings, report.block.handle));
      else if (name === "judgments") out.push(otherJudgments(found.others, named));
      else if (name === "findings") {
        // the block's own findings: the current first, the rest folded
        const now = placed.block.filter((x) => current(x.item));
        const past = placed.block.filter((x) => !current(x.item));
        out.push(findingRows(now, named) + (past.length ? '<details class="sec-history"><summary>history · ' + past.length + "</summary>" + findingRows(past, named) + "</details>" : ""));
      } else if (name === "rulings") {
        out.push("<ul>" + placed.earlier.map(({ line, i }) => '<li data-sec="rulings" data-i="' + i + '" data-line="' + escape(line) + '"><code class="sheet">' +
          linked(line, handles) + "</code></li>").join("") + "</ul>");
      }
      else if (name === "suggestions") out.push(suggestionRows(section));
      else if (name === "marks") out.push(markRows(section, handles, named, report.block.handle));
      else if (!steps) {
        out.push("<ul>" + section.lines.map((line, i) => '<li data-sec="' + name + '" data-i="' + i + '" data-line="' + escape(line) + '"><code class="sheet">' +
          linked(line, handles) + "</code></li>").join("") + "</ul>");
      }
      out.push("</div></details>");
    }
    const waiting = items.rows.waiting || [];
    if (waiting.length) out.push(sectionHead("waiting", "Waiting for a run", WAITING_HOLDS, waiting.length, named.opened.has("waiting"), "") + rowList(waiting) + "</div></details>");
    for (const note of report.notes || []) out.push('<p class="note">note: ' + escape(note) + "</p>");
    const drawn = out.join("");
    const shown = (drawn.match(/class="pcard /g) || []).length + (drawn.match(/class="finding sev-\S+ fs-open/g) || []).length;
    out[statusAt] = statusLine(found, items, roleSections, named, shown);
    out.push(sectionHead("record", "Record and command", "its ids and the commands that print it", null, named.opened.has("record"), "") + record(report) + "</div></details>");
    return out.join("");
  }

  // Each of `words` with its count, in the order met: a status counted
  // ahead ("2 established, unsupported"), a verb as its group's head
  // says it ("uses 6 · about 1").
  function tally(words, verbs = false) {
    const n = new Map();
    for (const w of words) n.set(w, (n.get(w) || 0) + 1);
    return verbs ? [...n].map(([w, k]) => w + " " + k).join(" · ") : [...n].map(([w, k]) => (k > 1 ? k + " " : "") + w).join(", ");
  }

  // What a folded section's head says it holds (`brief`, escaped), and
  // whether it holds something to act on now (`busy`).
  function summary(name, section, found, placed, steps, selection) {
    if (name === "standings") return { brief: escape(tally(found.rows.map((r) => (r.standing.claim_status || {}).status || r.standing.status))), busy: true };
    if (name === "findings") {
      const now = placed.block.filter((x) => current(x.item)).length;
      return { brief: now ? now + " open" : placed.block.length + " closed", busy: now > 0 };
    }
    if (name === "suggestions") {
      const now = section.items.filter((x) => x.state === "open" || x.state === "lapsed").length;
      return { brief: now ? now + " open" : section.items.length + " answered", busy: now > 0 };
    }
    if (name === "judgments") {
      const items = found.others.map((o) => o.item);
      const brief = items.length <= 2 ? items.map((j) => (ASPECTS[j.aspect] || j.aspect) + " " + j.status).join(", ") : tally(items.map((j) => j.status));
      return { brief: escape(brief), busy: items.some((j) => j.status !== "unjudged" && j.status !== "held") };
    }
    if (name === "marks") return { brief: section.items.filter((m) => !m.closed_by).length + " open", busy: true };
    if (EDGES.includes(name)) return { brief: escape(tally(section.items.map((item) => viewed(item, selection).verb), true)), busy: true };
    if (steps) return { brief: steps.latest, busy: false };
    return { brief: "", busy: false };
  }

  function sectionHead(name, title, holds, total, open, brief) {
    return '<details class="card-sec" data-sec="' + name + '"' + (open ? " open" : "") + '><summary class="sec-head"' + (holds ? ' title="' + escape(holds) + '"' : "") + ">" +
      '<span class="chev"></span><span class="title">' + escape(title) + "</span>" + (brief ? '<span class="sec-brief">' + brief + "</span>" : "") +
      (total === null ? "" : '<span class="count">' + total + "</span>") + '</summary><div class="sec-body">';
  }

  return { statusLine, problemCard, undeclaredCard, plainStatus, claimWays, findingWaysOf, waysFold, who, whoKind, step, noClaim, SECTIONS, FOLDED, WHILE_OPEN, VERBS, SELF, EDGES, GUARDS, GAPS, IN_GRAPH, ASPECTS, linked, viewed, claims, deciding, owedRow, place, html };
});
