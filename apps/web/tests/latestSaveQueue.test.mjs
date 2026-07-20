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

test("keeps candidate queues isolated when selection changes", async () => {
  const queue = new LatestSaveQueue();
  const candidateA = deferred();
  const candidateB = deferred();
  const saveA = queue.enqueue("A", { revision: 1 }, () => candidateA.promise);
  const saveB = queue.enqueue("B", { revision: 4 }, () => candidateB.promise);
  candidateB.resolve({ revision: 5, name: "B" });
  assert.deepEqual(await saveB.promise, { revision: 5, name: "B" });
  assert.equal(saveB.isLatest(), true);
  candidateA.resolve({ revision: 2, name: "A" });
  assert.deepEqual(await saveA.promise, { revision: 2, name: "A" });
  assert.equal(saveA.isLatest(), true);
});

test("invalidates an older preview application as soon as a newer draft is queued", async () => {
  const queue = new LatestSaveQueue();
  const first = queue.enqueue("candidate", { revision: 1 }, async () => ({ revision: 2 }));
  await first.promise;
  assert.equal(first.isLatest(), true);
  const second = queue.enqueue("candidate", { revision: 1 }, async (current) => ({ revision: current.revision + 1 }));
  assert.equal(first.isLatest(), false);
  assert.deepEqual(await second.promise, { revision: 3 });
});

test("invalidates an in-flight response during the debounce window", async () => {
  const queue = new LatestSaveQueue();
  const response = deferred();
  const save = queue.enqueue("candidate", { revision: 1 }, () => response.promise);
  await Promise.resolve();
  queue.supersede("candidate");
  response.resolve({ revision: 2 });
  await save.promise;
  assert.equal(save.isLatest(), false);
});
