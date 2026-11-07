// The mathematics in a paper's source: where each formula stands, and the
// formula rendered by the vendored KaTeX.
//
// Pure: the renderer is passed in. Offsets are the text's own, UTF-16 code
// units, as the text layer's are. A formula the renderer refuses is never
// shown as an error: the caller shows its source.
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else (root.AxView = root.AxView || {}).maths = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // The display environments, the reader's own list (`reader.layer`'s
  // DISPLAY_ENVS; `test_viewer_js.py` keeps the two equal), each also
  // starred. `math` is inline, and `displaymath` and `math` are kept by
  // their insides, which the renderer takes without them.
  const DISPLAY_ENVIRONMENTS = ["equation", "align", "alignat", "gather", "multline", "flalign", "eqnarray", "displaymath"];
  const BY_INSIDE = new Set(["displaymath", "math"]);
  const ENVIRONMENT_RE = /^\\begin\{([a-zA-Z]+)(\*?)\}/;
  const BLANK_LINE_RE = /\n[ \t]*\n/g;
  const INFIX_RE = /\\(?:over|atop|choose|brace|brack)(?![a-zA-Z])/;

  function escape(text) {
    return String(text).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  }

  // The text with each hidden range (`[start, end]`) blanked, its length kept.
  function blanked(text, hidden) {
    if (!hidden || !hidden.length) return text;
    const chars = text.split("");
    for (const [start, end] of hidden) for (let i = Math.max(0, start); i < Math.min(end, chars.length); i += 1) if (chars[i] !== "\n") chars[i] = " ";
    return chars.join("");
  }

  // Where each paragraph ends: a formula never runs past a blank line.
  function paragraphEnds(src) {
    const ends = [];
    BLANK_LINE_RE.lastIndex = 0;
    for (let m = BLANK_LINE_RE.exec(src); m; m = BLANK_LINE_RE.exec(src)) {
      ends.push(m.index);
      BLANK_LINE_RE.lastIndex = m.index + 1;
    }
    return (from) => {
      let lo = 0;
      let hi = ends.length;
      while (lo < hi) {
        const mid = (lo + hi) >> 1;
        if (ends[mid] < from) lo = mid + 1;
        else hi = mid;
      }
      return lo < ends.length ? ends[lo] : src.length;
    };
  }

  // The next `close` at or after `from` and before `limit` that no
  // backslash escapes, or -1.
  function closing(src, close, from, limit) {
    for (let j = src.indexOf(close, from); j >= 0 && j + close.length <= limit; j = src.indexOf(close, j + 1)) {
      if (src[j - 1] !== "\\") return j;
    }
    return -1;
  }

  // An inline `$…$`'s closing dollar. In LaTeX, the next unescaped one,
  // even with a formula opening right after it (`$a$$b$` is two, as TeX
  // reads it). In Markdown, pandoc's rule: the opening dollar has a
  // non-space right after it, the closing one a non-space right before it
  // and no digit after it.
  function inlineClosing(src, from, limit, markdown) {
    for (let j = closing(src, "$", from, limit); j >= 0; j = closing(src, "$", j + 1, limit)) {
      if (!markdown) return j;
      if (src[j + 1] === "$") return -1;
      if (!/\s/.test(src[j - 1]) && !/[0-9]/.test(src[j + 1] || "")) return j;
    }
    return -1;
  }

  // Every formula of a text, in order: `{start, end, texStart, tex,
  // display}`, `start` and `end` its whole source, delimiters included,
  // `tex` what the renderer takes, standing at `texStart`. `format` is the file's ("markdown" or "latex");
  // `hidden` the ranges read as blank (comments, the preamble).
  function find(text, format, hidden) {
    const src = blanked(text, hidden);
    const markdown = format === "markdown";
    // a comment's line ends no paragraph, as in TeX: read from the text
    // as written, not blanked
    const paragraphEnd = paragraphEnds(text);
    const found = [];
    let i = 0;
    while (i < src.length) {
      const c = src[i];
      if (markdown && c === "`") {
        let n = 1;
        while (src[i + n] === "`") n += 1;
        const fence = "`".repeat(n);
        let j = src.indexOf(fence, i + n);
        while (j >= 0 && src[j + n] === "`") j = src.indexOf(fence, j + n + 1);
        i = j >= 0 ? j + n : i + n;
        continue;
      }
      if (c === "\\") {
        const next = src[i + 1];
        // pandoc's Markdown reads `\(` and `\[` as escaped brackets
        if (!markdown && (next === "(" || next === "[")) {
          const close = next === "(" ? "\\)" : "\\]";
          const j = src.indexOf(close, i + 2);
          if (j >= 0 && j < paragraphEnd(i)) {
            found.push({ start: i, end: j + 2, texStart: i + 2, tex: src.slice(i + 2, j), display: next === "[" });
            i = j + 2;
            continue;
          }
        } else if (next === "b") {
          const m = ENVIRONMENT_RE.exec(src.slice(i, i + 40));
          const name = m && m[1];
          if (m && (DISPLAY_ENVIRONMENTS.includes(name) || name === "math")) {
            const end = "\\end{" + name + m[2] + "}";
            const j = src.indexOf(end, i + m[0].length);
            if (j >= 0) {
              const texStart = BY_INSIDE.has(name) ? i + m[0].length : i;
              const tex = BY_INSIDE.has(name) ? src.slice(texStart, j) : src.slice(i, j + end.length);
              found.push({ start: i, end: j + end.length, texStart, tex, display: name !== "math" });
              i = j + end.length;
              continue;
            }
          }
        }
        i += 2;
        continue;
      }
      if (c === "$") {
        const limit = paragraphEnd(i);
        if (src[i + 1] === "$") {
          const j = closing(src, "$$", i + 2, limit);
          if (j > i + 2) {
            found.push({ start: i, end: j + 2, texStart: i + 2, tex: src.slice(i + 2, j), display: true });
            i = j + 2;
          } else i += 2;
          continue;
        }
        if (!markdown || (i + 1 < src.length && !/\s/.test(src[i + 1]))) {
          const j = inlineClosing(src, i + 1, limit, markdown);
          if (j > i + 1) {
            found.push({ start: i, end: j + 1, texStart: i + 1, tex: src.slice(i + 1, j), display: false });
            i = j + 1;
            continue;
          }
        }
        i += 1;
        continue;
      }
      i += 1;
    }
    return found;
  }

  // A label's name as words in the renderer's text mode.
  function asText(words) {
    return "\\text{" + words.replace(/[\\{}]/g, "").replace(/[_#$%&^~]/g, (c) => (c === "^" || c === "~" ? " " : "\\" + c)) + "}";
  }

  // A formula's source as the renderer takes it: a `\label` prints nothing,
  // a reference prints its label's name (the page numbers nothing), and
  // the two environments the renderer lacks take their nearest kin.
  function prepare(tex) {
    return tex
      .replace(/\\label\{[^{}]*\}/g, "")
      .replace(/\\eqref\{([^{}]*)\}/g, (_, name) => "(" + asText(name) + ")")
      .replace(/\\(?:auto|c|C)?ref\{([^{}]*)\}/g, (_, name) => asText(name))
      .replace(/\\(begin|end)\{multline(\*?)\}/g, "\\$1{gather$2}")
      .replace(/\\begin\{eqnarray\*?\}/g, "\\begin{array}{rcl}")
      .replace(/\\end\{eqnarray\*?\}/g, "\\end{array}");
  }

  // `{…}` without its outer braces, when they enclose it whole.
  function unbraced(body) {
    if (body[0] !== "{" || body[body.length - 1] !== "}") return null;
    let depth = 0;
    for (let i = 0; i < body.length; i += 1) {
      if (body[i] === "\\") {
        i += 1;
        continue;
      }
      if (body[i] === "{") depth += 1;
      else if (body[i] === "}") {
        depth -= 1;
        if (depth === 0 && i < body.length - 1) return null;
      }
    }
    return depth === 0 ? body.slice(1, -1) : null;
  }

  // The paper's macros (the text layer's `macros`, in the paper's order)
  // as the renderer's table (the payload leaves out a definition with an
  // optional argument). An environment and a body the reader could not
  // take whole are left out too: a formula that uses one shows its
  // source.
  function macrosOf(list) {
    const table = {};
    for (const m of list || []) {
      if (!m.name || m.name[0] !== "\\" || /environment$/.test(m.command)) continue;
      const inside = unbraced(String(m.definition || "").trim());
      if (inside === null) continue;
      if (m.command === "providecommand" && Object.prototype.hasOwnProperty.call(table, m.name)) continue;
      table[m.name] = m.command === "DeclareMathOperator" ? "\\operatorname{" + inside + "}" : inside;
    }
    return table;
  }

  // One formula rendered: `{html}`, or `{error}` with the renderer's words.
  function render(katex, formula, macros) {
    try {
      const html = katex.renderToString(prepare(formula.tex), {
        displayMode: formula.display,
        throwOnError: true,
        // a copy: a `\newcommand` inside a formula would add to the table
        macros: Object.assign({}, macros),
        strict: "ignore",
      });
      return { html };
    } catch (error) {
      return { error: String((error && error.message) || error) };
    }
  }

  // A formula cut where what marks it changes: `pieces` its source's
  // pieces in order, `{start, end, key}`, the key what marks one; each
  // part a run of one key, `{start, end, key, tex}`, the delimiters left
  // out. Null where it is one part, an environment kept whole (its rows
  // and columns do not survive a cut), or a formula holding an infix
  // fraction (`\over`: a cut renders it wrongly, without an error).
  function parts(formula, pieces) {
    if (/^\\begin\{/.test(formula.tex) || INFIX_RE.test(formula.tex)) return null;
    const texEnd = formula.texStart + formula.tex.length;
    const out = [];
    for (const piece of pieces) {
      const start = Math.max(piece.start, formula.texStart);
      const end = Math.min(piece.end, texEnd);
      if (end <= start) continue;
      const last = out[out.length - 1];
      if (last && last.key === piece.key && last.end === start) last.end = end;
      else out.push({ start, end, key: piece.key });
    }
    if (out.length < 2) return null;
    return out.map((part) => ({ ...part, tex: formula.tex.slice(part.start - formula.texStart, part.end - formula.texStart) }));
  }

  // A formula's pieces keyed by what marks them, for `parts`: each piece
  // `{start, end, classes, runs, owner}` (its marks, its run's classes,
  // its block's index) gives `{start, end, key, classes, owner}`. A hidden
  // or a space piece (a comment, the blanks between words no block owns)
  // says nothing of the marks around it: it takes the key before it, or
  // the next where none is before, and never lends its own marks to the
  // key (`marks` reads only the others).
  function keyed(pieces) {
    const neutral = (piece) => piece.runs.includes("r-hidden") || piece.runs.includes("r-space");
    const out = pieces.map((piece) => ({ start: piece.start, end: piece.end, classes: piece.classes, owner: piece.owner, neutral: neutral(piece), key: neutral(piece) ? null : piece.owner + " " + piece.classes.join(" ") }));
    let before = null;
    for (const piece of out) {
      if (piece.key === null) piece.key = before;
      else before = piece.key;
    }
    let after = "";
    for (let i = out.length - 1; i >= 0; i -= 1) {
      if (!out[i].neutral) after = out[i].key;
      else if (out[i].key === null) out[i].key = after;
    }
    return out;
  }
  // Each key's marks and block, read from its first piece that is not
  // neutral.
  function marks(pieces) {
    const found = new Map();
    for (const piece of pieces) if (!piece.neutral && !found.has(piece.key)) found.set(piece.key, { classes: piece.classes, owner: piece.owner });
    return found;
  }

  // A formula's parts rendered, each on its own (a display's in display
  // style): `[{first, last, html}]`, each run of `cut`'s parts `first` to
  // `last` that renders. A part the renderer refuses on its own (a cut
  // inside a group, a fraction, a `\left … \right`), or one that would
  // begin with a script or a prime, joins the next and is tried again, so
  // a bad cut costs only itself. Null where nothing is left to cut: the
  // formula is then drawn whole.
  function renderParts(katex, formula, cut, macros) {
    const out = [];
    let first = 0;
    for (let last = 0; last < cut.length; last += 1) {
      const tex = cut.slice(first, last + 1).map((part) => part.tex).join("");
      const next = last + 1 < cut.length ? cut[last + 1].tex.trimStart() : "";
      if (last + 1 < cut.length && /^[_^']/.test(next)) continue;
      if (/^\s*[_^']/.test(tex)) return null;
      // an empty group at each inner cut keeps the spacing there: `{}=b`
      // is still a relation after something, `{}-b` still a binary minus
      const inner = (first > 0 ? "{}" : "") + tex + (last + 1 < cut.length ? "{}" : "");
      const done = render(katex, { tex: (formula.display ? "\\displaystyle " : "") + inner, display: false }, macros);
      if (done.error) continue;
      out.push({ first, last, html: done.html });
      first = last + 1;
    }
    if (first < cut.length || out.length < 2) return null;
    return out;
  }

  // A plain text as HTML, each formula in it rendered and its source kept
  // beside it for a click; a refused one left as its source.
  function withMaths(katex, text, format, macros) {
    const words = String(text);
    let out = "";
    let at = 0;
    for (const formula of find(words, format)) {
      const done = render(katex, formula, macros);
      if (done.error) continue;
      const source = words.slice(formula.start, formula.end);
      out += escape(words.slice(at, formula.start)) +
        '<span class="mxr' + (formula.display ? " mxd" : "") + '" title="click: the source">' +
        '<span class="mxr-r">' + done.html + '</span><code class="mxr-s">' + escape(source) + "</code></span>";
      at = formula.end;
    }
    return out + escape(words.slice(at));
  }

  return { DISPLAY_ENVIRONMENTS, find, prepare, macrosOf, render, keyed, marks, parts, renderParts, withMaths };
});
