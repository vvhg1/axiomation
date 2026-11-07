// Every colour the viewer paints, with what the product draws and the open
// view's kinds.
//
// One module: anything drawn gets its appearance from here, never from a
// literal at the call site. Calibrated against each theme's ground. Block bodies are opaque and read as surfaces; their borders
// carry the hue.
//
// Two axes for edges, never mixed: an edge's *kind* is its colour, and
// which way reliance runs, seen from the selection, is its line style:
// solid upstream (what the selection rests on), dotted downstream (what
// rests on it). Line style carries direction;
// an edge's *provenance* (the export's `by`) is a word, never a style.
// Decoration (selection, an open item) is an outline, a tint or a mark, in
// hues no kind uses.
(function (root, factory) {
  const api = factory(root);
  if (typeof module === "object" && module.exports) module.exports = api;
  else (root.AxView = root.AxView || {}).palette = api;
})(typeof self !== "undefined" ? self : this, function (root) {
  "use strict";

  // Two grounds, dark and light: each its surfaces, and the accents
  // calibrated against it (`SOFT`, `PAPER` below). The page names the
  // theme before this module loads (`AxView.theme`); without one, the
  // dark.
  const THEMES = {
    graphite: {
      label: "graphite",
      scheme: "dark",
      wordmark: "wordmark.svg",
      surface: {
        ground: "#141917", chrome: "#1c2220", raised: "#1c2220", rule: "#222a33", line: "#2e3740", "line-strong": "#3c4652",
        hover: "#1a2028", ink: "#e9e8e3", "ink-2": "#bec0c0", "ink-3": "#8a9096", dim: "#5a6168", quote: "#9ea3a8",
        ref: "#66badc", part: "#d6aa55", anchor: "#d283a6", self: "#6fbfcc", mark: "#d283a6", changed: "#e09455",
        sel: "rgba(233, 232, 227, 0.09)", "sel-edge": "#e9e8e3",
        // rendered words in no block, a cutter defect: the one mark filled,
        // its words in the ground's dark, unlike any other
        uncovered: "#f2d14a",
        // a warning in the header or the panel: the paper ahead, an error
        alarm: "#e0675f",
        overlay: "rgba(5, 7, 10, 0.72)", panel: "rgba(28, 34, 32, 0.96)", accent: "#66badc",
        // the words at the selection's lines' other ends: a muted amber,
        // set off from the ink without drawing the eye from the selection,
        // against the selection's blue, the pair a red–green reader keeps
        // apart
        linked: "#d6bea4",
        // the selected block's claims' quiet underline in the text
        "claim-line": "#7a848c",
        // the words on the uncovered mark's fill
        "on-uncovered": "#141917",
        // the card's tones for words: a yes, a wait, a provisional reading,
        // a lapse, a reviewer, a suggested work; a diff's words added,
        // deleted and carried
        good: "#6cc59a", warn: "#d8b25a", flag: "#e3c763", lapsed: "#b48ad6", reviewer: "#b9a2e8", suggest: "#2dd4bf",
        ins: "#a9dcc2", del: "#e8a39d", carried: "#a593d6", "carried-ink": "#c9bdf0",
        shadow: "rgba(0, 0, 0, 0.45)",
      },
    },
    // the light ground: the dark one's ink and ground swapped, every
    // accent a darker tier of its hue, so it keeps its contrast on white
    paper: {
      label: "paper",
      scheme: "light",
      wordmark: "wordmark-light.svg",
      surface: {
        ground: "#f7f6f2", chrome: "#edece7", raised: "#edece7", rule: "#e0ded7", line: "#d2cfc7", "line-strong": "#b9b6ad",
        hover: "#eeede8", ink: "#141917", "ink-2": "#3b4140", "ink-3": "#646a70", dim: "#9aa0a6", quote: "#5c6268",
        ref: "#1e7095", part: "#8d600e", anchor: "#a3426f", self: "#1c737f", mark: "#a3426f", changed: "#a35415",
        sel: "rgba(20, 25, 23, 0.07)", "sel-edge": "#141917",
        uncovered: "#f2d14a",
        alarm: "#be382b",
        overlay: "rgba(20, 25, 23, 0.32)", panel: "rgba(247, 246, 242, 0.97)", accent: "#1e7095",
        linked: "#8a5a1c",
        "claim-line": "#9aa0a6",
        "on-uncovered": "#141917",
        good: "#287752", warn: "#86640f", flag: "#80660a", lapsed: "#8550b3", reviewer: "#6e58b5", suggest: "#1c766a",
        ins: "#2e7652", del: "#b03a30", carried: "#6c5bb3", "carried-ink": "#5a4aa0",
        shadow: "rgba(20, 25, 23, 0.16)",
      },
    },
  };
  const DEFAULT_THEME = "graphite";
  const ASKED = root && root.AxView && root.AxView.theme;
  const THEME = THEMES[ASKED] ? ASKED : DEFAULT_THEME;
  const SURFACE = THEMES[THEME].surface;
  const GROUND = SURFACE.ground;
  const CHROME = SURFACE.chrome;
  const RULE = SURFACE.rule;
  const INK = SURFACE.ink;
  const INK_2 = SURFACE["ink-2"];

  // A block's role (docs/model.md § 2), else its form.
  // `body` is the fill, `border` the hue, `lane` the gutter, `tint` the
  // roles overlay.
  const ROLE = {
    claim: { label: "claim", body: "#1c2a4f", border: "#60a5fa", lane: "#3b82f6", tint: "rgba(59, 130, 246, 0.16)" },
    // a definition golden yellow, never the green a red–green reader
    // confuses with red
    definition: { label: "definition", body: "#3a3010", border: "#eab308", lane: "#ca8a04", tint: "rgba(202, 138, 4, 0.15)" },
    assumption: { label: "assumption", body: "#32205c", border: "#a78bfa", lane: "#8b5cf6", tint: "rgba(139, 92, 246, 0.18)" },
    // a justification, its proof and the justifies edge one amber, the
    // golden yellow the definitions' alone
    justification: { label: "justification", body: "#43280c", border: "#f59e0b", lane: "#d97706", tint: "rgba(217, 119, 6, 0.14)" },
    motivation: { label: "motivation", body: "#0c2a3a", border: "#7dd3fc", lane: "#0ea5e9", tint: "rgba(14, 165, 233, 0.12)" },
  };
  // A block without a role is drawn by its form, in greys and one cool hue
  // for displays, so that a role always stands out from the prose.
  const FORM = {
    prose: { label: "prose", body: "#1f2937", border: "#64748b", lane: "#475569" },
    display: { label: "display", body: "#0c3a4a", border: "#22d3ee", lane: "#0e7490" },
    heading: { label: "heading", body: "#1f2937", border: "#94a3b8", lane: "#94a3b8" },
    title: { label: "title", body: "#1f2937", border: "#94a3b8", lane: "#94a3b8" },
    statement: { label: "statement", body: "#1c2a4f", border: "#93c5fd", lane: "#1d4ed8" },
    environment: { label: "environment", body: "#34302d", border: "#a8a29e", lane: "#78716c" },
    float: { label: "float", body: "#34302d", border: "#a8a29e", lane: "#57534e" },
    footnote: { label: "footnote", body: "#1f2937", border: "#64748b", lane: "#334155" },
    item: { label: "item", body: "#1f2937", border: "#64748b", lane: "#52525b" },
  };
  const FORM_FALLBACK = { label: "block", body: "#1e293b", border: "#64748b", lane: "#475569" };

  // A group's bracket, by kind.
  const GROUP = {
    proof: { label: "proof", stroke: "#f59e0b" },
    "proof-section": { label: "proof section", stroke: "#d97706" },
    // a justification as a proof is, so of its amber; lighter, to be told apart
    derivation: { label: "derivation", stroke: "#fcd34d" },
    step: { label: "step", stroke: "#2dd4bf" },
    "step-run": { label: "step run", stroke: "#a78bfa" },
  };
  const GROUP_FALLBACK = { label: "group", stroke: "#94a3b8" };

  // Edge kinds (docs/model.md § 3): the stroke, and a lighter tint for a label's
  // text. Strokes are the 400 tier: on a dark ground the 500s read as dim.
  const EDGE = {
    uses: { label: "uses", stroke: "#2dd4bf", tint: "#99f6e4" },
    about: { label: "about", stroke: "#a78bfa", tint: "#ddd6fe" },
    justifies: { label: "justifies", stroke: "#f59e0b", tint: "#fde3b4" },
    qualifies: { label: "qualifies", stroke: "#f472b6", tint: "#fbcfe8" },
  };
  const EDGE_FALLBACK = { label: "edge", stroke: "#64748b", tint: "#cbd5e1" };

  // Which way reliance runs along an edge, seen from the selection; in
  // the graph, an edge between two neighbours is aside, thin and faint.
  const STREAM = {
    up: { label: "upstream", dash: "", width: 1.8, opacity: 1 },
    down: { label: "downstream", dash: "0.1 4", width: 2.4, opacity: 1 },
    aside: { label: "aside", dash: "", width: 1, opacity: 0.45 },
  };

  // An overlay's channels, each its own
  // style: words underlined in its line, or boxed, a whole block tinted.
  // A finding's words are boxed (a wavy line reads as
  // an unsupported claim's); the product's findings share the style.
  const OVERLAY = {
    key: { label: "key", colour: "#e3c763", line: "dotted" },
    judgment: { label: "judgment", colour: "#6fbf98", line: "solid" },
    finding: { label: "finding", colour: "#e05a78", line: "box" },
    custom: { label: "custom", colour: "#66badc", line: "dashed" },
  };

  // In change mode's graph, an edge the later state ended and a block it
  // deleted: faint, dashed, grey, beside what stands.
  const GHOST = { label: "ended", dash: "3 4", width: 1.3, opacity: 0.6 };

  // An edit's reach, two tones: a ring in the graph, a
  // tint in the text.
  const REACH = {
    changes: { label: "changes meaning", colour: "#f472b6", style: "solid" },
    asked: { label: "asked", colour: "#94a3b8", style: "dashed" },
  };
  // A block's change between two states (`changes`' classes): added and
  // deleted the plainest, a split or a merge violet, a change in its words
  // amber, a move sky, a change of maths, markup or a macro grey-blue.
  const CHANGE = {
    added: { label: "added", colour: "#6fbf98" },
    deleted: { label: "deleted", colour: "#e0675f" },
    split: { label: "split", colour: "#a593d6" },
    merged: { label: "merged", colour: "#a593d6" },
    changed: { label: "changed", colour: "#e0a955" },
    moved: { label: "moved", colour: "#66badc" },
    math: { label: "math", colour: "#8fa6bf" },
    markup: { label: "markup", colour: "#8fa6bf" },
    macro: { label: "macro", colour: "#8fa6bf" },
  };
  const CHANGE_FALLBACK = { label: "changed", colour: "#e0a955" };

  // In the graph, a block that uses an unsupported claim (the gutter's ring).
  const USES_UNSUPPORTED_GLYPH = "⚠";
  // In the graph, a block with a claim established.
  const ESTABLISHED_GLYPH = "✓";
  const GLYPH = { established: ESTABLISHED_GLYPH, "uses-unsupported": USES_UNSUPPORTED_GLYPH };

  // Provenance: a word on the card and in the hover, never a style. A
  // person's reading is marked; a model's or an agent's is the default.
  const PROVENANCE = {
    person: { label: "person" },
    agent: { label: "agent" },
  };

  // A claim's standing (docs/model.md § 4): its dot and its underline.
  // Unsupported is red, so it stands out.
  const STANDING = {
    justified: { label: "justified", colour: "#34d399" },
    self: { label: "self", colour: "#22d3ee" },
    restated: { label: "restated", colour: "#c4b5fd" },
    conjectured: { label: "conjectured", colour: "#94a3b8" },
    unsupported: { label: "unsupported", colour: "#ef4444" },
  };
  // A claim is drawn by its support (`rulings.Support`) where that says
  // more than its standing: one no argument is owed for in a neutral tone,
  // never a problem's red; one whose own reason is a gap in the
  // unsupported red, whatever it stands; one the author leaves unargued in
  // that neutral tone too, the author's call and no item to act on. A tone
  // is a standing's name, `no-argument-owed` or `left-unargued`.
  const NOT_OWED = { label: "no argument owed", colour: "#b5aea6" };
  const LEFT = { label: "left unargued", colour: NOT_OWED.colour };
  // (filled once the theme has set the standings' colours, below)
  const TONE = {};
  const SUPPORT_TONES = ["no-argument-owed", "left-unargued", "unsupported"];
  function toneOf(standing) {
    if (SUPPORT_TONES.includes(standing.support)) return standing.support;
    return standing.status;
  }

  // A claim's status (`claim_status.ClaimStatus`), its chip on the card:
  // what blocks in the reds, what asks a reading in the open view's tones
  // for it, what is settled green or neutral.
  const CLAIM_STATUS = {
    defect: { label: "defect", colour: "#ef4444" },
    disputed: { label: "disputed", colour: "#f87171" },
    conjectured: { label: "conjectured", colour: "#94a3b8" },
    unsupported: { label: "unsupported", colour: "#ef4444" },
    lapsed: { label: "lapsed", colour: "#c084fc" },
    stale: { label: "stale", colour: "#fb923c" },
    unjudged: { label: "unjudged", colour: "#94a3b8" },
    inconclusive: { label: "inconclusive", colour: "#8f979f" },
    "rests-on": { label: "rests on", colour: "#94a3b8" },
    "finding-open": { label: "finding open", colour: "#fdba74" },
    "left-unargued": { label: "left unargued", colour: "#b5aea6" },
    established: { label: "established", colour: "#34d399" },
  };

  // The open view's kinds (`queries.OpenKind`),
  // in its order: a colour for a count, a mark and a line. The judgments'
  // three are reds; the kept graph's four faults are one blue family apart
  // from them.
  const OPEN = {
    unsupported: { label: "unsupported claims", colour: "#ef4444" },
    circle: { label: "circles", colour: "#b91c1c" },
    "uses-unsupported": { label: "uses an unsupported claim", colour: "#fca5a5" },
    partial: { label: "argued in part", colour: "#bef264" },
    flag: { label: "flags", colour: "#fde047" },
    stale: { label: "stale", colour: "#fb923c" },
    "carried-unread": { label: "carried unread", colour: "#fdba74" },
    pending: { label: "pending", colour: "#94a3b8" },
    lapsed: { label: "lapsed judgments", colour: "#c084fc" },
    disputed: { label: "disputed", colour: "#f87171" },
    negative: { label: "negative readings", colour: "#dc2626" },
    finding: { label: "open findings", colour: "#f43f5e" },
    suggestion: { label: "suggested citations", colour: "#2dd4bf" },
    retracted: { label: "retracted works", colour: "#e0675f" },
    publication: { label: "questioned publications", colour: "#fde047" },
    unplaced: { label: "unplaced citations and quotations", colour: "#94a3b8" },
    unjudged: { label: "unjudged", colour: "#94a3b8" },
    inconclusive: { label: "inconclusive readings", colour: "#8f979f" },
    provisional: { label: "provisional readings", colour: "#94a3b8" },
    unchecked: { label: "unchecked positioning", colour: "#94a3b8" },
    unread: { label: "unread files", colour: "#94a3b8" },
    "dangling-reference": { label: "dangling references", colour: "#38bdf8" },
    "duplicate-label": { label: "duplicate labels", colour: "#6366f1" },
    "unused-premise": { label: "unused premises", colour: "#93c5fd" },
    "undeclared-assumption": { label: "undeclared assumptions", colour: "#a5b4fc" },
    "later-rule": { label: "under a later rule", colour: "#a8a29e" },
  };

  // A finding's severity: its gutter mark's tone and its box's
  // strength, the finding's hue full at 3 and dimmer below.
  const SEVERITY = {
    3: { label: "severity 3: critical", colour: "#f43f5e", strength: 1 },
    2: { label: "severity 2", colour: "#ec8a9d", strength: 0.75 },
    1: { label: "severity 1: minor", colour: "#b8939b", strength: 0.55 },
  };

  // A reading's verdict: a word on the card, in a tone.
  const VERDICT = {
    verified: { colour: "#6fbf98" },
    "not-shown": { colour: "#e3c763" },
    contradicted: { colour: "#e0675f" },
    inconclusive: { colour: "#8f979f" },
  };

  // The verdict's chip in the header: the paper passes, passes
  // with a gate warning, fails, or the verdict could not be read.
  const CHECK = {
    pass: { label: "passes", colour: VERDICT.verified.colour },
    warn: { label: "passes, a gate warns", colour: VERDICT["not-shown"].colour },
    fail: { label: "fails", colour: VERDICT.contradicted.colour },
    error: { label: "not read", colour: VERDICT.inconclusive.colour },
  };

  // A target with no place in the text: a file of the paper, `scope:paper`.
  const EXTERNAL = { label: "external", body: "#2a1636", border: "#e879f9", lane: "#e879f9" };

  // Decoration: outlines and tints in hues no kind claims.
  const DECORATION = {
    selected: "#a3e635", // lime
    uncovered: SURFACE.uncovered, // rendered text in no block, filled
  };

  // The dark ground's accents: the same hues, less saturated, so no colour
  // shouts on a neutral ground.
  const SOFT = {
    ROLE: {
      claim: { body: "#1a2433", border: "#7ea6d8", lane: "#5b88c0", tint: "rgba(126, 166, 216, 0.14)" },
      definition: { body: "#2a2514", border: "#dcbe5c", lane: "#bb9d3c", tint: "rgba(220, 190, 92, 0.13)" },
      assumption: { body: "#221c33", border: "#a593d6", lane: "#8471bd", tint: "rgba(165, 147, 214, 0.15)" },
      justification: { body: "#2c1f12", border: "#d39248", lane: "#b0752f", tint: "rgba(211, 146, 72, 0.14)" },
      motivation: { body: "#15252c", border: "#82bcd4", lane: "#5d9db8", tint: "rgba(130, 188, 212, 0.12)" },
    },
    FORM: {
      prose: { body: "#1c2128", border: "#6b737c", lane: "#4b535c" },
      display: { body: "#14262d", border: "#6cb8c9", lane: "#3f8496" },
      heading: { body: "#1c2128", border: "#9aa1a8", lane: "#9aa1a8" },
      title: { body: "#1c2128", border: "#9aa1a8", lane: "#9aa1a8" },
      statement: { body: "#1a2433", border: "#8fb3dd", lane: "#456fa3" },
      environment: { body: "#24221f", border: "#a39e97", lane: "#7a756e" },
      float: { body: "#24221f", border: "#a39e97", lane: "#625d57" },
      footnote: { body: "#1c2128", border: "#6b737c", lane: "#444b53" },
      item: { body: "#1c2128", border: "#6b737c", lane: "#55595f" },
    },
    GROUP: {
      proof: { stroke: "#d39248" },
      "proof-section": { stroke: "#b0752f" },
      step: { stroke: "#5bb8a9" },
      "step-run": { stroke: "#a593d6" },
    },
    EDGE: {
      uses: { stroke: "#5bb8a9", tint: "#a9ddd4" },
      about: { stroke: "#9d8fd3", tint: "#cdc5ec" },
      justifies: { stroke: "#d39248", tint: "#eccaa2" },
      qualifies: { stroke: "#d283a6", tint: "#ecc0d3" },
    },
    STANDING: {
      justified: { colour: "#6fbf98" },
      self: { colour: "#6fbfcc" },
      restated: { colour: "#b4a8dc" },
      conjectured: { colour: "#8f979f" },
      unsupported: { colour: "#e0675f" },
    },
    OPEN: {
      unsupported: { colour: "#e0675f" },
      circle: { colour: "#c0504a" },
      "uses-unsupported": { colour: "#e8a39d" },
      partial: { colour: "#a8c97a" },
      flag: { colour: "#e3c763" },
      stale: { colour: "#e09455" },
      "carried-unread": { colour: "#e3b27f" },
      pending: { colour: "#8f979f" },
      lapsed: { colour: "#b48ad6" },
      disputed: { colour: "#e0827a" },
      negative: { colour: "#c9504a" },
      finding: { colour: "#e05a78" },
      suggestion: { colour: "#5fbfb0" },
      retracted: { colour: "#e0675f" },
      publication: { colour: "#e3c763" },
      unplaced: { colour: "#8f979f" },
      unjudged: { colour: "#8f979f" },
      inconclusive: { colour: "#8f979f" },
      provisional: { colour: "#8f979f" },
      unchecked: { colour: "#8f979f" },
      unread: { colour: "#8f979f" },
      "dangling-reference": { colour: "#6aaed0" },
      "duplicate-label": { colour: "#7f84d0" },
      "unused-premise": { colour: "#9dbbe0" },
      "undeclared-assumption": { colour: "#a9b0dc" },
      "later-rule": { colour: "#a39e98" },
    },
    REACH: {
      changes: { colour: "#d283a6" },
      asked: { colour: "#8f979f" },
    },
    EXTERNAL: { body: "#261b2b", border: "#cf8fd8", lane: "#cf8fd8" },
    DECORATION: { selected: SURFACE["sel-edge"], uncovered: SURFACE.uncovered },
  };
  // The light ground's accents: bodies the palest tier of their hue,
  // borders, strokes and words a dark one; a label's tint darker than its
  // stroke, as on the dark ground it is lighter.
  const NEUTRAL = "#626a72";
  const PAPER = {
    ROLE: {
      claim: { body: "#e6edf6", border: "#3e6ba1", lane: "#6b92c2", tint: "rgba(63, 108, 163, 0.12)" },
      definition: { body: "#f5eed6", border: "#82660b", lane: "#c2a443", tint: "rgba(194, 164, 67, 0.18)" },
      assumption: { body: "#ede9f7", border: "#6450ad", lane: "#8a78c2", tint: "rgba(100, 80, 173, 0.12)" },
      justification: { body: "#f6e9da", border: "#9c591b", lane: "#c08546", tint: "rgba(192, 133, 70, 0.16)" },
      motivation: { body: "#e2eff4", border: "#28718b", lane: "#64a3bd", tint: "rgba(43, 120, 148, 0.12)" },
    },
    FORM: {
      prose: { body: "#eceef0", border: "#79818a", lane: "#a2a8ae" },
      display: { body: "#e1eff2", border: "#2d8496", lane: "#62a8b8" },
      heading: { body: "#eceef0", border: "#5c646c", lane: "#7d858d" },
      title: { body: "#eceef0", border: "#5c646c", lane: "#7d858d" },
      statement: { body: "#e6edf6", border: "#456ca0", lane: "#7a9bc6" },
      environment: { body: "#efede9", border: "#6d6962", lane: "#a7a29b" },
      float: { body: "#efede9", border: "#6d6962", lane: "#b6b1aa" },
      footnote: { body: "#eceef0", border: "#79818a", lane: "#b6bcc2" },
      item: { body: "#eceef0", border: "#79818a", lane: "#aaadb1" },
    },
    GROUP: {
      proof: { stroke: "#b0712c" },
      "proof-section": { stroke: "#8c5920" },
      derivation: { stroke: "#b8921f" },
      step: { stroke: "#2b8a7c" },
      "step-run": { stroke: "#7461b8" },
    },
    EDGE: {
      uses: { stroke: "#2b9080", tint: "#1d6a5e" },
      about: { stroke: "#7a68c4", tint: "#52449a" },
      justifies: { stroke: "#c07a2c", tint: "#865014" },
      qualifies: { stroke: "#c2558a", tint: "#8c3361" },
    },
    STANDING: {
      justified: { colour: "#287752" },
      self: { colour: "#1a7381" },
      restated: { colour: "#6c5bb3" },
      conjectured: { colour: NEUTRAL },
      unsupported: { colour: "#be382b" },
    },
    NOT_OWED: { colour: "#6d6761" },
    CLAIM_STATUS: {
      defect: { colour: "#be382b" },
      disputed: { colour: "#ae4942" },
      conjectured: { colour: NEUTRAL },
      unsupported: { colour: "#be382b" },
      lapsed: { colour: "#8550b3" },
      stale: { colour: "#a35415" },
      unjudged: { colour: NEUTRAL },
      inconclusive: { colour: NEUTRAL },
      "rests-on": { colour: NEUTRAL },
      "finding-open": { colour: "#965b25" },
      "left-unargued": { colour: "#6d6761" },
      established: { colour: "#287752" },
    },
    OPEN: {
      unsupported: { colour: "#be382b" },
      circle: { colour: "#962a24" },
      "uses-unsupported": { colour: "#9f534d" },
      partial: { colour: "#4f7422" },
      flag: { colour: "#80660a" },
      stale: { colour: "#a35415" },
      "carried-unread": { colour: "#8b6030" },
      pending: { colour: NEUTRAL },
      lapsed: { colour: "#8550b3" },
      disputed: { colour: "#ae4942" },
      negative: { colour: "#a02e28" },
      finding: { colour: "#bd3757" },
      suggestion: { colour: "#1c766a" },
      retracted: { colour: "#be382b" },
      publication: { colour: "#80660a" },
      unplaced: { colour: NEUTRAL },
      unjudged: { colour: NEUTRAL },
      inconclusive: { colour: NEUTRAL },
      provisional: { colour: NEUTRAL },
      unchecked: { colour: NEUTRAL },
      unread: { colour: NEUTRAL },
      "dangling-reference": { colour: "#266f99" },
      "duplicate-label": { colour: "#4b51ad" },
      "unused-premise": { colour: "#416ba2" },
      "undeclared-assumption": { colour: "#5960b0" },
      "later-rule": { colour: "#6d6962" },
    },
    REACH: {
      changes: { colour: "#a74874" },
      asked: { colour: NEUTRAL },
    },
    CHANGE: {
      added: { colour: "#287752" },
      deleted: { colour: "#be382b" },
      split: { colour: "#6c5bb3" },
      merged: { colour: "#6c5bb3" },
      changed: { colour: "#8f5f18" },
      moved: { colour: "#1e7095" },
      math: { colour: "#536c85" },
      markup: { colour: "#536c85" },
      macro: { colour: "#536c85" },
    },
    OVERLAY: {
      key: { colour: "#80660a" },
      judgment: { colour: "#287752" },
      finding: { colour: "#bd3757" },
      custom: { colour: "#1e7095" },
    },
    SEVERITY: {
      3: { colour: "#bd3757" },
      2: { colour: "#9d5365" },
      1: { colour: "#7c6269" },
    },
    VERDICT: {
      verified: { colour: "#287752" },
      "not-shown": { colour: "#80660a" },
      contradicted: { colour: "#be382b" },
      inconclusive: { colour: NEUTRAL },
    },
    EXTERNAL: { body: "#f5e6f6", border: "#a23dad", lane: "#c070ca" },
    DECORATION: { selected: SURFACE["sel-edge"], uncovered: SURFACE.uncovered },
  };
  const ACCENTS = { graphite: SOFT, paper: PAPER }[THEME];
  const SETS = { ROLE, FORM, GROUP, EDGE, STANDING, CLAIM_STATUS, OPEN, REACH, CHANGE, OVERLAY, SEVERITY, VERDICT };
  for (const [name, entries] of Object.entries(SETS)) {
    for (const [key, value] of Object.entries(ACCENTS[name] || {})) entries[key] = { ...entries[key], ...value };
  }
  Object.assign(EXTERNAL, ACCENTS.EXTERNAL);
  Object.assign(DECORATION, ACCENTS.DECORATION);
  Object.assign(NOT_OWED, ACCENTS.NOT_OWED || {});
  // what took its colour from another before the theme set it
  LEFT.colour = NOT_OWED.colour;
  CHECK.pass.colour = VERDICT.verified.colour;
  CHECK.warn.colour = VERDICT["not-shown"].colour;
  CHECK.fail.colour = VERDICT.contradicted.colour;
  CHECK.error.colour = VERDICT.inconclusive.colour;
  Object.assign(TONE, STANDING, { "no-argument-owed": NOT_OWED, "left-unargued": LEFT });

  function role(name) {
    return ROLE[name] || null;
  }
  function block(item) {
    // A block with one role takes its colour; a mixed one, its form's.
    const roles = item.roles || [];
    return (roles.length === 1 && ROLE[roles[0]]) || FORM[item.form] || FORM_FALLBACK;
  }
  function group(kind) {
    return GROUP[kind] || GROUP_FALLBACK;
  }
  function edge(kind) {
    return EDGE[kind] || EDGE_FALLBACK;
  }
  function standing(status) {
    return STANDING[status] || STANDING.conjectured;
  }
  // A claim's tone: its colour and label, by `toneOf`.
  function tone(standing) {
    return TONE[toneOf(standing)] || STANDING.conjectured;
  }
  function change(cls) {
    return CHANGE[cls] || CHANGE_FALLBACK;
  }
  function open(kind) {
    return OPEN[kind] || OPEN.pending;
  }
  // The CSS for words marked in a line style and colour, `width` px: an
  // underline, or for `box` a frame round the words, one per screen row.
  function wordsStyle(line, colour, width) {
    if (line === "box") {
      return "text-decoration: none; border-radius: 3px; -webkit-box-decoration-break: clone; box-decoration-break: clone; box-shadow: 0 0 0 " + width + "px " + colour + ";";
    }
    return "text-decoration: underline " + line + " " + colour + "; text-decoration-thickness: " + width + "px; text-underline-offset: 5px;";
  }
  // The same mark on a rendered formula's box, as the CSS rules for
  // `selector`: the renderer draws a formula of inline blocks, which an
  // underline never reaches, so `pseudo` holds a row of blank spaces as
  // wide as the box, set on the formula's baseline, and underlines them
  // as the text is underlined: the same line, colour, thickness and
  // offset (`width` null: the text's own), lower only where the formula
  // reaches deeper (a sum's limits). The page measures each box
  // (`--base-top`, its baseline under its top; `--depth`, its formula's
  // depth under the baseline) and the pane (`--asc`, a line's baseline
  // under its top). A frame stays a frame.
  const BLANKS = '"' + "\\0000a0".repeat(300) + '"';
  function formulaRules(selector, line, colour, width, offset, pseudo = "::after") {
    if (line === "box") return selector + " { " + wordsStyle(line, colour, width) + " }";
    return selector + " { text-decoration: none; position: relative; } " +
      selector + pseudo + " { content: " + BLANKS + "; position: absolute; left: 0; right: 0; top: calc(var(--base-top, 13px) - var(--asc, 13px)); " +
      "padding-bottom: calc(var(--depth, 0px) + 12px); overflow: hidden; white-space: pre; color: transparent; pointer-events: none; " +
      "text-decoration: underline " + line + " " + colour + "; text-decoration-thickness: " + (width === null ? "auto" : width + "px") + "; " +
      "text-underline-offset: max(" + offset + "px, var(--depth, 0px)); }";
  }
  // A check item's tone (`check.Cause`): its open kind's where the
  // cause is one; else a reading's verdict's, a circle the gap's red.
  // Read at the call, so a theme's softening carries over.
  const CAUSE_TONE = {
    circular: () => STANDING.unsupported,
    "not-shown": () => VERDICT["not-shown"],
    contradicted: () => VERDICT.contradicted,
  };
  function cause(kind) {
    return OPEN[kind] || (CAUSE_TONE[kind] ? CAUSE_TONE[kind]() : OPEN.pending);
  }
  function severity(n) {
    return SEVERITY[n] || SEVERITY[1];
  }

  return {
    CLAIM_STATUS,
    THEMES, DEFAULT_THEME, THEME, SURFACE, GROUND, CHROME, RULE, INK, INK_2,
    ROLE, FORM, GROUP, EDGE, STREAM, GHOST, REACH, CHANGE, OVERLAY, USES_UNSUPPORTED_GLYPH, ESTABLISHED_GLYPH, GLYPH, PROVENANCE, STANDING, NOT_OWED, LEFT, TONE, OPEN, CAUSE_TONE, CHECK, SEVERITY, VERDICT, EXTERNAL, DECORATION,
    role, block, group, edge, standing, toneOf, tone, open, cause, severity, change, wordsStyle, formulaRules,
  };
});
