// `node --test tests/viewer/`: a paper's markup rendered in place, each
// construct a source range hidden, shown as what it prints, or styled.
const test = require("node:test");
const assert = require("node:assert/strict");

const MK = require("../../src/axiomation/viewer/page/src/markup.js");

// The text as the pane draws it: a hidden range as what it puts, once.
function printed(text, items) {
  let out = "";
  for (let i = 0; i < text.length; i += 1) {
    const hidden = items.find((x) => x.hide && x.start <= i && i < x.end);
    if (!hidden) out += text[i];
    else if (hidden.start === i) out += hidden.put;
  }
  return out;
}
const styled = (text, items, cls) => items.filter((x) => !x.hide && x.cls === cls).map((x) => text.slice(x.start, x.end));
const span = (text, words, from = 0) => {
  const at = text.indexOf(words, from);
  return [at, at + words.length];
};

test("LaTeX: text styles, signs, dashes and quotes, a formula left alone", () => {
  const text = "An \\emph{odd} and \\textbf{bold $a_b$} case -- see~it ``here'' at 5\\% \\ldots{} done\\\\";
  const items = MK.find(text, "latex", [], [span(text, "$a_b$")], {});
  assert.equal(printed(text, items), "An odd and bold $a_b$ case – see it “here” at 5% … done");
  assert.deepEqual(styled(text, items, "mk-i"), ["odd"]);
  assert.deepEqual(styled(text, items, "mk-b"), ["bold $a_b$"]);
});

test("LaTeX: what prints nothing is hidden, a bare group's braces and a font switch too", () => {
  const text = "\\vspace{1em}\\noindent A {\\bf strong} word \\ifdraft x";
  const items = MK.find(text, "latex", [], [], {});
  assert.equal(printed(text, items), "A strong word x");
  assert.deepEqual(styled(text, items, "mk-b"), ["strong"]);
});

test("a heading: its marks hidden, its number put, unless its title numbers itself", () => {
  const text = "\\section{Results}\n# 2. Laws";
  const items = MK.find(text, "latex", [], [], {
    sections: [{ span: span(text, "\\section{Results}"), level: 1, number: "3" }, { span: span(text, "# 2. Laws"), level: 2, number: "2" }],
  });
  assert.equal(printed(text, items), "3 Results\n2. Laws");
  assert.deepEqual(styled(text, items, "mk-h mk-h1"), ["Results"]);
});

test("a statement's and a proof's head, an enumerate's items in its label", () => {
  const text = "\\begin{thm}[Main]\nBody.\n\\end{thm}\n\\begin{proof}\nSo.\n\\end{proof}\n\\begin{enumerate}[label=(\\roman*)]\n\\item one\n\\item two\n\\end{enumerate}";
  const env = (name, kind, printedName) => {
    const [start] = span(text, "\\begin{" + name + "}");
    const close = "\\end{" + name + "}";
    const end = text.indexOf(close, start) + close.length;
    // the reader's title, as the payload carries it
    return { name, kind, meaning: name, printed: printedName, title: name === "thm" ? "Main" : null, span: [start, end], body: [start + ("\\begin{" + name + "}").length + (name === "thm" ? 6 : 0), end - close.length] };
  };
  const items = MK.find(text, "latex", [], [], { environments: [env("thm", "statement", "Theorem"), env("proof", "proof", null), env("enumerate", "list", null)] });
  assert.equal(printed(text, items), "Theorem (Main).\nBody.\n\nProof.\nSo.\n∎\n\n(i) one\n(ii) two\n");
});

test("a citation in its brackets with its locator, a textual one bare, a reference by its section's number", () => {
  const text = "As \\autocite[p.~3]{smith2001} and \\citet{jones} show, see \\ref{sec:a} and \\eqref{eq:b}.";
  const items = MK.find(text, "latex", [], [], {
    sections: [],
    citations: [{ span: span(text, "\\autocite[p.~3]{smith2001}"), command: "autocite" }, { span: span(text, "\\citet{jones}"), command: "citet" }],
    references: [{ span: span(text, "\\ref{sec:a}"), command: "ref", targets: ["sec:a"] }, { span: span(text, "\\eqref{eq:b}"), command: "eqref", targets: ["eq:b"] }],
  });
  assert.equal(printed(text, items), "As [p. 3, smith2001] and jones show, see sec:a and (eq:b).");
  const numbered = MK.find("\\ref{sec:a}", "latex", [], [], { sections: [{ span: [20, 20], level: 1, number: "1.4", label: "sec:a" }], references: [{ span: [0, 11], command: "ref", targets: ["sec:a"] }] });
  // `\\ref` prints the number alone, as LaTeX does; `\\cref` names it
  assert.equal(printed("\\ref{sec:a}", numbered), "1.4");
  const named = MK.find("\\cref{sec:a}", "latex", [], [], { sections: [{ span: [20, 20], level: 1, number: "1.4", label: "sec:a" }], references: [{ span: [0, 12], command: "cref", targets: ["sec:a"] }] });
  assert.equal(printed("\\cref{sec:a}", named), "§1.4");
});

test("a footnote: its number put, its words beside it styled", () => {
  const text = "Word\\footnote{A note.} on.";
  const items = MK.find(text, "latex", [], [], { footnotes: [{ span: span(text, "\\footnote{A note.}"), text: span(text, "A note.") }] });
  assert.equal(printed(text, items), "Word¹ A note. on.");
  assert.deepEqual(styled(text, items, "mk-fn"), ["A note."]);
});

test("Markdown: emphasis by pandoc's flanking rule, code, links, list and quote marks, curled quotes", () => {
  const text = "A **strong** and *soft* `x_1` [site](http://a.b) snake_case_name \"said\"\n- item\n> quoted";
  const items = MK.find(text, "markdown", [], [], {});
  assert.equal(printed(text, items), "A strong and soft x_1 site snake_case_name “said”\n• item\nquoted");
  assert.deepEqual(styled(text, items, "mk-tt"), ["x_1"]);
  assert.deepEqual(styled(text, items, "mk-q"), ["quoted"]);
});

test("a rule of dashes is no dash; a hidden range and a formula hold no markup", () => {
  const text = "---\n% a **comment**\n$a--b$ x";
  const comment = span(text, "% a **comment**");
  const items = MK.find(text, "markdown", [comment], [span(text, "$a--b$")], {});
  assert.equal(printed(text, items), text);
});

test("silent: what prints nothing, for the lines that fold away", () => {
  const text = "\\end{lawbox}\nword";
  const items = [{ start: 0, end: 12, hide: true, put: "", cls: "" }];
  const mask = MK.silent(text.length, [], items);
  assert.equal(mask[0], 1);
  assert.equal(mask[13], 0);
});

test("an excerpt's HTML: its markup rendered, a formula by the page's renderer, null where nothing renders", () => {
  const text = "a \\emph{b} $x$ \\autocite{k}";
  const html = MK.html(text, "latex", [{ start: 11, end: 14 }], () => "<F>");
  assert.equal(html, 'a <span class="mk-i">b</span> <F> <span class="mk-cite">[</span><span class="mk-cite">k</span><span class="mk-cite">]</span>');
  assert.equal(MK.html("plain words", "latex", [], () => null), null);
});

test("verbatim and code print as written: no dash, quote, group or emphasis read inside", () => {
  const latex = "\\begin{verbatim}\na--b ~ ``x'' {\\bf y}\n\\end{verbatim}";
  const env = { name: "verbatim", kind: "verbatim", span: [0, latex.length], body: [span(latex, "\n")[0], latex.indexOf("\\end")] };
  assert.equal(printed(latex, MK.find(latex, "latex", [], [], { environments: [env] })), "\na--b ~ ``x'' {\\bf y}\n");
  const fence = '```\nfoo -- bar **x** "q"\n```';
  const fenced = { name: "code", kind: "verbatim", span: [0, fence.length], body: [4, fence.length - 3] };
  assert.equal(printed(fence, MK.find(fence, "markdown", [], [], { environments: [fenced] })), 'foo -- bar **x** "q"\n');
  const code = 'Use `--budget "x"` now -- ok';
  const items = MK.find(code, "markdown", [], [], {});
  assert.equal(printed(code, items), 'Use --budget "x" now – ok');
  assert.deepEqual(styled(code, items, "mk-tt"), ['--budget "x"']);
});

test("nothing inline enters a formula, even where a hidden run nests inside it", () => {
  const text = "$$\\label{e} x^*_1 = y^*_2$$ and *this*";
  const formula = span(text, "$$\\label{e} x^*_1 = y^*_2$$");
  const items = MK.find(text, "markdown", [span(text, "\\label{e}")], [formula], {});
  assert.ok(items.every((x) => x.end <= formula[0] || x.start >= formula[1]), JSON.stringify(items));
  assert.deepEqual(styled(text, items, "mk-i"), ["this"]);
});

test("an item commented out is not counted", () => {
  const text = "\\begin{enumerate}\n\\item one\n% \\item two\n\\item three\n\\end{enumerate}";
  const env = { name: "enumerate", kind: "list", span: [0, text.length], body: ["\\begin{enumerate}".length, text.indexOf("\\end")] };
  const items = MK.find(text, "latex", [span(text, "% \\item two")], [], { environments: [env] });
  const shown = printed(text, items);
  assert.match(shown, /1\. one/);
  assert.match(shown, /2\. three/);
});

test("a frame hides its options and the arguments it takes, never the words after them; a boxed theorem keeps its title", () => {
  const quote = "\\begin{quote}{\\em Quoted words} rest\\end{quote}";
  const q = { name: "quote", kind: "other", span: [0, quote.length], body: ["\\begin{quote}".length, quote.indexOf("\\end")] };
  const items = MK.find(quote, "latex", [], [], { environments: [q] });
  assert.equal(printed(quote, items), "Quoted words rest");
  assert.deepEqual(styled(quote, items, "mk-i"), ["Quoted words"]);
  const page = "\\begin{minipage}[t]{0.5\\textwidth}Text\\end{minipage}";
  const m = { name: "minipage", kind: "other", span: [0, page.length], body: ["\\begin{minipage}".length, page.indexOf("\\end")] };
  assert.equal(printed(page, MK.find(page, "latex", [], [], { environments: [m] })), "Text");
  const boxed = "\\begin{theorembox}[colback=red]{Key Result}{key}\nBody.\n\\end{theorembox}";
  const b = { name: "theorembox", kind: "statement", printed: "Theorem", title: "Key Result", span: [0, boxed.length], body: [boxed.indexOf("\n"), boxed.indexOf("\\end")] };
  const found = MK.find(boxed, "latex", [], [], { environments: [b] });
  assert.equal(printed(boxed, found), "Theorem (Key Result).\nBody.\n");
});

test("a footnote takes its number in the paper; a title that numbers itself, by one rule; Markdown prints its tilde", () => {
  const text = "Words\\footnote{A note.} end.";
  const items = MK.find(text, "latex", [], [], { footnotes: [{ span: span(text, "\\footnote{A note.}"), text: span(text, "A note."), number: 3 }] });
  assert.equal(printed(text, items), "Words³ A note. end.");
  for (const title of ["2.3 Second Law", "2.3: Title", "IV. Results", "A.1 Proofs", "B2 Bounds", "7"]) assert.ok(MK.numbersItself(title), title);
  for (const title of ["A Note on Systems", "I think", "Results", "3D Shapes"]) assert.ok(!MK.numbersItself(title), title);
  assert.equal(printed("about ~5 s", MK.find("about ~5 s", "markdown", [], [], {})), "about ~5 s");
});

test("a title as it prints, on one line: no comment, no footnote, no markup", () => {
  const text = "{\\bf Comparing} Things\\footnote{A note.} \\\\ Again % x\n";
  const hidden = [span(text, "% x")];
  const items = MK.find(text, "latex", hidden, [], { footnotes: [{ span: span(text, "\\footnote{A note.}"), text: span(text, "A note.") }] });
  assert.equal(MK.printed(text, 0, text.length, MK.silent(text.length, hidden, items), items), "Comparing Things Again");
});
