// A paper's markup rendered in place: each construct a source range, its
// markup hidden or shown as what it prints, its words styled. The text
// stays the source, every offset its own, so whatever marks the text (a
// block's runs, an anchor, an edge's evidence) marks it as before.
//
// Pure. The structure is the reader's (the text payload's headings,
// environments, citations, references and footnotes); the inline markup
// the reader records nowhere is read here: LaTeX's text styles, escapes,
// dashes and quotes and list items, Markdown's emphasis, code, links and
// list and quote marks. A Markdown paper is read for raw LaTeX too, as
// pandoc passes it through. Offsets are UTF-16 code units, as the text
// layer's are.
//
// Each item is `{start, end, cls}`: its words styled by `cls`; or
// `{start, end, hide: true, put, cls}`: its source not shown, `put` (if
// any) shown in its place, styled by `cls`.
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else (root.AxView = root.AxView || {}).markup = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // a command's argument styled: its class, and the marks put around it
  const STYLES = {
    emph: "mk-i", textit: "mk-i", textsl: "mk-i", textbf: "mk-b", texttt: "mk-tt", textsc: "mk-sc",
    underline: "mk-u", textsf: "", textrm: "", textup: "", textmd: "", textnormal: "", mbox: "", text: "",
    caption: "mk-cap", subcaption: "mk-cap", enquote: "", smash: "", textsuperscript: "mk-sup", textsubscript: "mk-sub",
    paragraph: "mk-b", subparagraph: "mk-b",
  };
  const AROUND = { enquote: ["“", "”"] };
  // an old font switch, read in a bare group: `{\\bf words}`
  const SWITCHES = { bf: "mk-b", bfseries: "mk-b", it: "mk-i", itshape: "mk-i", em: "mk-i", sl: "mk-i", slshape: "mk-i", tt: "mk-tt", ttfamily: "mk-tt", sc: "mk-sc", scshape: "mk-sc", rm: "", normalfont: "", small: "", footnotesize: "", large: "", Large: "", normalsize: "" };
  // a command that prints a word or a sign, or nothing
  const WORDS = {
    ldots: "…", dots: "…", textellipsis: "…", S: "§", P: "¶", textendash: "–", textemdash: "—", LaTeX: "LaTeX", TeX: "TeX",
    textasciitilde: "~", textbackslash: "\\", textbar: "|", ss: "ß", copyright: "©", dag: "†", ddag: "‡", quad: " ", qquad: "  ",
    noindent: "", medskip: "", bigskip: "", smallskip: "", par: "", centering: "", newline: "", linebreak: "", pagebreak: "",
    raggedright: "", raggedleft: "", itemsep: "", parskip: "", parindent: "", hfill: " ", vfill: "", break: "", protect: "", relax: "", nolinebreak: "", allowbreak: "",
  };
  // a command and its arguments that print nothing
  const SILENT = new Set(["addtocontents", "vspace", "hspace", "addvspace", "phantomsection", "label", "index", "nocite", "addcontentsline", "setlength", "setcounter", "addtocounter", "thispagestyle", "pagestyle"]);
  const ESCAPED = new Set(["%", "&", "_", "#", "$", "{", "}"]);
  const SPACES = { ",": " ", ";": " ", ":": " ", "!": "", " ": " ", "@": "", "/": "", "\\": "" };
  // a citation that names its work in the sentence (`Smith (2001) shows`)
  const TEXTUAL_RE = /^(?:text|Text)cite|^cite(?:t|author|year|title|alp|alt)\b|^Cite(?:t|author|alp|alt)\b/;
  const NUMBERING = { arabic: (n) => String(n), roman: roman, Roman: (n) => roman(n).toUpperCase(), alph: (n) => String.fromCharCode(96 + ((n - 1) % 26) + 1), Alph: (n) => String.fromCharCode(64 + ((n - 1) % 26) + 1) };
  const DEPTH_LABELS = ["\\arabic*.", "(\\alph*)", "\\roman*.", "\\Alph*."];

  // Whether a heading's title numbers itself (`2.3 Second Law`, `A.1
  // Proofs`, `IV. Results`), so its number is not put again.
  const SELF_NUMBERED_RE = /^(?:\d+(?:\.\d+)*[.):]?|[A-Z]\d*(?:\.\d+)+[.):]?|[A-Z]\d+[.):]?|[IVXL]+[.):])(?:\s|$)/;
  function numbersItself(title) {
    return SELF_NUMBERED_RE.test(String(title || "").trim());
  }

  // An environment's brace arguments that print nothing, by its name: the
  // reader keeps them in its body (a width, a column count, a placement).
  const ENVIRONMENT_ARGUMENTS = { minipage: 1, multicols: 1, "multicols*": 1, wrapfigure: 2, wraptable: 2, subfigure: 1, subtable: 1, adjustbox: 1, spacing: 1, tcolorbox: 0 };

  function roman(n) {
    const table = [[1000, "m"], [900, "cm"], [500, "d"], [400, "cd"], [100, "c"], [90, "xc"], [50, "l"], [40, "xl"], [10, "x"], [9, "ix"], [5, "v"], [4, "iv"], [1, "i"]];
    let out = "";
    for (const [value, word] of table) while (n >= value) {
      out += word;
      n -= value;
    }
    return out;
  }

  // The offset just past the group opening at `open` (`{` or `[`), or -1:
  // braces nest, a backslash escapes, a bracket group closes at its first
  // `]` outside braces.
  function groupEnd(text, open, limit) {
    const close = text[open] === "[" ? "]" : "}";
    let depth = 0;
    for (let i = open + 1; i < limit; i += 1) {
      const c = text[i];
      if (c === "\\") i += 1;
      else if (c === "{") depth += 1;
      else if (c === "}") {
        if (depth === 0) return close === "}" ? i + 1 : -1;
        depth -= 1;
      } else if (c === close && depth === 0) return i + 1;
    }
    return -1;
  }

  // Past the spaces and tabs at `at`: what TeX skips after a control word.
  function spaced(text, at) {
    while (text[at] === " " || text[at] === "\t") at += 1;
    return at;
  }

  // The command at `at` (a backslash): `{name, end}`, `name` its letters
  // or its one sign, `end` past it and a star.
  function commandAt(text, at) {
    const m = /^\\([a-zA-Z@]+\*?|.)/.exec(text.slice(at, at + 40));
    return m ? { name: m[1].replace(/\*$/, ""), end: at + m[0].length } : null;
  }

  // Whether [start, end) meets a blocked range: a comment, the preamble,
  // a formula, a construct already read. The ranges may nest (a label in
  // a display) or overlap; they are merged, so the search runs on
  // disjoint ones.
  function blocker(ranges) {
    const sorted = [];
    for (const [start, end] of ranges.slice().sort((a, b) => a[0] - b[0])) {
      const last = sorted[sorted.length - 1];
      if (last && start <= last[1]) last[1] = Math.max(last[1], end);
      else sorted.push([start, end]);
    }
    return {
      meets(start, end) {
        let lo = 0;
        let hi = sorted.length;
        while (lo < hi) {
          const mid = (lo + hi) >> 1;
          if (sorted[mid][1] <= start) lo = mid + 1;
          else hi = mid;
        }
        return lo < sorted.length && sorted[lo][0] < end;
      },
      // the end of the blocked range holding `at`, or -1
      skip(at) {
        let lo = 0;
        let hi = sorted.length;
        while (lo < hi) {
          const mid = (lo + hi) >> 1;
          if (sorted[mid][1] <= at) lo = mid + 1;
          else hi = mid;
        }
        return lo < sorted.length && sorted[lo][0] <= at ? sorted[lo][1] : -1;
      },
    };
  }

  // The structure the reader read, rendered: `items` added to, `taken`
  // the ranges no inline reading enters (a reference whole, a heading's
  // marks).
  function structure(text, format, given, items, taken, quiet) {
    if (given.title) items.push({ start: given.title[0], end: given.title[1], cls: "mk-h mk-title" });
    const hide = (start, end, put, cls) => {
      if (end > start || put) items.push({ start, end, hide: true, put: put || "", cls: cls || "" });
      taken.push([start, end]);
    };
    const style = (start, end, cls) => {
      if (end > start && cls) items.push({ start, end, cls });
    };
    for (const h of given.sections || []) {
      const [start, end] = h.span;
      const cls = "mk-h mk-h" + Math.min(Math.max(h.level || 1, 1), 4);
      const source = text.slice(start, end);
      // a title that numbers itself (`# 2. The Laws`) is not numbered again
      const words = source.replace(/^(?:\\[a-z]+\*?(?:\[[^\]]*\])?\{|#+[ \t]*)/, "");
      const number = h.number && !numbersItself(words) ? h.number + " " : "";
      if (source[0] === "\\") {
        const command = commandAt(text, start);
        let open = command ? command.end : start;
        if (text[open] === "[") open = Math.max(open, groupEnd(text, open, end));
        if (text[open] !== "{") continue;
        const close = groupEnd(text, open, end);
        if (close < 0) continue;
        hide(start, open + 1, number, cls);
        style(open + 1, close - 1, cls);
        hide(close - 1, close);
      } else {
        const marks = /^(#+)[ \t]*/.exec(source);
        const lead = marks ? marks[0].length : 0;
        if (lead) hide(start, start + lead, number, cls);
        // a setext heading's underline
        const under = /\n[ \t]*(=+|-+)[ \t]*$/.exec(source);
        style(start + lead, under ? start + under.index : end, cls);
        if (under) hide(start + under.index + 1, end);
      }
    }
    // the lists' items numbered as they nest
    const lists = [];
    for (const e of given.environments || []) {
      const [start, end] = e.span;
      const [bodyStart, bodyEnd] = e.body;
      if (e.kind === "display") continue;
      const source = text.slice(start, bodyStart);
      const latex = source.startsWith("\\begin");
      const closing = text.slice(bodyEnd, end);
      if (e.kind === "verbatim") {
        style(bodyStart, bodyEnd, "mk-tt");
        taken.push([bodyStart, bodyEnd]);
        if (latex || /^(`{3,}|~{3,})/.test(source)) {
          hide(start, bodyStart);
          hide(bodyEnd, end);
        }
        continue;
      }
      if (!latex) continue;
      const head = /^\\begin\{[^}]*\}/.exec(source);
      const opened = start + head[0].length;
      const word = e.printed || capital(e.meaning || e.name);
      const closed = closing.startsWith("\\end") ? end : -1;
      if (e.kind === "statement" || e.kind === "proof") {
        const proof = e.kind === "proof";
        const cls = proof ? "mk-i" : "mk-b";
        // its title where the reader found it: amsthm's `[title]`, a
        // tcolorbox's `{title}`, read in place
        const title = (e.title || "").trim();
        const at = title ? text.indexOf(title, opened) : -1;
        if (at >= 0 && at + title.length <= bodyStart) {
          hide(start, at, proof ? "" : word + " (", cls);
          style(at, at + title.length, proof ? "mk-i" : "");
          hide(at + title.length, bodyStart, proof ? "." : ").", cls);
        } else hide(start, bodyStart, word + ".", cls);
        if (closed > 0) hide(bodyEnd, end, proof ? "∎" : "", proof ? "mk-qed" : "");
        continue;
      }
      if (e.kind === "list") {
        lists.push(e);
        hide(start, bodyStart);
        if (text[bodyStart] === "[") hide(bodyStart, Math.max(bodyStart, groupEnd(text, bodyStart, bodyEnd)));
        if (closed > 0) hide(bodyEnd, end);
        continue;
      }
      if (e.name === "abstract") {
        hide(start, bodyStart, "Abstract", "mk-b mk-abstract");
        if (closed > 0) hide(bodyEnd, end);
        continue;
      }
      if (/tabular|array|matrix/.test(e.name)) {
        style(start, end, "mk-tt");
        continue;
      }
      // a frame, a float, a wrapper: its opening and closing print nothing,
      // its options and the arguments it takes with them; a group after
      // those is the paper's words
      let after = bodyStart;
      let braces = ENVIRONMENT_ARGUMENTS[e.name] || 0;
      while (text[after] === "[" || (text[after] === "{" && braces > 0)) {
        const next = groupEnd(text, after, bodyEnd);
        if (next < 0) break;
        if (text[after] === "{") braces -= 1;
        after = next;
      }
      hide(start, after);
      if (closed > 0) hide(bodyEnd, end);
    }
    items.push(...listItems(text, lists, taken, quiet));
    for (const c of given.citations || []) {
      const [start, end] = c.span;
      const source = text.slice(start, end);
      if (source[0] !== "\\") {
        style(start, end, "mk-cite");
        continue;
      }
      const command = commandAt(text, start);
      if (!command) continue;
      const groups = [];
      for (let at = command.end; at < end && (text[at] === "[" || text[at] === "{");) {
        const next = groupEnd(text, at, end);
        if (next < 0) break;
        groups.push([at, next]);
        at = next;
      }
      const keys = groups[groups.length - 1];
      if (!keys || text[keys[0]] !== "{") continue;
      const textual = TEXTUAL_RE.test(command.name);
      const notes = groups.slice(0, -1);
      // `[pre][post]{keys}` read in its order: [pre post, keys]
      hide(start, (notes[0] || keys)[0] + 1, textual ? "" : "[", "mk-cite");
      notes.forEach(([from, to], k) => {
        style(from + 1, to - 1, "mk-cite");
        const next = notes[k + 1] || keys;
        hide(to - 1, next[0] + 1, next === keys ? (textual ? " " : ", ") : " ", "mk-cite");
      });
      style(keys[0] + 1, keys[1] - 1, "mk-cite");
      hide(keys[1] - 1, keys[1], textual ? "" : "]", "mk-cite");
      taken.push([start, start + 1]);
    }
    const numbered = new Map((given.sections || []).filter((h) => h.label && h.number).map((h) => [h.label, h.number]));
    for (const r of given.references || []) {
      const [start, end] = r.span;
      const source = text.slice(start, end);
      if (source[0] !== "\\") {
        style(start, end, "mk-ref");
        continue;
      }
      const open = source.indexOf("{");
      if (open < 0) continue;
      const eq = r.command === "eqref";
      if (r.targets.length && r.targets.every((t) => numbered.has(t))) {
        // `\\ref` prints the number alone (`Section~\\ref{x}`); `\\cref`
        // and `\\autoref` name what it is
        const named = /^(?:c|C|auto)ref$/.test(r.command || "");
        const numbers = r.targets.map((t) => (named ? "§" : "") + numbered.get(t)).join(", ");
        hide(start, end, numbers, "mk-ref");
        continue;
      }
      hide(start, start + open + 1, eq ? "(" : "", "mk-ref");
      style(start + open + 1, end - 1, "mk-ref");
      hide(end - 1, end, eq ? ")" : "", "mk-ref");
    }
    (given.footnotes || []).forEach((n, k) => {
      const [start, end] = n.span;
      const [inside, insideEnd] = n.text;
      // its number in the paper, else in this text
      const mark = superscript(n.number || k + 1);
      hide(start, inside, mark + " ", "mk-fnm");
      style(inside, insideEnd, "mk-fn");
      hide(insideEnd, end);
    });
  }

  function capital(word) {
    const w = String(word || "").replace(/\*$/, "");
    return w ? w[0].toUpperCase() + w.slice(1) : "";
  }
  function superscript(n) {
    return String(n).replace(/[0-9]/g, (d) => "⁰¹²³⁴⁵⁶⁷⁸⁹"[d]);
  }

  // Each `\item` of the lists given, put as its list's mark: a bullet, a
  // number in the list's label (`label=(\roman*)`, enumitem's) or its
  // depth's, or the item's own `[label]`, bold.
  function listItems(text, lists, taken, quiet) {
    const out = [];
    const inside = (e, other) => other !== e && other.span[0] <= e.span[0] && e.span[1] <= other.span[1];
    for (const e of lists) {
      const depth = lists.filter((other) => inside(e, other) && other.name === "enumerate").length;
      const nested = lists.filter((other) => other !== e && inside(other, e)).map((other) => other.span);
      const options = text[e.body[0]] === "[" ? text.slice(e.body[0], Math.max(e.body[0], groupEnd(text, e.body[0], e.body[1]))) : "";
      const label = (/label\s*=\s*\{?([^,\]}]*\\(?:arabic|roman|Roman|alph|Alph)\*[^,\]}]*)/.exec(options) || [])[1] || DEPTH_LABELS[Math.min(depth, 3)];
      const ITEM_RE = /\\item(?![a-zA-Z@])\s*/g;
      ITEM_RE.lastIndex = e.body[0];
      let n = 0;
      for (let m = ITEM_RE.exec(text); m && m.index < e.body[1]; m = ITEM_RE.exec(text)) {
        if (nested.some(([s, t]) => s <= m.index && m.index < t)) continue;
        // commented out, in a conditional's false branch, in a formula
        if (quiet && quiet.meets(m.index, m.index + 5)) continue;
        n += 1;
        const at = m.index;
        const after = at + m[0].length;
        taken.push([at, after]);
        if (text[after] === "[") {
          const close = groupEnd(text, after, e.body[1]);
          if (close > after) {
            out.push({ start: at, end: after + 1, hide: true, put: "", cls: "" });
            out.push({ start: after + 1, end: close - 1, cls: "mk-b" });
            out.push({ start: close - 1, end: close, hide: true, put: " ", cls: "" });
            taken.push([after, close]);
            continue;
          }
        }
        const mark = e.name === "enumerate"
          ? label.replace(/\\(arabic|roman|Roman|alph|Alph)\*/, (_, kind) => NUMBERING[kind](n)).replace(/[{}]/g, "")
          : "•";
        out.push({ start: at, end: after, hide: true, put: mark + " ", cls: "mk-item" });
      }
    }
    return out;
  }

  // The inline markup outside `blocked`: LaTeX's commands, signs and
  // ligatures, and in Markdown its own marks.
  function inline(text, format, blocked, items) {
    const markdown = format === "markdown";
    const hide = (start, end, put, cls) => items.push({ start, end, hide: true, put: put || "", cls: cls || "" });
    const style = (start, end, cls) => {
      if (end > start && cls) items.push({ start, end, cls });
    };
    for (let i = 0; i < text.length; i += 1) {
      const skipped = blocked.skip(i);
      if (skipped > i) {
        i = skipped - 1;
        continue;
      }
      const c = text[i];
      if (c === "\\") {
        const command = commandAt(text, i);
        if (!command) continue;
        const name = command.name;
        if (ESCAPED.has(name)) {
          hide(i, i + 1);
          i += 1;
          continue;
        }
        if (Object.hasOwn(SPACES, name)) {
          // a line break `\\` and its `[1em]`
          let end = command.end;
          if (name === "\\" && text[end] === "[") end = Math.max(end, groupEnd(text, end, text.length));
          hide(i, end, SPACES[name]);
          i = end - 1;
          continue;
        }
        if (Object.hasOwn(STYLES, name) && text[command.end] === "{") {
          const close = groupEnd(text, command.end, text.length);
          if (close > command.end && !blocked.meets(close - 1, close)) {
            const [open, shut] = AROUND[name] || ["", ""];
            hide(i, command.end + 1, open);
            style(command.end + 1, close - 1, STYLES[name]);
            hide(close - 1, close, shut);
            i = command.end;
            continue;
          }
        }
        // a link: its words shown, its address not
        if ((name === "href" || name === "url") && text[command.end] === "{") {
          const first = groupEnd(text, command.end, text.length);
          const words = name === "href" && text[first] === "{" ? [first, groupEnd(text, first, text.length)] : [command.end, first];
          if (first > 0 && words[1] > words[0]) {
            hide(i, words[0] + 1);
            style(words[0] + 1, words[1] - 1, "mk-a");
            hide(words[1] - 1, words[1]);
            i = words[1] - 1;
            continue;
          }
        }
        // a conditional's opening (`\\ifexpository`): its `\\else` and
        // `\\fi` print nothing, as the reader reads them
        if (/^if[a-zA-Z@]+$/.test(name) && name !== "iff") {
          const end = spaced(text, command.end);
          hide(i, end);
          i = end - 1;
          continue;
        }
        if ((name === "part" || name === "chapter") && text[command.end] === "{") {
          const close = groupEnd(text, command.end, text.length);
          if (close > command.end) {
            hide(i, command.end + 1, "", "mk-h mk-h1");
            style(command.end + 1, close - 1, "mk-h mk-h1");
            hide(close - 1, close);
            i = command.end;
            continue;
          }
        }
        // a footnote of the paper's own (`\\freefootnote`), unnumbered
        if (/footnote/i.test(name) && text[command.end] === "{") {
          const close = groupEnd(text, command.end, text.length);
          if (close > command.end) {
            hide(i, command.end + 1, "* ", "mk-fnm");
            style(command.end + 1, close - 1, "mk-fn");
            hide(close - 1, close);
            i = command.end;
            continue;
          }
        }
        // a figure's file, named
        if (name === "includegraphics") {
          let end = command.end;
          while (text[end] === "[" || text[end] === "{") {
            const next = groupEnd(text, end, text.length);
            if (next < 0) break;
            end = next;
          }
          const file = /\{([^{}]*)\}$/.exec(text.slice(command.end, end));
          hide(i, end, "[figure: " + (file ? file[1].split("/").pop() : "") + "]", "mk-cap");
          i = end - 1;
          continue;
        }
        if (SILENT.has(name)) {
          let end = command.end;
          while (text[end] === "{" || text[end] === "[") {
            const next = groupEnd(text, end, text.length);
            if (next < 0) break;
            end = next;
          }
          hide(i, end);
          i = end - 1;
          continue;
        }
        if (Object.hasOwn(WORDS, name)) {
          // `\ldots{}`, or a control word's following spaces, as TeX eats them
          let end = /^[a-zA-Z@]/.test(name) ? spaced(text, command.end) : command.end;
          if (text.slice(end, end + 2) === "{}") end += 2;
          hide(i, end, WORDS[name]);
          i = end - 1;
          continue;
        }
        i = command.end - 1;
        continue;
      }
      // a bare group's braces print nothing, a font switch at its start
      // styles it (TeX's own reading; Markdown prints its braces); after a
      // construct's hidden marks (`\\begin{quote}`) it is no argument
      if (!markdown && c === "{" && (!/[a-zA-Z@*\]}]/.test(text[i - 1] || " ") || blocked.meets(i - 1, i))) {
        const close = groupEnd(text, i, text.length);
        if (close > i && !blocked.meets(close - 1, close)) {
          const switched = /^\\([a-zA-Z]+)(?![a-zA-Z])\s*/.exec(text.slice(i + 1, i + 40));
          const lead = switched && Object.hasOwn(SWITCHES, switched[1]) ? switched[0].length : 0;
          hide(i, i + 1 + lead);
          if (lead) style(i + 1 + lead, close - 1, SWITCHES[switched[1]]);
          hide(close - 1, close);
          i += lead;
          continue;
        }
      }
      if (!markdown && c === "~") {
        hide(i, i + 1, " ");
        continue;
      }
      if (c === "-" && text[i + 1] === "-") {
        const em = text[i + 2] === "-";
        const lineStart = text.lastIndexOf("\n", i) + 1;
        const lineEnd = text.indexOf("\n", i);
        // a rule (`---` alone on its line) is no dash
        if (/^[ \t]*-+[ \t]*$/.test(text.slice(lineStart, lineEnd < 0 ? text.length : lineEnd))) {
          i = (lineEnd < 0 ? text.length : lineEnd) - 1;
          continue;
        }
        hide(i, i + (em ? 3 : 2), em ? "—" : "–");
        i += em ? 2 : 1;
        continue;
      }
      if (!markdown && c === "`") {
        const double = text[i + 1] === "`";
        hide(i, i + (double ? 2 : 1), double ? "“" : "‘");
        if (double) i += 1;
        continue;
      }
      if (markdown && c === '"') {
        // pandoc's smart quotes: an opening one after a space or a bracket
        hide(i, i + 1, /[\s([{\u2014\u2013-]/.test(text[i - 1] || " ") ? "“" : "”");
        continue;
      }
      if (c === "'" && text[i + 1] === "'") {
        hide(i, i + 2, "”");
        i += 1;
        continue;
      }
    }
  }

  // Markdown's own marks: emphasis and strong (pandoc's flanking rule: the
  // opener before a non-space, the closer after one; `_` never inside a
  // word), code, strike, links, list and quote marks, a table's rows.
  function markdownMarks(text, blocked, items) {
    const hide = (start, end, put, cls) => items.push({ start, end, hide: true, put: put || "", cls: cls || "" });
    const style = (start, end, cls) => {
      if (end > start) items.push({ start, end, cls });
    };
    const taken = [];
    const free = (start, end) => !blocked.meets(start, end) && !taken.some(([s, t]) => s < end && start < t);
    const pairs = [
      [/`([^`\n]+)`/g, 1, "mk-tt"],
      [/\*\*(?=\S)([^\n]*?\S)\*\*/g, 2, "mk-b"],
      [/(?<![\w_])__(?=\S)([^\n]*?\S)__(?![\w_])/g, 2, "mk-b"],
      [/~~(?=\S)([^\n]*?\S)~~/g, 2, "mk-s"],
      [/(?<![*\\])\*(?=[^\s*])([^\n*]*?[^\s*\\])\*(?!\*)/g, 1, "mk-i"],
      [/(?<![\w_\\])_(?=[^\s_])([^\n_]*?[^\s_\\])_(?![\w_])/g, 1, "mk-i"],
    ];
    for (const [pattern, n, cls] of pairs) {
      for (const m of text.matchAll(pattern)) {
        const start = m.index;
        const end = start + m[0].length;
        // the marks must be free; a formula inside a strong span is fine
        if (!free(start, start + n) || !free(end - n, end)) continue;
        hide(start, start + n);
        style(start + n, end - n, cls);
        hide(end - n, end);
        taken.push([start, start + n], [end - n, end]);
        if (cls === "mk-tt") taken.push([start, end]);
      }
    }
    for (const m of text.matchAll(/(?<!!)\[([^\]\n]+)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g)) {
      const start = m.index;
      const end = start + m[0].length;
      if (!free(start, start + 1) || m[1][0] === "@") continue;
      const close = start + 1 + m[1].length;
      hide(start, start + 1);
      style(start + 1, close, "mk-a");
      hide(close, end);
    }
    let at = 0;
    for (const line of text.split("\n")) {
      const list = /^([ \t]*)([-*+])[ \t]+/.exec(line);
      const quote = /^[ \t]*(>[ \t]?)+/.exec(line);
      const fence = /^[ \t]*:::/.test(line);
      if (fence && free(at, at + line.length)) hide(at, at + line.length);
      else if (list && free(at + list[1].length, at + list[1].length + 1)) hide(at + list[1].length, at + list[0].length, "• ", "mk-item");
      else if (quote && free(at, at + quote[0].length)) {
        hide(at, at + quote[0].length);
        style(at + quote[0].length, at + line.length, "mk-q");
      } else if (/^[ \t]*\|/.test(line) && free(at, at + 1)) style(at, at + line.length, "mk-tt");
      at += line.length + 1;
    }
  }

  // A Markdown text's code spans (`` `x` ``) outside `blocked`.
  function codeSpans(text, blocked) {
    const out = [];
    for (const m of text.matchAll(/`([^`\n]+)`/g)) {
      const span = [m.index, m.index + m[0].length];
      if (!blocked.meets(span[0], span[0] + 1)) out.push(span);
    }
    return out;
  }

  // Every item of a file's text. `hidden` the ranges that print nothing
  // (the text layer's hidden runs), `formulas` the formulas' `[start,
  // end]`, `given` the reader's structure in this file's offsets.
  function find(text, format, hidden, formulas, given) {
    const items = [];
    const taken = [];
    structure(text, format, given || {}, items, taken, blocker([...(hidden || []), ...(formulas || [])]));
    // a reference or a citation inside a formula is the formula's
    const maths = blocker(formulas || []);
    for (let k = items.length - 1; k >= 0; k -= 1) if (items[k].hide && maths.meets(items[k].start, items[k].end)) items.splice(k, 1);
    // a construct's own marks read once: no inline reading enters them,
    // nor anything that prints nothing, nor a formula
    const marks = items.filter((item) => item.hide).map((item) => [item.start, item.end]);
    const blocked = [...(hidden || []), ...(formulas || []), ...taken, ...marks];
    if (format === "markdown") {
      // code prints as it is written: Markdown's own marks read it, as
      // code, and nothing else does
      const base = blocker(blocked);
      inline(text, format, blocker([...blocked, ...codeSpans(text, base)]), items);
      markdownMarks(text, base, items);
    } else inline(text, format, blocker(blocked), items);
    return items.sort((a, b) => a.start - b.start || a.end - b.end);
  }

  // Each character of a text that prints nothing when rendered: under a
  // hidden range, or an item hidden with nothing put in its place.
  function silent(length, hidden, items) {
    const mask = new Uint8Array(length);
    for (const [start, end] of hidden || []) mask.fill(1, Math.max(0, start), Math.min(end, length));
    for (const item of items) if (item.hide && !item.put) mask.fill(1, item.start, item.end);
    return mask;
  }

  // An excerpt's structure, where no reader read it (a claim's words on
  // the card, a quote, a row in a list): its citations, references and
  // footnotes found by their commands.
  function excerptStructure(text) {
    const given = { citations: [], references: [], footnotes: [] };
    for (const m of text.matchAll(/\\([a-zA-Z]*cite[a-zA-Z]*)\*?(?:\[[^\]]*\]){0,2}\{[^{}]*\}/g)) given.citations.push({ span: [m.index, m.index + m[0].length], command: m[1] });
    for (const m of text.matchAll(/\\((?:eq|c|C|auto|name|page|v|V)?ref)\*?\{([^{}]*)\}/g)) given.references.push({ span: [m.index, m.index + m[0].length], command: m[1], targets: m[2].split(",") });
    for (const m of text.matchAll(/\\footnote\{/g)) {
      const end = groupEnd(text, m.index + m[0].length - 1, text.length);
      if (end > 0) given.footnotes.push({ span: [m.index, end], text: [m.index + m[0].length, end - 1] });
    }
    return given;
  }

  // An excerpt as HTML, its markup rendered: `formulas` its formulas
  // (`maths.find`), `formulaHtml` one rendered, or null where it stays its
  // source. Null where nothing in it renders.
  function html(text, format, formulas, formulaHtml) {
    const words = String(text);
    const drawn = (formulas || []).map((f) => ({ ...f, html: formulaHtml(f) })).filter((f) => f.html !== null);
    const items = find(words, format, [], (formulas || []).map((f) => [f.start, f.end]), excerptStructure(words));
    if (!items.length && !drawn.length) return null;
    const cuts = new Set([0, words.length]);
    for (const x of [...items, ...drawn]) {
      cuts.add(x.start);
      cuts.add(x.end);
    }
    const points = [...cuts].filter((at) => at >= 0 && at <= words.length).sort((a, b) => a - b);
    const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
    const wrap = (classes, body) => (classes.length ? '<span class="' + classes.join(" ") + '">' + body + "</span>" : body);
    let out = "";
    for (let k = 0; k + 1 < points.length; k += 1) {
      const start = points[k];
      const end = points[k + 1];
      const styles = items.filter((x) => !x.hide && x.cls && x.start <= start && end <= x.end).map((x) => x.cls);
      const formula = drawn.find((f) => f.start <= start && end <= f.end);
      if (formula) {
        if (formula.start === start) out += wrap(styles, formula.html);
        continue;
      }
      const hidden = items.find((x) => x.hide && x.start <= start && end <= x.end);
      if (hidden) {
        if (hidden.start === start && hidden.put) out += wrap([...styles, ...(hidden.cls ? [hidden.cls] : [])], esc(hidden.put));
        continue;
      }
      out += wrap(styles, esc(words.slice(start, end)));
    }
    return out;
  }

  // The words [start, end) of a text print, as one line: what prints
  // nothing (`mask`, from `silent`) left out, a hidden mark as what it
  // puts, a footnote (its mark and its words) left out. A header's title.
  function printed(text, start, end, mask, items) {
    const note = items.filter((x) => x.cls === "mk-fn" || x.cls === "mk-fnm");
    let out = "";
    for (let i = start; i < end; i += 1) {
      if (note.some((x) => x.start <= i && i < x.end)) continue;
      const hidden = items.find((x) => x.hide && x.start <= i && i < x.end);
      if (hidden) {
        if (hidden.start === i) out += hidden.put;
        continue;
      }
      if (!mask[i]) out += text[i];
    }
    return out.replace(/\s+/g, " ").trim();
  }

  return { find, silent, roman, html, printed, numbersItself };
});
