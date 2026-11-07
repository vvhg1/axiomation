// `node --test tests/viewer/`: the mathematics in the source, found and
// rendered by the vendored KaTeX.
const test = require("node:test");
const assert = require("node:assert/strict");

const M = require("../../src/axiomation/viewer/page/src/maths.js");
const katex = require("../../src/axiomation/viewer/page/vendor/katex/katex.min.js");

const sources = (text, format, hidden) => M.find(text, format, hidden).map((f) => text.slice(f.start, f.end));

test("Markdown: inline and display dollars, each with its own tex", () => {
  const text = "Let $x \\in X$ and\n$$\n\\mu(x) \\propto g(x)\n$$\nhold.";
  const found = M.find(text, "markdown");
  assert.deepEqual(found.map((f) => [f.tex, f.display]), [["x \\in X", false], ["\n\\mu(x) \\propto g(x)\n", true]]);
  assert.equal(text.slice(found[1].start, found[1].end), "$$\n\\mu(x) \\propto g(x)\n$$");
});

test("Markdown: pandoc's rule keeps prices and spaced dollars out", () => {
  assert.deepEqual(sources("It costs $5 and $10 today.", "markdown"), []);
  assert.deepEqual(sources("from $ a $ to $b$", "markdown"), ["$b$"]);
  // a closing dollar before a digit is passed over, as pandoc passes it
  assert.deepEqual(sources("$a$1 then $c$", "markdown"), ["$a$1 then $c$"]);
  assert.deepEqual(sources("an escaped \\$ then $y$", "markdown"), ["$y$"]);
});

test("a formula never runs past a blank line", () => {
  assert.deepEqual(sources("open $x\n\ny$ shut", "markdown"), []);
  assert.deepEqual(sources("open $$x\n\ny$$ shut", "latex"), []);
});

test("a comment's line ends no paragraph: the formula runs on past it", () => {
  const text = "\\[\n a = b\n % c = d\n\\] and $a +\n% note\nb$";
  const hidden = [[text.indexOf("%"), text.indexOf("\n", text.indexOf("%"))], [text.lastIndexOf("%"), text.indexOf("\n", text.lastIndexOf("%"))]];
  assert.equal(M.find(text, "latex", hidden).length, 2);
});

test("Markdown: `\\[` and `\\(` are escaped brackets, as pandoc reads them", () => {
  assert.deepEqual(sources("see \\[1\\] and \\(2\\) then $x$", "markdown"), ["$x$"]);
});

test("Markdown: code spans and fences hold no mathematics", () => {
  assert.deepEqual(sources("`$x$` and ``a`$b$`` then $c$", "markdown"), ["$c$"]);
  assert.deepEqual(sources("```\n$x$\n```\n$y$", "markdown"), ["$y$"]);
});

test("LaTeX: dollars to the next unescaped one, brackets and environments", () => {
  const text = "A $ x $ and \\(y\\), \\[z\\] then\n\\begin{align*}\na &= b \\label{e}\n\\end{align*}\nand \\begin{math}w\\end{math}.";
  const found = M.find(text, "latex");
  assert.deepEqual(found.map((f) => [text.slice(f.start, f.end), f.tex, f.display]), [
    ["$ x $", " x ", false],
    ["\\(y\\)", "y", false],
    ["\\[z\\]", "z", true],
    ["\\begin{align*}\na &= b \\label{e}\n\\end{align*}", "\\begin{align*}\na &= b \\label{e}\n\\end{align*}", true],
    ["\\begin{math}w\\end{math}", "w", false],
  ]);
});

test("LaTeX: two formulas back to back are two, as TeX reads them", () => {
  assert.deepEqual(sources("so $a$$b$ and $$c$$", "latex"), ["$a$", "$b$", "$$c$$"]);
});

test("a hidden range reads as blank: a dollar in a comment pairs with nothing", () => {
  const text = "% costs $5\nthen $x$ here";
  assert.deepEqual(sources(text, "latex", [[0, 10]]), ["$x$"]);
  assert.deepEqual(sources(text, "latex"), ["$5\nthen $"]);
});

test("the reader's display environments, each also starred", () => {
  for (const name of M.DISPLAY_ENVIRONMENTS) {
    for (const star of ["", "*"]) {
      const text = "\\begin{" + name + star + "}a\\end{" + name + star + "}";
      assert.deepEqual(sources(text, "latex"), [text], name + star);
    }
  }
});

test("prepare: a label prints nothing, a reference its label's name", () => {
  assert.equal(M.prepare("a \\label{eq:a} = \\eqref{eq:b_1} + \\ref{x}"), "a  = (\\text{eq:b\\_1}) + \\text{x}");
  assert.equal(M.prepare("\\begin{multline*}a\\end{multline*}"), "\\begin{gather*}a\\end{gather*}");
  assert.equal(M.prepare("\\begin{eqnarray}a&=&b\\end{eqnarray}"), "\\begin{array}{rcl}a&=&b\\end{array}");
});

test("the paper's macros as the renderer's table", () => {
  const table = M.macrosOf([
    { command: "newcommand", name: "\\R", arguments: 0, definition: "{\\mathbb{R}}" },
    { command: "newcommand", name: "\\norm", arguments: 1, definition: "{\\lVert #1 \\rVert}" },
    { command: "DeclareMathOperator", name: "\\argmax", arguments: 0, definition: "{arg\\,max}" },
    { command: "providecommand", name: "\\R", arguments: 0, definition: "{R}" },
    { command: "def", name: "\\pair", arguments: 0, definition: "{{a}{b}}" },
    { command: "newenvironment", name: "proofsketch", arguments: 0, definition: "{}{}" },
  ]);
  assert.deepEqual(table, { "\\R": "\\mathbb{R}", "\\norm": "\\lVert #1 \\rVert", "\\argmax": "\\operatorname{arg\\,max}", "\\pair": "{a}{b}" });
});

test("render: the vendored KaTeX, the paper's macros used, a refusal its words", () => {
  const macros = M.macrosOf([{ command: "newcommand", name: "\\norm", arguments: 1, definition: "{\\lVert #1 \\rVert}" }]);
  const done = M.render(katex, { tex: "\\norm{x} \\label{a}", display: false }, macros);
  assert.ok(done.html.includes("katex"), done.error);
  const display = M.render(katex, { tex: "\\begin{align}a &= b \\tag{3}\\end{align}", display: true }, {});
  assert.ok(display.html.includes("katex-display"), display.error);
  const refused = M.render(katex, { tex: "\\nosuchcommand x", display: false }, {});
  assert.equal(refused.html, undefined);
  assert.match(refused.error, /nosuchcommand/);
  // the table is copied: a definition inside a formula does not leak
  M.render(katex, { tex: "\\newcommand{\\leak}{1} \\leak", display: false }, macros);
  assert.deepEqual(Object.keys(macros), ["\\norm"]);
});

test("withMaths: the words escaped, each formula rendered beside its source, a refused one its source", () => {
  const html = M.withMaths(katex, "a < b with $x^2$ and $\\bogus$", "markdown", {});
  assert.ok(html.startsWith("a &lt; b with <span class=\"mxr\""));
  assert.ok(html.includes('<code class="mxr-s">$x^2$</code>'));
  assert.ok(html.endsWith(" and $\\bogus$"));
  assert.equal(M.withMaths(katex, "no maths <here>", "markdown", {}), "no maths &lt;here&gt;");
});

test("each formula knows where its tex stands", () => {
  const text = "a $x$ b $$y$$ \\[z\\] \\begin{math}w\\end{math} \\begin{align}v\\end{align}";
  for (const f of M.find(text, "latex")) assert.equal(text.slice(f.texStart, f.texStart + f.tex.length), f.tex);
});

test("parts: a formula cut where its marks change, the delimiters left out", () => {
  const text = "$$g(x)=a, \\quad b=1$$";
  const [formula] = M.find(text, "markdown");
  const pieces = [{ start: 0, end: 2, key: "a" }, { start: 2, end: 10, key: "a pt0" }, { start: 10, end: 19, key: "a" }, { start: 19, end: 21, key: "a" }];
  assert.deepEqual(M.parts(formula, pieces).map((p) => [p.tex, p.key]), [["g(x)=a, ", "a pt0"], ["\\quad b=1", "a"]]);
  // one key throughout: one part, drawn whole
  assert.equal(M.parts(formula, [{ start: 0, end: 21, key: "a" }]), null);
  // an environment kept whole is never cut
  const [env] = M.find("\\begin{align}a&=b\\end{align}", "latex");
  assert.equal(M.parts(env, [{ start: 0, end: 15, key: "a" }, { start: 15, end: 28, key: "b" }]), null);
});

test("parts: a formula holding an infix fraction is never cut", () => {
  const [formula] = M.find("$a \\over b + c$", "markdown");
  assert.equal(M.parts(formula, [{ start: 0, end: 6, key: "a" }, { start: 6, end: 16, key: "b" }]), null);
});

test("keyed: a comment or an unowned blank takes its neighbours' key and lends no marks", () => {
  const piece = (start, end, classes, runs, owner) => ({ start, end, classes, runs, owner });
  const pieces = [
    piece(0, 2, [], ["r-space"], -1),
    piece(2, 7, ["b3", "x5"], [], 3),
    piece(7, 12, ["b3"], ["r-hidden"], 3),
    piece(12, 16, ["b3"], [], 3),
  ];
  const keyed = M.keyed(pieces);
  // the leading blank takes the next key, the comment the one before
  assert.deepEqual(keyed.map((p) => p.key), ["3 b3 x5", "3 b3 x5", "3 b3 x5", "3 b3"]);
  const marks = M.marks(keyed);
  assert.deepEqual(marks.get("3 b3 x5"), { classes: ["b3", "x5"], owner: 3 });
  assert.deepEqual(marks.get("3 b3"), { classes: ["b3"], owner: 3 });
});

test("renderParts: each part on its own; a part refused alone joins the next", () => {
  const [formula] = M.find("$$g(x)=\\sum_{k} a(k), \\quad b=1$$", "markdown");
  const at = (tex) => formula.texStart + formula.tex.indexOf(tex);
  const cut = (...ks) => M.parts(formula, [formula.start, ...ks, formula.end].slice(0, -1).map((k, i, all) => ({ start: k, end: i + 1 < all.length ? all[i + 1] : formula.end, key: "k" + i })));
  const two = M.renderParts(katex, formula, cut(at("\\quad")), {});
  assert.deepEqual(two.map((p) => [p.first, p.last]), [[0, 0], [1, 1]]);
  assert.ok(two.every((p) => p.html.includes("katex")));
  // a cut inside `\sum_{k}`'s braces is dropped; the cut at `\quad` stays
  const kept = M.renderParts(katex, formula, cut(at("k}") + 1, at("\\quad")), {});
  assert.deepEqual(kept.map((p) => [p.first, p.last]), [[0, 1], [2, 2]]);
  // nothing left to cut: drawn whole
  assert.equal(M.renderParts(katex, formula, cut(at("k}") + 1), {}), null);
  // a part never begins with a script: `w` and `^2` stay together
  const [square] = M.find("$a + w^2$", "markdown");
  const scripted = M.parts(square, [{ start: 0, end: 6, key: "a" }, { start: 6, end: 9, key: "b" }]);
  assert.equal(M.renderParts(katex, square, scripted, {}), null);
});
