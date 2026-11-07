// `node --test tests/viewer/`: the help.
// It fails when something the page draws, or a control it offers, has no
// words in the help, and when the help names something the page does not
// write.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const PAGE = path.join(__dirname, "../../src/axiomation/viewer/page");
const help = require(path.join(PAGE, "src/help.js"));
const palette = require(path.join(PAGE, "src/palette.js"));
const open = require(path.join(PAGE, "src/open.js"));
const arcs = require(path.join(PAGE, "src/arcs.js"));
const read = (file) => fs.readFileSync(path.join(PAGE, file), "utf-8");
const KINDS = { open: open.KINDS, families: open.FAMILIES, lenses: Object.keys(arcs.LENSES) };

function sameKeys(map, list, what) {
  assert.deepEqual(Object.keys(map).sort(), [...list].sort(), what);
}

test("every kind, provenance, standing, role, open kind and lens has its words, and no more", () => {
  sameKeys(help.KIND_TEXT, Object.keys(palette.EDGE), "edge kinds");
  sameKeys(help.BY_TEXT, Object.keys(palette.PROVENANCE), "provenances");
  sameKeys(help.STATUS_TEXT, Object.keys(palette.STANDING), "standings");
  sameKeys(help.ROLE_TEXT, Object.keys(palette.ROLE), "roles");
  sameKeys(help.OPEN_TEXT, open.KINDS, "open kinds");
  sameKeys(help.LENS_TEXT, Object.keys(arcs.LENSES), "lenses");
});

test("no entry says undefined, and every term and text is filled", () => {
  for (const section of help.sections(palette, KINDS)) {
    for (const item of section.items) {
      for (const field of ["term", "text", "sample"]) {
        const value = item[field];
        if (field === "sample" && value === undefined) continue;
        assert.ok(typeof value === "string", section.id + ": " + item.term + " " + field);
        assert.ok(!/undefined|NaN|\[object/.test(value), section.id + ": " + item.term + " " + field + ": " + value);
      }
      assert.ok(item.term.trim() && item.text.trim(), section.id);
    }
  }
});

// The controls: the page's inputs, selects and buttons by id, and those
// the scripts write.
test("every control the page offers has an entry, and every entry a control", () => {
  const written = [read("index.html"), read("src/app.js"), read("src/open.js"), read("src/check.js")].join("\n");
  const ids = new Set();
  for (const m of written.matchAll(/<(?:input|select|button)[^>]*\sid=\\?"([a-z][a-z-]*)\\?"/g)) ids.add(m[1]);
  assert.deepEqual(Object.keys(help.CONTROLS).sort(), [...ids].sort());
});

// The class names the page writes: literal class strings, and `"om-" + …`
// built from a prefix and a known value.
const WRITTEN = [read("index.html"), read("src/app.js"), read("src/card.js"), read("src/open.js")].join("\n");
function written(name) {
  if (new RegExp("[\"' ]" + name.replace(/[-]/g, "\\-") + "[\"' ]").test(WRITTEN)) return true;
  const dash = name.indexOf("-");
  return dash > 0 && WRITTEN.includes(name.slice(0, dash + 1)) && (open.KINDS.includes(name.slice(dash + 1)) || WRITTEN.includes('"' + name.slice(dash + 1) + '"'));
}

test("every show-me and every labelled region points at something the page writes", () => {
  const page = read("index.html");
  const selectors = [
    ...help.sections(palette, KINDS).flatMap((s) => s.items.map((i) => i.show)).filter(Boolean),
    ...help.REGIONS.map(([selector]) => selector),
  ];
  for (const selector of selectors) {
    for (const part of selector.split(/\s+/)) {
      for (const [, sign, name] of part.replace(/:not\([^)]*\)/g, "").matchAll(/([#.])([a-z][a-z-]*)/g)) {
        if (sign === "#") assert.ok(page.includes('id="' + name + '"') || WRITTEN.includes('id="' + name) || WRITTEN.includes("id=\\\"" + name) || name in help.CONTROLS, selector + ": #" + name);
        else assert.ok(written(name), selector + ": ." + name);
      }
    }
  }
});

test("the gutter figure is drawn with classes the page writes, and its key numbers every badge", () => {
  for (const name of help.GUTTER_CLASSES) assert.ok(written(name), "." + name);
  const figure = help.gutterFigure(palette);
  for (const [, cls] of figure.matchAll(/class="([^"]+)"/g)) {
    for (const name of cls.split(/\s+/)) {
      if (name.startsWith("hf") || name.startsWith("h-")) continue; // the figure's own
      if (name.startsWith("om-")) continue; // an open kind's mark
      assert.ok(help.GUTTER_CLASSES.includes(name), "the figure uses ." + name);
    }
  }
  const badges = [...figure.matchAll(/class="hf-n[^"]*"[^>]*>(\d+)</g)].map((m) => Number(m[1])).sort((a, b) => a - b);
  const notes = (figure.match(/<li>/g) || []).length;
  assert.deepEqual(badges, Array.from({ length: notes }, (_, i) => i + 1));
});

test("every claim status has its words and its help entry, in the fold's order", () => {
  sameKeys(help.CLAIM_STATUS_TEXT, Object.keys(palette.CLAIM_STATUS), "claim statuses");
  const card = help.sections(palette, KINDS).find((s) => s.id === "card").items.map((i) => i.term);
  for (const name of Object.keys(palette.CLAIM_STATUS)) assert.ok(card.includes("Status: " + name), name);
});

test("every change class and overlay channel has its words", () => {
  sameKeys(help.CHANGE_TEXT, Object.keys(palette.CHANGE), "change classes");
  const words = help.sections(palette, KINDS).find((s) => s.id === "overlays").items.map((i) => i.term);
  for (const channel of Object.keys(palette.OVERLAY)) assert.ok(words.includes("Channel: " + channel), channel);
});

test("every severity, verdict and judgment mark the page draws has its words", () => {
  sameKeys(help.SEVERITY_TEXT, Object.keys(palette.SEVERITY), "severities");
  sameKeys(help.VERDICT_TEXT, Object.keys(palette.VERDICT), "verdicts");
  const terms = help.sections(palette, KINDS).flatMap((s) => s.items.map((i) => s.id + ": " + i.term));
  const wanted = [
    "gutter: Open-item mark",
    "changes: The lapsed tier",
    "card: A claim", "overview: Established, provisional", "card: A reading", "card: A cited page", "card: Findings",
    "text: An open item's words", "text: The open item picked", "text: In no block", "text: Waiting on a review",
    ...Object.keys(palette.SEVERITY).map((n) => "text: Open finding: severity " + n),
    ...Object.keys(palette.SEVERITY).map((n) => "card: Severity " + n),
    ...Object.keys(palette.VERDICT).map((v) => "card: Verdict: " + v),
  ];
  for (const term of wanted) assert.ok(terms.includes(term), term);
  // and the page writes each: the marks, the glyph, the tier
  const page = ["src/app.js", "src/card.js", "src/changes.js"].map(read).join("\n");
  for (const written of ["om-bang t-", '"item-box"', "P.severity(item.severity)", "stroke-dasharray", 'class="readings"', 'class="page-chip', 'class="verdict v-', "cx-badge cx-lapsed", 'class="e-tally"']) assert.ok(page.includes(written), written);
  assert.ok(read("src/app.js").includes("P.ESTABLISHED_GLYPH"));
});

test("the open view's help names each family, its sentence and its kinds", () => {
  const terms = help.sections(palette, KINDS).find((s) => s.id === "open").items;
  for (const f of open.FAMILIES) {
    const entry = terms.find((i) => i.term === "Family: " + f.title);
    assert.ok(entry && entry.text.startsWith(f.sentence), f.key);
    for (const k of f.kinds) assert.ok(entry.text.includes(k), k);
  }
});
