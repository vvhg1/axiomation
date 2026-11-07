// Change mode: what changed between
// two kept states, as `axiomation changes` answers it, laid out for the
// page: each changed block by its node, its old text beside its new as a
// word diff, and the update's tiers, changed, reached, handled and open,
// and what it lapsed. And the edit tab: the paper's files against the
// latest state, an edit not yet updated, as `changes` answers it then.
//
// Pure: everything here is the query's answer indexed or regrouped; the
// classes, the Δ lines, the reasons and the authors are the query's.
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else (root.AxView = root.AxView || {}).changes = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // The classes `changes` gives a block's text (identity's; a
  // re-cut's within one version), in the order a badge names the
  // first of several.
  const CLASSES = ["added", "deleted", "split", "merged", "changed", "moved", "math", "markup", "macro"];

  // Old and new text as words, each run `{op, text}`: `same`, `del` (only
  // in the old) or `add` (only in the new), whitespace kept with its word.
  // A longest common subsequence over words; past `most` cells, the whole
  // old deleted and the whole new added.
  function words(old, now, most = 250000) {
    if (!old) return now ? [{ op: "add", text: now }] : [];
    if (!now) return [{ op: "del", text: old }];
    const a = old.split(/(?<=\s)/);
    const b = now.split(/(?<=\s)/);
    const steps = align(a, b, most);
    if (!steps) return [{ op: "del", text: old }, { op: "add", text: now }];
    const runs = [];
    for (const [op, i, j] of steps) push(runs, op, op === "del" ? a[i] : b[j]);
    return runs;
  }

  // Two word lists aligned, `[op, i, j]` a step: `same` (a[i] is b[j]),
  // `del` (a[i] only), `add` (b[j] only); null past `most` cells.
  function align(a, b, most = 250000) {
    const n = a.length;
    const m = b.length;
    if (n * m > most) return null;
    const lcs = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
    for (let i = n - 1; i >= 0; i -= 1) {
      for (let j = m - 1; j >= 0; j -= 1) {
        lcs[i][j] = a[i].trim() === b[j].trim() ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
      }
    }
    const steps = [];
    let i = 0;
    let j = 0;
    while (i < n && j < m) {
      if (a[i].trim() === b[j].trim()) steps.push(["same", i++, j++]);
      else if (lcs[i + 1][j] >= lcs[i][j + 1]) steps.push(["del", i++, j]);
      else steps.push(["add", i, j++]);
    }
    while (i < n) steps.push(["del", i++, j]);
    while (j < m) steps.push(["add", i, j++]);
    return steps;
  }

  // A run appended, joined to the last where it is the same op (and, for
  // `carried`, from the same node).
  function push(runs, op, text, other) {
    const last = runs[runs.length - 1];
    if (last && last.op === op && last.other === other) last.text += text;
    else runs.push(other === undefined ? { op, text } : { op, text, other });
  }

  // The word change across a split or a merge: the old texts of the
  // nodes it joined (`olds`, `{node, text}` in the earlier state's reading
  // order) against their new ones (`news`, in the later's), aligned as one
  // run of words, so words that only changed block are neither deleted nor
  // added. Each new node's runs: `same`, `add`, `del`, `carried` (words
  // that stood in another node, `other`), and `to` (where its words went on
  // into another node, `other`, at that point). A node alone is `words`.
  function carried(olds, news, most = 250000) {
    const cut = (parts) => parts.flatMap((p) => (p.text || "").split(/(?<=\s)/).filter(Boolean).map((w) => ({ w, node: p.node })));
    const a = cut(olds);
    const b = cut(news);
    const out = new Map(news.map((p) => [p.node, []]));
    const steps = align(a.map((t) => t.w), b.map((t) => t.w), most);
    if (!steps) {
      for (const p of news) out.set(p.node, words(olds.filter((o) => o.node === p.node).map((o) => o.text).join(""), p.text));
      return out;
    }
    let at = news.length ? news[0].node : null;
    let went = null;
    for (const [op, i, j] of steps) {
      if (op === "add") {
        at = b[j].node;
        push(out.get(at), "add", b[j].w);
        continue;
      }
      if (op === "del") {
        // struck where it stood: in the node the alignment is in
        push(out.get(at), "del", a[i].w);
        continue;
      }
      at = b[j].node;
      const from = a[i].node;
      if (from === at) {
        push(out.get(at), "same", b[j].w);
        went = null;
        continue;
      }
      push(out.get(at), "carried", b[j].w, from);
      // the node the words left, where it holds on, says where they went
      if (out.has(from) && went !== at) out.get(from).push({ op: "to", text: "", other: at });
      went = at;
    }
    return out;
  }

  // A changed block's badge: the first of its classes in `CLASSES` order.
  function badge(classes) {
    return CLASSES.find((c) => classes.includes(c)) || classes[0] || "changed";
  }

  // The report's text items by node: `{node, handle, classes, badge, old,
  // new, joined, line}`; a deleted block has no `new`, an added no `old`.
  function byNode(report) {
    const found = new Map();
    const text = report.text || { items: [], lines: [] };
    text.items.forEach((item, i) => {
      found.set(item.node, { ...item, badge: badge(item.classes), line: text.lines[i] });
    });
    return found;
  }

  // The update's tiers: changed, its text items and the Δ lines
  // its writer gave; reached, each block it asked again with why; handled,
  // the graph's lines begun and ended, with their authors; open, what the
  // later state still holds open from it, its stale marks and pending
  // edits. `open` is `open.items(...)` of the later state. Each tier a list
  // of `{kind, node, block, handle, line, data}`.
  const HANDLED = ["edges", "standings", "roles", "groups", "carried"];
  function tiers(report, open) {
    const rows = (name, kind, nodeOf, blockOf) => {
      const section = report[name];
      if (!section) return [];
      return section.items.map((item, i) => ({ kind, node: nodeOf(item), block: blockOf(item), handle: item.handle || null, line: section.lines[i], data: item }));
    };
    const changed = [
      ...rows("text", "text", (t) => t.node, (t) => (t.new ? t.new.block : null)),
      ...rows("deltas", "delta", (d) => d.node, (d) => d.block),
    ];
    const reached = rows("reopened", "reopened", (r) => r.node, (r) => r.block);
    const handled = HANDLED.flatMap((name) => rows(name, name, (x) => x.node || null, () => null));
    const stillOpen = (open || [])
      .filter((item) => item.kind === "stale" || item.kind === "pending")
      .map((item) => ({ kind: item.kind, node: item.node, block: item.block, handle: item.handle, line: item.line, data: item }));
    // what the edit lapsed: judgments and findings to read
    // again, not stale graph lines; the query's own section
    const lapsed = rows("lapsed", "lapsed", (x) => x.node, (x) => x.block);
    return { changed, reached, handled, open: stillOpen, lapsed };
  }

  // A block's words where the file holds them, in reading order: its owned
  // spans `[path, start, end]` cut at white space, each `{path, start,
  // end, word}`.
  function tokens(owned, textOf) {
    const out = [];
    for (const [path, start, end] of owned || []) {
      const slice = textOf(path).slice(start, end);
      for (const m of slice.matchAll(/\S+/g)) out.push({ path, start: start + m.index, end: start + m.index + m[0].length, word: m[0] });
    }
    return out;
  }

  // A word diff laid on the file: the new text's words found in order
  // among the block's own `tokens`, the kept text standing for what it
  // elides by ELIDED. Each run of added words a span `[path, start, end]`;
  // the words deleted before a word found, a ghost `[path, at, words]`
  // set before it (after the last word found, at the block's end); each
  // run of words carried from another node a span `[path, start, end,
  // node]`, and where words went on into another, `[path, at, node]`
  // (`carried`). A word not found is passed over, so a text the file does
  // not match marks less, never the wrong words.
  const ELIDED = "\u27e6\u2026\u27e7";
  const NEAR = 4;
  function place(runs, found) {
    const added = [];
    const ghosts = [];
    const carriedSpans = [];
    const tos = [];
    let k = 0;
    let skipping = false;
    let gone = [];
    let last = null;
    let addedAt = -2;
    const next = (word) => {
      const limit = skipping ? found.length : Math.min(found.length, k + NEAR);
      for (let j = k; j < limit; j += 1) {
        if (found[j].word === word) {
          k = j + 1;
          skipping = false;
          return j;
        }
      }
      return -1;
    };
    let carriedAt = -2;
    for (const run of runs) {
      if (run.op === "to") {
        tos.push(last ? [last.path, last.end, run.other] : found.length ? [found[0].path, found[0].start, run.other] : null);
        continue;
      }
      const words = (run.text.match(/\S+/g) || []).filter((w) => w !== ELIDED || run.op !== "del");
      if (run.op === "del") {
        gone.push(...words);
        continue;
      }
      for (const word of words) {
        if (word === ELIDED) {
          skipping = true;
          continue;
        }
        const j = next(word);
        if (j < 0) continue;
        const token = found[j];
        if (gone.length) {
          ghosts.push([token.path, token.start, gone.join(" ") + " "]);
          gone = [];
          addedAt = -2;
        }
        if (run.op === "add") {
          const span = added[added.length - 1];
          if (span && addedAt === j - 1 && span[0] === token.path) span[2] = token.end;
          else added.push([token.path, token.start, token.end]);
          addedAt = j;
        }
        if (run.op === "carried") {
          const span = carriedSpans[carriedSpans.length - 1];
          if (span && carriedAt === j - 1 && span[0] === token.path && span[3] === run.other) span[2] = token.end;
          else carriedSpans.push([token.path, token.start, token.end, run.other]);
          carriedAt = j;
        }
        last = token;
      }
    }
    if (gone.length && last) ghosts.push([last.path, last.end, " " + gone.join(" ")]);
    return { added, ghosts, carried: carriedSpans, tos: tos.filter(Boolean) };
  }

  function escape(text) {
    return String(text).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  }

  // A word diff as marked-up text: deletions struck, additions marked,
  // and a long stretch both say cut to `keep` words each side of a change
  // (all of it where `keep` is null).
  function diffHtml(runs, keep = null, nameOf = (node) => node) {
    const shown = runs.map((r, i) => {
      if (r.op === "carried") return '<span class="carried" title="' + escape("from " + nameOf(r.other)) + '">' + escape(r.text) + "</span>";
      if (r.op === "to") return '<span class="carried-to">→ continues in ' + escape(nameOf(r.other)) + "</span>";
      if (r.op !== "same") {
        const tag = r.op === "del" ? "del" : "ins";
        return "<" + tag + ">" + escape(r.text) + "</" + tag + ">";
      }
      if (keep === null) return escape(r.text);
      const w = r.text.split(/(?<=\s)/);
      const first = i === 0;
      const last = i === runs.length - 1;
      if (first && last) return escape(r.text);
      if (first) return w.length > keep ? "… " + escape(w.slice(-keep).join("")) : escape(r.text);
      if (last) return w.length > keep ? escape(w.slice(0, keep).join("")) + "…" : escape(r.text);
      return w.length > 2 * keep + 1 ? escape(w.slice(0, keep).join("")) + "… " + escape(w.slice(-keep).join("")) : escape(r.text);
    });
    return shown.join("");
  }
  const CONTEXT = 8;

  // The changes tab: a head naming the two states, then a section per
  // tier in the card's style, each row its query line kept in
  // `data-line`, a block's handle a link where the later state has it.
  const TIERS = [
    ["changed", "Changed", "the text, and the Δ lines its writer gave"],
    ["reached", "Reached", "asked again, and why"],
    ["handled", "Handled", "what the graph began and ended"],
    ["open", "Still open", "stale marks and pending edits"],
    ["lapsed", "Lapsed", "judgments and findings to read again"],
  ];
  function html(report, t, ctx) {
    const go = (row) => (row.block ? '<a class="go" data-ref="' + escape(row.block) + '">' + escape(row.handle || row.block) + "</a>" : '<span class="gone">' + escape(row.handle || row.node || "") + "</span>");
    const out = ['<div class="cx-head"><b>' + escape(ctx.name(report.from)) + "</b> → <b>" + escape(ctx.name(report.to)) + "</b></div>"];
    for (const [name, title, holds] of TIERS) {
      const rows = t[name];
      out.push('<details class="card-sec" data-sec="' + name + '"' + (ctx.shut && ctx.shut.has(name) ? "" : " open") + '><summary class="sec-head"><span class="chev"></span><span class="title">' + title +
        '</span><span class="holds">' + holds + '</span><span class="count">' + rows.length + '</span></summary><div class="sec-body">');
      if (!rows.length) out.push('<p class="cx-none">none</p>');
      for (const row of rows) {
        out.push('<div class="cx-row" data-line="' + escape(row.line) + '"' + (row.block ? ' data-block="' + escape(row.block) + '"' : "") + ">");
        if (row.kind === "text") {
          const d = row.data;
          // a split's pieces and a merge's blocks name each other
          const joined = (d.joined || []).map((node) => {
            const at = ctx.byNode ? ctx.byNode(node) : null;
            return at ? '<a class="go" data-ref="' + escape(at.block) + '">' + escape(at.handle) + "</a>" : escape(node);
          });
          out.push('<div class="cx-who">' + go(row) + d.classes.map((c) => ' <span class="cx-badge cx-' + escape(c) + '">' + escape(c) + "</span>").join("") +
            (joined.length ? ' <span class="cx-with">with ' + joined.join(", ") + "</span>" : "") + "</div>");
          if (d.classes.includes("moved") && d.old && d.new && d.old.text === d.new.text) out.push('<div class="cx-note">the same words, elsewhere</div>');
          else {
            out.push(diffBlock(d.old && d.old.text, d.new && d.new.text));
          }
        } else if (row.kind === "delta") {
          const d = row.data;
          out.push('<div class="cx-who">' + go(row) + ' <span class="cx-delta">Δ ' + escape((d.entry ? "[" + d.entry + "] " : "") + d.class) + "</span></div>" +
            '<div class="cx-note">' + escape(d.note || "") + " · by " + escape(d.by) + "</div>");
        } else if (row.kind === "reopened") {
          const d = row.data;
          out.push('<div class="cx-who">' + go(row) + "</div><div class=\"cx-note\">" + escape(d.why) + (d.from ? " · from " + escape(d.from) : "") + "</div>");
        } else if (row.kind === "lapsed") {
          // a lapsed judgment or finding: its line, then its cause, the
          // element that changed a link where the later state has it
          const d = row.data;
          const cause = d.cause || {};
          const element = cause.element || {};
          const at = (element.kind === "node" || element.kind === "edge") && ctx.byNode ? ctx.byNode(element.name) : null;
          out.push('<div class="cx-who">' + go(row) + ' <span class="cx-badge cx-lapsed">' + (d.finding ? "finding" : "judgment") + "</span></div>" +
            '<code class="sheet">' + escape(row.line) + "</code>" +
            (cause.why ? '<div class="cx-note">' + escape(cause.kind + ": " + cause.why) + (at ? ' · <a class="go" data-ref="' + escape(at.block) + '">' + escape(at.handle) + "</a>" : "") + "</div>" : "") +
            (d.clears ? '<div class="cx-note">clears <code>' + escape(d.clears) + "</code></div>" : ""));
        } else out.push('<code class="sheet">' + escape(row.line) + "</code>");
        out.push("</div>");
      }
      out.push("</div></details>");
    }
    return out.join("");
  }

  // A word diff, trimmed to the changes; a click shows it whole.
  function diffBlock(old, now, runs = words(old, now), nameOf = undefined) {
    const cut = diffHtml(runs, CONTEXT, nameOf);
    const whole = diffHtml(runs, null, nameOf);
    return cut === whole
      ? '<div class="cx-diff">' + whole + "</div>"
      : '<div class="cx-diff cut" title="click: the whole of it">' + cut + '</div><div class="cx-diff whole" hidden>' + whole + "</div>";
  }

  // The edit tab: the paper's files against the latest kept state, as
  // `axiomation changes` answers it with no state named: each block
  // identity classes other than `same`, old beside new, then the blocks
  // the update would ask again, outright and on condition. A block of the
  // kept state is a link by its node where the state shown has it
  // (`ctx.byNode`); the files' blocks are named by their handles alone,
  // as no state holds them yet.
  const PENDING = [
    ["text", "Changed", "the text, by identity's class"],
    ["reopened", "Would ask again", "what the update would ask, and why"],
    ["on_condition", "On condition", "asked only if a condition holds"],
    ["corrections_lost", "Corrections lost", "a cut correction the update cannot carry, and why"],
    ["groups_lost", "Groups lost", "a group the update cannot carry, and why"],
  ];
  // shown only where it holds any: a correction or a group whose run the
  // edit cut is rare
  const PENDING_WHEN_ANY = new Set(["corrections_lost", "groups_lost"]);
  function pendingHtml(report, ctx) {
    const named = (old) => {
      const at = ctx.byNode ? ctx.byNode(old.node) : null;
      return at ? '<a class="go" data-ref="' + escape(at.block) + '">' + escape(at.handle) + "</a>" : escape(old.handle);
    };
    const out = ['<div class="cx-head">the paper\'s files (version <b>' + escape(String(report.version).slice(0, 12)) + "</b>) against <b>" + escape(ctx.name(report.from)) +
      "</b>: not yet updated; <code>axiomation update</code> carries it onto the graph</div>"];
    for (const [name, title, holds] of PENDING) {
      const section = report[name] || { items: [], lines: [] };
      if (PENDING_WHEN_ANY.has(name) && !section.items.length) continue;
      out.push('<details class="card-sec" data-sec="' + name + '"' + (ctx.shut && ctx.shut.has(name) ? "" : " open") + '><summary class="sec-head"><span class="chev"></span><span class="title">' + title +
        '</span><span class="holds">' + holds + '</span><span class="count">' + section.items.length + '</span></summary><div class="sec-body">');
      if (!section.items.length) out.push('<p class="cx-none">none</p>');
      section.items.forEach((d, i) => {
        out.push('<div class="cx-row" data-line="' + escape(section.lines[i]) + '">');
        if (name === "text") {
          const who = d.old.length ? d.old.map(named) : d.new.map((n) => escape(n.handle));
          const now = d.new.map((n) => n.handle).filter((h) => !d.old.some((o) => o.handle === h));
          out.push('<div class="cx-who">' + who.join(", ") + ' <span class="cx-badge cx-' + escape(d.class) + '">' + escape(d.class) + "</span>" +
            (d.old.length && now.length ? ' <span class="cx-with">now ' + now.map(escape).join(", ") + "</span>" : "") + "</div>");
          const old = d.old.map((o) => o.text).join(" ");
          const fresh = d.new.map((n) => n.text).join(" ");
          if (d.class === "moved" && old === fresh) out.push('<div class="cx-note">the same words, elsewhere</div>');
          else out.push(diffBlock(old, fresh));
        } else if (name === "reopened") {
          out.push('<div class="cx-who">' + escape(d.handle) + '</div><div class="cx-note">' + escape(d.why) + (d.from ? " · from " + escape(d.from) : "") + "</div>");
        } else if (name === "corrections_lost") {
          out.push('<div class="cx-who">' + named(d) + '</div><div class="cx-note">its ' + escape(d.op) + " at “" + escape(d.words) + "”: " + escape(d.said) + "</div>");
        } else if (name === "groups_lost") {
          out.push('<div class="cx-who">' + named(d) + '</div><div class="cx-note">' + escape(d.said) + "</div>");
        } else {
          out.push('<div class="cx-who">' + escape(d.handle) + "</div>" + d.conditions.map((c) => '<div class="cx-note">' + escape(c) + "</div>").join(""));
        }
        out.push("</div>");
      });
      out.push("</div></details>");
    }
    return out.join("");
  }

  return { CLASSES, TIERS, CONTEXT, PENDING, ELIDED, words, tokens, place, badge, byNode, tiers, HANDLED, carried, diffHtml, diffBlock, html, pendingHtml };
});
