import test from "node:test";
import assert from "node:assert/strict";
import { LatestSaveQueue, rebaseChangedFields } from "../src/latestSaveQueue.ts";

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

test("serializes revisions and marks a delayed older response as superseded", async () => {
  const queue = new LatestSaveQueue();
  const firstResponse = deferred();
  const secondResponse = deferred();
  const expectedRevisions = [];
  const initial = { revision: 1, name: "initial" };

  const first = queue.enqueue("candidate", initial, async (current) => {
    expectedRevisions.push(current.revision);
    return firstResponse.promise;
  });
  const second = queue.enqueue("candidate", initial, async (current) => {
    expectedRevisions.push(current.revision);
    return secondResponse.promise;
  });

  assert.deepEqual(expectedRevisions, []);
  await Promise.resolve();
  assert.deepEqual(expectedRevisions, [1]);
  firstResponse.resolve({ revision: 2, name: "older draft" });
  await first.promise;
  await Promise.resolve();
  assert.equal(first.isLatest(), false);
  assert.deepEqual(expectedRevisions, [1, 2]);
  secondResponse.resolve({ revision: 3, name: "newest draft" });
  assert.deepEqual(await second.promise, { revision: 3, name: "newest draft" });
  assert.equal(second.isLatest(), true);
});

test("recovers a queued newer draft from the authoritative conflict candidate", async () => {
  const queue = new LatestSaveQueue();
  const authoritative = { revision: 5, name: "external" };
  const conflict = Object.assign(new Error("conflict"), { currentCandidate: authoritative });
  const attempts = [];
  const first = queue.enqueue("candidate", { revision: 1, name: "initial" }, async () => {
    throw conflict;
  });
  const second = queue.enqueue(
    "candidate",
    { revision: 1, name: "initial" },
    async (current) => {
      attempts.push(current.revision);
      return { revision: current.revision + 1, name: "newest draft" };
    },
    (error) => error.currentCandidate,
  );

  await assert.rejects(first.promise, /conflict/);
  assert.deepEqual(await second.promise, { revision: 6, name: "newest draft" });
  assert.deepEqual(attempts, [5]);
});

test("rebases only locally changed fields onto the authoritative candidate", () => {
  const base = { name: "base", inputs: { process: { speed: 100, temp: 800 } } };
  const draft = { name: "draft", inputs: { process: { speed: 100, temp: 800 } } };
  const current = { name: "external", inputs: { process: { speed: 120, temp: 800 } } };

  assert.deepEqual(rebaseChangedFields(base, draft, current), {
    name: "draft",
    inputs: { process: { speed: 120, temp: 800 } },
  });
});
