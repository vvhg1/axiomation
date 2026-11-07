// Keeping up with the store: what the page does when `/api/now`
// answers, and what of the page a re-read carries to the state it then
// shows. Pure, so `node --test` reaches it; `app.js` does the reading.
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else (root.AxView = root.AxView || {}).current = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // What a poll's revision asks, given the one the page's reads were kept
  // under and the newer one seen at the poll before: "none", the page is
  // current; "wait", it moved, and a run may still be appending; "reread",
  // it held for one poll, or the page was shown again (`atOnce`).
  function step(seen, read, moving, atOnce = false) {
    if (seen === read) return "none";
    if (atOnce || seen === moving) return "reread";
    return "wait";
  }

  // The state a re-read shows: the newest, where the page showed the
  // newest of the states it listed before; else the one shown, while it is
  // still kept. `before` and `after` the states lists, oldest first.
  function shownAfter(before, after, shown) {
    const newest = (list) => (list.length ? list[list.length - 1].id : null);
    if (shown === newest(before) || !after.some((s) => s.id === shown)) return newest(after);
    return shown;
  }

  // Block or group ids of one state carried to another by their nodes:
  // `nodeOf` the old state's id → node, `byNode` the new state's node → id.
  // A block id is a position within one cut, so an id is never carried as
  // it is; one whose node the new state does not hold is dropped.
  function carried(ids, nodeOf, byNode) {
    const found = [];
    for (const id of ids) {
      const node = id === null || id === undefined ? undefined : nodeOf.get(id);
      const now = node === undefined ? undefined : byNode.get(node);
      if (now !== undefined) found.push(now);
    }
    return found;
  }

  return { step, shownAfter, carried };
});
