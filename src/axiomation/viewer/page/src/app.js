// The page: a kept state's text with its gutter, the selection's
// edges as arcs, its card and what is open.
// The reads are the server's, the command line's
// own JSON, and nothing here derives what a query answers. Read-only:
// nothing here writes anywhere but the URL fragment.
(async function () {
  "use strict";
  const { text: T, gutter: G, palette: P, api: A, arcs: R, open: O, card: C, help: H, changes: X, anchors: N, check: K, current: U, maths: MX, markup: MK, inbox: I } = window.AxView;

  const state = {
    // the kept state shown, and the selection by its block's or group's id
    stateId: null,
    block: null,
    tab: "card",
    lens: "all",
    roles: false,
    faint: false,
    // the paper shown as its source, its formulas and markup not rendered
    tex: false,
    lines: true,
    gutter: true,
    // the inspector beside the paper, closed for reading
    side: true,
    split: 62,
    // the open tab's list narrowed to one gate's items, by its name
    gate: null,
    // the open view's families opened
    families: new Set(),
    // the open view's kinds the reader opened, and its kinds and parts
    // folded, each by its `data-fold` key
    openFolds: { opened: new Set(), shut: new Set() },
    // the open item walked to, by its index
    item: null,
    // the check tab's item clicked, by its index in `K.items`, and its
    // sections and parts the reader opened or shut, each by its
    // `data-fold` key
    check: null,
    checkFolds: { opened: new Set(), shut: new Set() },
    // the inbox's item clicked, by its index in `I.items`, and its
    // sections and kinds the reader opened or shut, by `data-fold` key
    inbox: null,
    inboxFolds: { opened: new Set(), shut: new Set() },
    // the selections, by node id, and where in them we are
    trail: [],
    at: -1,
    // tint the blocks an edit to the selection reaches
    reach: false,
    // the card's sections the reader folded or unfolded, by name; kept
    // across selections over the card's own defaults
    shut: new Set(),
    opened: new Set(),
    // change mode: the earlier state compared with the one shown, or null
    from: null,
  };
  // change mode's reads: the report, its blocks by node, the tiers, and
  // each changed block's badge by the line it starts on
  let compared = null;
  // what the page may be behind on (`/api/now`): the store's revision its
  // reads were kept under, a newer one seen once, and the paper's files
  // against the latest state
  let revision = null;
  let moving = null;
  let files = null;
  // the edit tab: the pending edit, keyed by the files' version, the
  // state it was read against and the store's revision (what it would ask
  // reads the marks open there); `report` null while it is read
  let pendingEdit = null;
  // a person's clicks, keys and scrolls, counted: a re-read that sees the
  // count move leaves what they did as it stands
  let touched = 0;
  // the overlays `--overlay` gave, and each item placed at this state:
  // `{overlay, item, placed}`, `placed` the anchor resolved (or the edge's
  // evidence), unanchored where it does not place
  let overlays = [];
  let placements = [];
  function place() {
    const at = anchorIndex;
    const edgeById = new Map(layer.edges.map((e) => [e.id, e]));
    placements = [];
    for (const overlay of overlays) {
      for (const item of overlay.items) {
        let placed;
        if (item.edge) {
          const edge = edgeById.get(item.edge);
          placed = edge
            ? { placed: true, kind: "edge", block: edge.source, spans: edge.evidence.length ? edge.evidence : (index.blocks.get(edge.source) || { owned: [] }).owned }
            : { placed: false, kind: "edge", block: null, spans: [], why: "edge " + item.edge + " is not in this state" };
        } else placed = N.resolve(at, item.anchor);
        placements.push({ overlay: overlay.name, item, placed });
      }
    }
  }
  // each marked open item's anchors placed at this state
  // (`anchors.placeItems`), where its `!` stands and what its box draws:
  // an undeclared assumption's claim alone, a block using an unsupported
  // claim none (its `!` is that claim's). A place is boxed at rest
  // (`drawn`) where its words are anchored or it is a finding's, unless
  // its kind is only to read again or not yet reviewed (`QUIET_KINDS`);
  // picked, any place of the item is boxed. A finding's anchor that does
  // not place marks its item unanchored, as an overlay's does.
  let anchorIndex = null;
  let itemPlaces = [];
  let placesByItem = new Map();
  const anchorsOf = (item) => {
    if (!G.MARKED.includes(item.kind) || item.kind === "uses-unsupported") return [];
    const anchors = item.anchors || (item.anchor ? [item.anchor] : []);
    return item.kind === "undeclared-assumption" ? anchors.slice(0, 1) : anchors;
  };
  function placeItems() {
    const { places, unanchored } = N.placeItems(anchorIndex, openItems, anchorsOf);
    itemPlaces = places.map((p) => {
      const item = openItems[p.item];
      return { ...p, drawn: !QUIET_KINDS.has(item.kind) && (p.words || Boolean(item.finding)) };
    });
    placesByItem = new Map();
    itemPlaces.forEach((p, k) => {
      if (!placesByItem.has(p.item)) placesByItem.set(p.item, []);
      placesByItem.get(p.item).push(k);
    });
    for (const [i, why] of unanchored) if (openItems[i].finding) openItems[i].unanchored = why;
  }
  // A popup as the Block tab's cards are drawn: a label line of small
  // capitals (`label`: `[words, colour]`, the words escaped here), a title,
  // a line under it, the words it is about (`body`, HTML, its formulas
  // rendered, folded to its first lines), its rows (`[label, HTML]`) and
  // a line of what a click does.
  function tipCard({ label = [], title = "", meta = "", body = "", bodyClass = "", rows = [], foot = "" }) {
    const labels = label.filter(([words]) => words).map(([words, colour]) => "<span" + (colour ? ' style="color:' + colour + '"' : "") + ">" + escape(words) + "</span>");
    const shown = rows.filter(([, value]) => value);
    return '<div class="tc">' + (labels.length ? '<div class="tc-label">' + labels.join("") + "</div>" : "") +
      (title ? '<div class="tc-title">' + title + "</div>" : "") + (meta ? '<div class="tc-meta">' + meta + "</div>" : "") +
      (body ? '<div class="tc-body' + (bodyClass ? " " + bodyClass : "") + '">' + body + "</div>" : "") +
      (shown.length ? '<div class="tc-rows">' + shown.map(([k, v]) => '<div class="tc-row"><span class="tc-k">' + escape(k) + '</span><span class="tc-v">' + v + "</span></div>").join("") + "</div>" : "") +
      (foot ? '<div class="tc-foot">' + escape(foot) + "</div>" : "") + "</div>";
  }
  // an open item, for its gutter mark's popup, or its words' (a
  // finding's): its kind, its block, the passage it is about, why, what
  // clears it; `count` how many of its kind the mark stands for
  function openTip(i, count = 1, foot = "") {
    const item = openItems[i];
    const kind = P.open(item.kind);
    const places = (item.anchors || []).length;
    const label = item.kind === "finding"
      ? [["open finding", O.tone(item, P)], ["severity " + item.severity, O.tone(item, P)], [places > 1 ? "at " + places + " places" : ""]]
      : [[kind.label, kind.colour], [count > 1 ? count + " here, the first:" : ""]];
    const why = O.reason(item);
    const proposal = item.proposal ? escape(item.proposal.note || "") + ' <span class="tc-by">by ' + escape(O.shortName(item.proposal.author)) + "</span>" : "";
    return tipCard({
      label,
      title: escape(item.handle || "the paper"),
      meta: escape(item.section || ""),
      body: O.passage(item),
      rows: [["Why", item.kind === "finding" ? "" : why], [item.proposal ? "Proposed " + item.proposal.status : "", proposal], ["Clears", O.prose(item.clears || "")]],
      foot,
    });
  }
  // A row's `!`, for its popup: every open item marked on the row, worst
  // first, each kind with how many and why the first stands
  const TONE_WORDS = { fail: "fails a gate", warn: "warns", none: "in no gate" };
  function rowTip({ tone, marks }) {
    const total = marks.reduce((n, m) => n + m.count, 0);
    const handles = [...new Set(marks.map((m) => openItems[m.index].handle || "the paper"))];
    const lines = marks.map((m) => {
      const item = openItems[m.index];
      const t = gateTone(m);
      const what = item.kind === "finding" ? "open finding, severity " + m.severity : P.open(m.kind).label;
      const why = item.kind === "finding" ? escape(item.line || "") : O.reason(item);
      return '<div class="tc-item"><span class="tc-kind" style="color:' + O.tone(item, P) + '">' + escape(what) + "</span>" +
        (m.count > 1 ? " ×" + m.count : "") + ' <span class="tc-tone t-' + t + '">' + TONE_WORDS[t] + "</span>" +
        (why ? '<div class="tc-why">' + why + "</div>" : "") + "</div>";
    });
    return tipCard({
      label: [[total === 1 ? "1 open item" : total + " open items", toneColour(tone)], [verdict ? "" : "the check is still read"]],
      title: escape(handles.join(", ")),
      body: lines.join(""),
      bodyClass: "tc-list",
      foot: "Click: " + (marks.some((m) => m.blocks.length) ? "select its block, at the items on it" : "the first in the open view"),
    });
  }
  function toneColour(tone) {
    return tone === "none" ? "" : P.CHECK[tone].colour;
  }
  // a block, for its bar's popup: what it is, where, its opening words
  function blockTip(b) {
    const block = layer.blocks[b];
    const what = block.roles.length ? block.roles.join(" + ") : block.form;
    const words = opening(block.id, 200);
    return tipCard({ label: [[what, P.block(block).border]], title: escape(block.handle), meta: escape(lineOf(block.id) || ""), body: words ? "“" + escape(words) + "”" : "", foot: "Click: select it" });
  }

  // a group's, for its bracket's popup: its kind, which its bracket shows
  // only by colour, what it justifies and its edges in/out, as a block's
  // count reads
  function groupTip(g) {
    const group = layer.groups[g];
    const kind = P.group(group.kind);
    const target = group.names ? names.handle.get(group.names) || group.names : group.named_label || "";
    const first = R.rowOf(index, group.id);
    const last = index.blocks.get(group.blocks[group.blocks.length - 1]);
    const end = last && last.lines[last.lines.length - 1];
    const at = !first ? "" : (layer.files.length > 1 ? first[0] + ", " : "") +
      (end && end[0] === first[0] && end[1] !== first[1] ? "lines " + first[1] + "–" + end[1] : "line " + first[1]);
    const words = opening(group.id, 200);
    return tipCard({
      label: [[kind.label + " group", kind.stroke]], title: escape(group.handle), meta: escape(at), body: words ? "“" + escape(words) + "”" : "",
      rows: [["justifies", escape(target)], ["edges in/out", (group.edges_in || 0) + "/" + (group.edges_out || 0)]], foot: "Click: select it",
    });
  }

  // the overlays' open items, for the open view
  function overlayItems() {
    return placements.filter((p) => p.item.open).map((p) => ({
      kind: O.OVERLAY + p.overlay,
      line: p.item.label + (p.item.value ? " · " + p.item.value : "") + (p.item.note ? " — " + p.item.note : ""),
      block: p.placed.block,
      node: p.placed.block ? names.node.get(p.placed.block) : null,
      handle: p.placed.block ? names.handle.get(p.placed.block) : null,
      section: null,
      status: p.item.value || p.item.channel,
      anchor: p.item.anchor,
      colour: (P.OVERLAY[p.item.channel] || P.OVERLAY.custom).colour,
      unanchored: p.placed.placed ? null : p.placed.why,
    }));
  }

  // the state's text layer, indexed
  let layer = null;
  let index = null; // arcs' index: blocks and groups by id
  let blockNumber = new Map(); // block id -> its index in the layer (its runs' owner)
  let fileIndex = new Map();
  let starts = []; // per file index
  let startsByPath = new Map();
  // the formulas of each file (`maths.find`), numbered across the files as
  // the text pane's `qN` classes; the paper's macros as KaTeX's table; the
  // format the cards' texts are read in, the root file's
  let formulas = [];
  // each file's markup rendered in place (`markup.find`), and its
  // characters that then print nothing (`markup.silent`)
  let markups = [];
  let silences = [];
  let macros = {};
  let paperFormat = "markdown";
  // each formula rendered, once a state: by its number (`{html}` or
  // `{error}`), and its parts by where the cuts fall (`"q@cuts"`)
  let renderedFormulas = new Map();
  let names = { handle: new Map(), byHandle: new Map(), byNode: new Map(), node: new Map() };
  let openItems = [];
  // the open query's notes: where it could not read what a kind needs
  let openNotes = [];
  // `check --json` at the state shown, or `{error}` where it was refused
  let verdict = null;
  // `inbox --json` at the state shown, its items, or `{error}`
  let inbox = null;
  let inboxItems = [];
  let states = [];
  // The established blocks the open tab walks, and the selection’s reach.
  let established = new Set();
  // the blocks with a live reading read off a copy that is not the version
  // of record (`blocks --provisional`)
  let provisional = new Set();
  let reached = null;

  const LANE = 5;
  const ARC_WIDTH = 150;
  const IN_OUT_GAP = 4;
  const HEAD = 6;
  // how many block lanes the gutter holds: as many as the blocks may need
  // (`laneBound`), then as many as the drawn rows use
  let laneSize = 1;
  let laneBound = 1;

  const $ = (id) => document.getElementById(id);
  function escape(text) {
    return String(text).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  }
  const plural = (n, one, many) => n + " " + (n === 1 ? one : many);
  function say(words) {
    $("text-note").textContent = words;
  }
  // The toolbar's two toggles and whether the menu holds a setting that is
  // not its default, marked on its button so a closed menu hides nothing.
  function showTools() {
    $("math").setAttribute("aria-pressed", String(!state.tex));
    // the panel's formulas follow it: each is drawn rendered beside its source
    document.body.classList.toggle("maths-source", state.tex);
    $("lines").setAttribute("aria-pressed", String(state.lines));
    $("gutter").setAttribute("aria-pressed", String(state.gutter));
    const set = state.lens !== "all" || state.faint || state.roles || state.reach;
    $("more-tools").classList.toggle("set", set);
    $("more-tools").querySelector("summary").title = "more connection settings: the lens, every edge, roles, reach" + (set ? " (one is on)" : "");
  }

  // --- a state, read ---

  // The states the one shown descends from, nearest first.
  function ancestors(stateId) {
    const byId = new Map(states.map((s) => [s.id, s]));
    const found = [];
    for (let at = byId.get(stateId); at && at.parent; at = byId.get(at.parent)) if (byId.has(at.parent)) found.push(byId.get(at.parent));
    return found;
  }
  function stateName(id) {
    const kept = states.find((s) => s.id === id);
    return kept ? id.slice(0, 8) + " " + kept.command + " " + kept.at.slice(0, 16).replace("T", " ") : id.slice(0, 8);
  }
  // A kept time as the header gives it, in the zone it was kept in:
  // "27 Oct 21:46", the year added when it is not this year's.
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function when(at) {
    const [date, time = ""] = at.split("T");
    const [year, month, day] = date.split("-");
    return Number(day) + " " + MONTHS[Number(month) - 1] + (year === String(new Date().getFullYear()) ? "" : " " + year) + " " + time.slice(0, 5);
  }
  // The page follows the newest state; any other is held as new ones are kept.
  function isLatest(id) {
    return states.length > 0 && states[states.length - 1].id === id;
  }
  function stateLabel(kept) {
    return (isLatest(kept.id) ? "Latest" : "Earlier") + " · " + kept.command + " · " + when(kept.at);
  }

  // Change mode: `changes` between `state.from` and the state shown,
  // badged in the text and in its tab.
  async function compare(from) {
    state.from = from && ancestors(state.stateId).some((s) => s.id === from) ? from : null;
    compared = null;
    if (state.from) {
      const report = await A.changes(state.from, state.stateId);
      const nodes = X.byNode(report);
      const at = new Map();
      for (const [node, item] of nodes) {
        const block = item.new && index.blocks.get(item.new.block);
        const first = block && block.lines[0];
        if (!first) continue;
        const key = first[0] + ":" + first[1];
        if (!at.has(key)) at.set(key, []);
        at.get(key).push({ node, block: block.id, badge: item.badge });
      }
      compared = { report, nodes, tiers: X.tiers(report, openItems), at, marks: await changeMarks(nodes) };
    }
    $("compare").value = state.from || "";
    $("what-changed").hidden = Boolean(state.from) || !ancestors(state.stateId).length;
    showComparing();
    $("tab-changes").hidden = !compared;
    if (compared) tabLabel("tab-changes", "changes", compared.tiers.changed.length + compared.tiers.reached.length + compared.tiers.lapsed.length);
    else if (state.tab === "changes") showTab("card", { open: false });
    renderText();
    renderChanges();
    drawArcs();
    writeHash();
  }
  // the ghosts a render drew, so each stands once
  let ghostsDrawn = new Set();
  // What changed, laid on the text: each changed block's added words by
  // path, its deleted words as ghosts by path and offset. A deleted
  // block's words stand as a ghost before the first block after it in
  // the earlier state that the state shown still has.
  async function changeMarks(nodes) {
    const added = new Map();
    const ghosts = new Map();
    const carried = new Map();
    const tos = new Map();
    const textOf = (path) => (fileIndex.has(path) ? layer.files[fileIndex.get(path)].text : "");
    const put = (map, path, item) => {
      if (!map.has(path)) map.set(path, []);
      map.get(path).push(item);
    };
    const ghost = (path, at, words) => {
      if (!ghosts.has(path)) ghosts.set(path, new Map());
      const here = ghosts.get(path);
      here.set(at, (here.get(at) || "") + words);
    };
    // the nodes a split or a merge joined, each set once: its words are
    // compared as one run, so words that only changed block are carried
    const cuts = new Map();
    for (const [node, item] of nodes) {
      const members = [node, ...(item.joined || [])].filter((n) => nodes.has(n));
      if (members.length < 2) continue;
      const set = new Set(members.flatMap((n) => [n, ...(cuts.get(n) || [])]));
      for (const n of set) cuts.set(n, set);
    }
    const before = cuts.size || [...nodes.values()].some((i) => !i.new) ? await A.text(state.from).catch(() => null) : null;
    const order = before ? before.blocks.map((b) => b.node) : [];
    const runsOf = new Map();
    for (const set of new Set(cuts.values())) {
      const members = [...set].map((n) => [n, nodes.get(n)]);
      const olds = members.filter(([, i]) => i.old).sort(([a], [b]) => order.indexOf(a) - order.indexOf(b)).map(([n, i]) => ({ node: n, text: i.old.text }));
      const news = members.filter(([, i]) => i.new && blockNumber.has(i.new.block))
        .sort(([, a], [, b]) => blockNumber.get(a.new.block) - blockNumber.get(b.new.block)).map(([n, i]) => ({ node: n, text: i.new.text }));
      for (const [n, runs] of X.carried(olds, news)) runsOf.set(n, runs);
    }
    const deleted = [];
    for (const [node, item] of nodes) {
      if (!item.new) {
        // a block that ended in a merge left its words in another
        if (item.old && item.old.text && !cuts.has(node)) deleted.push({ node, text: item.old.text });
        continue;
      }
      const n = blockNumber.get(item.new.block);
      if (n === undefined) continue;
      const block = layer.blocks[n];
      const runs = runsOf.get(node) || X.words(item.old && item.old.text, item.new.text);
      const placed = X.place(runs, X.tokens(block.owned, textOf));
      for (const [path, start, end] of placed.added) put(added, path, { start, end });
      for (const [path, at, words] of placed.ghosts) ghost(path, at, words);
      for (const [path, start, end, other] of placed.carried) put(carried, path, { start, end, other });
      for (const [path, at, other] of placed.tos) {
        if (!tos.has(path)) tos.set(path, new Map());
        tos.get(path).set(at, other);
      }
    }
    for (const { node, text } of deleted) {
      let anchor = null;
      for (let i = order.indexOf(node) + 1; i > 0 && i < order.length && !anchor; i += 1) {
        const id = names.byNode.get(order[i]);
        const n = id === undefined ? undefined : blockNumber.get(id);
        if (n !== undefined && layer.blocks[n].owned.length) anchor = layer.blocks[n].owned[0];
      }
      if (anchor) ghost(anchor[0], anchor[1], text + "\n");
    }
    return { added, ghosts, carried, tos };
  }
  // a node's block and handle at the state shown, or null
  function atNode(node) {
    const block = names.byNode.get(node);
    return block ? { block, handle: names.handle.get(block) } : null;
  }
  function renderChanges() {
    $("changes").innerHTML = compared ? X.html(compared.report, compared.tiers, { name: stateName, shut: state.shut, byNode: atNode }) : "";
  }

  // --- the paper's files ahead of the latest state ---

  // The header's line and the edit tab, while the paper's files are not
  // the latest state's version: an edit no update has carried yet.
  function showDrift() {
    const ahead = Boolean(files && files.pending);
    $("drift").hidden = !ahead;
    $("tab-edit").hidden = !ahead;
    if (ahead) {
      $("drift").title = "The paper's files changed since state " + stateName(files.state) + ", the latest: no update has run, so the graph does not hold the edit.";
      $("drift").innerHTML = 'the paper changed; no update has run · <a id="see-edit" title="the paper\'s files against that state, as axiomation changes reads them">see the edit</a>';
    } else if (state.tab === "edit") showTab("card", { open: false });
  }
  // The pending edit, read on demand (it runs identity on the files: a
  // second or two) and again only once the files or the latest state move.
  async function renderEdit() {
    if (!files || !files.pending) return;
    const key = files.version + " " + files.state + " " + revision;
    if (pendingEdit && pendingEdit.key === key) return drawEdit();
    pendingEdit = { key, report: null };
    $("edit").innerHTML = '<p class="cx-none">reading the paper\'s files against state ' + escape(stateName(files.state)) + " …</p>";
    let report;
    try {
      report = await A.pending();
    } catch (error) {
      if (pendingEdit && pendingEdit.key === key) pendingEdit = null;
      $("edit").innerHTML = '<p class="error">' + escape(error.message) + "</p>";
      return;
    }
    if (!pendingEdit || pendingEdit.key !== key) return;
    if (!report.pending) {
      pendingEdit = null;
      $("edit").innerHTML = '<p class="cx-none">the paper\'s files are the latest state\'s version again</p>';
      return;
    }
    pendingEdit.report = report;
    drawEdit();
  }
  function drawEdit() {
    if (!pendingEdit || !pendingEdit.report) return;
    $("edit").innerHTML = X.pendingHtml(pendingEdit.report, { name: stateName, shut: state.shut, byNode: atNode });
  }

  // --- keeping up with the store ---

  // Every few seconds while the page is visible: the files against the
  // latest state, and the store's revision. A revision that moved and held
  // for one poll re-reads the page in place, so a run appending answers
  // one by one re-reads it once it pauses, not on every answer; on the
  // page shown again (`at once`), a moved revision re-reads it straight.
  const POLL_MS = 5000;
  let polling = false;
  let unanswered = false;
  async function poll({ atOnce = false } = {}) {
    if (polling || document.hidden) return;
    polling = true;
    try {
      const now = await A.now();
      if (unanswered) say("");
      unanswered = false;
      const was = files;
      files = now.files;
      if (!was || was.pending !== files.pending || was.version !== files.version || was.state !== files.state) {
        showDrift();
        if (verdict) renderCheck();
        if (state.tab === "edit") renderEdit();
      }
      const next = U.step(now.revision, revision, moving, atOnce);
      moving = next === "wait" ? now.revision : null;
      if (next === "reread") {
        try {
          await reread(now.revision);
        } catch (error) {
          say("the page could not be read again (" + error.message + "): reload it");
        }
      }
    } catch (error) {
      unanswered = true;
      say("the server does not answer (" + error.message + "): the page shows what it last read");
    } finally {
      polling = false;
    }
  }
  // The page read again in place: its kept reads forgotten, the states
  // listed again, the state shown loaded again (the newest, where it showed
  // the newest), the selection, comparison and tab
  // carried by node, and the text's scroll kept (on a newer state, the
  // selection scrolled to). The revision counts as read once the state is
  // loaded; a failure before leaves it, so a later poll reads again. A
  // person who acts meanwhile keeps what they did.
  async function reread(next) {
    const epoch = touched;
    const before = states;
    const was = { stateId: state.stateId, block: state.block, tab: state.tab, nodeOf: names.node };
    const top = $("text").scrollTop;
    A.forget();
    states = (await A.states()).states;
    const shown = U.shownAfter(before, states, was.stateId);
    state.block = null;
    await load(shown);
    revision = next;
    if (touched !== epoch) return;
    const [ident] = U.carried([was.block], was.nodeOf, names.byNode);
    await compare(state.from);
    state.keepTab = true;
    if (ident) await select(ident, { scroll: shown !== was.stateId, trail: false });
    else renderCard();
    state.keepTab = false;
    if (state.tab !== was.tab && !$("tab-" + was.tab).hidden) showTab(was.tab, { open: false });
    if (state.tab === "edit") renderEdit();
    if (shown === was.stateId) $("text").scrollTop = top;
    say("read again at " + new Date().toTimeString().slice(0, 8) + ": the store changed");
  }
  // an overlay item, for its words' popup
  function overlayTip(p) {
    const channel = P.OVERLAY[p.item.channel] || P.OVERLAY.custom;
    return tipCard({ label: [[p.item.value || "", channel.colour], [p.overlay], [channel.label]], title: escape(p.item.label), body: p.item.note ? escape(p.item.note) : "" });
  }

  // a changed block's diff, for the badge's popup
  function diffTip(node) {
    const item = compared && compared.nodes.get(node);
    if (!item) return "";
    const runs = X.words(item.old && item.old.text, item.new && item.new.text);
    return tipCard({ label: [...item.classes.map((c) => [c, P.change(c).colour]), ["since " + stateName(state.from)]], title: escape(item.handle), body: X.diffHtml(runs), bodyClass: "cx-diff" });
  }

  // The reader's structure in one file, in its offsets, for `markup.find`.
  function markupOf(path) {
    const here = (span) => span && span[0] === path;
    const at = (span) => [span[1], span[2]];
    return {
      title: here(layer.title_at) ? at(layer.title_at) : null,
      sections: layer.sections.filter((h) => here(h.heading)).map((h) => ({ span: at(h.heading), level: h.level, number: h.number, label: h.label })),
      environments: (layer.environments || []).filter((e) => here(e.span) && here(e.body)).map((e) => ({ ...e, span: at(e.span), body: at(e.body) })),
      citations: layer.citations.filter((c) => here(c.span)).map((c) => ({ ...c, span: at(c.span) })),
      references: (layer.references || []).filter((r) => here(r.span)).map((r) => ({ ...r, span: at(r.span) })),
      footnotes: (layer.footnotes || []).filter((n) => here(n.span) && here(n.text)).map((n) => ({ span: at(n.span), text: at(n.text), number: n.number })),
    };
  }

  async function load(stateId) {
    const started = performance.now();
    // the verdict reads every gate: the page is drawn without waiting for it
    const checked = A.check(stateId).catch((error) => ({ error: error.message }));
    const [found, open, settled, copies] = await Promise.all([A.text(stateId), A.open(stateId), A.blocks(stateId, { established: "yes" }), A.blocks(stateId, { provisional: true })]);
    layer = found;
    state.stateId = layer.state.id;
    index = R.indexOf(layer);
    blockNumber = new Map(layer.blocks.map((b, i) => [b.id, i]));
    fileIndex = new Map(layer.files.map((f, i) => [f.path, i]));
    starts = layer.files.map((f) => T.lineStarts(f.text));
    startsByPath = new Map(layer.files.map((f, i) => [f.path, starts[i]]));
    formulas = layer.files.flatMap((f, i) => {
      const hidden = f.runs.filter((run) => run[2] === "hidden").map((run) => [run[0], run[1]]);
      return MX.find(f.text, f.format, hidden).map((formula) => ({ ...formula, file: i }));
    });
    markups = layer.files.map((f, i) => {
      const hidden = f.runs.filter((run) => run[2] === "hidden").map((run) => [run[0], run[1]]);
      const own = formulas.filter((formula) => formula.file === i).map((formula) => [formula.start, formula.end]);
      return MK.find(f.text, f.format, hidden, own, markupOf(f.path));
    });
    silences = layer.files.map((f, i) => MK.silent(f.text.length, f.runs.filter((run) => run[2] === "hidden").map((run) => [run[0], run[1]]), markups[i]));
    macros = MX.macrosOf(layer.macros);
    renderedFormulas = new Map();
    paperFormat = (layer.files.find((f) => f.path === layer.state.root) || layer.files[0] || {}).format || "markdown";
    names = { handle: new Map(), byHandle: new Map(), byNode: new Map(), node: new Map() };
    for (const item of [...layer.blocks, ...layer.groups]) {
      names.handle.set(item.id, item.handle);
      names.byHandle.set(item.handle, item.id);
      names.byNode.set(item.node, item.id);
      names.node.set(item.id, item.node);
    }
    anchorIndex = N.index(layer);
    place();
    openItems = O.items(open, overlayItems());
    openNotes = open.notes || [];
    placeItems();
    established = new Set(settled.blocks.items.map((b) => b.block));
    provisional = new Set(copies.blocks.items.map((b) => b.block));
    reached = null;
    state.item = null;
    verdict = null;
    state.check = null;
    inbox = null;
    inboxItems = [];
    state.inbox = null;
    edgeIndex = null;
    header();
    renderText();
    renderOpen();
    renderCheck();
    renderInbox();
    drawEdit();
    console.log("axview: state " + state.stateId + " drawn in " + (performance.now() - started).toFixed(0) + " ms");
    const shown = state.stateId;
    A.inbox(state.stateId).catch((error) => ({ error: error.message })).then((found) => {
      if (state.stateId !== shown) return;
      inbox = found;
      inboxItems = I.items(found);
      renderInbox();
    });
    checked.then((found) => {
      if (state.stateId !== shown) return;
      verdict = found;
      redrawLanes();
      showVerdict();
      renderCheck();
      renderOpen();
      console.log("axview: state " + shown + " checked in " + (performance.now() - started).toFixed(0) + " ms");
    });
  }

  // A reference as the card and the search give it: a block's or group's
  // id, a node id or a handle at this state.
  function resolve(ref) {
    if (blockNumber.has(ref) || index.groups.has(ref)) return ref;
    return names.byNode.get(ref) || names.byHandle.get(ref) || null;
  }

  // --- the state control: the state shown, its details, the others ---

  function showState() {
    const kept = states.find((s) => s.id === state.stateId);
    const button = $("state");
    button.textContent = kept ? stateLabel(kept) : state.stateId || "";
    button.classList.toggle("earlier", Boolean(kept) && !isLatest(kept.id));
    if (!kept) return;
    $("state-detail").innerHTML = '<div class="sd-head">' + escape(stateLabel(kept)) + "</div>" +
      '<div class="sd-meta">state <code>' + escape(kept.id) + "</code> · version <code>" + escape(kept.version.slice(0, 12)) + "</code> · " + escape(plural(kept.nodes, "node", "nodes")) +
      (kept.parent ? " · made from <code>" + escape(kept.parent.slice(0, 8)) + "</code>" : "") + "</div>" +
      (isLatest(kept.id) ? "" : '<p class="sd-note">An earlier state: the page stays on it as new states are kept. Pick the latest to follow them.</p>');
    $("state-list").innerHTML = states.slice().reverse().map((s) =>
      '<button type="button" class="state-row' + (s.id === kept.id ? " on" : "") + '" data-state="' + escape(s.id) + '"' + (s.id === kept.id ? ' aria-current="true"' : "") + ">" +
      '<span class="sr-when">' + escape(when(s.at)) + '</span><span class="sr-cmd">' + escape(s.command) + '</span><span class="sr-id">' + escape(s.id.slice(0, 8)) + "</span>" +
      (isLatest(s.id) ? '<span class="sr-tag">latest</span>' : s.head ? '<span class="sr-tag">head</span>' : "") + "</button>").join("");
  }
  function stateMenu(open) {
    $("state-panel").hidden = !open;
    $("state").setAttribute("aria-expanded", String(open));
    if (open) {
      const on = $("state-list").querySelector(".state-row.on");
      if (on) on.scrollIntoView({ block: "nearest" });
    }
  }
  // While comparing: the two states, in the header, until it is ended.
  function showComparing() {
    const shown = states.find((s) => s.id === state.stateId);
    const from = state.from && states.find((s) => s.id === state.from);
    $("comparing").hidden = !from;
    // in change mode the counts' column holds a block's change badge, wider
    // than a count: the lanes are measured again once it widens
    if (document.body.classList.contains("comparing") !== Boolean(from)) {
      document.body.classList.toggle("comparing", Boolean(from));
      redrawLanes();
    }
    if (from && shown) {
      $("comparing-text").textContent = "Comparing " + when(from.at) + " → " + when(shown.at);
      $("comparing").title = "changes since " + stateName(from.id) + ", at " + stateName(shown.id);
    }
  }
  async function showStateOf(id) {
    const node = state.block !== null ? names.node.get(state.block) : null;
    state.block = null;
    await load(id);
    await compare(state.from);
    const ident = node && names.byNode.get(node);
    if (ident) select(ident, { trail: false });
    else renderCard();
  }

  // --- the header ---

  // The title as it prints (`markup.printed`): no comment, spacing or
  // footnote of its source; the payload's words where it stands nowhere.
  function titleWords() {
    const at = layer.title_at;
    const f = at ? fileIndex.get(at[0]) : undefined;
    if (f === undefined) return layer.title;
    return MK.printed(layer.files[f].text, at[1], at[2], silences[f], markups[f]) || layer.title;
  }
  function header() {
    const title = titleWords();
    const titled = Boolean(title);
    $("paper").textContent = title || layer.state.root;
    $("paper").title = title ? title + " · " + layer.state.root : layer.state.root;
    $("paper-file").textContent = titled ? layer.state.root : "";
    $("paper-file").hidden = !titled;
    document.title = (title || layer.state.root) + " — axiomation";
    showState();
    showVerdict();
    const uncovered = $("uncovered");
    uncovered.className = layer.uncovered ? "some" : "none";
    uncovered.textContent = layer.uncovered ? layer.uncovered + " characters in no block" : "";
    const made = states.find((s) => s.id === state.stateId);
    $("what-changed").hidden = !(made && made.parent && states.some((s) => s.id === made.parent)) || Boolean(state.from);
    $("compare").innerHTML = '<option value="">no comparison</option>' + ancestors(state.stateId).map((s) =>
      '<option value="' + s.id + '"' + (s.id === state.from ? " selected" : "") + ">changes since " + escape(stateName(s.id)) + "</option>").join("");
    showComparing();
    showDrift();
    $("block-list").innerHTML = layer.blocks.map((b) => '<option value="' + escape(b.handle) + '">' + escape(b.form + " " + b.node) + "</option>").join("");
  }

  // --- the text pane ---

  // Layers other than the runs, bucketed by line per file.
  function layersOf(f) {
    const path = layer.files[f].path;
    const own = starts[f];
    const buckets = new Map();
    const add = (item) => {
      const first = T.lineOf(own, item.start);
      const last = T.lineOf(own, Math.max(item.start, item.end - 1));
      for (let line = first; line <= last; line += 1) {
        if (!buckets.has(line)) buckets.set(line, []);
        buckets.get(line).push(item);
      }
    };
    // the cutter's parts and the entries are not drawn: a part splits
    // nothing a reader acts on, and the words an edge names are lit
    // while the edge is the selection's (`light`); a claim's words and a
    // self mark's are kept apart for the selection's lines alone
    layer.standings.forEach((standing, i) => {
      if (standing.claim && standing.claim[0] === path) add({ start: standing.claim[1], end: standing.claim[2], cls: "s" + i });
    });
    layer.self.forEach((mark, i) => {
      for (const span of mark.evidence) if (span[0] === path) add({ start: span[1], end: span[2], cls: "m" + i });
    });
    // each block's role segments, tinted when the roles overlay is on
    for (const block of layer.blocks) {
      for (const [file, start, end, role] of block.role_spans || []) if (file === path) add({ start, end, cls: "rl rl-" + role });
    }
    // each overlay item placed: words underlined in its channel's line, a
    // whole block or group tinted
    placements.forEach((p, k) => {
      if (!p.placed.placed) return;
      const whole = p.placed.kind === "block" || p.placed.kind === "group";
      for (const span of p.placed.spans) if (span[0] === path) add({ start: span[1], end: span[2], cls: (whole ? "ovw" : "ov") + " ov-" + p.item.channel + " o" + k });
    });
    // in change mode, the words the later state added; and a cut where a
    // ghost of deleted words stands, so a piece starts there
    if (compared) {
      for (const span of compared.marks.added.get(path) || []) add({ start: span.start, end: span.end, cls: "dfa" });
      for (const span of compared.marks.carried.get(path) || []) add({ start: span.start, end: span.end, cls: "dfc" });
      for (const at of (compared.marks.ghosts.get(path) || new Map()).keys()) add({ start: at, end: at, cls: "" });
      for (const at of (compared.marks.tos.get(path) || new Map()).keys()) add({ start: at, end: at, cls: "" });
    }
    // each open item's places, measured for its box and its `!`
    // (`drawBoxes`, `drawMarks`)
    itemPlaces.forEach((p, k) => {
      for (const span of p.spans) if (span[0] === path) add({ start: span[1], end: span[2], cls: "ip f" + k });
    });
    // every edge's evidence and its target's anchor, so that hovering an
    // arc lights them by a style rule
    layer.edges.forEach((edge, i) => {
      for (const span of edge.evidence) if (span[0] === path) add({ start: span[1], end: span[2], cls: "x" + i });
      for (const span of edge.anchors) if (span[0] === path) add({ start: span[1], end: span[2], cls: "y" + i });
    });
    // each formula: the pieces are cut at its edges, so that its pieces
    // can be drawn as one rendered span
    formulas.forEach((formula, q) => {
      if (formula.file === f) add({ start: formula.start, end: formula.end, cls: null, formula: q });
    });
    // the markup rendered in place: its words styled, its marks hidden
    // or shown as what they print (`pieceHtml`)
    if (!state.tex) for (const item of markups[f]) if (item.end > item.start) add({ start: item.start, end: item.end, cls: item.cls || null, mark: item.hide ? item : undefined });
    return buckets;
  }

  // A piece's classes and its owning block's index (-1: none).
  function pieceClasses(piece, run) {
    const classes = [];
    let owner = -1;
    if (run) {
      const [, , cls, held] = run;
      if (cls !== "owned") classes.push("r-" + cls);
      if (held >= 0) {
        classes.push("b" + held);
        owner = held;
      }
    }
    for (const item of piece.layers) if (item.cls) classes.push(item.cls);
    return { classes, owner };
  }
  function pieceHtml(file, piece, run) {
    // rendered, what prints nothing is not drawn, and a mark hidden shows
    // what it prints once, at its start
    let body = null;
    if (!state.tex) {
      if (run && run[2] === "hidden") return "";
      const mark = piece.layers.find((l) => l.mark);
      if (mark) {
        if (piece.start !== mark.start || !mark.mark.put) return "";
        body = escape(mark.mark.put);
      }
    }
    const { classes, owner } = pieceClasses(piece, run);
    if (body === null) body = escape(file.text.slice(piece.start, piece.end));
    return classes.length
      ? '<span class="' + classes.join(" ") + '"' + (owner >= 0 ? ' data-b="' + owner + '"' : "") + ">" + body + "</span>"
      : body;
  }

  // A formula rendered, once a state: its HTML, or null where the
  // renderer refuses it (it is then drawn as its source).
  function renderedFormula(q) {
    if (!renderedFormulas.has(q)) renderedFormulas.set(q, MX.render(window.katex, formulas[q], macros));
    return renderedFormulas.get(q).html || null;
  }
  // How a piece is drawn in a row [from, to): `{q, rendered}` for one in
  // a formula drawn rendered or with its source shown by a click, else
  // null.
  function formulaOf(piece, from, to) {
    if (state.tex) return null;
    const item = piece.layers.find((l) => l.formula !== undefined);
    if (!item) return null;
    const q = item.formula;
    const formula = formulas[q];
    if (formula.start < from || formula.end > to || renderedFormula(q) === null) return null;
    return { q, rendered: true };
  }
  // A rendered formula's parts, cut where its marks change
  // (`maths.parts`, `maths.renderParts`): each `{classes, owner, html}`,
  // or null where it is drawn whole. Cached with the formulas, by where
  // the cuts fall.
  function formulaParts(q, pieces) {
    const keyed = MX.keyed(pieces);
    const cut = MX.parts(formulas[q], keyed);
    if (!cut) return null;
    const sign = q + "@" + cut.map((part) => part.start + "-" + part.end).join(",");
    if (!renderedFormulas.has(sign)) renderedFormulas.set(sign, MX.renderParts(window.katex, formulas[q], cut, macros));
    const drawn = renderedFormulas.get(sign);
    if (!drawn) return null;
    // a part joined to the next carries the marks of both
    const byKey = MX.marks(keyed);
    return drawn.map(({ first, last, html }) => {
      const joined = cut.slice(first, last + 1).map((part) => byKey.get(part.key)).filter(Boolean);
      const owner = joined.length ? joined[0].owner : -1;
      return { classes: [...new Set(joined.flatMap((marked) => marked.classes))], owner, html };
    });
  }

  // One row's text, [from, to) of a file: each piece as its span. A
  // rendered formula is one box; where its marks change inside it, it is
  // drawn in parts, each its own box with its own marks, else whole, with
  // every mark any of its pieces carries. Either way what is measured from
  // the spans (the lanes, the arcs, the marks, the selection) stays on
  // the formula. A run's class marks it only where all its words hold it,
  // a hidden or a space piece never (a comment inside an equation does
  // not dim it). A formula whose source a click showed is its pieces as
  // ever, held in a span that names it for the click back.
  function rowHtml(file, from, to, layers) {
    let html = "";
    let group = null;
    // the deleted words standing at an offset of this row, struck, never
    // part of the source: no block's, no line's, not selectable; each once,
    // the row's end included (words deleted after a block's last word)
    const here = compared ? compared.marks.ghosts.get(file.path) : null;
    const went = compared ? compared.marks.tos.get(file.path) : null;
    // where a split's words went on into another block, its name
    const toAt = (at) => {
      const other = went ? went.get(at) : undefined;
      const key = "to:" + file.path + ":" + at;
      if (other === undefined || ghostsDrawn.has(key)) return "";
      ghostsDrawn.add(key);
      const name = names.handle.get(names.byNode.get(other)) || other;
      return '<span class="dft" aria-label="its words continue in ' + escape(name) + '">→ ' + escape(name) + "</span>";
    };
    const ghostAt = (at) => {
      const words = here ? here.get(at) : undefined;
      const key = file.path + ":" + at;
      if (words === undefined || ghostsDrawn.has(key)) return toAt(at);
      ghostsDrawn.add(key);
      return '<span class="dfd" aria-label="deleted: ' + escape(words.trim()) + '">' + escape(words) + "</span>" + toAt(at);
    };
    const flush = () => {
      if (!group) return;
      const formula = formulas[group.q];
      if (!group.rendered) {
        html += '<span class="mxs" data-q="' + group.q + '"' + (group.owner >= 0 ? ' data-holder="' + group.owner + '"' : "") + ">" + group.inner + "</span>";
        group = null;
        return;
      }
      let runs = null;
      for (const piece of group.pieces) {
        if (piece.runs.includes("r-hidden") || piece.runs.includes("r-space")) continue;
        runs = runs ? runs.filter((c) => piece.runs.includes(c)) : piece.runs;
      }
      const head = "mxt" + (formula.display ? " mxd" : "");
      const cut = formulaParts(group.q, group.pieces);
      if (cut) {
        html += '<span class="' + [head, ...(runs || [])].join(" ") + '"' + (group.owner >= 0 ? ' data-holder="' + group.owner + '"' : "") + ' data-q="' + group.q + '">' +
          cut.map((part) => '<span class="' + ["mxb mxc", ...part.classes].join(" ") + '"' + (part.owner >= 0 ? ' data-b="' + part.owner + '"' : "") + ">" + part.html + "</span>").join("") + "</span>";
      } else {
        const classes = new Set();
        for (const piece of group.pieces) for (const c of piece.classes) classes.add(c);
        html += '<span class="' + [head, "mxb", ...classes, ...(runs || [])].join(" ") + '"' +
          (group.owner >= 0 ? ' data-b="' + group.owner + '"' : "") + ' data-q="' + group.q + '">' + renderedFormula(group.q) + "</span>";
      }
      html += group.after || "";
      group = null;
    };
    for (const run of T.runsOn(file.runs, from, to)) {
      const start = Math.max(run[0], from);
      const end = Math.min(run[1], to);
      for (const piece of T.pieces(start, end, layers)) {
        const found = formulaOf(piece, from, to);
        const struck = ghostAt(piece.start);
        if (found !== null) {
          if (!group || group.q !== found.q) {
            flush();
            group = { ...found, pieces: [], owner: -1, inner: "", after: "" };
          }
          // a ghost inside a rendered formula stands after it
          if (struck) {
            if (group.rendered) group.after += struck;
            else group.inner += struck;
          }
          const { classes, owner } = pieceClasses(piece, run);
          if (group.owner < 0) group.owner = owner;
          if (group.rendered) {
            group.pieces.push({ start: piece.start, end: piece.end, owner, classes: classes.filter((c) => !c.startsWith("r-")), runs: classes.filter((c) => c.startsWith("r-")) });
          } else group.inner += pieceHtml(file, piece, run);
          continue;
        }
        flush();
        html += struck + pieceHtml(file, piece, run);
      }
    }
    flush();
    return html + ghostAt(to);
  }

  // Whether a row [start, end) of a file prints something when rendered:
  // `"blank"` where it holds only spaces, `"silent"` where all it holds
  // prints nothing (`markup.silent`), else null.
  function printsNothing(f, text, start, end) {
    const mask = silences[f];
    let any = false;
    for (let i = start; i < end; i += 1) {
      const c = text.charCodeAt(i);
      if (c === 32 || c === 9 || c === 13) continue;
      if (!mask[i]) return null;
      any = true;
    }
    return any ? "silent" : "blank";
  }

  function blockWords(block) {
    const what = block.roles.length ? block.roles.join(" + ") : block.form;
    return block.handle + " (" + block.node + "): a " + what + " block (its bar's colour). Click to select.";
  }

  function groupCells(columns, line) {
    let html = "";
    for (const column of columns) {
      const cell = column[line - 1];
      if (!cell) {
        html += "<i></i>";
        continue;
      }
      const group = layer.groups[cell.group];
      const colour = P.group(group.kind).stroke;
      html += '<i class="g' + (cell.start ? " gs" : "") + (cell.end ? " ge" : "") +
        '" style="border-color:' + colour + '" data-g="' + cell.group + '"></i>';
    }
    return '<span class="gl">' + html + "</span>";
  }

  // The open view's mark on a block's first line: a stale tick, a ring for
  // a block that uses an unsupported claim, a lapsed or a disputed
  // judgment's mark, an open finding's in its highest severity;
  // `mark` the gutter's, standing for `count` items of its kind.
  const MARK_WORDS = {
    stale: (item) => "stale since " + (item.since || "").slice(0, 8) + ": " + O.causeWords(item) + ".",
    "uses-unsupported": (item) => item.line + ".",
    lapsed: (item, mark) => (mark.count > 1 ? mark.count + " lapsed here; the first: " : "lapsed: ") + item.line + " · " + O.causeWords(item) + ".",
    disputed: (item, mark) => (mark.count > 1 ? mark.count + " disputed here; the first: " : "disputed: ") + item.line + ".",
    finding: (item, mark) => (mark.count > 1 ? mark.count + " open findings here, the highest severity " + mark.severity + "; the first: " : "open finding: ") + item.line + places(item) + ".",
  };
  // the kept graph's four faults, marked on every block they sit on
  for (const kind of G.MARKED.filter((k) => !MARK_WORDS[k])) {
    MARK_WORDS[kind] = (item, mark) => (mark.count > 1 ? mark.count + " " + P.open(kind).label + " here; the first: " : kind + ": ") + item.line + places(item) + ".";
  }
  // A finding anchored at several places is marked at each: its mark says so.
  function places(item) {
    const n = (item.anchors || []).length;
    return n > 1 ? " (anchored at " + n + " places, each marked)" : "";
  }
  // the blocks an open item stands at (`anchors.blocksOf`)
  const blocksOf = (item) => N.blocksOf(anchorIndex, item);
  // the blocks its `!` stands on: where it is settled. An undeclared
  // assumption is settled at its claim (its uses edges, or its J), never
  // at the premises its J reaches, so it marks the claim alone; picked,
  // its premises are tinted. A block using an unsupported claim is
  // settled at that claim, whose own `!` it is: it marks none.
  const markedBlocks = (item) => item.kind === "uses-unsupported" ? [] : item.kind === "undeclared-assumption" ? blocksOf({ ...item, anchors: (item.anchors || []).slice(0, 1) }) : blocksOf(item);
  const premisesOf = (item) => item.kind === "undeclared-assumption"
    ? ((item.cause || {}).premises || []).map((p) => names.byHandle.get(p.premise)).filter(Boolean) : [];
  // An open kind's gate tone (`check.kindTones`): `fail`, `warn`, or
  // `none` while the check is read or no gate lists it.
  function kindTone(kind) {
    const tones = K.kindTones(verdict);
    return (tones && tones[kind] && tones[kind].tone) || "none";
  }
  // A finding's gate tone is its own (`check.findingTones`), not its
  // kind's: a severity-3 one failing `defects` turns no severity-2 one red.
  // `indices`: the findings a mark stands for, the worst one's.
  function findingGate(indices) {
    const tones = K.findingTones(verdict) || {};
    return indices.map((i) => tones[openItems[i].finding] || "none").reduce((a, b) => (G.TONE_RANK[b] > G.TONE_RANK[a] ? b : a), "none");
  }
  const gateTone = (mark) => (mark.kind === "finding" ? findingGate(mark.indices || [mark.index]) : kindTone(mark.kind));
  // A mark's tone: grey for a kind of a quiet family (to read again, not
  // yet reviewed: nothing there says the paper is wrong), else its gate's;
  // a finding no gate fails or warns on `finding`, in its severity's tone.
  const QUIET_KINDS = new Set(O.FAMILIES.filter((f) => f.quiet).flatMap((f) => f.kinds));
  function markTone(mark) {
    if (QUIET_KINDS.has(mark.kind)) return "none";
    const tone = gateTone(mark);
    return tone === "none" && mark.kind === "finding" ? "finding" : tone;
  }
  // the rows' marks as last drawn, for their cards; the widest count drawn
  // and the width its column has
  let rowMarks = [];
  let countChars = 3;
  let shownCountChars = null;

  function renderText() {
    const out = [];
    ghostsDrawn = new Set();
    const seen = new Set();
    const blocksById = index.blocks;
    laneBound = G.laneBound(layer.blocks);
    laneSize = laneBound;
    $("text").style.setProperty("--bl-width", laneBound * LANE + 2 + "px");
    // the numbers as wide as the longest line number; the counts as the
    // widest drawn (`drawLanes`)
    $("text").style.setProperty("--num-chars", String(Math.max(2, ...starts.map((lines) => String(lines.length).length))));
    const lanes = layer.files.map((file, f) => {
      const count = starts[f].length;
      return {
        groups: G.groupLane(layer.groups, blocksById, file.path, count),
        layers: layersOf(f),
      };
    });
    for (const segment of layer.segments) {
      const f = segment.file;
      const file = layer.files[f];
      const lane = lanes[f];
      // a file's head only where there are several: one file's name is
      // the header's, under the title
      out.push('<div class="seg">' + (layer.files.length > 1 ? '<div class="file-head">' + escape(file.path) +
        (segment.resumes ? ' <span class="resumes">resumes at line ' + segment.line + "</span>" : "") + "</div>" : ""));
      // a display over several source lines is one row (`mergedRows`)
      const shown = state.tex ? [] : formulas.filter((formula, q) => formula.file === f && formula.start >= segment.start && formula.end <= segment.end &&
        renderedFormula(q) !== null);
      // rendered, a line that prints nothing folds away, and of a run of
      // blank lines one shows
      let blankBefore = true;
      for (const { first: line, last, start, end } of T.mergedRows(T.segmentLines(segment, starts[f], file.text.length), shown)) {
        const key = f + "-" + line;
        const id = seen.has(key) ? "" : ' id="L' + key + '"';
        seen.add(key);
        if (!state.tex) {
          const quiet = printsNothing(f, file.text, start, end);
          if (quiet === "silent" || (quiet === "blank" && blankBefore)) {
            for (let at = line; at <= last; at += 1) {
              const own = at === line ? id : seen.has(f + "-" + at) ? "" : ' id="L' + f + "-" + at + '"';
              seen.add(f + "-" + at);
              if (own) out.push('<div class="ln-at"' + own + "></div>");
            }
            continue;
          }
          blankBefore = quiet === "blank";
        }
        // each later line of a joined row keeps its place, at the row's top
        for (let joined = line + 1; joined <= last; joined += 1) {
          if (seen.has(f + "-" + joined)) continue;
          seen.add(f + "-" + joined);
          out.push('<div class="ln-at" id="L' + f + "-" + joined + '"></div>');
        }
        const layers = [];
        for (let at = line; at <= last; at += 1) for (const item of lane.layers.get(at) || []) if (!layers.includes(item)) layers.push(item);
        const html = rowHtml(file, start, end, layers);
        let row = '<div class="ln"' + id + ' data-f="' + f + '" data-l="' + line + '">';
        row += groupCells(lane.groups, line);
        row += '<span class="bl"></span>';
        // the marks and counts are drawn by the measured layer
        // (`drawMarks`), at the screen row each belongs to
        row += '<span class="tk"></span>';
        row += '<span class="ct"></span>';
        row += '<span class="num"' + (last > line ? ' title="lines ' + line + "–" + last + '"' : "") + ">" + line + "</span>";
        row += '<span class="tx">' + (html || " ") + "</span></div>";
        out.push(row);
      }
      out.push("</div>");
    }
    $("text").classList.toggle("rendered", !state.tex);
    // one column at a reading width, centred, its arcs at its right edge
    $("text").innerHTML = '<div id="column">' + out.join("") + '<svg id="arcs" aria-hidden="true"></svg></div>';
    drawLanes();
    light();
  }

  // --- the measured lanes ---
  //
  // Each block's bar runs from the first to the last rendered row of its
  // text, as wrapped. Measured from the text, so drawn again whenever it
  // reflows.
  function drawLanes(again = true) {
    const pane = $("text");
    let usedBlocks = 1;
    for (const old of pane.querySelectorAll(".lane-layer")) old.remove();
    // where a line under a formula stands (`palette.formulaRules`): the
    // pane told where a line's baseline stands under its top, each box
    // where its formula's baseline stands under its top and how deep the
    // formula reaches under it; all read first, then written, so the page
    // lays out once
    const first = pane.querySelector(".ln .tx");
    let asc = null;
    if (first) {
      const probe = document.createElement("i");
      probe.style.cssText = "display:inline-block;width:0;height:0;vertical-align:baseline";
      first.prepend(probe);
      asc = probe.getBoundingClientRect().bottom - first.getBoundingClientRect().top;
      probe.remove();
    }
    const boxes = [];
    for (const formula of pane.querySelectorAll(".tx .mxt")) {
      const strut = formula.querySelector(".katex-strut");
      if (!strut) continue;
      const baseline = strut.getBoundingClientRect().bottom + (parseFloat(getComputedStyle(strut).verticalAlign) || 0);
      let deepest = -Infinity;
      // a strut spans its base's height and depth: its bottom is the
      // deepest glyph's (the base's own box reaches lower, by its leading)
      for (const each of formula.querySelectorAll(".katex-strut")) deepest = Math.max(deepest, each.getBoundingClientRect().bottom);
      for (const box of formula.classList.contains("mxb") ? [formula] : formula.children) boxes.push([box, baseline - box.getBoundingClientRect().top, deepest - baseline]);
    }
    if (asc !== null) pane.style.setProperty("--asc", asc.toFixed(1) + "px");
    for (const [box, top, depth] of boxes) {
      box.style.setProperty("--base-top", top.toFixed(1) + "px");
      box.style.setProperty("--depth", depth.toFixed(1) + "px");
    }
    const box = pane.getBoundingClientRect();
    const at = (r) => ({ top: r.top - box.top + pane.scrollTop, bottom: r.bottom - box.top + pane.scrollTop });
    const bar = (item) => "top:" + (item.top - 1) + "px;height:" + Math.max(1, item.bottom - item.top + 1) + "px;";
    const column = pane.querySelector(".ln .bl");
    if (!column) return;
    const x = column.getBoundingClientRect().left - box.left + pane.scrollLeft;
    const rows = new Map();
    for (const span of pane.querySelectorAll(".tx span[data-b]")) {
      const owner = Number(span.dataset.b);
      if (!rows.has(owner)) rows.set(owner, []);
      for (const r of span.getClientRects()) if (r.height) rows.get(owner).push(at(r));
    }
    const items = [];
    for (const [owner, segs] of G.blockSegments(rows, 4)) segs.forEach((seg, k) => items.push({ key: owner + ":" + k, owner, ...seg }));
    const lane = G.lanes(items);
    usedBlocks = Math.max(usedBlocks, ...[...lane.values()].map((k) => k + 1));
    const bars = [];
    for (const item of items) {
      const block = layer.blocks[item.owner];
      bars.push('<i class="lane-bar k' + item.owner + '" data-b="' + item.owner + '" role="img" aria-label="' + escape(blockWords(block)) +
        '" style="left:' + (x + lane.get(item.key) * LANE) + "px;" + bar(item) + "background:" + P.block(block).lane + '"></i>');
    }
    // every open item place's rects (f), in one pass over the text, for
    // its `!` and its box
    placeRects = new Map();
    for (const el of pane.querySelectorAll(".tx .ip")) {
      const keys = [...el.classList].filter((c) => /^f\d+$/.test(c));
      const blank = !el.textContent.trim();
      const rects = [...el.getClientRects()].filter((r) => r.height).map((r) => ({
        ...at(r), left: r.left - box.left + pane.scrollLeft, right: r.right - box.left + pane.scrollLeft, blank,
      }));
      for (const key of keys) {
        if (!placeRects.has(key)) placeRects.set(key, []);
        placeRects.get(key).push(...rects);
      }
    }
    const lanesLayer = document.createElement("div");
    lanesLayer.className = "lane-layer";
    lanesLayer.innerHTML = bars.join("") + drawMarks(pane, box, at, rows);
    pane.appendChild(lanesLayer);
    // the block and count columns as wide as the drawn rows need: set,
    // the text reflows, and the lanes are drawn once more
    const want = Math.min(laneBound, usedBlocks);
    if (again && (want !== laneSize || countChars !== shownCountChars)) {
      laneSize = want;
      shownCountChars = countChars;
      pane.style.setProperty("--bl-width", want * LANE + 2 + "px");
      pane.style.setProperty("--ct-chars", String(countChars));
      drawLanes(false);
      return;
    }
    drawBoxes();
    drawArcs();
  }
  // Each open item's words boxed as a hovered edge's target is
  // (`arcs.outline`): a finding in its severity's tone, fainter and
  // thinner as it is less severe; another item in its kind's colour, as
  // strong as the most severe finding. A place on words or a finding's is
  // boxed at rest (`placeItems`); the item picked is boxed at each place,
  // a pixel thicker. Two items on the same words are both drawn, the
  // second a box round the first (a finding on an unsupported claim's
  // words stays seen); a third or more share that outer box, in the
  // strongest's style, since a box further out crosses the next row's
  // words. The hover names them all. From the rects `drawLanes`
  // measured, so the selection redraws them without measuring.
  let placeRects = new Map();
  const BOX_STEP = 3;
  function boxStyle(item) {
    const strength = item.finding ? P.severity(item.severity).strength : 1;
    // a proposal filed on it waits on a review: dashed, its tone kept
    return { colour: item.finding ? P.severity(item.severity).colour : P.open(item.kind).colour, width: 0.5 + strength, waits: Boolean(item.proposal) };
  }
  function drawBoxes() {
    const pane = $("text");
    for (const old of pane.querySelectorAll(".item-box")) old.remove();
    // the items on each outline, by its innermost path, in order
    const on = new Map();
    itemPlaces.forEach((p, k) => {
      const picked = state.item === p.item;
      if (!p.drawn && !picked) return;
      const rects = placeRects.get("f" + k) || [];
      R.outline(rects).forEach((d, i) => {
        if (!on.has(d)) on.set(d, { rects, i, items: [] });
        const here = on.get(d);
        if (!here.items.some((x) => x.item === p.item)) here.items.push({ item: p.item, style: boxStyle(openItems[p.item]), picked });
      });
    });
    const paths = [];
    for (const [d, { rects, i, items }] of on) {
      const [inner, ...outer] = items;
      paths.push({ d, ...inner.style, picked: inner.picked });
      if (!outer.length) continue;
      const strongest = outer.reduce((a, b) => (b.style.width > a.style.width ? b : a));
      paths.push({ d: R.outline(rects, 1 + BOX_STEP)[i], ...strongest.style, picked: outer.some((x) => x.picked) });
    }
    if (!paths.length) return;
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "item-box");
    svg.setAttribute("aria-hidden", "true");
    // the picked drawn last, over the others
    svg.innerHTML = paths.sort((a, b) => Number(a.picked) - Number(b.picked))
      .map((b) => '<path style="stroke:' + b.colour + ";stroke-width:" + (b.width + (b.picked ? 1 : 0)).toFixed(2) + (b.waits ? ";stroke-dasharray:4 3" : "") + '" d="' + b.d + '"></path>').join("");
    pane.appendChild(svg);
  }
  // The gutter's marks and counts, each at the screen row its own block or
  // anchor starts on, measured as the bars are (a long source line holds
  // several blocks, and their marks belong at their own rows): a block's
  // open marks and its edge count (in change mode, how it changed). A
  // claim's standing has no mark: its words carry its line, and an
  // unsupported one is an open item, marked as the others are. Marks
  // meeting on one row stand side by side, more than fit folded into a +N;
  // none is dropped.
  function drawMarks(pane, box, at, rows) {
    const column = (name) => {
      const cell = pane.querySelector(".ln ." + name);
      return cell ? cell.getBoundingClientRect().left - box.left + pane.scrollLeft : null;
    };
    const tkX = column("tk");
    const ctX = column("ct");
    if (tkX === null || ctX === null) return "";
    // a row's box: the first rendered row of the spans, else the source
    // line's own row
    const firstRow = (rects) => rects && rects.length ? rects.reduce((a, b) => (b.top < a.top ? b : a)) : null;
    const rectsOf = (key) => placeRects.get(key) || [];
    const lineRow = (where) => {
      const element = where && rowElement(where);
      if (!element) return null;
      const r = at(element.getBoundingClientRect());
      return { top: r.top, bottom: r.top + Math.min(18, r.bottom - r.top) };
    };
    const items = [];
    const put = (row, col, rank, html, words, data, edges, mark) => {
      if (row) items.push({ top: row.top, height: row.bottom - row.top, col, rank, html, words, data, edges, mark });
    };
    // an item marks where it is anchored: the first row of each anchor's
    // words (a finding's: one on line 212's words
    // is not on line 202), else its blocks' first rows
    const placed = new Map();
    const blockRow = (id) => {
      const b = blockNumber.get(id);
      return b === undefined ? null : firstRow(rows.get(b)) || lineRow(layer.blocks[b].lines[0]);
    };
    // and each row the blocks its marks stand on, the block a click selects
    const rowBlocks = new Map();
    const holds = (id, row) => (rows.get(blockNumber.get(id)) || []).some((r) => r.top <= row.top + 1 && r.bottom > row.top);
    const placesOf = (item, i) => {
      const blocks = markedBlocks(item);
      let found = (placesByItem.get(i) || []).map((k) => firstRow(rectsOf("f" + k))).filter(Boolean)
        .map((row) => ({ row, block: blocks.find((id) => holds(id, row)) }));
      if (!found.length) found = blocks.map((id) => ({ row: blockRow(id), block: id })).filter((f) => f.row);
      return found.map(({ row, block }) => {
        const key = row.top.toFixed(1);
        placed.set(key, row);
        if (block) {
          if (!rowBlocks.has(key)) rowBlocks.set(key, []);
          if (!rowBlocks.get(key).includes(block)) rowBlocks.get(key).push(block);
        }
        return key;
      });
    };
    const opened = G.openMarks(openItems, placesOf);
    const badges = new Map();
    if (compared) for (const list of compared.at.values()) for (const b of list) badges.set(b.block, b);
    layer.blocks.forEach((block, b) => {
      const row = firstRow(rows.get(b)) || lineRow(block.lines[0]);
      if (compared) {
        const badge = badges.get(block.id);
        if (badge) put(row, "ct", 0, '<span class="cxb cx-' + escape(badge.badge) + '" data-node="' + escape(badge.node) + '">' + escape(badge.badge) + "</span>", block.handle + ": " + badge.badge);
      } else if (block.edges_in || block.edges_out) {
        const words = block.handle + ": " + plural(block.edges_in, "edge points", "edges point") + " at it, " + plural(block.edges_out, "is", "are") + " its own (in/out)";
        put(row, "ct", 0, "", words, null, [block.edges_in, block.edges_out]);
      }
    });
    for (const [key, list] of opened) {
      for (const m of list) put(placed.get(key), "tk", G.MARKED.indexOf(m.kind), "", MARK_WORDS[m.kind](openItems[m.index], m), null, null, { ...m, blocks: rowBlocks.get(key) || [] });
    }
    // marks whose rows overlap stand together: one `!` in the worst one's
    // gate tone, its card naming them all
    const out = [];
    rowMarks = [];
    countChars = 3;
    for (const col of ["tk", "ct"]) {
      for (const g of G.markRows(items.filter((x) => x.col === col))) {
        let inner;
        if (col === "tk") {
          const row = G.worstFirst(g.items.map((x) => x.mark), markTone);
          rowMarks.push(row);
          // a finding the worst there: its severity's tone, as its box
          const lead = row.marks[0];
          const own = lead.kind === "finding" && row.tone !== "none" ? ' style="color:' + P.severity(lead.severity).colour + '"' : "";
          inner = '<i class="om-bang t-' + row.tone + '"' + own + ' data-item="' + row.marks[0].index + '" data-row="' + (rowMarks.length - 1) + '" role="img" aria-label="' +
            escape(g.items.map((x) => x.words).join("; ")) + '">!</i>';
        } else if (g.items.every((x) => x.edges)) {
          // the column holds one count: the blocks starting on one row
          // summed, edges in and edges out, each block's named on hover
          const [into, own] = G.edgeSums(g.items.map((x) => x.edges));
          countChars = Math.max(countChars, (into + "/" + own).length);
          inner = '<span class="io" title="' + escape(g.items.map((x) => x.words).join("\n")) + '">' + into + "/" + own + "</span>";
        } else {
          // in change mode, two blocks starting on one row show the first's
          // badge and a +N naming the others (none clipped)
          const { shown, folded } = G.shownMarks(g.items, 1);
          inner = shown.map((x) => x.html).join("") +
            (folded.length ? '<span class="ct-more" title="' + escape(folded.map((x) => x.words).join("\n")) + '">+' + folded.length + "</span>" : "");
        }
        out.push('<span class="' + col + ' mk" style="left:' + (col === "tk" ? tkX : ctX) + "px;top:" + g.top + "px;height:" + g.height + 'px">' + inner + "</span>");
      }
    }
    return out.join("");
  }

  // Debounced by a timer, not an animation frame: a page in a tab the
  // browser does not show gets no frames, and its bars would stay stale.
  let laneTimer = 0;
  function redrawLanes() {
    clearTimeout(laneTimer);
    laneTimer = setTimeout(() => drawLanes(), 60);
  }
  window.addEventListener("resize", redrawLanes);
  // the formulas' fonts load after the first draw and change their widths
  if (document.fonts) document.fonts.addEventListener("loadingdone", redrawLanes);

  function rowElement([path, line]) {
    return document.getElementById("L" + fileIndex.get(path) + "-" + line);
  }

  function scrollTo(ident) {
    const row = R.rowOf(index, ident);
    const element = row && rowElement(row);
    if (element) element.scrollIntoView({ block: "center" });
  }

  // --- the arcs ---

  function drawArcs() {
    const pane = $("text");
    const svg = $("arcs");
    if (!svg || !layer) return;
    const box = pane.getBoundingClientRect();
    const yOf = (row) => {
      const element = row && rowElement(row);
      if (!element) return null;
      const r = element.getBoundingClientRect();
      return r.top - box.top + pane.scrollTop + Math.min(r.height, 18) / 2;
    };
    // An edge's end on the screen line its words stand on (a wrapped
    // source line is several screen lines): the
    // source's end at its evidence, the target's at the part it names, else
    // the block's first word; the numbered line's top where none is drawn.
    const yOfSpan = (selector) => {
      const element = pane.querySelector(selector);
      const rect = element && element.getClientRects()[0];
      return rect ? rect.top - box.top + pane.scrollTop + rect.height / 2 : null;
    };
    const firstWord = (ident) => {
      const group = index.groups.get(ident);
      const n = blockNumber.get(group ? group.blocks[0] : ident);
      return n === undefined ? null : yOfSpan(".tx span.b" + n);
    };
    const yOfEnd = (i, end, row) => {
      const edge = layer.edges[i];
      const y = end === "from"
        ? (edge.evidence.length ? yOfSpan(".tx span.x" + i) : null) ?? firstWord(edge.source)
        : (edge.anchors.length ? yOfSpan(".tx span.y" + i) : null) ?? firstWord(edge.target);
      return y ?? yOf(row);
    };
    svg.setAttribute("height", pane.scrollHeight);
    svg.style.height = pane.scrollHeight + "px";
    // Lines off hides the arcs only: the underlines, boxes and tints stay
    if (!state.lines) {
      svg.innerHTML = "";
      return;
    }
    const x = 6;
    const parts = [];
    if (state.faint) {
      for (const arc of R.every(layer, index, startsByPath, state.lens)) {
        const y1 = yOfEnd(arc.edge, "from", arc.near);
        const y2 = yOfEnd(arc.edge, "to", arc.far);
        if (y1 === null || y2 === null || y1 === y2) continue;
        parts.push('<path class="faint" d="' + R.path(x, y1, y2, ARC_WIDTH - 6) + '" stroke="' + P.edge(arc.kind).stroke + '"/>');
      }
    }
    const ident = state.block;
    if (ident !== null) {
      const arcs = R.selected(layer, index, startsByPath, ident, state.lens)
        .map((arc) => {
          const [near, far] = arc.dir === "out" ? ["from", "to"] : ["to", "from"];
          return { ...arc, y1: yOfEnd(arc.edge, near, arc.near), y2: yOfEnd(arc.edge, far, arc.far) };
        })
        .filter((arc) => arc.y1 !== null && arc.y2 !== null);
      // on every row a line leaves a little above the row's middle and
      // arrives, at its arrowhead, a little below: an edge out leaves the
      // selection's evidence and arrives at its target, an edge in leaves
      // its source and arrives at the selection. Where lines meet on a row,
      // at the selection or at a block they reach or leave from, an
      // arrowhead never sits on another line's start
      for (const arc of arcs) {
        const leaves = arc.dir === "out" ? "y1" : "y2";
        const arrives = arc.dir === "out" ? "y2" : "y1";
        arc[leaves] -= IN_OUT_GAP;
        arc[arrives] += IN_OUT_GAP;
      }
      const lanes = R.fan(arcs, 10, ARC_WIDTH - 12);
      const heads = new Map(); // an arrowhead's path: the colours drawn there
      arcs.forEach((arc, k) => {
        const { y1, y2 } = arc;
        const colour = P.edge(arc.kind).stroke;
        // the line says which way reliance runs: solid upstream, dotted
        // downstream (line style for direction)
        // the line stops at its arrowhead's base: at the far end for an
        // edge out, at the selection's for an edge in
        const d = arc.dir === "out" ? R.bracket(x, y1, y2, x + lanes[k], 5, x + HEAD) : R.bracket(x + HEAD, y1, y2, x + lanes[k], 5, x);
        // hovered, a line over the ground beneath it, so a dotted line shows
        // no other line through its gaps
        parts.push('<path class="backing" data-edge="' + arc.edge + '" d="' + d + '"/>');
        parts.push('<path class="arc ' + arc.stream + '" data-edge="' + arc.edge + '" d="' + d + '" stroke="' + colour + '"/>');
        parts.push('<path class="hit" data-edge="' + arc.edge + '" data-ref="' + escape(arc.other) + '" d="' + d + '"/>');
        // the edge's way: an arrowhead at its target, pointing into the
        // words it names; at the other end a click target as the line is
        const far = arc.dir === "out";
        const ref = (far ? ' data-ref="' + escape(arc.other) + '"' : "") + ' data-edge="' + arc.edge + '"';
        const tip = R.arrowhead(x, arc.dir === "out" ? y2 : y1, HEAD);
        parts.push('<path class="head' + (far ? " far" : "") + '"' + ref + ' d="' + tip + '" fill="' + colour + '"/>');
        (heads.get(tip) || heads.set(tip, new Set()).get(tip)).add(colour);
      });
      // arcs of two kinds arriving at one point share an arrowhead: in the
      // colour of neither, over theirs; a hovered arc is drawn above it
      for (const [tip, colours] of heads) if (colours.size > 1) parts.push('<path class="head mixed" d="' + tip + '"/>');
    }
    svg.innerHTML = parts.join("");
  }

  // --- the selection ---

  // Selection lights by a style rule, not by walking the text: the
  // selected block's runs and lane bars share a class; the blocks at its
  // arcs' other ends are tinted in their edge's kind.
  const selection = document.createElement("style");
  document.head.appendChild(selection);
  const hover = document.createElement("style");
  document.head.appendChild(hover);
  function light() {
    const rules = [];
    const ident = state.block;
    if (ident !== null) {
      const members = index.groups.has(ident) ? index.groups.get(ident).blocks : [ident];
      for (const id of members) {
        const n = blockNumber.get(id);
        if (n === undefined) continue;
        // its words, formulas included, in the accent: a colour, not a line,
        // so it never meets an underline, and no ground, which is the
        // connected blocks' mark
        rules.push(".tx span.b" + n + ":not(.r-hidden) { color: var(--accent); }");
        rules.push(".lane-bar.k" + n + " { box-shadow: 0 0 0 1px var(--sel-edge); }");
      }
      // its claims' words under the claim's quiet line, its self marks'
      // under a dashed one: the selection's alone, never at rest; before
      // the edges' lines, which win where they meet
      layer.standings.forEach((st, i) => {
        if (members.includes(st.block)) rules.push(".tx span.s" + i + " { text-decoration: underline solid var(--claim-line); text-underline-offset: 4px; } " + P.formulaRules(".tx .mxb.s" + i, "solid", "var(--claim-line)", null, 4));
      });
      layer.self.forEach((mark, i) => {
        if (members.includes(mark.block)) rules.push(".tx span.m" + i + " { text-decoration: underline dashed var(--self); text-underline-offset: 4px; } " + P.formulaRules(".tx .mxb.m" + i, "dashed", "var(--self)", null, 4));
      });
      // the blocks at the arcs' other ends, their words in the linked
      // colour, and the words each edge stands on underlined at both ends
      // (its evidence, the part it names), only while the lines show
      for (const arc of state.lines ? R.selected(layer, index, startsByPath, ident, state.lens) : []) {
        const other = index.groups.has(arc.other) ? index.groups.get(arc.other).blocks : [arc.other];
        for (const id of other) {
          const n = blockNumber.get(id);
          if (n !== undefined && !members.includes(id)) rules.push(".tx span.b" + n + ":not(.r-hidden) { color: var(--linked); }");
        }
        rules.push(".tx span:is(.x" + arc.edge + ", .y" + arc.edge + ") { text-decoration: underline solid currentColor; text-decoration-thickness: 1.5px; text-underline-offset: 4px; }");
      }
    }
    if (reached) {
      for (const [id, tone] of reached) {
        const n = blockNumber.get(id);
        if (n !== undefined) rules.push(".tx span.b" + n + " { background: var(--reach-" + tone + "); }");
      }
    }
    if (state.item !== null && openItems[state.item]) {
      const item = openItems[state.item];
      for (const id of premisesOf(item)) {
        const members = index.groups.has(id) ? index.groups.get(id).blocks : [id];
        for (const n of members.map((m) => blockNumber.get(m)).filter((n) => n !== undefined)) {
          rules.push(".tx span.b" + n + ":not(.r-hidden) { background: " + P.open(item.kind).colour + "26; }");
        }
      }
    }
    selection.textContent = rules.join("\n");
    // the item picked boxed, thicker
    drawBoxes();
  }

  async function select(ident, { scroll = true, trail = true } = {}) {
    if (ident === null || ident === undefined) return;
    const started = performance.now();
    state.block = ident;
    reached = null;
    // a tooltip named the edge from the selection before
    hideTip();
    lightEdge(null);
    if (trail) {
      const node = names.node.get(ident);
      state.trail = state.trail.slice(0, state.at + 1);
      if (state.trail[state.trail.length - 1] !== node) state.trail.push(node);
      state.at = state.trail.length - 1;
    }
    light();
    drawArcs();
    if (scroll) scrollTo(ident);
    if (state.reach) askReach();
    writeHash();
    await renderCard();
    console.log("axview: select " + (performance.now() - started).toFixed(1) + " ms");
  }

  // The trail's back and forward, by name: the selections before and
  // after this one; with none selected, back is the one just cleared.
  function trailNav() {
    const trailName = (at) => {
      const id = names.byNode.get(state.trail[at]);
      return id ? names.handle.get(id) || id : "a block not at this state";
    };
    const [before, after] = trailEnds();
    return (before >= 0 ? '<button type="button" id="back" title="the previous selection ([)">← ' + escape(trailName(before)) + "</button>" : "") +
      (after < state.trail.length ? '<button type="button" id="forward" title="the next selection (])">' + escape(trailName(after)) + " →</button>" : "");
  }
  const trailEnds = () => (state.block === null && state.trail.length ? [state.at, state.at + 1] : [state.at - 1, state.at + 1]);

  function step(by) {
    const at = trailEnds()[by < 0 ? 0 : 1];
    if (at < 0 || at >= state.trail.length) return;
    const ident = names.byNode.get(state.trail[at]);
    if (!ident) return;
    state.at = at;
    select(ident, { trail: false });
  }

  // What an edit to the selection reaches (`axiomation reach`), in the
  // text, in two tones.
  async function askReach() {
    const ident = state.block;
    let report;
    try {
      report = await A.reach(state.stateId, ident);
    } catch (error) {
      say(error.message);
      return;
    }
    if (ident !== state.block || !state.reach) return;
    reached = new Map(report.reached.items.map((r) => [r.block, r.meaning_moved ? "changes" : "asked"]));
    const moved = report.reached.items.filter((r) => r.meaning_moved).length;
    say("an edit to " + (names.handle.get(ident) || ident) + " reaches " + plural(reached.size, "block", "blocks") + ": " + moved + " change meaning, " + (reached.size - moved) + " asked");
    light();
  }

  // --- the card ---

  let asked = 0;
  let lastReport = null;
  async function renderCard() {
    const ident = state.block;
    const card = $("card");
    if (ident === null) {
      // the trail stays: back is the selection just cleared
      const nav = trailNav();
      card.innerHTML = (nav ? '<div class="card-nav">' + nav + "</div>" : "") + '<p class="meta">Select a block: click its text or its bar, search for it, or walk the open items (j, k).</p>';
      return;
    }
    const mine = ++asked;
    let report;
    try {
      report = await A.show(state.stateId, ident);
    } catch (error) {
      card.innerHTML = '<p class="error">' + escape(error.message) + "</p>";
      return;
    }
    if (mine !== asked) return;
    const back = trailNav();
    lastReport = report;
    const selected = report.block.block || report.block.group;
    card.innerHTML = C.html(report, new Set(names.byHandle.keys()), back, {
      open: openOnCard(selected),
      placeOf: (ref, quote = true) => placeOf(ref, quote),
      linesOf: (refs, most) => linesOf(refs, most),
      ordered: (refs) => inOrder(refs),
      nameOf: (id) => names.handle.get(id) || id,
      whatOf: (id) => {
        const group = index.groups.get(id);
        const block = index.blocks.get(id);
        return group ? "group " + group.kind : block ? block.roles.join("+") || block.form : "";
      },
      lineOf: (id) => lineOf(id) || "",
      nodeName: (node) => names.handle.get(names.byNode.get(node)) || node,
      whenOf: (id) => {
        const kept = states.find((k) => k.id === id);
        return kept ? when(kept.at) + " · " + kept.command : id.slice(0, 8);
      },
      parentOf: (id) => {
        const kept = states.find((k) => k.id === id);
        return kept ? kept.parent : null;
      },
      // the findings the open view piles with this one, whichever heads it
      sameOf: (id) => {
        const pile = openItems.find((i) => i.kind === "finding" && [i.finding, ...(i.same || [])].includes(id));
        return pile ? [pile.finding, ...(pile.same || [])].filter((f) => f !== id) : [];
      },
      shut: state.shut,
      opened: state.opened,
    });
    mathsIn(card, ".claim-words, .quote, .r-value .q, .h-cause, .h-edge .parts, .e-words, .e-quote");
    if (state.tab !== "card" && !state.keepTab) showTab("card", { open: false });
    historyDiffs(card, mine);
  }

  // Where the card shows an open item that `show`'s sections hold no row
  // of: under the claim its words are, in what it rests on (a reference
  // with no target, a cited work), in what rests on it (nothing: an unused
  // premise), on the graph here (its label, a stage's doubt, a newer
  // rule), or waiting for a run. The kinds not named here have their row
  // in `show`'s own sections (a claim's status, a finding, a stale mark).
  // the card section that holds a row of each other kind
  const SECTION_OF = { finding: "findings", suggestion: "suggestions", stale: "marks" };
  const CARD_HOMES = {
    "undeclared-assumption": "claim", unchecked: "claim",
    "dangling-reference": "rests_on", retracted: "rests_on", publication: "rests_on",
    "unused-premise": "rested_on_by",
    "duplicate-label": "graph", flag: "graph", "later-rule": "graph",
    "carried-unread": "waiting",
  };
  // A kind as its card names one item of it, and several.
  const ONE = {
    unsupported: "unsupported claim", circle: "claim in a circle", partial: "claim argued in part", flag: "doubt on the graph",
    stale: "stale line", "carried-unread": "block carried unread", lapsed: "lapsed reading", disputed: "disputed claim",
    negative: "negative reading", finding: "open finding", suggestion: "suggested citation", retracted: "retracted work cited",
    publication: "questioned work cited", unjudged: "not yet judged", inconclusive: "inconclusive reading",
    provisional: "provisional reading", unchecked: "positioning claim not yet checked", "dangling-reference": "reference with no target",
    "duplicate-label": "label held twice", "unused-premise": "unused premise", "undeclared-assumption": "undeclared assumption",
    "later-rule": "line a newer rule refuses",
  };
  const kindWords = (kind, n) => (n === 1 ? ONE[kind] || kind : n + " " + (kind in P.OPEN ? P.open(kind).label.toLowerCase() : kind));
  // A block's opening words for a title or a quote: its Markdown's
  // emphasis marks left out, which the words do not show.
  // A LaTeX block's leading commands (`\\footnote{`, `\\item`, `\\emph{`)
  // left out too: its words begin after them.
  const openingWords = (id, most) => {
    const words = opening(id, 600).replace(/…$/, "")
      // an environment's frame and an item's mark, its options with them
      .replace(/\\(?:begin|end)\{[^}]*\}(?:\[[^\]]*\])?/g, " ").replace(/\\item\b(?:\[[^\]]*\])?/g, " ")
      // a text style's braces around its words: the words
      .replace(/\\(?:textbf|textit|emph|underline|textsc)\{([^{}]*)\}/g, "$1")
      // any other leading command, as `\\footnote{`
      .replace(/^(?:\s*\\[a-zA-Z]+\*?(?:\[[^\]]*\])?\s*\{?)+/, "").replace(/^([^{]*?)\}/, "$1")
      .replace(/\*\*|__|(^|\s)[*_]+(?=\S)|(?<=\S)[*_]+(?=\s|$)/g, "$1").replace(/\s+/g, " ").trim();
    return words.length > most ? words.slice(0, most) + "…" : words;
  };
  // A block named for its reader: its line, its opening words on hover;
  // `quote`, its opening words after it.
  function placeOf(ref, quote = true) {
    const id = names.byHandle.get(ref) || ref;
    const at = lineOf(id);
    if (!at) return '<span class="e-ref">' + escape(names.handle.get(id) || ref) + "</span>";
    // cut short of a formula it would split, so the formula renders
    let first = openingWords(id, 44);
    if (((first.match(/\$/g) || []).length % 2) === 1) first = first.slice(0, first.lastIndexOf("$")).trimEnd() + "…";
    return '<a class="go" data-ref="' + escape(id) + '">' + escape(at) + "</a>" +
      (quote && first ? ' <span class="e-quote">“' + escape(first) + "”</span>" : "");
  }
  // Blocks in the paper's order, each once, by node id.
  const inOrder = (refs) => [...new Set(refs.map((r) => names.byHandle.get(r) || r))].sort((a, b) => (blockNumber.get(a) ?? 1e9) - (blockNumber.get(b) ?? 1e9));
  // Several blocks by their lines, in the paper's order, the first few.
  function linesOf(refs, most = 6) {
    const ids = inOrder(refs);
    const shown = ids.slice(0, most).map((id) => placeOf(id, false));
    return shown.join(", ") + (ids.length > most ? " and " + (ids.length - most) + " more" : "");
  }
  // An open item on the card: what it is and whose move (the text, the
  // graph, or a run), one plain sentence of what holds and what is
  // missing, and the next step; the open view's own words on hover.
  function cardRow(item, index, selected) {
    let problem;
    let ways;
    if (item.kind === "unused-premise") {
      problem = "The graph records no use of this assumption: no link uses it, and it narrows no claim.";
      ways = [{ action: "Link a passage that uses it", how: "graph", command: "axiomation change --add BLOCK '→ uses " + (names.handle.get(item.block) || "") + " · \"…\"'", starter: "the block and its words" }, { action: "Remove it from the text", when: "if nothing needs it", how: "text" }];
    } else {
      problem = O.reason(item) || escape(item.line || "");
      ways = item.clears ? [{ action: O.prose(item.clears), how: "none" }] : [];
    }
    const other = item.block && item.block !== selected ? "On " + placeOf(item.block, false) + ": " : "";
    return C.problemCard(ONE[item.kind] || item.kind, QUIET_KINDS.has(item.kind) ? "needs a reading" : "here", other + problem, ways,
      gateTone({ kind: item.kind, index }) === "fail" ? "bad" : "open", { record: item.clears || "", here: index });
  }

  // The open items on a selection, as its card reads them: those to
  // settle here (a fix in the text or the graph, or a ruling), what
  // stands in its way from elsewhere (the unsupported claims it uses),
  // and those waiting for a run (a review, an update); each kind
  // `show` holds no row of placed in its home, a row of the open view.
  function openOnCard(selected) {
    const here = openItems.map((item, index) => ({ item, index, kind: item.kind }))
      .filter(({ item }) => item.block === selected || markedBlocks(item).includes(selected));
    const { marks: sorted } = G.worstFirst(here.filter((m) => m.kind !== "uses-unsupported"), markTone);
    const settle = sorted.filter((m) => !QUIET_KINDS.has(m.kind));
    const waiting = sorted.filter((m) => QUIET_KINDS.has(m.kind));
    const tally = (marks) => {
      const counts = new Map();
      for (const { kind } of marks) counts.set(kind, (counts.get(kind) || 0) + 1);
      return [...counts].map(([kind, n]) => kindWords(kind, n)).join(", ");
    };
    // each kind to settle, named, its first item's row or section the
    // link's
    const named = [];
    const waitingNamed = [];
    for (const m of waiting) {
      const seen = waitingNamed.find((x) => x.kind === m.kind);
      if (seen) seen.n += 1;
      else waitingNamed.push({ kind: m.kind, n: 1, goto: CARD_HOMES[m.kind] ? "here:" + m.index : "sec:" + (SECTION_OF[m.kind] || "standings") });
    }
    for (const m of settle) {
      const seen = named.find((x) => x.kind === m.kind);
      if (seen) seen.n += 1;
      else named.push({ kind: m.kind, n: 1, index: m.index, colour: O.tone(m.item, P), goto: CARD_HOMES[m.kind] ? "here:" + m.index : "sec:" + (SECTION_OF[m.kind] || "standings") });
    }
    const rows = { claim: [], rests_on: [], rested_on_by: [], graph: [], waiting: [] };
    for (const { item, index } of sorted) {
      const home = CARD_HOMES[item.kind];
      if (!home) continue;
      const html = item.kind === "undeclared-assumption" ? "" : cardRow(item, index, selected);
      rows[home].push(home === "claim" ? { words: item.anchor && item.anchor.words, item, index, html } : html);
    }
    const uses = here.find((m) => m.kind === "uses-unsupported" && m.item.block === selected);
    const elsewhere = uses ? (uses.item.cause || []).map((h) => names.byHandle.get(h) || h) : [];
    // the ends an edge row marks: a claim's block with no argument
    const flawed = new Map();
    for (const item of openItems) if (item.block && (item.kind === "unsupported" || item.kind === "circle")) flawed.set(item.block, { label: item.kind === "circle" ? "circular" : "unsupported", colour: P.open(item.kind).colour });
    const worst = G.worstFirst(settle, gateTone).tone;
    return {
      settle: settle.length, settleWhat: tally(settle), settleColour: worst === "none" ? "" : P.CHECK[worst].colour,
      settleKinds: named.map((x) => ({ words: kindWords(x.kind, x.n), colour: x.colour, goto: x.goto })),
      waiting: waiting.length, waitingWhat: tally(waiting),
      waitingKinds: waitingNamed.map((x) => ({ words: kindWords(x.kind, x.n), goto: x.goto })),
      elsewhere, rows, flawed,
      unread: here.filter((m) => m.kind === "unjudged" && m.item.aspect === "cited" && m.item.block === selected).map((m) => m.item),
    };
  }

  // Each history step where the block's text changed, its word change:
  // its text at the state before and at the step's, as `show` gives them.
  // Across a split or a merge, every node it joined, read at both states:
  // the words that only changed block carried, not deleted and added; the
  // step named by which nodes began (split) and ended (merged).
  async function historyDiffs(card, mine) {
    for (const place of card.querySelectorAll(".h-diff[data-to]")) {
      try {
        if (place.dataset.joined !== undefined) await cutDiff(place, mine);
        else {
          const [old, now] = await Promise.all([A.show(place.dataset.from, place.dataset.fromBlock), A.show(place.dataset.to, place.dataset.toBlock)]);
          if (mine !== asked) return;
          place.innerHTML = X.diffBlock(old.text || "", now.text || "");
        }
      } catch (error) {
        if (mine !== asked) return;
        place.textContent = "its texts could not be read: " + error.message;
      }
    }
  }
  async function cutDiff(place, mine) {
    const { from, to, node } = place.dataset;
    if (!from) return;
    const joined = place.dataset.joined.split(" ").filter(Boolean);
    const members = [node, ...joined];
    const read = (state, n) => A.show(state, n).catch(() => null);
    const [before, after, ...reads] = await Promise.all([A.text(from), A.text(to), ...members.flatMap((n) => [read(from, n), read(to, n)])]);
    if (mine !== asked) return;
    const at = (layer, n) => layer.blocks.findIndex((b) => b.node === n);
    const name = new Map();
    const side = (k, layer) => members.map((n, i) => ({ node: n, report: reads[2 * i + k] }))
      .filter((m) => m.report && at(layer, m.node) >= 0)
      .sort((x, y) => at(layer, x.node) - at(layer, y.node))
      .map((m) => {
        name.set(m.node, m.report.block.handle);
        return { node: m.node, text: m.report.text || "" };
      });
    const olds = side(0, before);
    const news = side(1, after);
    const runs = X.carried(olds, news).get(node);
    const nameOf = (n) => name.get(n) || n;
    place.innerHTML = runs ? X.diffBlock(null, null, runs, nameOf) : "";
    // which way each joined node went, from this node's side
    const was = new Set(olds.map((o) => o.node));
    const is = new Set(news.map((o) => o.node));
    const row = place.closest(".h-row");
    const link = (n) => '<a class="go" data-ref="' + escape(n) + '">' + escape(nameOf(n)) + "</a>";
    const words = [];
    const heads = new Set();
    for (const j of joined) {
      const [head, said] = !was.has(node) ? ["split off", "split off "]
        : !was.has(j) && is.has(j) ? ["split", "its words continue in "]
        : was.has(j) && !is.has(j) ? ["merged", "took in "]
        : [null, "with "];
      if (head) heads.add(head);
      words.push(said + link(j));
    }
    const cut = row && row.querySelector(".h-cut");
    if (cut && heads.size) {
      cut.textContent = [...heads].join(" · ");
      // the folded history's head names the newest step as its row does
      const brief = place.closest("#card") && place.closest("#card").querySelector('.sec-brief .h-cut[data-state="' + CSS.escape(cut.dataset.state) + '"]');
      if (brief) brief.textContent = cut.textContent;
    }
    const line = row && row.querySelector(".h-joined");
    if (line && words.length) line.innerHTML = words.join("; ");
    // a node no longer at the state shown, named as it was there
    if (row) for (const a of row.querySelectorAll("a.go[data-ref]")) if (a.textContent === a.dataset.ref && name.has(a.dataset.ref)) a.textContent = name.get(a.dataset.ref);
  }

  // --- the open view ---

  function shownItems() {
    // the list's own grouping: its families, and by block where it reads so
    return O.families(openItems);
  }
  // The blocks with a claim established, and those resting on a
  // provisional reading: a click walks to the next one.
  function walkLinks() {
    const links = [
      established.size ? '<span class="e-tally" title="' + escape(plural(established.size, "block has", "blocks have") + " a claim established (any claim, not every one)") + '">' + P.ESTABLISHED_GLYPH + " " + established.size + " established</span>" : "",
      provisional.size ? '<span class="e-tally" title="' + escape(plural(provisional.size, "block has", "blocks have") + " a live reading read off a copy that is not the version of record") + '">' + provisional.size + " provisional</span>" : "",
    ].filter(Boolean);
    return links.join(" · ");
  }
  function renderOpen() {
    const total = openItems.length;
    tabLabel("tab-open", "open", total);
    const gate = state.gate && verdict && !verdict.error ? verdict.gates.find((g) => g.gate === state.gate && g.items.length) : null;
    if (gate) {
      // the gate's items by their index in the check's items
      let at = 0;
      for (const g of verdict.gates) { if (g === gate) break; at += g.items.length; }
      $("open").innerHTML = O.gateHtml(gate, at, P, state.check, { toneOf: (item) => K.tone(item, P), sentences: K.CAUSE_SENTENCES, folds: state.openFolds });
      mathsIn($("open"), ".e-words");
      return;
    }
    $("open").innerHTML = O.html(openItems, shownItems(), P, state.item, "none", state.families, K.kindTones(verdict), openNotes, walkLinks(), state.openFolds);
    mathsIn($("open"), ".e-words");
  }

  // --- the check tab ---

  function showVerdict() {
    const chip = K.chip(verdict);
    $("verdict").className = "v-" + chip.tone;
    $("verdict").textContent = chip.text;
    $("verdict").title = chip.title;
  }

  function renderCheck() {
    const n = K.failing(verdict);
    tabLabel("tab-check", "check", n === null ? "?" : n);
    const ahead = Boolean(files && files.pending && verdict && files.state === verdict.state);
    $("check").innerHTML = K.html(verdict, O, P, state.check, state.checkFolds, { ahead });
    mathsIn($("check"), ".e-words");
  }
  // --- the inbox tab ---

  function renderInbox() {
    tabLabel("tab-inbox", "inbox", inbox === null || inbox.error ? "?" : I.decisions(inboxItems));
    const lapsed = openItems.filter((item) => item.kind === "lapsed").length;
    $("inbox").innerHTML = I.html(inbox, inboxItems, O, P, state.inbox, state.inboxFolds, { lapsed });
    mathsIn($("inbox"), ".i-words");
  }
  // An inbox item clicked: marked, and its block selected where it has one.
  async function pickInbox(i) {
    state.inbox = i;
    renderInbox();
    const item = inboxItems[i];
    if (item && item.block) {
      state.keepTab = true;
      await select(item.block, { scroll: true });
      state.keepTab = false;
    }
  }

  // A check item clicked: marked, and its block selected where it has one.
  async function pickCheck(i) {
    state.check = i;
    renderCheck();
    if (state.gate) renderOpen();
    const item = K.items(verdict)[i];
    if (item && item.block) {
      state.keepTab = true;
      await select(item.block, { scroll: true });
      state.keepTab = false;
    }
  }

  async function pick(i, { scroll = true } = {}) {
    state.item = i;
    renderOpen();
    const current = $("open").querySelector(".open-item.current");
    if (current) current.scrollIntoView({ block: "nearest" });
    const item = openItems[i];
    if (item.block) {
      state.keepTab = true;
      await select(item.block, { scroll });
      state.keepTab = false;
    } else light();
  }

  function walk(by) {
    const shown = O.order(shownItems());
    const next = O.walk(shown, state.item, by);
    if (next === null) return;
    showTab("open");
    pick(next);
  }

  // A command copied from the card's record; where the page may not
  // write the clipboard, selected for a copy by hand.
  async function copied(button) {
    try {
      await navigator.clipboard.writeText(button.dataset.copy);
      say("copied: " + button.dataset.copy);
    } catch {
      const code = button.previousElementSibling;
      getSelection().selectAllChildren(code);
      say("selected: copy it with your keys");
    }
  }

  // A step of the block's history compared in the paper: change mode
  // between the state before it and its own, the later shown.
  async function compareStep(from, to) {
    const node = state.block !== null ? names.node.get(state.block) : null;
    if (to !== state.stateId) {
      state.block = null;
      await load(to);
    }
    await compare(from);
    showTab("changes");
    const ident = node && names.byNode.get(node);
    if (ident) {
      state.keepTab = true;
      await select(ident, { trail: false });
      state.keepTab = false;
    }
    say("");
  }

  // A tab chosen opens the inspector; one the page switches to by itself
  // (a refresh, a state read) leaves it as it is.
  // A tab's name and its count, the count set smaller.
  function tabLabel(id, name, count) {
    $(id).innerHTML = escape(name) + " <small>" + escape(String(count)) + "</small>";
  }
  function showTab(tab, { open = true } = {}) {
    state.tab = tab;
    if (open) showSide(true);
    $("card").hidden = tab !== "card";
    $("open").hidden = tab !== "open";
    $("check").hidden = tab !== "check";
    $("inbox").hidden = tab !== "inbox";
    $("changes").hidden = tab !== "changes";
    $("edit").hidden = tab !== "edit";
    if (tab === "edit") renderEdit();
    for (const button of document.querySelectorAll("#tabs button")) button.classList.toggle("on", button.dataset.tab === tab);
    writeHash();
  }

  // --- the fragment ---

  function writeHash() {
    const params = new URLSearchParams();
    if (state.stateId) params.set("state", state.stateId);
    if (state.block !== null) params.set("node", names.node.get(state.block));
    if (state.tab !== "card") params.set("tab", state.tab);
    if (state.lens !== "all") params.set("lens", state.lens);
    if (state.roles) params.set("roles", "1");
    if (state.tex) params.set("tex", "1");
    if (!state.lines) params.set("lines", "0");
    if (!state.gutter) params.set("gutter", "0");
    if (!state.side) params.set("side", "0");
    if (state.faint) params.set("faint", "1");
    if (state.split !== 62) params.set("split", String(state.split));
    if (state.gate) params.set("gate", state.gate);
    if (state.families.size) params.set("families", [...state.families].join(","));
    if (state.reach) params.set("reach", "1");
    // the reader's own folds, so a link keeps a section opened that is
    // folded by default, and one folded that opens
    if (state.shut.size) params.set("shut", [...state.shut].join(","));
    if (state.opened.size) params.set("open", [...state.opened].join(","));
    if (state.from) params.set("from", state.from);
    history.replaceState(null, "", "#" + params.toString());
  }

  // --- the events ---

  $("text").addEventListener("click", (event) => {
    const target = event.target;
    const cxb = target.closest(".cxb[data-node]");
    if (cxb) {
      const ident = names.byNode.get(cxb.dataset.node);
      showTab("changes");
      state.keepTab = true;
      select(ident, { scroll: false }).then(() => (state.keepTab = false));
      return;
    }
    const far = target.closest("#arcs [data-ref]");
    if (far) return select(resolve(far.dataset.ref));
    const bang = target.closest(".lane-layer .om-bang");
    if (bang) return selectMarked(rowMarks[Number(bang.dataset.row)]);
    const mark = target.closest("[data-item]");
    if (mark) {
      showTab("open");
      return pick(Number(mark.dataset.item), { scroll: false });
    }
    const bracketCell = target.closest(".ln .gl i.g");
    if (bracketCell) return select(layer.groups[Number(bracketCell.dataset.g)].id, { scroll: false });
    // a formula selects the block that holds it; `¶` off shows the
    // sources, all at once
    const formula = target.closest(".mxt, .mxs");
    const holder = formula ? formula.dataset.b ?? formula.dataset.holder : undefined;
    if (holder !== undefined) return select(layer.blocks[Number(holder)].id, { scroll: false });
    const owned = target.closest("[data-b]");
    if (owned) return select(layer.blocks[Number(owned.dataset.b)].id, { scroll: false });
    // a connection line keeps the selection
    if (target.closest("#arcs [data-edge]")) return;
    // a click on a row's ground, between or past its words but not in the
    // lines' lane, or on its gutter: the block whose words stand nearest,
    // on the click's screen line first; anywhere else (the margins, the
    // lines' lane, a row no block holds) clears the selection
    const row = target.closest(".ln");
    const tx = row && row.querySelector(".tx");
    if (tx) {
      const box = tx.getBoundingClientRect();
      const lane = parseFloat(getComputedStyle(tx).paddingRight) || 0;
      const inGutter = !target.closest(".tx");
      if (inGutter || event.clientX <= box.right - lane) {
        const near = nearestBlock(tx, inGutter ? box.left : event.clientX, event.clientY);
        if (near !== null && near !== undefined) return select(layer.blocks[near].id, { scroll: false });
      }
    }
    // a drag that selected words is no click to clear on
    if (String(getSelection()).length) return;
    deselect();
  });
  // The selection cleared: no block, its card the prompt, its arcs gone.
  function deselect() {
    if (state.block === null) return;
    state.block = null;
    reached = null;
    hideTip();
    lightEdge(null);
    light();
    drawArcs();
    writeHash();
    renderCard();
  }
  // A row's `!` clicked: the first block its marks stand on, in the
  // paper's order, selected, its card at the open items on it; a mark on
  // no block (the paper's, a work's) the worst in the open view.
  async function selectMarked({ marks }) {
    const blocks = [...new Set(marks.flatMap((m) => m.blocks))].sort((a, b) => blockNumber.get(a) - blockNumber.get(b));
    if (!blocks.length) {
      showTab("open");
      return pick(marks[0].index, { scroll: false });
    }
    state.shut.delete("open");
    await select(blocks[0], { scroll: false });
    showTab("card");
  }
  function nearestBlock(tx, x, y) {
    let best = null;
    let far = Infinity;
    for (const span of tx.querySelectorAll("[data-b]")) {
      for (const r of span.getClientRects()) {
        const dy = y < r.top ? r.top - y : y > r.bottom ? y - r.bottom : 0;
        const dx = x < r.left ? r.left - x : x > r.right ? x - r.right : 0;
        const d = dy * 1000 + dx;
        if (d < far) {
          far = d;
          best = Number(span.dataset.b);
        }
      }
    }
    return best;
  }
  // --- an edge, named: where its ends stand and what it quotes ---

  // A block's or group's first line, as the gutter numbers it; null for a
  // file or the paper.
  function lineOf(ident) {
    const row = R.rowOf(index, ident);
    if (!row) return null;
    return layer.files.length > 1 ? row[0] + ":" + row[1] : "line " + row[1];
  }
  function quoted(span) {
    const f = fileIndex.get(span[0]);
    return f === undefined ? "" : layer.files[f].text.slice(span[1], span[2]);
  }
  // A block's or a group's opening words, its text as the state keeps it.
  function opening(ident, most = 260) {
    const group = index.groups.get(ident);
    const block = index.blocks.get(group ? group.blocks[0] : ident);
    if (!block) return "";
    const words = blockText(block).replace(/\s+/g, " ").trim();
    return words.length > most ? words.slice(0, most) + "…" : words;
  }
  // words quoted in a popup's row, their formulas rendered
  const quotedRow = (words) => (words ? '<span class="tc-q">“' + escape(words) + "”</span>" : "");
  // An edge named for the popup, each thing once: the head, the verb and the other block
  // as the selection reads it, and which way reliance runs (an edge not
  // touching the selection: its source, verb and target); then what the
  // other block says, under its role and line, the part named and the
  // evidence. A person's edge says so; an agent's is the default.
  function edgeTip(i, foot = "") {
    const edge = layer.edges[i];
    const kind = P.edge(edge.kind);
    const ident = state.block;
    const out = edge.source === ident;
    const mine = ident !== null && (out || edge.target === ident);
    const other = mine && !out ? edge.source : edge.target;
    const verb = (C.VERBS[edge.kind] || [edge.kind, edge.kind + " by"])[mine && !out ? 1 : 0];
    const name = (id) => escape(names.handle.get(id) || (id === "scope:paper" ? "the paper" : id));
    const up = mine && R.stream(layer, edge, out) === "up";
    const part = edge.parts.length ? edge.parts.join(", ") : "";
    const evidence = edge.evidence.map(quoted).join(" … ");
    const words = opening(other);
    return tipCard({
      label: [[verb, kind.stroke], [mine ? (up ? "↑ upstream" : "↓ downstream") : ""], [edge.by === "person" ? "by a person" : ""]],
      title: (mine ? "" : name(edge.source) + ' <span class="tc-verb" style="color:' + kind.stroke + '">' + escape(verb) + "</span> ") + name(other),
      meta: escape(endWhat(other)),
      body: words ? "“" + escape(words) + "”" : "",
      rows: [
        [part ? "Part" + (mine && edge.target === ident ? ", here" : "") : "", part ? escape(part) + (edge.anchors.length ? " " + quotedRow(edge.anchors.map(quoted).join(" … ")) : "") : ""],
        [mine && !out ? "Evidence there" : "Evidence", quotedRow(evidence.length > 300 ? evidence.slice(0, 300) + "…" : evidence)],
      ],
      foot,
    });
  }
  function endWhat(ident) {
    const block = index.blocks.get(ident);
    const group = index.groups.get(ident);
    const what = group ? "group " + group.kind : block ? (block.roles.join("+") || block.form) : ident.startsWith("file:") ? "a file" : "the paper";
    const at = lineOf(ident);
    return what + (at ? ", " + at : "");
  }

  // --- the mathematics, rendered ---

  // Each text under `root` that `selector` names, rendered as the paper
  // is (`markup.html`): its formulas a click from their source
  // (`maths.withMaths`), its markup shown as it prints; its source kept
  // beside it for `¶` off.
  function mathsIn(root, selector) {
    const formulaHtml = (formula, source) => {
      const done = MX.render(window.katex, formula, macros);
      return done.error ? null : '<span class="mxr' + (formula.display ? " mxd" : "") + '">' +
        '<span class="mxr-r">' + done.html + '</span><code class="mxr-s">' + escape(source) + "</code></span>";
    };
    for (const element of root.querySelectorAll(selector)) {
      const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
      const nodes = [];
      for (let node = walker.nextNode(); node; node = walker.nextNode()) if (/[$\\*_`~"'{-]/.test(node.data) && !node.parentElement.closest(".mkr, .mxr")) nodes.push(node);
      for (const node of nodes) {
        const words = node.data;
        const done = MK.html(words, paperFormat, MX.find(words, paperFormat), (formula) => formulaHtml(formula, words.slice(formula.start, formula.end)));
        if (done === null) continue;
        const holder = document.createElement("span");
        holder.innerHTML = '<span class="mkr"><span class="mkr-r">' + done + '</span><span class="mkr-s">' + escape(words) + "</span></span>";
        node.replaceWith(...holder.childNodes);
      }
    }
  }

  const tip = document.createElement("div");
  tip.className = "tip";
  document.body.appendChild(tip);
  // what the tip holds, so that a pointer moving within one thing renders
  // its formulas once
  let tipHtml = null;
  function showTip(event, html) {
    if (html !== tipHtml) {
      tipHtml = html;
      tip.innerHTML = html;
      mathsIn(tip, ".tc-body:not(.cx-diff), .tc-q");
    }
    tip.style.display = "block";
    const x = Math.min(event.clientX + 14, window.innerWidth - tip.offsetWidth - 8);
    const y = event.clientY + 16 + tip.offsetHeight > window.innerHeight ? event.clientY - tip.offsetHeight - 10 : event.clientY + 16;
    tip.style.left = Math.max(4, x) + "px";
    tip.style.top = Math.max(4, y) + "px";
  }
  function hideTip() {
    tip.style.display = "none";
  }
  // An edge hovered: its evidence lit in the text, what it points at
  // boxed (the part it names, else the whole block or group), its arc and
  // arrowhead out of the arcs' resting fade and a little thicker: enough to
  // tell it where lines run close, and no other line changes.
  function lightEdge(i) {
    const svg = $("arcs");
    const pane = $("text");
    for (const old of pane.querySelectorAll(".edge-box")) old.remove();
    if (i === null) {
      hover.textContent = "";
      // the shared arrowheads back on top
      if (svg) for (const el of svg.querySelectorAll("path.head.mixed")) svg.appendChild(el);
      return;
    }
    // the hovered arc and its arrowhead drawn last, over the lines it runs
    // beside and a shared arrowhead
    if (svg && svg.lastElementChild && svg.lastElementChild.dataset.edge !== String(i)) {
      for (const el of svg.querySelectorAll(':is(path.backing, path.arc, path.head)[data-edge="' + i + '"]')) svg.appendChild(el);
    }
    const edge = layer.edges[i];
    const colour = P.edge(edge.kind).stroke;
    // an end with no words of its own (no evidence, no part named) is the
    // whole block or group, so that the hovered edge's end stands apart
    // from the selection's other linked blocks
    const whole = (ident) => (index.groups.has(ident) ? index.groups.get(ident).blocks : [ident])
      .map((id) => blockNumber.get(id)).filter((n) => n !== undefined).map((n) => ".tx span.b" + n + ":not(.r-hidden)");
    const sourceWhole = edge.evidence.length ? [] : whole(edge.source);
    // the target boxed as one outline around its words as they wrap, not a
    // box a piece (a formula, another mark's words break them into pieces)
    const targets = edge.anchors.length ? [".tx span.y" + i] : whole(edge.target);
    const box = pane.getBoundingClientRect();
    const rects = targets.length ? [...pane.querySelectorAll(targets.join(", "))].flatMap((el) => {
      const blank = !el.textContent.trim();
      return [...el.getClientRects()].map((r) => ({
        left: r.left - box.left + pane.scrollLeft, right: r.right - box.left + pane.scrollLeft,
        top: r.top - box.top + pane.scrollTop, bottom: r.bottom - box.top + pane.scrollTop, blank,
      }));
    }) : [];
    if (rects.length) {
      const outlined = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      outlined.setAttribute("class", "edge-box");
      outlined.setAttribute("aria-hidden", "true");
      outlined.innerHTML = R.outline(rects).map((d) => '<path d="' + d + '" stroke="' + colour + '"></path>').join("");
      pane.appendChild(outlined);
    }
    hover.textContent = ".tx span.x" + i + " { background: " + colour + "55; } " +
      (sourceWhole.length ? sourceWhole.join(", ") + " { background: " + colour + "26; } " : "") +
      '#arcs :is(path.arc, path.head)[data-edge="' + i + '"] { opacity: 1; } #arcs path.backing[data-edge="' + i + '"] { display: inline; } ' +
      ["up", "down"].map((st) => '#arcs path.arc.' + st + '[data-edge="' + i + '"] { stroke-width: ' + (P.STREAM[st].width + 1) + "px; }").join(" ");
  }

  // The edge a card line names, by its id; a self mark has none.
  const edgeById = () => new Map(layer.edges.map((e, i) => [e.id, i]));
  let edgeIndex = null;
  function cardEdge(li) {
    if (!li || !lastReport || !C.EDGES.includes(li.dataset.sec)) return null;
    const item = lastReport[li.dataset.sec].items[Number(li.dataset.i)];
    if (!item || !item.id) return null;
    edgeIndex = edgeIndex || edgeById();
    const i = edgeIndex.get(item.id);
    return i === undefined ? null : i;
  }

  $("text").addEventListener("mousemove", (event) => {
    const cxb = event.target.closest(".cxb[data-node]");
    if (cxb) return showTip(event, diffTip(cxb.dataset.node));
    const marked = event.target.closest(".ov, .ovw");
    if (marked) {
      const ks = [...marked.classList].filter((c) => /^o\d+$/.test(c)).map((c) => Number(c.slice(1)));
      if (ks.length) return showTip(event, ks.map((k) => overlayTip(placements[k])).join(""));
    }
    // a box's words: the cards of the items boxed there (at rest, or the
    // one picked)
    const found = event.target.closest(".ip");
    if (found) {
      const boxed = [...found.classList].filter((c) => /^f\d+$/.test(c)).map((c) => itemPlaces[Number(c.slice(1))])
        .filter((p) => p.drawn || p.item === state.item);
      const items = [...new Set(boxed.map((p) => p.item))];
      if (items.length) return showTip(event, items.map(openTip).join(""));
    }
    // the gutter's marks and bars, each named by its own card (a folded
    // +N keeps its list on hover)
    const mark = event.target.closest(".lane-layer .om-bang");
    if (mark) return showTip(event, rowTip(rowMarks[Number(mark.dataset.row)]));
    const bar = event.target.closest(".lane-layer .lane-bar");
    if (bar) return showTip(event, blockTip(Number(bar.dataset.b)));
    const bracketCell = event.target.closest(".ln .gl i.g");
    if (bracketCell) return showTip(event, groupTip(Number(bracketCell.dataset.g)));
    const arc = event.target.closest("#arcs [data-edge]");
    if (!arc) {
      lightEdge(null);
      return hideTip();
    }
    const i = Number(arc.dataset.edge);
    lightEdge(i);
    showTip(event, edgeTip(i, "Click: select " + (names.handle.get(arc.dataset.ref) || "its other end")));
  });
  $("text").addEventListener("mouseleave", () => {
    lightEdge(null);
    hideTip();
  });
  $("card").addEventListener("mousemove", (event) => {
    // a block or group named on the card, its card on hover, as its bar's
    const named = event.target.closest("#card a.go[data-ref]");
    if (named && !named.closest(".edge-row")) {
      const id = resolve(named.dataset.ref);
      const b = id === null ? undefined : blockNumber.get(id);
      const g = id === null ? -1 : layer.groups.findIndex((x) => x.id === id);
      if (b !== undefined || g >= 0) {
        lightEdge(null);
        return showTip(event, b !== undefined ? blockTip(b) : groupTip(g));
      }
    }
    const i = cardEdge(event.target.closest(".edge-row"));
    if (i === null) {
      lightEdge(null);
      return hideTip();
    }
    lightEdge(i);
    showTip(event, edgeTip(i));
  });
  // the open line's kinds: the first item's row, or its section, unfolded
  // into view
  $("card").addEventListener("click", (event) => {
    const link = event.target.closest(":is(.open-line, .status-line) [data-goto]");
    if (!link) return;
    const [how, what] = link.dataset.goto.split(":");
    const card = $("card");
    // the status line's count: the first open problem's card
    if (how === "first") {
      const first = card.querySelector(".pcard, .finding.fs-open");
      if (!first) return;
      for (let up = first; up && up !== card; up = up.parentElement) if (up.tagName === "DETAILS") up.open = true;
      first.scrollIntoView({ block: "center" });
      first.classList.add("flash");
      return setTimeout(() => first.classList.remove("flash"), 900);
    }
    // a reading's kind on a block with no claim: its Judgments
    const place = how === "here" ? card.querySelector('[data-here="' + what + '"]') : card.querySelector('details.card-sec[data-sec="' + what + '"]') ||
      card.querySelector('details.card-sec[data-sec="standings"]') || card.querySelector('details.card-sec[data-sec="judgments"]');
    if (!place) return;
    for (let up = place; up && up !== card; up = up.parentElement) if (up.tagName === "DETAILS") up.open = true;
    place.scrollIntoView({ block: "center" });
    place.classList.add("flash");
    setTimeout(() => place.classList.remove("flash"), 900);
  });
  // a finding's long problem, unfolded and folded again
  $("card").addEventListener("click", (event) => {
    const more = event.target.closest(".f-more");
    if (!more) return;
    const problem = more.previousElementSibling;
    const whole = problem.classList.toggle("clamped") === false;
    more.textContent = whole ? "less" : "more";
  });
  // a section the reader folds or unfolds stays so for the next
  // selection: read off the click on its head, never off a toggle, which
  // a card's own defaults fire as it is drawn
  $("card").addEventListener("click", (event) => {
    const head = event.target.closest("summary.sec-head");
    const section = head && head.parentElement;
    if (!section || !section.matches("details.card-sec") || event.target.closest("a, button")) return;
    const name = section.dataset.sec;
    if (section.open) {
      state.opened.delete(name);
      state.shut.add(name);
    } else {
      state.shut.delete(name);
      state.opened.add(name);
    }
    writeHash();
  });
  $("card").addEventListener("mouseleave", () => {
    lightEdge(null);
    hideTip();
  });
  $("side").addEventListener("click", (event) => {
    const link = event.target.closest("a.go");
    if (link && link.closest("#changes, #edit, #open")) {
      state.keepTab = true;
      return select(resolve(link.dataset.ref)).then(() => (state.keepTab = false));
    }
    if (link) return select(resolve(link.dataset.ref));
    const cut = event.target.closest(":is(#card, #changes, #edit) .cx-diff.cut");
    if (cut) {
      cut.hidden = true;
      cut.nextElementSibling.hidden = false;
      return;
    }
    const stepped = event.target.closest("#card .h-compare");
    if (stepped) return compareStep(stepped.dataset.from, stepped.dataset.to);
    if (event.target.closest("#card [data-reveal]")) return scrollTo(state.block);
    const copy = event.target.closest("#card .copy[data-copy]");
    if (copy) return copied(copy);
    if (event.target.closest("#back")) return step(-1);
    if (event.target.closest("#forward")) return step(1);
    const tab = event.target.closest("#tabs button[data-tab]");
    if (tab) return showTab(tab.dataset.tab);
    // a row's Block ›: the item picked, then its detail
    const detail = event.target.closest(".open-item [data-open-block]");
    if (detail) {
      const row = detail.closest(".open-item");
      const picked = row.dataset.check !== undefined ? pickCheck(Number(row.dataset.check)) : row.dataset.inbox !== undefined ? pickInbox(Number(row.dataset.inbox)) : pick(Number(row.dataset.here ?? row.dataset.item));
      return picked.then(() => showTab("card"));
    }
    const checked = event.target.closest(".open-item[data-check]");
    if (checked && !event.target.closest("a")) return pickCheck(Number(checked.dataset.check));
    const decided = event.target.closest(".open-item[data-inbox]");
    if (decided && !event.target.closest("a")) return pickInbox(Number(decided.dataset.inbox));
    const item = event.target.closest(".open-item");
    if (item && !event.target.closest("a")) return pick(Number(item.dataset.item));
  });
  $("verdict").addEventListener("click", () => showTab("check"));
  // a gate's link: its items alone in the open tab
  $("check").addEventListener("click", (event) => {
    const link = event.target.closest("[data-gate-link]");
    if (!link) return;
    event.preventDefault();
    state.gate = link.dataset.gateLink;
    showTab("open");
    renderOpen();
    writeHash();
  });
  $("drift").addEventListener("click", (event) => {
    if (event.target.closest("#see-edit")) showTab("edit");
  });
  // the inbox's line on a kind Open lists (the lapsed readings): that
  // kind in Open, its family and its fold opened, scrolled to
  $("inbox").addEventListener("click", (event) => {
    const link = event.target.closest("[data-open-kind]");
    if (!link) return;
    const kind = link.dataset.openKind;
    const family = O.FAMILIES.find((f) => f.kinds.includes(kind));
    state.gate = null;
    if (family) state.families.add(family.key);
    state.openFolds.shut.delete("k:" + kind);
    state.openFolds.opened.add("k:" + kind);
    showTab("open");
    renderOpen();
    const fold = $("open").querySelector('[data-kind="' + kind + '"]');
    if (fold) fold.scrollIntoView({ block: "start" });
  });
  $("open").addEventListener("toggle", (event) => {
    const family = event.target.dataset && event.target.dataset.family;
    if (!family) return;
    if (event.target.open) state.families.add(family);
    else state.families.delete(family);
    writeHash();
  }, true);
  // a section, a kind or a part the reader folds or unfolds stays so as
  // its tab is drawn again: read off the click on its head, as the card's
  // sections
  const keepFold = (folds) => (event) => {
    const head = event.target.closest("summary");
    const fold = head && head.parentElement;
    if (!fold || !fold.dataset.fold || event.target.closest("a")) return;
    const { opened, shut } = folds();
    if (fold.open) {
      opened.delete(fold.dataset.fold);
      shut.add(fold.dataset.fold);
    } else {
      shut.delete(fold.dataset.fold);
      opened.add(fold.dataset.fold);
    }
  };
  $("open").addEventListener("click", keepFold(() => state.openFolds));
  $("check").addEventListener("click", keepFold(() => state.checkFolds));
  $("inbox").addEventListener("click", keepFold(() => state.inboxFolds));
  $("open").addEventListener("click", (event) => {
    if (event.target.closest("[data-gate-all]")) {
      state.gate = null;
      renderOpen();
      writeHash();
      return;
    }
  });
  $("state").addEventListener("click", () => stateMenu($("state-panel").hidden));
  $("state-list").addEventListener("click", async (event) => {
    const row = event.target.closest(".state-row");
    if (!row) return;
    stateMenu(false);
    $("state").focus();
    if (row.dataset.state !== state.stateId) await showStateOf(row.dataset.state);
  });
  // the panel closes on Escape and on a click outside it
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || $("state-panel").hidden) return;
    stateMenu(false);
    $("state").focus();
  });
  document.addEventListener("click", (event) => {
    if (!$("state-panel").hidden && !event.target.closest("#state-control")) stateMenu(false);
  });
  $("compare").addEventListener("change", (event) => {
    stateMenu(false);
    compare(event.target.value || null);
  });
  $("what-changed").addEventListener("click", async () => {
    stateMenu(false);
    const made = states.find((s) => s.id === state.stateId);
    await compare(made && made.parent);
    showTab("changes");
  });
  $("compare-off").addEventListener("click", () => compare(null));
  // a link edited by hand: the page reads its fragment again
  window.addEventListener("hashchange", () => location.reload());
  $("lens").addEventListener("change", (event) => {
    state.lens = event.target.value;
    light();
    drawArcs();
    showTools();
    writeHash();
  });
  $("reach").addEventListener("change", (event) => {
    state.reach = event.target.checked;
    reached = null;
    if (state.reach && state.block !== null) askReach();
    else {
      say("");
      light();
    }
    showTools();
    writeHash();
  });
  $("math").addEventListener("click", () => {
    state.tex = !state.tex;
    reflowed(renderText);
    showTools();
    writeHash();
  });
  // Lines off gives the lines' lane to the text.
  $("lines").addEventListener("click", () => {
    state.lines = !state.lines;
    reflowed(() => document.body.classList.toggle("lines-off", !state.lines));
    light();
    showTools();
    writeHash();
  });
  // Gutter off gives its columns to the text: line numbers, brackets,
  // bars, marks and counts.
  $("gutter").addEventListener("click", () => {
    state.gutter = !state.gutter;
    reflowed(() => document.body.classList.toggle("gutter-off", !state.gutter));
    showTools();
    writeHash();
  });
  $("roles").addEventListener("change", (event) => {
    state.roles = event.target.checked;
    document.body.classList.toggle("roles-on", state.roles);
    showTools();
    writeHash();
  });
  $("faint").addEventListener("change", (event) => {
    state.faint = event.target.checked;
    drawArcs();
    showTools();
    writeHash();
  });
  // the settings menu closes on a click outside it
  document.addEventListener("click", (event) => {
    if ($("more-tools").open && !event.target.closest("#more-tools")) $("more-tools").open = false;
  });

  // The search: a node id, a handle or a block id; else a finding's or a
  // judgment's id or a unique prefix of it, as every list prints them
  // (hex with a digit, then its filing's numbers: `786c5cdb0b8a-1-1`),
  // read by `show`; else the next block (after the selected one) whose
  // text holds the words, ignoring case. A word that looks like an id
  // costs one read before its search.
  const filingLike = (value) => /^[0-9a-f]{4,}(-\d*){0,2}$/.test(value) && /\d/.test(value.split("-")[0]);
  function blockText(block) {
    return block.owned.map(([file, start, end]) => {
      const f = fileIndex.get(file);
      return f === undefined ? "" : layer.files[f].text.slice(start, end);
    }).join(" ");
  }
  $("search").addEventListener("change", async (event) => {
    const value = event.target.value.trim();
    if (!value) return;
    const found = resolve(value);
    if (found) return select(found);
    let refused = null;
    if (filingLike(value)) {
      try {
        const report = await A.show(state.stateId, value);
        if (report.finding || report.judgment) return selectFiling(report);
      } catch (error) {
        refused = error.message;
      }
    }
    const needle = value.toLowerCase();
    const blocks = layer.blocks;
    const from = state.block !== null && blockNumber.has(state.block) ? blockNumber.get(state.block) + 1 : 0;
    for (let k = 0; k < blocks.length; k += 1) {
      const i = (from + k) % blocks.length;
      if (blockText(blocks[i]).toLowerCase().includes(needle)) return select(blocks[i].id);
    }
    say(refused || "no block holds “" + value + "”");
  });

  // A finding or a judgment by its id: the block `show` places it at
  // selected, and the card open on its row, every fold around it opened
  // (a section opened so stays open, as a click on its head leaves it).
  async function selectFiling(report) {
    const block = report.block && (report.block.block || report.block.group);
    const what = report.finding ? "finding " + report.finding.id : "judgment " + report.judgment.id;
    const onNode = report.judgment || report.anchors.items.some((a) => a.node);
    if (!block) return say(what + (onNode ? ": its block is not at this state (`axiomation history PAPER ID` says where it went)" : " is on no block (a work, a file or the paper)"));
    await select(block);
    const row = $("card").querySelector(report.finding
      ? '.finding[data-finding="' + CSS.escape(report.finding.id) + '"]'
      : '.reading[data-judgment="' + CSS.escape(report.judgment.id) + '"]');
    if (!row) return say(what + " is not on the card of " + (names.handle.get(block) || block));
    for (let up = row.parentElement; up; up = up.parentElement) if (up.tagName === "DETAILS") up.open = true;
    row.classList.add("named");
    row.scrollIntoView({ block: "center" });
  }

  document.addEventListener("keydown", (event) => {
    if (event.target.closest && event.target.closest("input, select, textarea")) return;
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    // the walk: j and k anywhere; the arrows while the open tab is shown,
    // elsewhere they scroll
    const arrows = state.tab === "open" && !$("open").hidden;
    if (event.key === "j" || (arrows && event.key === "ArrowDown")) {
      event.preventDefault();
      walk(1);
    } else if (event.key === "k" || (arrows && event.key === "ArrowUp")) {
      event.preventDefault();
      walk(-1);
    } else if (event.key === "[") step(-1);
    else if (event.key === "]") step(1);
  });

  // A change that rewraps the text (the lines' lane, the inspector): the
  // first row whole in view is held where it stood, and the lanes are
  // measured again (they draw the arcs).
  function reflowed(change) {
    const pane = $("text");
    const box = pane.getBoundingClientRect();
    const held = [...pane.querySelectorAll(".ln[id]")].find((row) => row.getBoundingClientRect().top >= box.top);
    const offset = held ? held.getBoundingClientRect().top - box.top : 0;
    change();
    // found again by its id: a change may draw the rows anew, a row folded
    // in one view keeping its id as the place it folds at
    const again = held && document.getElementById(held.id);
    if (again) pane.scrollTop += again.getBoundingClientRect().top - box.top - offset;
    drawLanes();
  }

  // The inspector closed for reading, and open again on its tab and its
  // place in the list. A selection leaves it closed; a tab chosen (the
  // verdict, n or p) opens it.
  let sideScroll = 0;
  function showSide(open) {
    if (open === state.side) return;
    if (!open) sideScroll = $("panel").scrollTop;
    state.side = open;
    reflowed(() => document.body.classList.toggle("side-closed", !open));
    $("side-toggle").setAttribute("aria-pressed", String(open));
    $("side-toggle").title = open ? "hide the inspector: the paper takes its width" : "show the inspector, on its tab, selection and place in the list";
    if (open) $("panel").scrollTop = sideScroll;
    writeHash();
  }
  $("side-toggle").addEventListener("click", () => showSide(!state.side));

  // --- the splitter: drag to resize, kept in the fragment ---

  function splitter() {
    const main = document.querySelector("main");
    const apply = () => {
      $("left").style.flex = "0 0 " + state.split + "%";
    };
    $("split-v").addEventListener("mousedown", (event) => {
      event.preventDefault();
      document.body.classList.add("dragging");
      const move = (e) => {
        const rect = main.getBoundingClientRect();
        state.split = Math.round(Math.min(85, Math.max(25, ((e.clientX - rect.left) / rect.width) * 100)));
        apply();
      };
      const up = () => {
        window.removeEventListener("mousemove", move);
        window.removeEventListener("mouseup", up);
        document.body.classList.remove("dragging");
        writeHash();
        redrawLanes();
      };
      window.addEventListener("mousemove", move);
      window.addEventListener("mouseup", up);
    });
    apply();
  }

  // --- start ---

  function styles() {
    // the theme's surfaces, as the stylesheet's tokens
    const tokens = Object.entries(P.SURFACE).map(([name, value]) => "--" + name + ": " + value + ";").join(" ");
    const rules = [":root { color-scheme: " + P.THEMES[P.THEME].scheme + "; " + tokens + " }"];
    for (const [role, r] of Object.entries(P.ROLE)) rules.push(".roles-on .tx .rl-" + role + " { background: " + r.tint + "; }");
    for (const [role, r] of Object.entries(P.ROLE)) rules.push("#card .card-head .role.rl-" + role + " { color: " + r.border + "; }");
    rules.push("#card .claim-words { border-left-color: " + P.ROLE.claim.border + "; background: " + P.ROLE.claim.tint + "; }");
    // change mode: the words added on a ground of the added colour, those
    // deleted struck in the deleted colour, outside the source
    rules.push("#text .tx span.dfa { background: " + P.change("added").colour + "40; } " +
      "#text .tx span.dfc { background: " + P.change("split").colour + "40; } " +
      "#text .tx .dft { color: " + P.change("split").colour + "; font-size: 0.9em; margin-left: 0.4em; user-select: none; cursor: default; } " +
      "#text .tx .dfd { color: " + P.change("deleted").colour + "; background: " + P.change("deleted").colour + "1f; text-decoration: line-through; user-select: none; cursor: default; }");
    // the verdict's chip and the check tab's head, in the outcome's tone
    for (const [tone, c] of Object.entries(P.CHECK)) rules.push("#verdict.v-" + tone + ", .h-verdict.v-" + tone + " { color: " + c.colour + "; border-color: " + c.colour + "; } #check .check-" + tone + " > summary .count, #open .check-" + tone + " > summary .count, #open .e-kindpart.check-" + tone + " > .e-kindhead .count, #check .k-v.check-" + tone + ", #check .k-gate.check-" + tone + " .count { color: " + c.colour + "; } #open .check-" + tone + " { --tone: " + c.colour + "; }");
    for (const tone of ["fail", "warn"]) rules.push(".tk i.om-bang.t-" + tone + ", .tip .tc-tone.t-" + tone + " { color: " + P.CHECK[tone].colour + "; }");
    // which way reliance runs, by the palette's line styles
    for (const [stream, st] of Object.entries(P.STREAM).filter(([name]) => name !== "aside")) {
      rules.push("#arcs path.arc." + stream + " { stroke-width: " + st.width + "px;" + (st.dash ? " stroke-dasharray: " + st.dash + ";" : "") + " }");
    }
    for (const [tone, st] of Object.entries(P.TONE)) rules.push("#card .st-chip.st-" + tone + " { color: " + st.colour + "; }");
    for (const [name, cs] of Object.entries(P.CLAIM_STATUS)) rules.push("#card .cs-chip.cs-" + name + ", #card .problem.cs-" + name + " .p-kind { color: " + cs.colour + "; } #card .problem.cs-" + name + " { border-left-color: " + cs.colour + "; }");
    for (const [n, sv] of Object.entries(P.SEVERITY)) rules.push("#card .sev.sev-" + n + " { color: " + sv.colour + "; } " +
      // a finding the author has to act on tinted in its severity's colour
      "#card .finding.sev-" + n + ":is(.fs-open, .fs-postponed):not(.fs-refiled) { background: " + sv.colour + "12; border-color: " + sv.colour + "60; }");
    for (const [verdict, v] of Object.entries(P.VERDICT)) rules.push("#card .verdict.v-" + verdict + " { color: " + v.colour + "; }");
    for (const [cls, ch] of Object.entries(P.CHANGE)) rules.push(".cx-" + cls + " { color: " + ch.colour + "; }");
    rules.push(".cx-lapsed { color: " + P.open("lapsed").colour + "; }");
    for (const [channel, ov] of Object.entries(P.OVERLAY)) {
      rules.push(".tx .ov.ov-" + channel + " { " + P.wordsStyle(ov.line, ov.colour, ov.line === "box" ? 1.5 : 2) + " }");
      rules.push(P.formulaRules(".tx .mxb.ov.ov-" + channel, ov.line, ov.colour, ov.line === "box" ? 1.5 : 2, 5));
      rules.push(".tx .ovw.ov-" + channel + " { background-color: " + ov.colour + "1c; }");
    }
    for (const [tone, r] of Object.entries(P.REACH)) {
      rules.push(":root { --reach-" + tone + ": " + r.colour + "38; }");
    }
    for (const [kind, k] of Object.entries(P.EDGE)) {
      rules.push("#card .verb.k-" + kind + " { color: " + k.stroke + "; }");
      rules.push("#card .rail.k-" + kind + " { border-left-color: " + k.stroke + "8c; }");
    }
    const style = document.createElement("style");
    style.textContent = rules.join("\n");
    document.head.appendChild(style);
    $("brand").src = P.THEMES[P.THEME].wordmark;
  }

  styles();
  $("lens").innerHTML = Object.entries(R.LENSES).map(([key, lens]) => '<option value="' + key + '">' + escape(lens.label) + "</option>").join("");
  const params = new URLSearchParams(location.hash.slice(1));
  state.tab = ["open", "check", "inbox", "changes", "edit"].includes(params.get("tab")) ? params.get("tab") : "card";
  state.lens = R.LENSES[params.get("lens")] ? params.get("lens") : "all";
  state.roles = params.get("roles") === "1";
  state.tex = params.get("tex") === "1";
  state.faint = params.get("faint") === "1";
  state.lines = params.get("lines") !== "0";
  state.gutter = params.get("gutter") !== "0";
  const sideShut = params.get("side") === "0";
  state.split = Math.min(85, Math.max(25, Number(params.get("split")) || 62));
  state.gate = K.GATES.includes(params.get("gate")) ? params.get("gate") : null;
  state.families = new Set((params.get("families") || "").split(",").filter((k) => O.FAMILIES.some((f) => f.key === k)));
  state.reach = params.get("reach") === "1";
  const known = (name) => name === "record" || C.SECTIONS.some(([k]) => k === name);
  for (const name of (params.get("shut") || "").split(",")) if (known(name)) state.shut.add(name);
  for (const name of (params.get("open") || "").split(",")) if (known(name) && !state.shut.has(name)) state.opened.add(name);
  $("reach").checked = state.reach;
  $("lens").value = state.lens;
  $("roles").checked = state.roles;
  $("faint").checked = state.faint;
  showTools();
  document.body.classList.toggle("lines-off", !state.lines);
  document.body.classList.toggle("gutter-off", !state.gutter);
  document.body.classList.toggle("roles-on", state.roles);
  const help = H.install(document, P, { open: O.KINDS, families: O.FAMILIES, lenses: Object.keys(R.LENSES), inbox: I.SECTIONS });
  $("help-toggle").addEventListener("click", help.open);
  splitter();
  try {
    overlays = (await A.overlays()).overlays;
    // the revision first: a write while the page reads is then caught by
    // the next poll
    const now = await A.now();
    revision = now.revision;
    files = now.files;
    states = (await A.states()).states;
    const wanted = params.get("state");
    const chosen = states.find((s) => wanted && s.id.startsWith(wanted)) || states[states.length - 1];
    await load(chosen.id);
  } catch (error) {
    $("text").innerHTML = '<p class="error">' + escape(error.message) + "</p>";
    return;
  }
  if (params.get("from")) await compare(params.get("from"));
  showTab((state.tab === "changes" && !compared) || (state.tab === "edit" && !files.pending) ? "card" : state.tab, { open: false });
  const node = params.get("node");
  const ident = node && names.byNode.get(node);
  // the first selection keeps the tab the link named
  state.keepTab = true;
  if (ident) await select(ident);
  else renderCard();
  state.keepTab = false;
  if (sideShut) showSide(false);
  for (const kind of ["pointerdown", "keydown", "wheel"]) document.addEventListener(kind, () => (touched += 1), { capture: true, passive: true });
  setInterval(() => poll(), POLL_MS);
  document.addEventListener("visibilitychange", () => poll({ atOnce: true }));
})();
