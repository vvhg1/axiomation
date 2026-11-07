// The page's reads: each the server's answer,
// the command line's own JSON. A kept state's export never changes, but
// what is filed against it does: a state's reads are kept once fetched
// until the store's revision moves (`now`), then `forget` drops them all.
// The states list is asked again each time (a run may have kept a new
// state meanwhile).
(function (root) {
  "use strict";
  const kept = new Map();

  async function get(path, { keep = true } = {}) {
    if (keep && kept.has(path)) return kept.get(path);
    const asked = fetch(path).then(async (response) => {
      const body = await response.json().catch(() => ({ error: response.statusText }));
      if (!response.ok) throw new Error(body.error || response.statusText);
      return body;
    });
    if (keep) {
      kept.set(path, asked);
      asked.catch(() => kept.delete(path));
    }
    return asked;
  }

  const at = (state) => "/api/state/" + encodeURIComponent(state);
  const query = (params) => {
    const found = new URLSearchParams();
    for (const [name, value] of Object.entries(params || {})) {
      for (const one of [].concat(value)) if (one !== undefined && one !== null && one !== false) found.append(name, one === true ? "1" : one);
    }
    const text = found.toString();
    return text ? "?" + text : "";
  };

  root.AxView = root.AxView || {};
  root.AxView.api = {
    // the store's revision and the paper's files against the latest
    // state, asked every few seconds
    now: () => get("/api/now", { keep: false }),
    forget: () => kept.clear(),
    states: () => get("/api/states", { keep: false }),
    // the paper's files against the latest state: an edit not yet updated
    pending: () => get("/api/pending?whole=1", { keep: false }),
    overlays: () => get("/api/overlays"),
    text: (state) => get(at(state) + "/text"),
    open: (state) => get(at(state) + "/open"),
    inbox: (state) => get(at(state) + "/inbox"),
    // `check --json`: the paper's `[gates]` may change while the page is
    // open, so the verdict is asked again with each state's load
    check: (state) => get(at(state) + "/check", { keep: false }),
    show: (state, ref) => get(at(state) + "/show/" + encodeURIComponent(ref) + query({ whole: true, history: true })),
    reach: (state, ref) => get(at(state) + "/reach/" + encodeURIComponent(ref)),
    blocks: (state, filters) => get(at(state) + "/blocks" + query(filters)),
    // between two kept states: kept until the store's revision moves
    changes: (from, to) => get("/api/changes" + query({ from, to, whole: true })),
  };
})(self);
