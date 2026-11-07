// `node --test tests/viewer/`: keeping up with the store: when a poll
// re-reads the page, which state it then shows, and what it carries there
// by node.
const test = require("node:test");
const assert = require("node:assert/strict");

const U = require("../../src/axiomation/viewer/page/src/current.js");

test("a moved revision is read once it holds for one poll, or at once on the page shown again", () => {
  assert.equal(U.step("r1", "r1", null), "none");
  assert.equal(U.step("r2", "r1", null), "wait", "moved: a run may still be appending");
  assert.equal(U.step("r2", "r1", "r2"), "reread", "held for one poll");
  assert.equal(U.step("r3", "r1", "r2"), "wait", "moved again: wait once more");
  assert.equal(U.step("r2", "r1", null, true), "reread", "the page shown again");
  assert.equal(U.step("r1", "r1", null, true), "none");
});

test("a page on the newest state follows a newer one; one on an older state stays", () => {
  const before = [{ id: "a" }, { id: "b" }];
  const after = [{ id: "a" }, { id: "b" }, { id: "c" }];
  assert.equal(U.shownAfter(before, after, "b"), "c");
  assert.equal(U.shownAfter(before, after, "a"), "a");
  assert.equal(U.shownAfter(before, before, "b"), "b");
  assert.equal(U.shownAfter(before, [{ id: "c" }], "a"), "c", "a state no longer kept gives way to the newest");
});

test("ids are carried by node, never as they are: an update shifts block ids", () => {
  // the old state: block paper.md:10:1 is node n5, paper.md:20:1 is n6;
  // the update inserted lines, so n5 is now paper.md:12:1 and the old id
  // paper.md:20:1 names another node
  const nodeOf = new Map([["paper.md:10:1", "n5"], ["paper.md:20:1", "n6"], ["g1", "n9"]]);
  const byNode = new Map([["n5", "paper.md:12:1"], ["n6", "paper.md:22:1"], ["n7", "paper.md:20:1"]]);
  assert.deepEqual(U.carried(["paper.md:10:1"], nodeOf, byNode), ["paper.md:12:1"]);
  assert.deepEqual(U.carried(["paper.md:20:1"], nodeOf, byNode), ["paper.md:22:1"], "not the id's new owner");
  assert.deepEqual(U.carried(["g1", null, undefined, "nosuch"], nodeOf, byNode), [], "a node gone, or nothing selected, is dropped");
});
