// `node --test tests/viewer/`: a mark on a rendered formula's box.
const test = require("node:test");
const assert = require("node:assert/strict");

const P = require("../../src/axiomation/viewer/page/src/palette.js");

test("a formula's line is the text's own underline, on a blank row at the formula's baseline", () => {
  const css = P.formulaRules(".f", "dotted", "var(--part)", null, 3);
  assert.ok(css.startsWith(".f { text-decoration: none; position: relative; } .f::after { content: \"\\0000a0"));
  assert.match(css, /top: calc\(var\(--base-top, 13px\) - var\(--asc, 13px\)\);/);
  assert.match(css, /text-decoration: underline dotted var\(--part\); text-decoration-thickness: auto; text-underline-offset: max\(3px, var\(--depth, 0px\)\);/);
  // an overlay's thickness, as its words carry it
  assert.match(P.formulaRules(".f", "dashed", "#66badc", 2, 5), /text-decoration-thickness: 2px; text-underline-offset: max\(5px,/);
  assert.match(P.formulaRules(".f", "wavy", "#ef4444", null, 4, "::before"), /\.f::before \{.*text-decoration: underline wavy #ef4444;/);
});

test("a frame on a formula stays a frame", () => {
  assert.equal(P.formulaRules(".f", "box", "#e05a78", 1.5, 5), ".f { " + P.wordsStyle("box", "#e05a78", 1.5) + " }");
});

test("a claim is drawn by its support where it says more than its standing", () => {
  assert.equal(P.toneOf({ status: "unsupported", support: "no-argument-owed" }), "no-argument-owed");
  assert.equal(P.toneOf({ status: "justified", support: "unsupported" }), "unsupported", "a gap of its own");
  assert.equal(P.toneOf({ status: "justified", support: "supported" }), "justified");
  assert.equal(P.toneOf({ status: "conjectured", support: null }), "conjectured");
  assert.equal(P.tone({ status: "unsupported", support: "no-argument-owed" }), P.NOT_OWED);
  assert.notEqual(P.NOT_OWED.colour, P.STANDING.unsupported.colour, "never a problem's red");
  assert.ok(Object.keys(P.STANDING).every((s) => P.TONE[s] === P.STANDING[s]));
});

test("a claim the author leaves unargued is drawn in the neutral tone, under its own name", () => {
  assert.equal(P.toneOf({ status: "unsupported", support: "left-unargued" }), "left-unargued");
  assert.equal(P.tone({ status: "unsupported", support: "left-unargued" }), P.LEFT);
  assert.equal(P.LEFT.colour, P.NOT_OWED.colour);
  assert.equal(P.LEFT.label, "left unargued");
});

// the palette as the page loads it, with `AxView.theme` named first
function themed(theme) {
  const path = require.resolve("../../src/axiomation/viewer/page/src/palette.js");
  delete require.cache[path];
  globalThis.self = { AxView: { theme } };
  try {
    return require(path);
  } finally {
    delete globalThis.self;
    delete require.cache[path];
  }
}

test("the light theme swaps the ground and the ink, and darkens every accent it draws on that ground", () => {
  const dark = themed(undefined);
  const light = themed("paper");
  assert.equal(dark.THEME, "graphite");
  assert.equal(light.THEME, "paper");
  assert.equal(light.SURFACE.ink, dark.SURFACE.ground, "the dark ground is the light ink");
  assert.equal(light.THEMES[light.THEME].scheme, "light");
  const luminance = (hex) => {
    const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  // every stroke and word colour darker in the light theme than in the dark
  const pairs = [
    ...Object.keys(dark.EDGE).map((k) => [dark.EDGE[k].stroke, light.EDGE[k].stroke]),
    ...Object.keys(dark.STANDING).map((k) => [dark.STANDING[k].colour, light.STANDING[k].colour]),
    ...Object.keys(dark.OPEN).map((k) => [dark.OPEN[k].colour, light.OPEN[k].colour]),
    ...Object.keys(dark.CLAIM_STATUS).map((k) => [dark.CLAIM_STATUS[k].colour, light.CLAIM_STATUS[k].colour]),
    ...Object.keys(dark.ROLE).map((k) => [dark.ROLE[k].border, light.ROLE[k].border]),
  ];
  for (const [d, l] of pairs) assert.ok(luminance(l) < luminance(d), d + " → " + l);
  // a block's body pale on the light ground, as it is dark on the dark one
  for (const k of Object.keys(light.FORM)) assert.ok(luminance(light.FORM[k].body) > 0.8, k);
  // what takes its colour from another follows the theme
  assert.equal(light.CHECK.pass.colour, light.VERDICT.verified.colour);
  assert.equal(light.LEFT.colour, light.NOT_OWED.colour);
  assert.ok(Object.keys(light.STANDING).every((s) => light.TONE[s] === light.STANDING[s]));
});

test("an unknown theme draws the dark one", () => {
  assert.equal(themed("neon").THEME, "graphite");
});

test("the stylesheet's first paint holds each theme's surfaces as the palette has them", () => {
  const css = require("node:fs").readFileSync(require.resolve("../../src/axiomation/viewer/page/view.css"), "utf8");
  const block = (selector) => {
    const at = css.indexOf(selector + " {");
    const body = css.slice(at, css.indexOf("}", at));
    return Object.fromEntries([...body.matchAll(/--([\w-]+):\s*([^;]+);/g)].map((m) => [m[1], m[2].trim()]));
  };
  for (const [theme, selector] of [["graphite", ":root"], ["paper", ':root[data-theme="paper"]']]) {
    const tokens = block(selector);
    for (const [name, value] of Object.entries(themed(theme).SURFACE)) assert.equal(tokens[name], value, theme + " --" + name);
  }
});

test("on the light ground every colour drawn as words meets WCAG AA (4.5:1) on the ground and the panels", () => {
  const P = themed("paper");
  const lin = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
  const L = (h) => { const [r, g, b] = [1, 3, 5].map((i) => lin(parseInt(h.slice(i, i + 2), 16))); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
  const ratio = (a, b) => { const [x, y] = [L(a), L(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
  const S = P.SURFACE;
  const words = ["ink", "ink-2", "ink-3", "quote", "ref", "part", "anchor", "self", "changed", "alarm", "accent", "linked", "good", "warn", "flag", "lapsed", "reviewer", "suggest", "ins", "del", "carried", "carried-ink"]
    .map((k) => ["surface " + k, S[k]]);
  for (const [set, key] of [["STANDING", "colour"], ["CLAIM_STATUS", "colour"], ["OPEN", "colour"], ["VERDICT", "colour"], ["SEVERITY", "colour"], ["CHANGE", "colour"], ["ROLE", "border"], ["EDGE", "tint"]]) {
    for (const [k, v] of Object.entries(P[set])) words.push([set + " " + k, v[key]]);
  }
  for (const [name, colour] of words) for (const ground of ["ground", "chrome"]) {
    assert.ok(ratio(colour, S[ground]) >= 4.5, name + " " + colour + " on " + ground + ": " + ratio(colour, S[ground]).toFixed(2));
  }
});
