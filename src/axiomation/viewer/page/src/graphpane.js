// The local graph, drawn: `graph.js`'s model
// laid out by ELK (the bundled build, in the page's thread), nodes and
// ELK's own edge routes drawn as SVG, so no edge passes behind a node. Two channels on a node: the border is the role, the fill the
// standing where the block holds a claim; a glyph marks a block with a
// claim established, another one that uses an unsupported claim. An
// edge's colour is its kind, its line which way reliance runs as the
// selection sees it (the palette's `STREAM`); a person's edge says so.
// The selection glows, an edit's reach rings.
//
// The page owns the state; this module draws what it is given and reports
// what was clicked and hovered (`hooks`).
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else (root.AxView = root.AxView || {}).graphpane = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const SVG = "http://www.w3.org/2000/svg";
  // ELK's routing: splines, routed around the nodes
  const ROUTING = {
    "elk.edgeRouting": "SPLINES",
    "elk.layered.spacing.edgeNodeBetweenLayers": "14",
    "elk.spacing.edgeEdge": "8",
  };

  const PAD = 18;
  const PER_CHAR = 6.9;

  function escape(text) {
    return String(text).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  }

  // A node's look: `what` the page's names (`labelOf`, `blockOf`,
  // `groupOf`), the block's standing, its glyphs (`M.glyphs`: a claim
  // established, an unsupported claim used).
  function nodeLook(P, M, node, what) {
    if (node.type === "more") return { label: "+" + node.count + " more", border: P.SURFACE.dim, fill: P.CHROME, fillOpacity: 1 };
    if (node.type === "gone") return { label: what.labelOf(node.id), border: P.SURFACE.dim, fill: P.GROUND, fillOpacity: 1 };
    const block = what.blockOf(node.id);
    const group = what.groupOf(node.id);
    let label = what.labelOf(node.id);
    if (node.type === "external") return { label, border: P.EXTERNAL.border, fill: P.EXTERNAL.body, fillOpacity: 1 };
    if (group) return { label: node.type === "group" ? label + " · " + group.blocks.length : label, border: P.group(group.kind).stroke, fill: P.CHROME, fillOpacity: 1 };
    const look = P.block(block);
    const standing = what.standing.get(node.id);
    const marks = M.glyphs(node.id, what);
    if (marks.length) label = marks.map((g) => P.GLYPH[g]).join("") + " " + label;
    return standing
      ? { label, border: look.border, fill: (P.TONE[standing] || P.standing(standing)).colour, fillOpacity: 0.3 }
      : { label, border: look.border, fill: look.body, fillOpacity: 1 };
  }

  // A label in at most two lines within `width`, cut anywhere (handles
  // have no spaces), the second ending in … where it does not fit.
  function lines(label, width, perChar = PER_CHAR) {
    const fit = Math.max(4, Math.floor(width / perChar));
    if (label.length <= fit) return [label];
    const second = label.slice(fit);
    return [label.slice(0, fit), second.length > fit ? second.slice(0, fit - 1) + "…" : second];
  }

  // An ELK route as an SVG path: a spline's bend points are cubic control
  // points, three a segment; anything else a polyline.
  function routePath(points) {
    let d = "M" + points[0].x + " " + points[0].y;
    if (points.length > 1 && (points.length - 1) % 3 === 0) {
      for (let i = 1; i < points.length; i += 3) d += " C" + [points[i], points[i + 1], points[i + 2]].map((p) => p.x + " " + p.y).join(" ");
    } else for (const p of points.slice(1)) d += " L" + p.x + " " + p.y;
    return d;
  }

  // The pane: `create(container, ELK, P, M, hooks)`, hooks `{select(id),
  // recentre(id), toggle(id), more(side), hoverNode(id|null),
  // hoverEdge(ids|null, event)}`.
  function create(container, ELK, P, M, hooks) {
    const elk = new ELK();
    const svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("class", "graph-svg");
    const view = document.createElementNS(SVG, "g");
    svg.appendChild(view);
    container.appendChild(svg);
    let drawn = 0;
    let model = null;
    let size = { width: 1, height: 1 };
    // the view: a scale and a shift, fitted on each draw, then the user's
    let at = { k: 1, x: 0, y: 0 };
    const apply = () => view.setAttribute("transform", "translate(" + at.x + " " + at.y + ") scale(" + at.k + ")");

    function fit() {
      const box = container.getBoundingClientRect();
      if (!box.width || !box.height) return;
      const k = Math.min(1.15, (box.width - 2 * PAD) / size.width, (box.height - 2 * PAD) / size.height);
      at = { k, x: (box.width - size.width * k) / 2, y: (box.height - size.height * k) / 2 };
      apply();
    }

    // pan by dragging the ground, zoom by the wheel about the pointer
    let drag = null;
    svg.addEventListener("mousedown", (event) => {
      if (event.target.closest(".gnode, .gedge")) return;
      drag = { x: event.clientX - at.x, y: event.clientY - at.y };
      svg.classList.add("panning");
    });
    window.addEventListener("mousemove", (event) => {
      if (!drag) return;
      at.x = event.clientX - drag.x;
      at.y = event.clientY - drag.y;
      apply();
    });
    window.addEventListener("mouseup", () => {
      drag = null;
      svg.classList.remove("panning");
    });
    svg.addEventListener("wheel", (event) => {
      event.preventDefault();
      const box = svg.getBoundingClientRect();
      const px = event.clientX - box.left;
      const py = event.clientY - box.top;
      const k = Math.min(3, Math.max(0.15, at.k * Math.exp(-event.deltaY * 0.0015)));
      at = { k, x: px - ((px - at.x) * k) / at.k, y: py - ((py - at.y) * k) / at.k };
      apply();
    }, { passive: false });

    // a click selects (a group also opens or closes), a double click
    // centres, a "+N more" shows the rest of its side
    svg.addEventListener("click", (event) => {
      const element = event.target.closest(".gnode");
      if (!element || !model) return;
      const node = model.nodes.find((n) => n.id === element.dataset.id);
      if (!node || node.type === "external") return;
      if (node.type === "more") return hooks.more(node.side);
      if (event.detail === 2) return hooks.recentre(node.id);
      if (node.type === "group" || node.type === "open") hooks.toggle(node.id);
      hooks.select(node.id);
    });
    let overNode = null;
    svg.addEventListener("mousemove", (event) => {
      const edge = event.target.closest(".gedge");
      if (edge && edge.dataset.ids) {
        lightEdge(edge.dataset.edge);
        hooks.hoverEdge(edge.dataset.ids.split(",").map(Number), event);
      } else {
        lightEdge(null);
        hooks.hoverEdge(null);
      }
      const element = event.target.closest(".gnode");
      const id = element && element.dataset.type !== "more" ? element.dataset.id : null;
      if (id !== overNode) {
        overNode = id;
        hooks.hoverNode(id);
      }
    });
    svg.addEventListener("mouseleave", () => {
      lightEdge(null);
      hooks.hoverEdge(null);
      overNode = null;
      hooks.hoverNode(null);
    });
    function lightEdge(id) {
      for (const element of view.querySelectorAll(".gedge.hover")) element.classList.remove("hover");
      if (id !== null) for (const element of view.querySelectorAll('.gedge[data-edge="' + CSS.escape(id) + '"]')) element.classList.add("hover");
    }

    // ELK's input for `next`, its looks by node.
    function input(next, what) {
      const looks = new Map(next.nodes.map((n) => [n.id, nodeLook(P, M, n, what)]));
      const sizes = new Map([...looks].map(([id, look]) => [id, M.sizeOf(look.label)]));
      const graph = M.seeded(M.elkGraph(next, sizes), what.seed);
      Object.assign(graph.layoutOptions, ROUTING);
      return { graph, looks };
    }

    // Where ELK puts each node of `next`, without drawing: change mode's
    // earlier state, whose positions seed the later one's.
    async function positions(next, what) {
      return M.centres(await elk.layout(input(next, what).graph)).at;
    }

    // Lay out and draw `next` (the model); `what` names and marks it
    // (`labelOf`, `blockOf`, `groupOf`, `standing`, `established`,
    // `usesUnsupported`; in
    // change mode `changeOf`, a node's class, and `seed`, earlier
    // positions). A later call wins: an earlier layout still running is
    // dropped.
    async function draw(next, what) {
      const mine = ++drawn;
      const started = performance.now();
      const { graph, looks } = input(next, what);
      const laid = await elk.layout(graph);
      if (mine !== drawn) return null;

      // every node's box in absolute coordinates; an edge's route is
      // relative to its container
      const origin = new Map([["root", { x: 0, y: 0 }]]);
      const boxes = new Map();
      (function visit(item, dx, dy) {
        for (const child of item.children || []) {
          const x = dx + child.x;
          const y = dy + child.y;
          origin.set(child.id, { x, y });
          boxes.set(child.id, { x, y, w: child.width, h: child.height });
          visit(child, x, y);
        }
      })(laid, 0, 0);

      const out = [];
      // open groups first, under everything
      for (const node of next.nodes.filter((n) => n.type === "open")) {
        const b = boxes.get(node.id);
        const look = looks.get(node.id);
        out.push('<g class="gnode open" data-id="' + escape(node.id) + '" data-type="open"><rect class="body" x="' + b.x + '" y="' + b.y + '" width="' + b.w + '" height="' + b.h + '" rx="7" fill="' + look.border + '" fill-opacity="0.06" stroke="' + look.border + '"/>' +
          '<text class="glabel" x="' + (b.x + 8) + '" y="' + (b.y + 14) + '">' + escape(look.label) + "</text></g>");
      }
      // the edges, ELK's routes; one ELK was not given (a description that
      // yields) a plain curve between the two
      const routed = new Map((laid.edges || []).map((e) => [e.id, e]));
      for (const edge of next.edges) {
        const colour = edge.kind === "more" || edge.stream === "ended" ? P.SURFACE.dim : P.edge(edge.kind).stroke;
        const st = edge.stream === "ended" ? P.GHOST : P.STREAM[edge.stream];
        let d = "";
        const route = routed.get(edge.id);
        if (route && route.sections && route.sections.length) {
          const off = origin.get(route.container || "root") || { x: 0, y: 0 };
          d = route.sections.map((s) => routePath([s.startPoint, ...(s.bendPoints || []), s.endPoint].map((p) => ({ x: p.x + off.x, y: p.y + off.y })))).join(" ");
        } else {
          const a = boxes.get(edge.from);
          const b = boxes.get(edge.to);
          if (!a || !b) continue;
          const [x1, y1, x2, y2] = [a.x + a.w / 2, a.y, b.x + b.w / 2, b.y + b.h];
          d = "M" + x1 + " " + y1 + " C" + x1 + " " + (y1 - 30) + " " + x2 + " " + (y2 + 30) + " " + x2 + " " + y2;
        }
        out.push('<g class="gedge ' + edge.stream + (edge.by === "person" ? " person" : "") + '" data-edge="' + escape(edge.id) + '" data-ids="' + edge.ids.join(",") + '">' +
          '<path class="line" d="' + d + '" stroke="' + colour + '" stroke-width="' + st.width + '" stroke-opacity="' + st.opacity + '"' + (st.dash ? ' stroke-dasharray="' + st.dash + '"' : "") + "/>" +
          '<path class="hit" d="' + d + '"/></g>');
      }
      // the nodes on top
      for (const node of next.nodes.filter((n) => n.type !== "open")) {
        const b = boxes.get(node.id);
        if (!b) continue;
        const look = looks.get(node.id);
        const text = lines(look.label, b.w - 12);
        const cx = b.x + b.w / 2;
        const top = b.y + b.h / 2 - (text.length - 1) * 6.5 + 4;
        const classes = ["gnode", node.type, node.side === "centre" ? "centre" : ""].filter(Boolean).join(" ");
        // change mode: how the block changed, a word above its box
        const change = node.type === "gone" ? "deleted" : what.changeOf ? what.changeOf(node.id) : null;
        const badge = change ? '<text class="gbadge" x="' + b.x + '" y="' + (b.y - 4) + '" fill="' + P.change(change).colour + '">' + escape(change) + "</text>" : "";
        out.push('<g class="' + classes + '" data-id="' + escape(node.id) + '" data-type="' + node.type + '">' +
          '<rect class="ring" x="' + (b.x - 4) + '" y="' + (b.y - 4) + '" width="' + (b.w + 8) + '" height="' + (b.h + 8) + '" rx="9"/>' +
          '<rect class="ground" x="' + b.x + '" y="' + b.y + '" width="' + b.w + '" height="' + b.h + '" rx="6"/>' +
          '<rect class="body" x="' + b.x + '" y="' + b.y + '" width="' + b.w + '" height="' + b.h + '" rx="6" fill="' + look.fill + '" fill-opacity="' + look.fillOpacity + '" stroke="' + look.border + '"/>' +
          text.map((t, i) => '<text x="' + cx + '" y="' + (top + i * 13) + '">' + escape(t) + "</text>").join("") + badge + "</g>");
      }
      view.innerHTML = out.join("");
      model = next;
      size = { width: laid.width || 1, height: laid.height || 1 };
      fit();
      return performance.now() - started;
    }

    // The marks that change without a layout: the selection, the reach,
    // a block hovered in the text.
    function mark({ selected = null, reach = null, hovered = null } = {}) {
      for (const element of view.querySelectorAll(".gnode")) {
        const id = element.dataset.id;
        element.classList.toggle("selected", id === selected);
        element.classList.toggle("hover", id === hovered);
        element.classList.toggle("reach-changes", Boolean(reach && reach.get(id) === "changes"));
        element.classList.toggle("reach-asked", Boolean(reach && reach.get(id) === "asked"));
      }
    }

    return {
      draw,
      positions,
      mark,
      fit,
      resize: fit,
      has: (id) => Boolean(model && model.nodes.some((n) => n.id === id)),
      model: () => model,
    };
  }

  // The legend: each channel and its values, from the palette.
  function legend(P) {
    const swatch = (style) => '<i class="lg-sw" style="' + style + '"></i>';
    const row = (sample, words) => '<div class="lg-row">' + sample + "<span>" + words + "</span></div>";
    const lineSample = (colour, dash, width, opacity = 1) =>
      '<svg class="lg-line" width="34" height="8"><line x1="2" y1="4" x2="32" y2="4" stroke="' + colour + '" stroke-width="' + width + '" stroke-linecap="round"' +
      (dash ? ' stroke-dasharray="' + dash + '"' : "") + ' opacity="' + opacity + '"/></svg>';
    const out = [];
    out.push('<div class="lg-head">border: role</div>');
    for (const r of Object.values(P.ROLE)) out.push(row(swatch("border-color:" + r.border), r.label));
    out.push('<div class="lg-head">fill: standing</div>');
    for (const s of Object.values(P.TONE)) out.push(row(swatch("border-color:" + P.SURFACE.dim + ";background:" + s.colour + "55"), s.label));
    out.push('<div class="lg-head">colour: kind</div>');
    for (const k of Object.values(P.EDGE)) out.push(row(lineSample(k.stroke, "", 2), k.label));
    out.push('<div class="lg-head">line: which way</div>');
    out.push(row(lineSample(P.INK_2, P.STREAM.up.dash, P.STREAM.up.width), "upstream: the selection rests on it"));
    out.push(row(lineSample(P.INK_2, P.STREAM.down.dash, P.STREAM.down.width), "downstream: it rests on the selection"));
    out.push(row(lineSample(P.INK_2, P.STREAM.aside.dash, P.STREAM.aside.width, P.STREAM.aside.opacity), "between neighbours (with “between” on)"));
    out.push('<div class="lg-head">marks</div>');
    out.push(row(swatch("border-style:dashed;border-color:" + P.group("").stroke), "a group, closed (click to open)"));
    out.push(row(swatch("border-style:dashed;border-color:" + P.SURFACE.dim), "+N more: the rest of a side (click to show)"));
    out.push(row('<span class="lg-glyph">' + P.ESTABLISHED_GLYPH + "</span>", "a claim of it established"));
    out.push(row('<span class="lg-glyph">' + P.USES_UNSUPPORTED_GLYPH + "</span>", "uses an unsupported claim"));
    out.push(row(swatch("box-shadow:0 0 0 3px " + P.DECORATION.selected + "88"), "selected"));
    for (const r of Object.values(P.REACH)) out.push(row(swatch("outline:3px " + r.style + " " + r.colour), "reach: " + r.label));
    return out.join("");
  }

  return { ROUTING, create, legend, lines, routePath };
});
