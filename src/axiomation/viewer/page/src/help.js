// The page's help: what every
// mark, line, colour, tab and control means, each with a sample drawn
// from the palette, so the help shows exactly what the page draws, and a
// "show me" that points at the real element. "Label the page" puts
// numbered badges on the live page.
//
// `sections` is pure (`node --test` reads it, and fails when a kind,
// provenance, standing, role, open-view kind, lens or control has no
// entry, or an entry names something the page does not draw); `install`
// wires the overlay into the page.
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else (root.AxView = root.AxView || {}).help = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // --- samples, drawn from the palette ---

  function swatch(colour, border) {
    return '<span class="h-sw" style="background:' + colour + (border ? ";border:2px solid " + border : "") + '"></span>';
  }
  function outline(colour, dashed) {
    return '<span class="h-sw" style="border:2px ' + (dashed ? "dashed" : "solid") + " " + colour + '"></span>';
  }
  function line(colour, dash, width) {
    return '<svg class="h-ln" width="42" height="10"><line x1="2" y1="5" x2="40" y2="5" stroke="' + colour +
      '" stroke-width="' + (width || 2.4) + '"' + (dash ? ' stroke-dasharray="' + dash + '"' : "") + "/></svg>";
  }
  // A bracket as the page draws it: solid upstream, dotted downstream, a
  // dot at the other end.
  function arc(colour, stream) {
    const dash = stream.dash ? ' stroke-dasharray="' + stream.dash + '" stroke-linecap="round"' : "";
    return '<svg class="h-ln" width="26" height="22"><path d="M5 3 H11 Q15 3 15 7 V15 Q15 19 11 19 H5" fill="none" stroke="' + colour + '" stroke-width="' + stream.width + '"' + dash +
      '/><circle cx="5" cy="19" r="2.5" fill="' + colour + '"/></svg>';
  }
  function text(style, words) {
    return '<span class="h-tx" style="' + style + '">' + words + "</span>";
  }
  function dot(colour) {
    return '<span class="h-dot" style="background:' + colour + '"></span>';
  }

  // The gutter, drawn: made-up lines built with the text pane's own classes
  // (so the figure looks as the page does, and the help test fails if a
  // class goes), numbered badges on each mark, and their key. Its sentences
  // and line numbers are invented: no paper's words go into every page.
  const GUTTER_CLASSES = ["ln", "gl", "g", "gs", "ge", "bl", "lane-bar", "tk", "om-bang", "ct", "io", "num", "tx"];
  function gutterFigure(P) {
    const n = (k, below) => '<b class="hf-n' + (below ? " hf-below" : "") + '">' + k + "</b>";
    const bar = (lane, colour, top = 0, bottom = 0, badge = "") => '<i class="lane-bar" style="left:' + lane * 6 + "px;top:" + top + "%;bottom:" + bottom +
      "%;background:" + colour + '"></i>' + badge;
    const blocks = (inner) => '<span class="bl" style="width:14px">' + inner + "</span>";
    const bracket = (cls, badge) => '<span class="gl">' + (cls ? '<i class="g ' + cls + '" style="border-color:' + P.GROUP.proof.stroke + '"></i>' : "<i></i>") + (badge || "") + "</span>";
    const bang = (tone) => '<i class="om-bang"' + (tone === "none" ? "" : ' style="color:' + P.CHECK[tone].colour + '"') + ">!</i>";
    const row = (cls, gl, bl, tk, ct, num, tx) => '<div class="ln' + (cls ? " " + cls : "") + '">' + gl + bl + '<span class="tk">' + tk + "</span>" +
      '<span class="ct">' + ct + '</span><span class="num">' + num + '</span><span class="tx">' + tx + "</span></div>";
    const hf = (inner, k, below) => '<span class="hf">' + inner + n(k, below) + "</span>";
    const prose = P.FORM.prose.lane, assumption = P.ROLE.assumption.lane, claimLane = P.ROLE.claim.lane, just = P.ROLE.justification.lane;
    const rows = [
      row("hf-row", bracket("", ""), blocks(bar(0, prose, 0, 0, n(2))), "",
        hf('<span class="io">4/1</span>', 6), hf("20", 7),
        "A weighted mean of the trials is unbiased once its weights sum to one \\citep{x}."),
      row("", bracket("", ""), blocks(bar(0, prose)), "", "", "21", "Put as a recursion over the trials, the update reads"),
      row("hf-row", bracket("", ""), blocks(bar(0, assumption, 0, 45, n(3)) + bar(1, claimLane, 40, 0)),
        "",
        '<span class="io">7/1</span>', "24",
        "Let every weight $w_k$ be positive. Then the limit $\\mu_\\infty$ exists."),
      row("hf-row", bracket("gs", n(1)), blocks(bar(0, just)),
        hf(bang("fail"), 5),
        '<span class="io">0/1</span>',
        "61", "Bounding the error is delicate when the steps come near the boundary."),
      row("hf-row", bracket("g", n(4)), blocks(bar(0, just)), hf(bang("none"), 8, true), "", "62", "Run on such inputs, the scheme may stall before it settles."),
      row("", bracket("g ge", ""), blocks(bar(0, just)), "", "", "63", "Inputs of this kind arise mostly for a small $m$."),
    ];
    const notes = [
      "Group bracket: one column per nesting depth, coloured by kind (amber: a proof). It spans the group's blocks, here lines 61 to 63.",
      "Block bar: one per block, as tall as the block's text exactly, row by row as the text wraps; coloured by its role, else its form (grey: prose with no single role). A thin ring marks the selected block's.",
      "Two blocks on one line (line 24: an assumption, then a claim): their bars stand side by side, each from its own first row to its last; selecting one colours its words alone, so where it gives way to the next shows.",
      "Hover a group's bracket: its card, with its kind, its lines, the block it justifies (by handle) and the group's edges in/out. Click it to select the group.",
      "Open items on this row (red: one of them fails a gate): here the block makes an unsupported claim, its words boxed in red, and an update marked one of its lines stale. Hover: each item, worst first; click: the block selected, its card.",
      "The block's edges, in/out: how many point at it, then how many are its own. Where two blocks start on a row (line 24), their edges summed; hover: each block's.",
      "The source file's line number.",
      "An open item waiting for a run (grey): here, no review has judged the block's claim yet. Nothing there says the paper is wrong.",
    ];
    const reading = "Line 20 reads: a prose block starts here (grey bar); 4 edges point at it, 1 is its own. " +
      "Line 24 holds two blocks: an assumption (violet bar, top) gives way mid-line to a claim (blue bar, beside it); their edges summed. " +
      "Line 61 reads: a proof group starts here (amber bracket; hovered, it justifies thm:1); its first block, a justification (amber bar), makes an unsupported claim and was marked stale by an update (one red mark, its card naming both); the block on line 62 makes a claim no review has judged yet (a grey mark).";
    return '<div class="h-fig">' + rows.join("") + '</div><ol class="h-notes">' + notes.map((t) => "<li>" + t + "</li>").join("") + "</ol>" +
      '<p class="h-intro">' + reading + "</p>";
  }

  // The page's controls, by their element ids (`index.html`, and those
  // `app.js` and `open.js` write).
  const CONTROLS = {
    state: "The kept state shown: Latest, or Earlier (in amber) for an older one, with the command that made it and when. Click: its id, version, nodes and the state it was made from; every state the paper's store keeps, newest first; and the comparison. The selection stays on its node where the other state has it. An earlier state stays shown as new ones are kept; the latest is followed.",
    search: "A node id, a handle or a block id selects it; a finding's or a judgment's id (or its first four characters or more) selects the block it is on, the card open on it; any other words select the next block holding them.",
    "help-toggle": "This help.",
    lens: "In the ⋯ menu beside ↳ (lines), with every edge, roles and reach; the ⋯ turns amber with a dot while one of them is not its default. Which edges the paper connections draw: all; reliance (uses, qualifies); support (justifies); description (about).",
    math: "¶ Rendered: on, the paper as it prints: each formula rendered with the paper's own macros, its markup (headings, a theorem's head, lists, citations, references, footnotes, emphasis) shown as it prints, and a line that prints nothing folded away; off, the source as written. It holds for the panels' excerpts too.",
    gutter: "# (gutter): on, the gutter left of the text (line numbers, group brackets, block bars, open-item marks and edge counts); off, the text alone, unnumbered, and wider (the row at the top stays put).",
    lines: "↳ (lines): on, the selected passage's connection lines along the paper's right edge; off, none, and the text takes their lane's width (the row at the top stays put). The blocks at the lines' other ends lose their colour with them; the claims' lines and the findings' boxes stay either way.",
    roles: "Tint each block's words by its role segments: a block of two roles shows where one gives way to the other.",
    faint: "Every edge in the lens as a faint arc: which regions of the paper link, not a reading.",
    "tab-card": "The selection's card.",
    "side-toggle": "The sidebar icon at the right end of the toolbar row, over the inspector's tabs: hide the inspector, for reading, and the paper takes its width (the row at the top stays put); again, show it on the tab, selection and place in the list it had. A selection leaves it hidden; a tab chosen (the verdict, n or p) shows it.",
    "tab-open": "What is open at this state, with its count.",
    verdict: "Whether the paper passes `axiomation check` at this state, under the paper's [gates]: ✓ passes, or ✗ fails on how many gates; how many warn after it. Hover: each failing and warning gate with its count. Click: the check tab.",
    "tab-check": "The check tab: whether the paper passes, and what stops it: each gate that fails or warns, linked to its items in the open tab; its count, how many items stand in the gates that fail.",
    "tab-inbox": "Your decisions at this state, with their count: what only a person decides, a section per kind, each item with its next step. What waits on a review is not counted.",
    compare: "In the state control. Change mode: compare the state shown with an earlier one on its chain. Each changed block is badged in the gutter with how it changed (hover: its old words beside its new); the changes tab lists the update's tiers. A step of a block's history, “compare in paper”, does the same for that step.",
    "what-changed": "In the state control, on a state made from another: compare it with the one it was made from, and open the changes tab.",
    "compare-off": "While comparing, beside the state control: the two states compared; × ends the comparison.",
    "tab-changes": "In change mode: what changed, in four tiers: changed (the text, and the Δ lines its writer gave), reached (each block the update asked again, and why), handled (what the graph began and ended, with its authors), still open (stale marks, pending edits).",
    "tab-edit": "While the paper's files are not the latest state's version: the edit no update has carried yet, as `axiomation changes` reads it: each block changed, old beside new, and what the update would ask again.",
    back: "The previous selection, by its name ([).",
    forward: "The next selection, by its name (]).",
    reach: "Tint what an edit to the selection reaches: blocks whose meaning changes, and blocks only asked again.",
  };

  // What each kind, provenance, standing, role, open kind and lens means.
  // Keyed exactly as the lists the page draws from (`help.test.js` checks
  // both ways), so a new one without words fails the test.
  const KIND_TEXT = {
    uses: "the source relies on the target's content: an edit to a definition or assumption it uses changes the source's meaning",
    about: "the source says something about the target (names it, describes it, sums it up); an edit asks the source whether it still holds",
    justifies: "a proof or argument (a group, or a block) supports the target's claim",
    qualifies: "the source, an assumption, constrains what the target means: an edit to the assumption changes the target's meaning",
  };
  const BY_TEXT = {
    person: "a person wrote it, in an edit of the sheet",
    agent: "a model wrote it (the edges stage, an update's session, an agent's edit)",
  };
  const STATUS_TEXT = {
    justified: "a justifies edge comes in",
    self: "the claim carries its own reason or citation (a self mark)",
    restated: "it restates what one of its about edges points at, and takes its support from there",
    conjectured: "offered without support, and saying so",
    unsupported: "asserted and supported by nothing in the paper",
  };
  // A claim's support (`rulings.Support`), where its tone is not its
  // standing's.
  const SUPPORT_TEXT = {
    "no-argument-owed": "a claim standing unsupported that a ruling says the paper owes no argument (known, immediate from a result of its own, an interpretation nothing rests on, or positioning, which the contribution session checks against the literature): no problem, and drawn neutral, never red; it still asks its true to be established, or, for positioning, a contribution check of its words",
    "left-unargued": "a claim a referee would want argued that the author leaves unargued, by a person's ruling and its reason, where nothing rests on it: the author's call, drawn neutral like a claim no argument is owed for; it still asks its true to be established",
    unsupported: "a claim the paper owes an argument and does not give one: standing unsupported with no ruling that clears it, or standing justified or restated with its own reason a gap (no justifies edge, nothing restated, a conjecture restated, a circle)",
  };
  // A claim's status (`claim_status.ClaimStatus`), the first that holds,
  // in this order; and the gate that lists it.
  const CLAIM_STATUS_TEXT = {
    defect: "a negative reading on its route (its true, or the valid of a justification onto it), live or lapsed and unresolved, or a severity-3 finding on it: check's defects lists it",
    disputed: "readers differ on a subject of its route: a person's judgment decides; unreviewed lists it",
    conjectured: "the paper conjectures it and does not assert it: no gate asks it",
    unsupported: "the paper owes it an argument and gives none (unruled, ruled owed, a ruling guarded, a circle, a conjecture restated): unsupported-claims lists it",
    lapsed: "a reading on its route lapsed with an edit: review asks it again; unreviewed lists it",
    stale: "an update marked a line on its route stale: a reread or a reading closes it; unreviewed lists it",
    unjudged: "a subject on its route has no reading yet: unreviewed lists it",
    inconclusive: "the reading that holds a subject of its route could not conclude: unreviewed lists it, a warning",
    "rests-on": "its own route holds, but a claim it rests on is not established: that claim is the one listed, and the link goes to it",
    "finding-open": "established but for an open finding of severity 1 or 2 on it: no gate lists it, but it is not shown established until the finding is answered",
    "left-unargued": "owed an argument the author leaves unargued, by a person's ruling and its reason: out of the gate's count",
    established: "its route holds and so does all it rests on: argued, or a ruling says the paper owes it no argument",
  };
  const ROLE_TEXT = {
    claim: "asserts something that can be true or false",
    definition: "fixes what a name or symbol means",
    assumption: "a premise the paper takes as given",
    justification: "argues for a claim: a proof, a derivation, a reason",
    motivation: "why the paper asks what it asks; it argues for nothing",
  };
  const OPEN_TEXT = {
    unsupported: "a claim whose status is unsupported, as the unsupported-claims gate lists it: standing unsupported with no ruling that clears it, or a justified or restated one whose own reason is a gap (a circle is its own item). Its line ends with its true's reading beside its rulings. Cleared by a justifies edge onto it, a self mark, or a ruling that the paper owes it no argument; a claim a ruling cleared is check's to list, not open's, and one read negative or disputed is listed as such",
    circle: "claims each resting on the others, listed once: each line on the circle as the sheet holds it, with where its ends may name less than the text relies on (a whole block holding a definition, no claim of several named), and the claims resting on it. Cleared by each line's ends named as far as the text relies on, or an argument for one of its claims that does not rest on the others",
    "uses-unsupported": "a block that uses an unsupported claim directly: what rests on the gap. A claim no argument is owed for is no gap",
    partial: "a justified or self claim the paper argues only as a sketch or by analogy to another argument. Cleared by a held verified valid of its justification on it (a true, for a self claim), or a fuller justification",
    flag: "a position a stage or a rule raised on the work (a doubt about a role, a group of two blocks) that no correction decided",
    stale: "an update marked a line of this block whose far end changed, and no answer has read it since; its cause names the line and the change that reached it",
    "carried-unread": "an update moved these blocks' meaning by the rule and read none of them: past the profile's moved_delta_lines_max, no Δ line of their own was asked. Nothing is blocked by it; it stands until a later update gives them their Δ lines or a review's verified reading reads them",
    pending: "an edit no state applied yet (what it reopens is unanswered), or a question the run that made this state left unanswered",
    lapsed: "a judgment whose basis changed since it was made: re-ask it, or reaffirm it; or an open finding whose anchors changed: answer it, or file the problem again, since a finding is never re-asked; or an unanswered suggestion whose place's words changed: still the author's to take or decline",
    disputed: "readings that differ: persons who differ, or an open severity-3 finding against a held judgment",
    negative: "a live reading that found its subject not shown or contradicted: a defect. Cleared by the text corrected and read again, or your judgment over it",
    finding: "an open finding that has not lapsed, its dot in its severity's tone",
    suggestion: "a work a review or a person offers the author to cite here, not yet taken or declined: a pointer, never a problem. Answered by `axiomation answer-suggestion`",
    retracted: "a work the paper cites is retracted, listed with every block citing it: a defect. Cleared by each citation of it replaced or removed",
    publication: "a cited work published as a preprint, a thesis or a report, or with an erratum: argue what the paper takes from it, cite refereed work, or accept resting on it (`axiomation sources accept`)",
    unplaced: "a citation or quotation whose words stand in no one block, so no reading can be placed on it. Cleared by its words standing once in one block",
    unjudged: "a subject a review session judges that no reading has judged yet; what clears it names the session. An unsupported claim's true is not listed here: its own item names its reading",
    inconclusive: "the reading that holds it could not tell; the works it could not read are named. Cleared by a reading that concludes, or your judgment",
    provisional: "verified, but read off a copy that is not the version of record. Cleared by a reading on the version of record's pages",
    unchecked: "a claim ruled positioning, the contribution session's to check against the literature, that no check of it has read yet",
    unread: "the state's kept files could not be read by today's reader, so its labels or quotations were not checked. Cleared by the paper built or updated again",
    "dangling-reference": "a reference to a label no block holds (defined nowhere, or outside every block): the cut lost the edge it would have made. Cleared by the label on the block meant, or the reference corrected",
    "duplicate-label": "a label two or more blocks hold, listed on each holder: every reference to it reads the first, so a reference meant for another is wrong. Cleared by the label renamed on all but one",
    "unused-premise": "an assumption no uses edge reaches and that qualifies nothing: dead weight, or an edge the writer missed. Cleared by the edge where a block relies on it, or the assumption removed",
    "undeclared-assumption": "a premise a justified claim's justification rests on that the claim's own statement does not name, marked on the claim alone, where it is fixed (its uses edges, or its justification); picked, its premises are tinted. The item says why (a statement with no uses edge, a line naming no claim, a discharge from outside the justification, a narrower copy of a premise the statement names, or a premise it leaves out) and prints the path to it",
    "later-rule": "a line written before a check was added, which that check now refuses: marked on the block the line belongs to, its cause the check's own words. Nothing is blocked by it, and no edit is refused for it; it stands until an edit rewrites the line",
  };
  // What the kept graph got wrong or cannot settle (`axiomation open`'s
  // four soundness kinds), each a gutter mark.
  // What each gate of `axiomation check` asks (`check.Gate`).
  const GATE_TEXT = {
    "unsupported-claims": "does the paper argue all it asserts: each unsupported claim, standing unsupported with no ruling that clears it whatever its true, each justified claim no justifies edge reaches, each restated one that restates nothing or a conjecture, each circle; the claims rulings cleared and those the author leaves unargued counted apart, in the audit",
    defects: "has anything been found wrong: each negative reading (not shown, contradicted), each open severity-3 finding but an outside reviewer's, each retracted work the paper cites",
    reviews: "is every outside reviewer answered: each finding a reviewer filed through a journal, any severity, until it is fixed, a duplicate, or dismissed with the author's argument",
    unreviewed: "has everything been read: each subject a review session judges with no verified reading of the version of record (unjudged, lapsed, disputed, inconclusive, provisional), each claim ruled positioning no contribution check reads, each site no block can hold, each lapsed severity-3 finding, each open stale mark no reading carries, and each undecided flag",
    "cited-publications": "what does the paper rest on outside itself: each cited work a referee would question as ground (a preprint no version of record is known for, a thesis or a report, a work with an erratum), with the blocks citing it and how many established claims rest on it; a warning, never a verdict moved. The works the author accepts resting on listed apart",
    "dangling-references": "is the graph sound: each reference to a label no block holds",
    "duplicate-labels": "is the graph sound: each label two or more blocks hold",
    "unused-premises": "is the graph sound: each assumption nothing uses or qualifies",
    "undeclared-assumptions": "is the graph sound: each premise a justified claim's justification rests on that its statement does not name",
  };
  // Why an item stands in a gate, where the cause is not an open kind
  // (those read as the open view's, `OPEN_TEXT`), as the card words a
  // claim's reasons.
  const CAUSE_TEXT = {
    circular: "its support runs in a circle back to it",
    "not-shown": "a reading found the paper does not show it",
    contradicted: "a reading found it false",
  };
  const SEVERITY_TEXT = {
    3: "critical: open, and on anything but how the paper reads or what it is worth, it disputes a verified judgment of its subject",
    2: "a problem to fix that does not break the argument",
    1: "minor",
  };
  const VERDICT_TEXT = {
    verified: "the reader found it holds",
    "not-shown": "the reader found the paper does not show it",
    contradicted: "the reader found it false",
    inconclusive: "the reader could not tell; it decides nothing",
  };
  const CHANGE_TEXT = {
    added: "a block the later state has and the earlier had not",
    deleted: "a block the earlier state had and the later has not; listed in the changes tab with its words",
    split: "a block cut into pieces; each piece names the others",
    merged: "blocks joined into one",
    changed: "its words changed",
    moved: "the same words, elsewhere in the paper",
    math: "only its mathematics changed",
    markup: "only its markup changed",
    macro: "a macro it uses changed",
  };
  const LENS_TEXT = {
    all: "every kind",
    reliance: "uses and qualifies: what the selection's meaning rests on, and what rests on it",
    support: "justifies: what argues for the selection's claims, or what it argues for",
    description: "about: what says something about the selection, or what it describes",
  };

  function sections(P, kinds) {
    const openKinds = kinds.open;
    const lenses = kinds.lenses;
    const s = [];

    s.push({
      id: "overview", title: "The page at a glance",
      intro: "The paper's source on the left, as one kept state holds it; the selection's edges as arcs along its right edge; on the right, a full-height inspector with the selection's card and what is open. " +
        "Click “label the page” to number every region on the live page.",
      items: [
        { term: "Header", text: "The paper's title over its root filename (the filename alone when no title is available); the state control (the kept state shown, its details, the others and the comparison); while comparing, the two states; a red line while the paper's files are ahead of the latest state, and a red count of rendered characters in no block; the search box; the verdict; this help.", show: "#bar" },
        { term: "The verdict", sample: '<span class="h-verdict v-fail">✗ fails on 2 gates · 1 warn</span>', text: "Whether the paper passes `axiomation check` at this state, the gates' modes from the paper's [gates]: ✓ passes, or ✗ fails on how many gates that block; how many only warn after it. Hover: each such gate and how many items stand in it. Its tone: green passes, yellow passes with a warning, red fails, grey not read (the hover says why). Click it: the check tab.", show: "#verdict" },
        { term: "Established, provisional", sample: "○ 40 open · " + P.ESTABLISHED_GLYPH + " 12 established · 3 provisional", text: "At the top of the open view, on one line: how many items are open; how many blocks have a claim established (any one of their claims), and how many rest on a live reading read off a copy that is not the version of record.", show: "#open .e-counts" },
        { term: "Text pane", text: "The paper as the state kept it, rendered (¶ on) or as its source (¶ off), file by file in reading order; in a paper of several files, a file head where each starts and where an including file resumes. The page is light or dark as the browser is; ?theme=light or ?theme=dark in its address sets one.", show: "#text" },
        { term: "Text tools", text: "Above the text: ¶ (the paper rendered or as its source), ↳ (the selection's connection lines) and # (the gutter: line numbers, brackets, bars, marks and counts), each lit while on, its name on hover; ⋯ holds the lens, every edge faint, the roles tint and reach.", show: "#text-tools" },
        { term: "Tabs", text: "The selection's card, the open view, the check tab and the inbox; the sidebar icon at their right end hides and shows the inspector.", show: "#tabs" },
        { term: "Splitter", sample: '<span class="h-split"></span>', text: "Drag the bar between the panes to resize them; the size goes into the link.", show: "#split-v" },
      ],
    });

    s.push({
      id: "text", title: "The text pane",
      items: [
        { term: "Hidden source", sample: text("color:var(--dim)", "\\label{…}"), text: "With ¶ off, text that prints nothing, drawn dim: preamble, comments, labels. With ¶ on it is left out, and a line of nothing else folds away." },
        { term: "In no block", sample: text("background:var(--uncovered);color:var(--on-uncovered)", "words"), text: "Rendered text no block owns, filled yellow, its words dark: the one mark of its kind. A cut covers every character; any here is a cutter defect." },
        ...Object.entries(P.SEVERITY).map(([n, sv]) => ({
          term: "Open finding: severity " + n,
          sample: text("box-shadow:0 0 0 " + (0.5 + sv.strength).toFixed(2) + "px " + sv.colour + ";border-radius:1px", "the words"),
          text: "The words an open finding anchors, boxed as they wrap in its severity's tone, fainter and thinner as it is less severe (" + SEVERITY_TEXT[n] + "); a block or group it anchors whole boxed the same. Hover: the finding's card. A finding anchored at several places is boxed at each.",
        })),
        {
          term: "An open item's words",
          sample: text("box-shadow:0 0 0 1.5px " + P.open("unsupported").colour + ";border-radius:1px", "the claim's words"),
          text: "What another open item is about, the same box in its kind's colour, as strong as a severity-3 finding: an unsupported claim's words, a circle's claims, a cited work's citations. An item only to read again or not yet reviewed is boxed only when picked; it has its grey `!`. Two items on the same words are both boxed, the second round the first; a third or more share that outer box, in the strongest's style. Hover: every item there.",
        },
        {
          term: "Waiting on a review",
          sample: text("outline:" + (0.5 + P.SEVERITY[2].strength).toFixed(2) + "px dashed " + P.SEVERITY[2].colour + ";outline-offset:1px;border-radius:1px", "the words"),
          text: "A finding answered with a proposal, or an unsupported claim with a proposed ruling: the same box, dashed, in the same tone and width. The next review of its kind settles it; nothing is yours to do there now. Hover: what was proposed.",
        },
        { term: "The open item picked", sample: text("box-shadow:0 0 0 2.5px " + P.open("unsupported").colour + ";border-radius:1px", "the claim's words"), text: "The item picked in Open, Check or Inbox: its box at each of its places, a pixel thicker; an undeclared assumption's premises tinted in its kind's colour." },
        { term: "The selection", sample: text("color:var(--accent)", "selected block"), text: "The selected block's text, formulas included, in the accent colour, on no ground of its own (a group: all its blocks')." },
        { term: "A claim", sample: text("color:var(--accent);text-decoration:underline solid var(--claim-line);text-underline-offset:4px", "the claim's words"), text: "In the selected block, a claim's words under one quiet line whatever it stands; never at rest. Its standing is on the Block tab's card. The standings: " + Object.keys(P.STANDING).map((s) => s + ", " + STATUS_TEXT[s]).join("; ") + "." },
        { term: "A self mark", sample: text("color:var(--accent);text-decoration:underline dashed var(--self);text-underline-offset:4px", "\\citep{x}"), text: "In the selected block, the reason or citation a claim carries itself; never at rest." },
        { term: "At a line's other end", sample: text("color:var(--linked)", "a block"), text: "A block the selection's edges reach or come from, its words in a muted amber while ↳ (lines) is on, against the selection's blue. The words each edge stands on, its evidence and the part it names, are underlined at both ends, over a claim's line where they meet." },
        ...Object.entries(P.ROLE).map(([role, r]) => ({
          term: "Role tint: " + role,
          sample: text("background:" + r.tint, r.label),
          text: "With “roles” on, a role segment's words: " + ROLE_TEXT[role] + ".",
        })),
        { term: "A formula", text: "Rendered in place with the paper's own macros; a display over several source lines is one row, numbered by its first line (hover the number for its range). A mark (a claim's line, an edge's evidence, an open item's box) marks the part of the formula its words cover, drawn as a part of its own; where that part cannot be drawn alone (a cut inside a sum's limits, a fraction, an `align`), it marks the part around it, the whole formula at most. A formula the renderer cannot take stays its source, dashed. “¶” off, above the text, shows every formula as its source, here and in the panel." },
        { term: "The paper rendered", text: "Its markup as it prints, each source line still its own row: a heading set as one, with its number; a theorem's head (Theorem (title).), a proof's (Proof. … ∎); a list's items marked; a citation [as here, its keys]; a reference by its section's number, else its label; a footnote's number with its words beside it, smaller and tinted. A line that prints nothing (a comment, a label, a spacing command, the preamble) folds away, and of a run of blank lines one shows. A table and the paper's own text macros stay as written. “¶” off shows the source." },
        { term: "Hovering an arc", text: "A card, as the Block tab draws a finding: the verb, upstream or downstream, and “by a person” where a person wrote it, in small capitals; the other block, its role and line; what it says; the part the edge names and the evidence. The evidence is lit in the text, what the edge points at boxed (the part it names, else the whole block or group); a source with no evidence is tinted whole. A gutter mark, a claim's dot or bar and a block's bar name themselves the same way on hover." },
      ],
    });

    s.push({
      id: "gutter", title: "The gutter, left of each line",
      intro: "Columns, left to right: groups, blocks, marks, counts, the line number. A mark and a count stand at the screen row their block or anchor starts on, so a long line that holds several blocks shows each at its own row. Hover any mark on the page to have it named.",
      figure: gutterFigure(P),
      items: [
        { term: "Group lane", sample: Object.values(P.GROUP).map((g) => outline(g.stroke)).join(""), text: "A bracket per group (proof, proof section, derivation, step, step run), one column per nesting depth, coloured by kind. Hover it: the group's kind, its lines, the block it justifies and its edges in/out; click it: select the group.", show: ".ln .gl" },
        { term: "Block lane", sample: Object.values(P.ROLE).map((r) => swatch(r.lane)).join(""), text: "A bar per block, coloured by its role, else by its form; side by side where blocks share rows; the selected block's ringed.", show: ".lane-bar" },
        { term: "Open-item mark", sample: [P.CHECK.fail.colour, P.CHECK.warn.colour, P.severity(2).colour, null].map((c) => '<span class="h-bang"' + (c ? ' style="color:' + c + '"' : "") + ">!</span>").join(" "), text: "One at each screen row where open items stand: at the row each one's words start on (a finding's anchors, each), else its block's first row; an undeclared assumption on its claim alone, not on the premises its justification reaches; a block using an unsupported claim on none: its `!` is that claim's, and its card names it. Its colour is the worst of them, by what the check's gates do with them, as the open view counts them: red where one fails a gate, amber where one warns, then a finding no gate fails or warns on, in its severity's tone, grey where no gate lists them or where each is only to read again or not yet reviewed (the open view's two families that say nothing there is wrong), whatever their gate does. A finding is judged by its own gate, not its kind's (a severity-3 finding fails `defects`, a severity-2 one does not), and where a finding is the worst there, the `!` takes its severity's tone, as its box does. Hover it: every item there, worst first, each kind with how many, its gate's word and why the first stands; click it: the block it stands on selected, its card (a mark on no block, the paper's or a work's: the worst in the open view). One mark says nothing of how many: the card does.", show: ".tk.mk" },
        { term: "Counts", sample: " 3/5 ", text: "At a block's first row, its edges in/out: how many point at it, then how many are its own; where several blocks start on one row, their edges summed, each block's on hover. In change mode: how it changed.", show: ".ct.mk" },
        { term: "Line number", text: "The source file's line.", show: ".ln .num" },
      ],
    });

    s.push({
      id: "arcs", title: "The arcs",
      intro: "The selection's edges, drawn along the right edge of the text pane: each a bracket from the screen line its evidence stands on to the screen line of what it points at (the part it names, else the block's first words). The nearest runs innermost; two whose spans overlap never share a lane. Colour is the kind; the lens chooses the kinds.",
      items: [
        ...Object.keys(KIND_TEXT).map((k) => ({ term: "Kind: " + P.edge(k).label, sample: line(P.edge(k).stroke), text: KIND_TEXT[k] + "." })),
        { term: "Upstream", sample: arc(P.edge("uses").stroke, P.STREAM.up), text: "Solid: something the selection rests on (it uses it, is about it, is justified or qualified by it). Line style always says which way reliance runs; colour says the kind." },
        { term: "Downstream", sample: arc(P.edge("uses").stroke, P.STREAM.down), text: "Dotted: something that rests on the selection (it is used by it, described by it, it justifies or qualifies it)." },
        { term: "Arrowhead", sample: '<svg class="h-ln" width="10" height="10"><path d="M1 5 L9 1 L9 9 Z" fill="' + P.edge("about").stroke + '"/></svg>', text: "Which way the edge runs: the arrowhead is at its target, pointing into the words it names; grey where edges of two kinds arrive at one point. Hover a line: it and its arrowhead show at full strength, a little thicker, over the others. Click the line, or its arrowhead at the other end, to select that block.", show: "#arcs .head" },
        { term: "Hovering an arc", text: "A card, as the Block tab draws a finding: the verb, upstream or downstream, and “by a person” where a person wrote it, in small capitals; the other block, its role and line; what it says; the part the edge names and the evidence. The evidence is lit in the text. A gutter mark, a claim's dot or bar and a block's bar name themselves the same way on hover." },
        { term: "Every edge, faint", text: "With “every edge” on, all edges in the lens as a faint texture under the selection's: which regions link." },
        ...Object.keys(LENS_TEXT).map((k) => ({ term: "Lens: " + k, text: LENS_TEXT[k] + "." })),
      ],
    });


    s.push({
      id: "changes", title: "Change mode",
      intro: "Pick an earlier state in the state control (“compare with”), or “compare in paper” on a step of a block's history: the state shown is compared with it, by `axiomation changes`.",
      items: [
        ...Object.entries(P.CHANGE).map(([cls, ch]) => ({ term: "Changed: " + cls, sample: '<span class="cx-badge" style="color:' + ch.colour + '">' + cls + "</span>", text: CHANGE_TEXT[cls] + "." })),
        { term: "The badge", text: "In the gutter, where a changed block starts: how it changed. Hover it: its old words struck, its new marked. Click it: the block, and the changes tab." },
        { term: "Words added", sample: '<span style="background:' + P.change("added").colour + '40">new words</span>', text: "In the text, the words the state shown has and the earlier lacked, on a ground of the added colour; a block added whole, all its words." },
        { term: "Words deleted", sample: '<span style="color:' + P.change("deleted").colour + ";background:" + P.change("deleted").colour + '1f;text-decoration:line-through">old words</span>', text: "Struck, where they stood: the earlier state's words the state shown dropped; a block deleted whole, its words before the first block after it that the state shown keeps. Not part of the source: no line number, no block, not selectable. A changed block with no words marked (moved, or a change of maths, markup or a macro) has its badge in the gutter alone." },
        { term: "Words carried", sample: '<span style="background:' + P.change("split").colour + '40">carried words</span>', text: "Across a split or a merge, the words that only changed block, on a ground of the split and merged badges' colour: a split's piece, the words it took from the block it was cut from; a merged block, the words it took in from the one that ended. They are neither struck nor marked added; where a split's words went on into its other piece, “→ name” stands at the cut." },
        { term: "The changes tab", text: "The update's tiers: changed (each block's words, old beside new, and each Δ line its writer gave), reached (each block it asked again, and why), handled (what the graph began and ended, with who wrote it), still open (stale marks and pending edits), lapsed (below)." },
        { term: "The lapsed tier", sample: '<span class="cx-badge" style="color:' + P.open("lapsed").colour + '">judgment</span>', text: "Each judgment and open finding lapsed at the later state that was not lapsed at the earlier, as `axiomation changes` lists it: its line, its cause and a link to the block that changed, and what clears it. A lapse is not a stale mark: a stale mark asks a graph line again, a lapse a reading." },
        { term: "A split", text: "A block split off another names it in its history (“split off n36”): click it to see the block it came from. Its words that came from there are carried, not added." },
      ],
    });

    s.push({
      id: "current", title: "Keeping up",
      intro: "The page asks every few seconds, while it is visible, whether the store or the paper's files moved since it read them.",
      items: [
        { term: "Read again", text: "A judgment, a finding, a mark or a state filed while the page is open: once the store has held still for a few seconds, the page reads itself again in place, keeping the block, the tab, the comparison and the text's scroll; on the latest state it follows a newer one. The note above the text says when." },
        { term: "The paper changed", text: "The header's red line: the paper's files are not the latest state's version, an edit no `axiomation update` has carried yet. “See the edit” opens the edit tab." },
        { term: "The edit tab", text: "The paper's files against the latest state, as `axiomation changes` reads them: each block changed by its class, old words beside new, then what the update would ask again, outright and on condition. Read on demand, as it runs identity on the files.", show: "#tab-edit" },
      ],
    });

    s.push({
      id: "overlays", title: "Overlays",
      intro: "Marks from outside the product (`axiomation view --overlay FILE`): each item placed at its anchor, a block, a group, a claim part, a span of words, the paper or a cited work's citations, or on an edge's evidence.",
      items: [
        ...Object.entries(P.OVERLAY).map(([channel, ov]) => ({
          term: "Channel: " + channel,
          sample: text(P.wordsStyle(ov.line, ov.colour, ov.line === "box" ? 1.5 : 2), "the words"),
          text: (ov.line === "box" ? "Its words boxed" : "Its words underlined " + ov.line) + "; a whole block or group tinted. Hover: the item's label, value and note.",
        })),
        { term: "Open items", text: "An item marked open is listed in the open view under its overlay's name." },
        { term: "Unanchored", text: "An item whose anchor no longer places (its words not found once, its block gone) is kept: listed at its block or the paper, its line struck through, and why." },
      ],
    });

    s.push({
      id: "card", title: "The card",
      intro: "The selection as `axiomation show --history` gives it, every handle a link, each section folding under its header (hover a header: what it holds): what a review reads first (its claims, findings and judgments), then its edges and history, and its record last. The paper holds the block's words and marks the selection; the card does not repeat them.",
      items: [
        { term: "The status", text: "Under the header, a sign for the block as a whole: ✓ where everything holds and is current, ○ where something is open or not yet read, ✕ where a reading or a finding is against it. Beside it, a hint where something is open: how many of its claims are established, an unsupported claim it relies on (whose own card says why), and how many problems the card shows (click: the first of them). What holds is said in quiet lines, each a question and its answer, with who answered as an icon (an agent, a person of the paper, an outside reviewer; hover: the name). Each open problem is a card, as a finding is: its name, whose move it is (here, elsewhere, needs a reading), one sentence, and its ways to resolve.", show: "#card .status-line" },
        { term: "Ways to resolve", text: "Under a problem, one line for each way that applies to it, under the case it fits where they split by what is wrong (If the graph is wrong, If the text is wrong): what to do and when, what it takes (a text edit, a graph edit, a ruling, a review, a judgment, a reread, or settling another claim first), and a copy of its command (hover: what to fill in, and whether it is paid). A way addresses the problem; it never promises the claim established: a review may find against it, and another problem may follow." },
        { term: "Sections by role", text: "Beside its claims, a block shows a section for each thing it does, named as its header names its roles: Justification, its own words where they argue a claim of the block (its truth reading checks them), and whether it proves each claim elsewhere it argues; Definition or Assumption, whether anything uses it and whether a review read it; Cites, each citation and its reading, or that none has read it yet. Other readings are under Judgments." },
        { term: "The trail", text: "Above the header, the selection before this one by its name (← name) and the one after (name →): a click, or [ and ], walks back and forward through what you selected. It stays at the top while the card scrolls. Gone while you have selected only one; with the selection cleared, ← returns to it.", show: "#card .card-nav" },
        { term: "Header", text: "Its handle; under it, its roles, each in its colour (its form where it has none); its section and groups; its first line, a link that scrolls the paper back to it. A claim's words and a quote render as the paper does, as “¶” sets.", show: "#card .card-head" },
        { term: "No claim to judge", text: "Under the header of a block a review judges nothing on (a definition, an assumption, a motivation): that a review files a finding on it only when it finds a problem, how many it filed, and the latest review that read it (one whose readings rest on it, or a findings session that read the paper whole), with its state and whether the text changed since. A block in a proof group a review judges as one says so instead: the group's card holds its readings.", show: "#card .no-claim" },
        { term: "A claim", text: "Its words, then a line for each question its route asks, with its answer and who answered: True (whether a reading finds it true), Proved (for a claim the graph gives an argument: whether a reading finds that argument proves it, the argument named under it), Argued (where it has no argument, or restates another claim), Relies on (what it waits on), Assumes, Under. A claim the paper argues is established by its argument read as proving it, not by a reading that it is true. Where it is not established, its card says what is open and the ways to resolve it." },
        ...Object.entries(P.CLAIM_STATUS).map(([name, cs]) => ({ term: "Status: " + name, sample: '<b style="color:' + cs.colour + '">' + name + "</b>", text: CLAIM_STATUS_TEXT[name] + "." })),
        { term: "sketch, analogy", text: "On a justifies edge's or a self mark's row, what the justification's own words say of it: sketch, its argument as written stops short (an outline, a heuristic, a special case with the rest declared to follow); analogy, another argument carried over and none of it written, the one it names a uses edge, a citation or its group." },
        { term: "Folding a section", text: "A section starts open where it asks something of you now: the claims, what it rests on, and the findings, suggestions, stale marks and judgments while one is open. The rest start folded: what rests on it, its history, earlier rulings and the record. A folded section's header says what it holds (used by 6, 1 closed, its newest step). Click a header to fold or unfold it; your choice stays for the next selection, over the default, and the link keeps it." },
        { term: "Stale marks", text: "Each line an update asked again and no one has read since: the edge (from the block's side) or its own line, when it was marked, and why (its cause: the change and the edge it came through). A claim resting on it is stale until it is read: a reread, a verified reading, an edit or the next update. Shown only while one is open; one a reread or a reading closed is in the history, on the step that marked it, with what closed it." },
        { term: "Upstream, downstream", text: "Its edges grouped by which way reliance runs: upstream, what it rests on, its self marks among them; downstream, what rests on it. Within each, a group per verb read from the selected block's side (uses, about, justifies, qualifies where the edge is its own; used by, described by, justified by, qualified by where it points at it), each in the paper's order. A row per edge: the other block, what it is, the parts the edge names ([..] the other's, on [..] its own), how the text points, and its line (click: select it); below, the words that make the link. Hover a row: the edge named, what the other block says, its evidence lit in the text and what it points at boxed." },
        { term: "Argument owed", text: "On a claim the paper does not argue, whether it owes the argument: no, the ruling that cleared it (neutral), agents alone or a person; or yes, and why: unruled, ruled owed, a ruling refused (the paper promises the argument; agents alone on a disputed or unshown true; immediate from no result; an interpretation something relies on), its own gap, or left unargued by the author. Only the ruling that decides it is shown, the latest of its kind, with its reason; a ruling of another kind that also speaks is named in a note; the others are in the claim's history. " + SUPPORT_TEXT.unsupported + "." },
        { term: "Earlier rulings", text: "The rulings on words the block no longer holds, in the order filed: its kind, author, the state it was made on, its reason, and whether it speaks, was replaced, withdrawn or lapsed. Folded until opened." },
        { term: "A claim", text: "Its words, its status and next step, then its rows, each a short label. Argument: whether an argument is owed, where the paper does not argue it. Support: what the graph holds for it, its standing: none (with what the paper declares of the argument it leaves out: omitted, left out with none named to carry over, or promised, said to be given elsewhere and nowhere given), justified (what justifies it a link where the block holds one claim), self, restated or conjectured; “argued by” sketch or analogy where it is argued less than in full. Truth: its status as judged and the verdict of the reading that holds it, provisional where that reading is; click it to unfold the readings; where it lapsed, the cause and what clears it, as a command. How it is established, where that says more than the status: Via what, Assumes what (more than three folded under their count), Under which conjecture its statement takes as a hypothesis. Note: the standing's note. Who wrote a ruling or a reading is named short: a person by name, an agent by its model; hover it for the whole, its host and the host's version. Then the open findings on its words, and its history, folded: the rulings that do not decide, the findings answered or lapsed." },
        { term: "Aspects", text: "The question a reading or a finding answers, named as a noun so it never reads as the answer: validity (the record's `valid`), truth (`true`), accuracy (`accurate`), citation (`cited`), well-posedness (`well-posed`), computation (`computed`), presentation, significance, novelty, literature. Hover one for the term the commands take (`--aspect`)." },
        { term: "Judgments", text: "Each judgment of something other than its claims, a row as a claim's Truth is: the aspect, its status and holding verdict, what it is of (a valid one the justification → the claim, a cited one the work, a computed one the file), its readings folded under their count." },
        { term: "A reading", text: "Its verdict, depth and author, the state it was made on, lapsed where it is, what it reaffirms; the reader's evidence, quoted; the cited pages it read; for a computed one the run (its file, arguments and how it ended), each value matched (the text's ↔ the output's) and the code lines it read the values from." },
        ...Object.entries(P.VERDICT).map(([verdict, v]) => ({ term: "Verdict: " + verdict, sample: '<b style="color:' + v.colour + '">' + verdict + "</b>", text: VERDICT_TEXT[verdict] + "." })),
        { term: "A cited page", sample: '<span class="h-label">key:12 ok</span>', text: "A page a reading or a finding read: its work's key and page, and how it was read; a dashed one was read off a copy that is not the version of record, and the reading is marked provisional. The page's own words are not shown: `axiomation sources` reads them." },
        { term: "Findings", text: "Each finding a card, tinted in its severity's colour while it is yours to act on: a label line, its severity, aspect and state (open, lapsed, or its answer: fixed, dismissed, duplicate, postponed; refiled, the filing it became); the problem, a long one folded to its first lines (more: the whole); then its rows, each a short label, as a claim's are: the problem as first filed, the fix, every anchor (a link where it is a block), its pages, the answer's note, a proposal, the findings the open view counts the same, a lapse's cause, and what clears it; last, its author and the id it was first filed under. A finding on a claim's words shows under that claim; the Findings section holds those on the block, the open first and the rest folded under history. A finding anchored on several blocks shows on each one's card." },
        { term: "Suggestions", text: "Each work offered to cite at this block: its state (open, lapsed, or its answer: taken, declined), author, the works, why a reader of the field expects it here, the pages read, the answer's note, and the command that answers an open one." },
        ...Object.entries(P.SEVERITY).map(([n, sv]) => ({ term: "Severity " + n, sample: '<b style="color:' + sv.colour + '">severity ' + n + "</b>", text: "A finding's severity, the tone of its card's edge, its box in the text and its hover: " + SEVERITY_TEXT[n] + "." })),
        { term: "History", text: "The kept states where something happened to it, the newest first, each headed by what happened: how it came from the state before (first kept, its text changed, moved, split, merged, deleted, …), what the update did to it (meaning moved by the rule, marked stale, reopened, not asked, a Δ its writer gave), and whether its edges changed; with when and by which command. A split or a merge is named from its side: split (its words continue in another), split off (from another), merged (it took another in), merged into (its words went on into another). Under the heading, each cause's words; each edge the step began (+), ended (−), changed (~: the same ends and kind, other parts named) or carried across the split or merge (→ to, ← from the other piece: the same edge, neither ended nor begun), read from its side, a person's marked; and, where its text changed, the change itself: its old words struck, its new marked, the words a split or a merge only moved between blocks carried, in violet (click: the whole of it). “compare in paper” compares that state with the one before it. A mark since closed shows on the step that marked it, with the reread or reading that closed it. The updates that only marked it stale, its edges unchanged, fold under one line; the states where nothing happened to it are counted.", show: "#card .h-row" },
        { term: "Record and command", text: "Its node id (the same block across states), block id, form and environment; `axiomation show` and `axiomation history` for it, each copied with a click, run in the paper's directory. Folded until opened.", show: "#card .rec-row" },
      ],
    });

    s.push({
      id: "open", title: "The open view",
      intro: "`axiomation open`'s list: everything not yet settled at this state, the full ledger. Its head counts what is open beside the blocks with a claim established. Five folded families by where an item comes from (structure, readings, cited works, to read again, not yet reviewed), every item a gate lists among them, each with a sentence on what it holds and its count in the tone of the worst of what the gates do with its kinds: fails a gate, warns, or in no gate (the gates by name on hover). Inside a family, each kind folds, with its count and tone and a sentence on what it holds, open where it is its family's only one; what clears most of its items is said once under it, a row's own only where it differs. An item is a card, its border in the tone of what the gates do with its kind: its block and its section, the passage it is about (the words around a part in grey, its formulas and markup rendered), one line of why. A block with several unsupported claims or lapsed judgments is named once, its items folded under it; the readings are listed by block, each row with its kind, so a negative reading and the finding that says why sit together. A justification's validity names the claim it argues for (“argues for t: …”), whose words those are; its verdict is the reader's (“not-shown, read by …”). The blocks the updates carried unread are counted once, and each update's are folded under its row, each a link. Click an item, or walk them with j and k (or ↓ and ↑ while the open tab is shown): its family and kind open and its block is selected beside the paper; Block › opens its detail on the Block tab. Your folds stay as the list is drawn again. A gate's link in the check tab lists that gate's items alone here, each also in its kind in the whole list, by kind, each kind folded with its count and a sentence on what it holds; “show all open items” returns to the whole list.",
      items: [
        ...openKinds.map((k) => ({ term: "Open: " + k, sample: dot(P.open(k).colour), text: OPEN_TEXT[k] + "." })),
        ...(kinds.families || []).map((f) => ({ term: "Family: " + f.title, text: f.sentence + " Its kinds: " + f.kinds.join(", ") + "." })),
        { term: "A kind's tone", text: "Its count in the check tab's tones: fails a gate where a gate that fails lists an item of the kind, warns where one that warns does, in no gate where none lists one. The count's hover names those gates.", show: "#open .open-group" },
        { term: "A note", text: "Above the list, where the query could not read what a kind needs: a state whose kept files today's reader cannot read has no labels, so no dangling reference, duplicate label or unused premise is listed, and the note says so.", show: "#open .open-note" },
      ],
    });

    s.push({
      id: "inbox", title: "The inbox",
      intro: "`axiomation inbox`'s list. First, a section per kind of decision only a person makes, headed by the decision, with its count and a sentence. Each item is a card, drawn as the Block tab draws a finding: a label line (what it is, its aspect, its state), the claim's or finding's words, then rows: where it is, what the records say (an unsupported claim's rulings, each with its reason, and what relies on it; who read a reading) and its next step, the cheapest route the records allow. Then, folded, what waits on a review: your proposed rulings and readings, and findings carrying a proposal, each with what was proposed and why; the next review of their kind settles each. A claim is listed once: one you proposed a ruling on waits, and is not counted as a decision. Last, one line counts the lapsed judgments, the next review's, and opens them in Open. Click a card: its block is selected; Block › opens its detail. The page writes nothing: each step is a command.",
      items: Object.entries(kinds.inbox || {}).map(([, words]) => ({ term: "Inbox: " + words.title, text: words.asks })),
    });

    s.push({
      id: "check", title: "The check tab",
      intro: "`axiomation check --json` at this state: whether the paper passes, and what stops it. The head says the verdict in its tone and how many gates fail, warn and pass. The sections fold as the open tab's families: Fails and Warns open, Passes and the audit folded, each head with its count in its tone and a sentence under it. A gate is a row as a kind's head: its dot and count in its outcome's tone, a sentence on what it counts, its count by kind where its items are of more than one, and what a pass left unread, such as cited works never looked up. A block gate fails the check; a warn gate only warns (hover a row). Click a gate with items: its items in the open tab, alone, by kind, named at the top with a way back to all. The audit is what no gate counts because someone's word cleared it, each part folded: the claims rulings cleared (no argument owed), by ruling, each claim with who ruled and why; the claims the author leaves unargued; the cited works the author accepts; the claims established under a conjecture their statement assumes; and the stale marks rereads and readings cleared, as counts. Where an agent alone cleared it, the check trusts that agent. Click a claim in a list: its block is selected; Block › opens its detail.",
      items: [
        ...Object.entries(P.CHECK).map(([tone, c]) => ({ term: "Verdict: " + c.label, sample: '<span class="h-verdict v-' + tone + '">' + c.label + "</span>", text: "The chip and the check tab's head in this tone." })),
        ...Object.keys(GATE_TEXT).map((g) => ({ term: "Gate: " + g, text: GATE_TEXT[g] + "." })),
        ...Object.keys(CAUSE_TEXT).map((c) => ({ term: "Cause: " + c, sample: dot(P.cause(c).colour), text: CAUSE_TEXT[c] + "." })),
      ],
    });

    s.push({
      id: "controls", title: "The controls",
      items: Object.entries(CONTROLS).map(([id, words]) => ({ term: id.replace(/-/g, " "), text: words, show: "#" + id, control: id })),
    });

    s.push({
      id: "terms", title: "The terms",
      items: [
        { term: "Sheet line", sample: '<code>→ uses def:x [σ] · "evidence" — note</code>', text: "An edge as the edges sheet writes it: kind, target's handle, the parts relied on, the evidence, a note. ⊢ status · \"claim\" is a standing; → self · \"…\" a self mark." },
        { term: "Handle", text: "A block's name at a state: its label, else its first words. A group's: proof:<label> or group:<first block>." },
        { term: "Node id", text: "A block's id across the kept states: n12 names the same block before and after an edit that kept it." },
        { term: "Kept state", text: "Each whole build, update or edit, kept in the paper's store with the files it read: the page shows its text, not the paper's directory." },
        ...Object.keys(BY_TEXT).map((b) => ({ term: "By: " + b, sample: b === "person" ? '<span class="h-label">person</span>' : undefined, text: BY_TEXT[b] + (b === "person" ? "; its row on the card says so" : "; the default, unmarked") + "." })),
      ],
    });

    s.push({
      id: "doing", title: "What you can do",
      items: [
        { term: "Select", text: "Click the text, its gutter (a bar, a line number), an arc or a link. A click elsewhere in the paper (its margins, the lines' lane, a line no block holds) clears the selection." },
        { term: "Walk what is open", text: "j and k: the next and previous open item, its block selected; ↓ and ↑ too while the open tab is shown." },
        { term: "Go back", text: "[ and ]: the previous and next selection." },
        { term: "Another state", text: "Pick it in the header: the selection follows its node." },
        { term: "The link", text: "The URL keeps the state, the selection's node, the tab, the lens, the open view's grouping and the kinds opened, and the paper width: share it, or reload to keep them." },
        { term: "Keys", text: "? opens this help; Esc closes it." },
      ],
    });
    return s;
  }

  // --- the overlay ---

  // The regions "label the page" numbers, in reading order of the page.
  const REGIONS = [
    ["#bar", "Header: paper, state, warnings, search, verdict"],
    ["#state", "The state shown: click for its details and the others"],
    ["#text-tools", "Text tools: ¶ rendered, ↳ lines, # gutter, and ⋯ for the lens, every edge, roles, reach"],
    ["#text", "Text pane: the paper in reading order"],
    [".ln .gl", "Gutter: group brackets"],
    [".ln .bl", "Gutter: blocks, by role"],
    [".tk.mk", "Gutter: open marks"],
    [".ct.mk", "Gutter: edge counts"],
    [".ln .num", "Line numbers"],
    ["#arcs .far", "An arc's other end"],
    ["#split-v", "Splitter: drag to resize"],
    ["#verdict", "The verdict: whether the paper passes check"],
    ["#tabs", "Tabs: the card, the open view, check"],
    ["#card", "The selection's card"],
  ];

  function inside(r, box) {
    return r.width > 0 && r.height > 0 && r.left >= box.left - 1 && r.top >= box.top - 1 && r.right <= box.right + 1 && r.bottom <= box.bottom + 1;
  }
  function paneOf(doc, el) {
    const pane = el.closest("#text, #side");
    if (!pane) return { left: 0, top: 0, right: doc.defaultView.innerWidth, bottom: doc.defaultView.innerHeight };
    return pane.getBoundingClientRect();
  }
  // An element's box as it shows: an arc's mark wholly inside its pane,
  // anything larger clipped to it; null when not shown.
  function shownBox(doc, el) {
    if (el.closest("[hidden]")) return null;
    const r = el.getBoundingClientRect();
    const pane = paneOf(doc, el);
    if (el.closest("#arcs")) return inside(r, pane) ? r : null;
    const box = {
      left: Math.max(r.left, pane.left), top: Math.max(r.top, pane.top),
      right: Math.min(r.right, pane.right), bottom: Math.min(r.bottom, pane.bottom),
    };
    if (box.right - box.left < 4 || box.bottom - box.top < 4) return null;
    return { ...box, width: box.right - box.left, height: box.bottom - box.top };
  }
  function visible(doc, selector) {
    if (selector.startsWith(".ln ")) {
      const pane = doc.getElementById("text").getBoundingClientRect();
      const middle = pane.top + pane.height * 0.35;
      const rows = [...doc.querySelectorAll("#text .ln")].filter((row) => {
        const r = row.getBoundingClientRect();
        const counts = row.querySelector(".ct");
        return r.top >= middle && r.bottom <= pane.bottom && counts && counts.textContent.trim() && row.querySelector(".tk i");
      });
      const cell = rows.length ? rows[0].querySelector(selector.slice(4)) : null;
      if (cell) return cell;
    }
    for (const el of doc.querySelectorAll(selector)) if (shownBox(doc, el)) return el;
    return null;
  }

  function install(doc, P, kinds) {
    const overlay = doc.createElement("div");
    overlay.id = "help";
    overlay.hidden = true;
    const all = sections(P, kinds);
    overlay.innerHTML = '<div class="h-box"><div class="h-head"><b>How to read this page</b>' +
      '<button type="button" data-help="label">label the page</button><button type="button" data-help="close">close (Esc)</button></div>' +
      '<div class="h-body"><nav>' + all.map((sec) => '<a data-help="go" data-to="h-' + sec.id + '">' + sec.title + "</a>").join("") + "</nav>" +
      '<div class="h-main">' + all.map((sec) => '<section id="h-' + sec.id + '"><h2>' + sec.title + "</h2>" +
        (sec.intro ? '<p class="h-intro">' + sec.intro + "</p>" : "") + (sec.figure || "") + "<dl>" +
        sec.items.map((item) => "<dt>" + (item.sample ? '<span class="h-sample">' + item.sample + "</span>" : "") + "<b>" + item.term + "</b>" +
          (item.show ? ' <a class="h-show" data-help="show" data-show="' + item.show + '">show me</a>' : "") + "</dt><dd>" + item.text + "</dd>").join("") +
        "</dl></section>").join("") + "</div></div></div>";
    doc.body.appendChild(overlay);
    const layer = doc.createElement("div");
    layer.id = "help-labels";
    layer.hidden = true;
    doc.body.appendChild(layer);

    const open = () => { overlay.hidden = false; layer.hidden = true; };
    const close = () => { overlay.hidden = true; layer.hidden = true; layer.innerHTML = ""; };

    // Point at one element: scrolled into view, outlined, with a callout.
    function point(selector, words) {
      close();
      const el = visible(doc, selector) || doc.querySelector(selector);
      if (!el) return;
      el.scrollIntoView({ block: "nearest", inline: "nearest" });
      const r = shownBox(doc, el) || el.getBoundingClientRect();
      layer.innerHTML = '<div class="hl-ring" style="left:' + (r.left - 4) + "px;top:" + (r.top - 4) + "px;width:" + (r.width + 8) + "px;height:" + (r.height + 8) + 'px"></div>' +
        '<div class="hl-callout" style="left:' + Math.max(8, Math.min(r.left, doc.defaultView.innerWidth - 320)) + "px;top:" + (r.bottom + 10 > doc.defaultView.innerHeight - 60 ? r.top - 44 : r.bottom + 10) + 'px">' + words + "</div>";
      layer.hidden = false;
      setTimeout(() => { if (!layer.hidden && layer.querySelector(".hl-ring")) close(); }, 3500);
    }

    // Number every region on the live page, with its key.
    function labels() {
      close();
      const parts = [];
      const keyRows = [];
      let n = 0;
      for (const [selector, words] of REGIONS) {
        const el = visible(doc, selector);
        if (!el) continue;
        n += 1;
        const r = shownBox(doc, el) || el.getBoundingClientRect();
        parts.push('<div class="hl-box" style="left:' + r.left + "px;top:" + r.top + "px;width:" + r.width + "px;height:" + r.height + 'px"></div>' +
          '<div class="hl-badge" style="left:' + Math.max(0, r.left - 2) + "px;top:" + Math.max(0, r.top - 2) + 'px">' + n + "</div>");
        keyRows.push("<li><b>" + n + "</b> " + words + "</li>");
      }
      layer.innerHTML = parts.join("") + '<div class="hl-key"><b>The page, labelled</b> <span class="meta">(click anywhere or Esc to close; ? for the help)</span><ol>' + keyRows.join("") + "</ol></div>";
      layer.hidden = false;
    }

    overlay.addEventListener("click", (event) => {
      const t = event.target.closest("[data-help]");
      if (!t) {
        if (event.target === overlay) close();
        return;
      }
      const what = t.dataset.help;
      if (what === "close") close();
      else if (what === "label") labels();
      else if (what === "go") doc.getElementById(t.dataset.to).scrollIntoView({ block: "start" });
      else if (what === "show") point(t.dataset.show, t.closest("dt").querySelector("b").textContent);
    });
    layer.addEventListener("click", close);
    doc.addEventListener("keydown", (event) => {
      if (event.target.closest && event.target.closest("input, select, textarea")) return;
      if (event.key === "Escape") close();
      else if (event.key === "?") open();
    });
    return { open, close, labels, point };
  }

  return {
    CONTROLS, REGIONS, GUTTER_CLASSES, gutterFigure, GATE_TEXT, CAUSE_TEXT, CHANGE_TEXT, KIND_TEXT, BY_TEXT, STATUS_TEXT, CLAIM_STATUS_TEXT, ROLE_TEXT, OPEN_TEXT, SEVERITY_TEXT, VERDICT_TEXT, LENS_TEXT,
    sections, install,
  };
});
