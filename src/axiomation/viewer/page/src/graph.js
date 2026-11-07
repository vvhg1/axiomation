// The local graph: the selection's
// neighbourhood, what it rests on and what rests on it to a depth, a group
// one node until opened, laid out by ELK's layered algorithm with a block
// below what it rests on.
//
// Pure: the model from the text layer (`neighbourhood`), the `uses`
// reduction, ELK's input (`elkGraph`) and its output as centres
// (`centres`); the page runs ELK and draws with Cytoscape (`graphpane.js`).
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else (root.AxView = root.AxView || {}).graph = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const DEPTHS = [1, 2, 3];
  // the centre's own neighbours drawn a side before the rest are one node
  const CAP = 8;
  const MORE = "more:";
  // a block holding several claims is filled by the one most in need
  // (by its tone, `palette.toneOf`: a claim no argument is owed for needs
  // less than a conjecture)
  const STANDING_ORDER = ["unsupported", "conjectured", "left-unargued", "no-argument-owed", "restated", "self", "justified"];

  function isExternal(id) {
    return id.startsWith("file:") || id === "scope:paper";
  }

  // An edge as reliance runs along it: `[rester, rested]`. A kind in the
  // layer's `rested_on_by_source` has its target rest on its source, any
  // other its source on its target (`queries.RESTED_ON_BY_SOURCE`).
  function reliance(layer, edge) {
    const reversed = (layer.rested_on_by_source || []).includes(edge.kind);
    return reversed ? [edge.target, edge.source] : [edge.source, edge.target];
  }

  // The groups each block and group sits in, shallowest first; a group
  // holds another when it holds all its blocks and is shallower.
  function nesting(layer) {
    const inBlock = new Map();
    const inGroup = new Map();
    const sets = layer.groups.map((g) => new Set(g.blocks));
    const byDepth = layer.groups.map((g, i) => [g, i]).sort((a, b) => (a[0].depth || 0) - (b[0].depth || 0));
    for (const [g, i] of byDepth) {
      for (const id of g.blocks) {
        if (!inBlock.has(id)) inBlock.set(id, []);
        inBlock.get(id).push(g.id);
      }
      inGroup.set(g.id, byDepth
        .filter(([h, j]) => j !== i && (h.depth || 0) < (g.depth || 0) && g.blocks.every((b) => sets[j].has(b)))
        .map(([h]) => h.id));
    }
    return { inBlock, inGroup };
  }

  // What stands for a block or group in the drawing: the shallowest group
  // around it that is not open, else itself.
  function representative(nest, open, ident) {
    const around = nest.inGroup.get(ident) || nest.inBlock.get(ident) || [];
    for (const g of around) if (!open.has(g)) return g;
    return ident;
  }

  // The neighbourhood of `centre` (a block's or group's id): every node
  // within `depth` steps up (what it rests on) or down (what rests on it)
  // along the edges of `kinds` (null: all), a group one node unless open:
  // the groups around the centre are open, and those in `expanded`, which
  // show all their members. Returns `{centre, nodes, edges, hidden}`: a
  // node `{id, type, side, parent, order}` (`type` block, group, open or
  // external; `side` centre, up, down or around), an edge `{id, from, to,
  // kind, stream, ids, by}` from the rester to the rested, `stream` up,
  // down or aside as the selection sees it, its layer edges' indices in
  // `ids`; `hidden` the `uses` edges the reduction dropped, `aside` the
  // edges on neither way (drawn only with `aside`), `capped` per side the
  // neighbours past `cap` (a `more` node and its edge stand for them).
  function neighbourhood(layer, centre, { depth = 1, kinds = null, expanded = new Set(), reduce = true, aside = false, cap = CAP, uncapped = new Set() } = {}) {
    const nest = nesting(layer);
    const groups = new Map(layer.groups.map((g) => [g.id, g]));
    const open = new Set([...expanded].filter((g) => groups.has(g)));
    for (const g of nest.inGroup.get(centre) || nest.inBlock.get(centre) || []) open.add(g);
    const rep = (id) => (isExternal(id) ? id : representative(nest, open, id));

    // the edges of `kinds` between representatives, one per pair and kind
    const merged = new Map();
    layer.edges.forEach((edge, i) => {
      if (kinds && !kinds.includes(edge.kind)) return;
      const [rester, rested] = reliance(layer, edge).map(rep);
      if (rester === rested) return;
      const key = rester + "\u0000" + rested + "\u0000" + edge.kind;
      if (!merged.has(key)) merged.set(key, { from: rester, to: rested, kind: edge.kind, ids: [] });
      merged.get(key).ids.push(i);
    });
    const links = [...merged.values()];

    // an open group at the centre is its members too
    const start = rep(centre);
    const starts = new Set([start]);
    if (groups.has(start) && open.has(start)) for (const b of groups.get(start).blocks) starts.add(rep(b));
    const walk = (forward) => {
      const next = new Map();
      for (const link of links) {
        const [a, b] = forward ? [link.from, link.to] : [link.to, link.from];
        if (!next.has(a)) next.set(a, []);
        next.get(a).push(b);
      }
      const seen = new Set();
      let frontier = [...starts];
      for (let d = 0; d < depth; d += 1) {
        const ahead = [];
        for (const id of frontier) {
          for (const b of next.get(id) || []) {
            if (starts.has(b) || seen.has(b)) continue;
            seen.add(b);
            // a file or the paper rests on nothing and is rested on by
            // everything: an end, never walked through
            if (!isExternal(b)) ahead.push(b);
          }
        }
        frontier = ahead;
      }
      return seen;
    };
    const up = walk(true);
    const down = walk(false);

    const drawn = new Set([...starts, ...up, ...down]);
    const parentOf = (id) => {
      const around = (nest.inGroup.get(id) || nest.inBlock.get(id) || []).filter((g) => open.has(g));
      return around.length ? around[around.length - 1] : null;
    };
    const containersOf = () => {
      const found = new Set();
      for (const id of drawn) for (let p = parentOf(id); p !== null && !found.has(p); p = parentOf(p)) found.add(p);
      return found;
    };
    // a group opened by a click shows all its members, on its side
    for (const g of new Set([...containersOf(), ...drawn])) {
      if (!expanded.has(g) || !groups.has(g)) continue;
      for (const b of groups.get(g).blocks) {
        const member = rep(b);
        if (drawn.has(member)) continue;
        drawn.add(member);
        if (up.has(g)) up.add(member);
        if (down.has(g)) down.add(member);
      }
    }
    const blockOrder = new Map(layer.blocks.map((b, i) => [b.id, i]));
    const groupFirst = new Map(layer.groups.map((g) => [g.id, Math.min(...g.blocks.map((b) => blockOrder.get(b) ?? Infinity))]));
    const orderOf = (id) => blockOrder.get(id) ?? groupFirst.get(id) ?? Infinity;

    // every edge between two drawn nodes, the containers included:
    // upstream where the selection rests on its end through it,
    // downstream where it reaches the selection from below, else aside
    const above = new Set([...starts, ...up]);
    const below = new Set([...starts, ...down]);
    const present = new Set([...drawn, ...containersOf()]);
    let edges = links
      .filter((l) => present.has(l.from) && present.has(l.to))
      .map((l) => ({
        ...l,
        id: l.from + "→" + l.to + "·" + l.kind,
        stream: above.has(l.from) && up.has(l.to) ? "up" : down.has(l.from) && below.has(l.to) ? "down" : "aside",
        by: l.ids.some((i) => layer.edges[i].by === "person") ? "person" : "agent",
      }));
    // an edge between two neighbours, on neither of the selection's ways,
    // only when asked for: most crossings are theirs
    const asideEdges = edges.filter((e) => e.stream === "aside").length;
    if (!aside) edges = edges.filter((e) => e.stream !== "aside");
    // the reduction never hides the selection's own edges: the card lists
    // them, and without them a hub's users hang off each other
    const own = (e) => starts.has(e.from) || starts.has(e.to);
    let hidden = 0;
    if (reduce) {
      const kept = reduced(edges, own);
      hidden = edges.length - kept.length;
      edges = kept;
    }

    // at most `cap` of the centre's own neighbours a side, the nearest in
    // reading order, unless that side is `uncapped`; the rest one node
    const capped = { up: 0, down: 0 };
    if (cap) {
      const centreOrder = orderOf(start);
      for (const side of ["up", "down"]) {
        if (uncapped.has(side)) continue;
        const direct = [...new Set(edges
          .filter((e) => (side === "up" ? starts.has(e.from) && up.has(e.to) : starts.has(e.to) && down.has(e.from)))
          .map((e) => (side === "up" ? e.to : e.from)))];
        // a "+1 more" or "+2 more" saves nothing: those are drawn
        if (direct.length <= cap + 2) continue;
        direct.sort((a, b) => Math.abs(orderOf(a) - centreOrder) - Math.abs(orderOf(b) - centreOrder) || orderOf(a) - orderOf(b));
        for (const id of direct.slice(cap)) drawn.delete(id);
        capped[side] = direct.length - cap;
      }
      if (capped.up || capped.down) {
        // what is left joined to the centre, through edges or a container
        const next = new Map();
        const join = (a, b) => {
          if (!next.has(a)) next.set(a, []);
          if (!next.has(b)) next.set(b, []);
          next.get(a).push(b);
          next.get(b).push(a);
        };
        for (const e of edges) if (drawn.has(e.from) && drawn.has(e.to)) join(e.from, e.to);
        for (const id of drawn) for (let p = parentOf(id); p !== null; p = parentOf(p)) join(id, p);
        const reached = new Set(starts);
        const stack = [...starts];
        while (stack.length) {
          for (const b of next.get(stack.pop()) || []) {
            if (reached.has(b)) continue;
            reached.add(b);
            stack.push(b);
          }
        }
        for (const id of [...drawn]) if (!reached.has(id)) drawn.delete(id);
      }
    }

    const containers = containersOf();
    const typeOf = (id) => (isExternal(id) ? "external" : groups.has(id) ? (containers.has(id) ? "open" : "group") : "block");
    const sideOf = (id) => (starts.has(id) ? "centre" : up.has(id) ? "up" : down.has(id) ? "down" : "around");
    const nodes = [...new Set([...drawn, ...containers])]
      .map((id) => ({ id, type: typeOf(id), side: sideOf(id), parent: parentOf(id), order: orderOf(id) }));
    const shown = new Set(nodes.map((n) => n.id));
    edges = edges.filter((e) => shown.has(e.from) && shown.has(e.to));
    for (const side of ["up", "down"]) {
      if (!capped[side]) continue;
      const id = MORE + side;
      nodes.push({ id, type: "more", side, parent: null, order: side === "up" ? -1 : Infinity, count: capped[side] });
      edges.push({ id, from: side === "up" ? start : id, to: side === "up" ? id : start, kind: "more", stream: side, ids: [], by: "agent" });
    }
    nodes.sort((a, b) => a.order - b.order || (a.id < b.id ? -1 : 1));
    return { centre: start, nodes, edges, hidden, aside: asideEdges, capped };
  }

  // The `uses` edges no other chain of `uses` edges implies: each dropped
  // in turn where its rester still reaches its rested without it, so the
  // reachability stays whole in a cycle too. Other kinds never imply and
  // are never implied; an edge `keep` names stays.
  function reduced(edges, keep = () => false) {
    const uses = edges.filter((e) => e.kind === "uses");
    const out = new Map();
    for (const e of uses) {
      if (!out.has(e.from)) out.set(e.from, new Set());
      out.get(e.from).add(e.to);
    }
    const dropped = new Set();
    for (const e of uses) {
      if (keep(e)) continue;
      out.get(e.from).delete(e.to);
      if (reaches(out, e.from, e.to)) dropped.add(e.id);
      else out.get(e.from).add(e.to);
    }
    return edges.filter((e) => !dropped.has(e.id));
  }

  function reaches(out, from, to) {
    const seen = new Set([from]);
    const stack = [from];
    while (stack.length) {
      for (const next of out.get(stack.pop()) || []) {
        if (next === to) return true;
        if (!seen.has(next)) {
          seen.add(next);
          stack.push(next);
        }
      }
    }
    return false;
  }

  // A block's standing for its fill: the one most in need among its claims,
  // each read by `key` (the page's: its tone).
  function standingOf(layer, key = (s) => s.status) {
    const found = new Map();
    for (const s of layer.standings) {
      const was = found.get(s.block);
      const is = key(s);
      if (was === undefined || STANDING_ORDER.indexOf(is) < STANDING_ORDER.indexOf(was)) found.set(s.block, is);
    }
    return found;
  }

  // The marks before a block's label, in order: `established`
  // where a claim of it is established (the `blocks --established yes`
  // set: any claim, never every one), `uses-unsupported` where it uses an
  // unsupported claim directly (the open view's items). A group has none.
  const GLYPHS = ["established", "uses-unsupported"];
  function glyphs(id, { established = new Set(), usesUnsupported = new Set() } = {}) {
    const found = [];
    if (established.has(id)) found.push("established");
    if (usesUnsupported.has(id)) found.push("uses-unsupported");
    return found;
  }

  // A node's size from its label: about 6.6 px a character at the page's
  // 11 px, within bounds; the label wraps to two lines past the widest.
  const SIZE = { min: 46, max: 200, height: 26, tall: 40, perChar: 7, pad: 18 };
  function sizeOf(label) {
    const width = Math.min(SIZE.max, Math.max(SIZE.min, Math.round(label.length * SIZE.perChar + SIZE.pad)));
    return { width, height: label.length * SIZE.perChar + SIZE.pad > SIZE.max ? SIZE.tall : SIZE.height };
  }

  // ELK's input: the drawn nodes nested in their containers, in reading
  // order (the model order ELK keeps), each edge from the rester to the
  // rested, laid out upward so a block stands below what it rests on; an
  // `about` edge against another between the same two left to Cytoscape.
  // `sizes` maps a node's id to `{width, height}`.
  const OPTIONS = {
    "elk.algorithm": "layered",
    "elk.direction": "UP",
    "elk.hierarchyHandling": "INCLUDE_CHILDREN",
    "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
    "elk.layered.crossingMinimization.forceNodeModelOrder": "false",
    "elk.spacing.nodeNode": "18",
    "elk.layered.spacing.nodeNodeBetweenLayers": "34",
    "elk.spacing.edgeNode": "10",
    "elk.padding": "[top=10,left=10,bottom=10,right=10]",
  };
  const CONTAINER_PADDING = "[top=22,left=8,bottom=8,right=8]";
  function elkGraph(model, sizes) {
    const byId = new Map();
    const root = { id: "root", layoutOptions: { ...OPTIONS }, children: [], edges: [] };
    for (const node of model.nodes) {
      const item = { id: node.id, children: [] };
      if (node.type === "open") item.layoutOptions = { "elk.padding": CONTAINER_PADDING };
      else Object.assign(item, sizes.get(node.id));
      byId.set(node.id, item);
    }
    for (const node of model.nodes) (node.parent && byId.has(node.parent) ? byId.get(node.parent).children : root.children).push(byId.get(node.id));
    for (const item of byId.values()) if (!item.children.length) delete item.children;
    // where a description runs against another edge between the same two,
    // an end's containers counting as it (a proof about the claim it
    // justifies), the other sets the layers
    const parent = new Map(model.nodes.map((n) => [n.id, n.parent]));
    const withContainers = (id) => {
      const found = [];
      for (let at = id; at; at = parent.get(at)) found.push(at);
      return found;
    };
    const against = new Set(model.edges.filter((e) => e.kind !== "about").map((e) => e.to + "\u0000" + e.from));
    const yields = (e) => e.kind === "about" &&
      withContainers(e.from).some((f) => withContainers(e.to).some((t) => against.has(f + "\u0000" + t)));
    root.edges = model.edges.filter((e) => !yields(e)).map((e) => ({ id: e.id, sources: [e.from], targets: [e.to] }));
    return root;
  }

  // ELK's input seeded with earlier positions (change mode): each
  // top-level node with a centre in `positions` starts there, and ELK's
  // interactive cycle breaking, layering, crossing minimisation and node
  // placement keep what they can, so an unchanged node holds still; a node
  // without one starts below the rest.
  const INTERACTIVE = {
    "elk.layered.cycleBreaking.strategy": "INTERACTIVE",
    "elk.layered.layering.strategy": "INTERACTIVE",
    "elk.layered.crossingMinimization.semiInteractive": "true",
    "elk.layered.nodePlacement.strategy": "INTERACTIVE",
  };
  function seeded(input, positions) {
    if (!positions || !positions.size) return input;
    Object.assign(input.layoutOptions, INTERACTIVE);
    const lowest = Math.max(0, ...[...positions.values()].map((p) => p.y)) + 80;
    for (const child of input.children) {
      const at = positions.get(child.id);
      const width = child.width || 0;
      const height = child.height || 0;
      child.x = at ? at.x - width / 2 : 0;
      child.y = at ? at.y - height / 2 : lowest;
    }
    return input;
  }

  // ELK's output as each leaf's centre in absolute coordinates, and each
  // container's box: ELK places a child by its top-left corner within its
  // parent, Cytoscape a node by its centre.
  function centres(laid) {
    const at = new Map();
    const boxes = new Map();
    const visit = (item, dx, dy) => {
      for (const child of item.children || []) {
        const x = dx + child.x;
        const y = dy + child.y;
        if (child.children && child.children.length) {
          boxes.set(child.id, { x, y, width: child.width, height: child.height });
          visit(child, x, y);
        } else at.set(child.id, { x: x + child.width / 2, y: y + child.height / 2 });
      }
    };
    visit(laid, 0, 0);
    return { at, boxes };
  }

  return { DEPTHS, CAP, MORE, INTERACTIVE, seeded, STANDING_ORDER, isExternal, reliance, nesting, neighbourhood, reduced, standingOf, GLYPHS, glyphs, sizeOf, elkGraph, centres };
});
