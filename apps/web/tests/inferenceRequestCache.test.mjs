import test from "node:test";
import assert from "node:assert/strict";
import {
  InferenceRequestCache,
  candidateInferencePrefix,
  candidateInferenceChanged,
  candidateInputIdentity,
  inferenceRequestKey,
  mergePreviewEntryIfCurrent,
  shouldRefreshPreviewAfterSave,
} from "../src/inferenceRequestCache.ts";

test("coalesces identical inference work and keeps shared work alive when one caller aborts", async () => {
  const cache = new InferenceRequestCache();
  const controller = new AbortController();
  let calls = 0;
  let complete;
  let upstreamAborts = 0;
  const loader = (signal) => {
    calls += 1;
    signal.addEventListener("abort", () => { upstreamAborts += 1; }, { once: true });
    return new Promise((resolve) => { complete = resolve; });
  };
  const first = cache.get("preview", loader, controller.signal);
  const second = cache.get("preview", loader);

  controller.abort();
  await assert.rejects(first, { name: "AbortError" });
  complete({ value: 42 });

  assert.deepEqual(await second, { value: 42 });
  assert.equal(calls, 1);
  assert.equal(upstreamAborts, 0);
  assert.deepEqual(cache.stats(), { hits: 1, misses: 1, coalesced: 1, invalidations: 0 });
});

test("aborts shared upstream exactly once when every pending caller leaves", async () => {
  const cache = new InferenceRequestCache();
  const firstController = new AbortController();
  const secondController = new AbortController();
  let upstreamAborts = 0;
  const loader = (signal) => new Promise((_resolve, reject) => {
    signal.addEventListener("abort", () => {
      upstreamAborts += 1;
      const error = new Error("upstream aborted");
      error.name = "AbortError";
      reject(error);
    }, { once: true });
  });
  const first = cache.get("curve", loader, firstController.signal);
  const second = cache.get("curve", loader, secondController.signal);

  firstController.abort();
  await assert.rejects(first, { name: "AbortError" });
  assert.equal(upstreamAborts, 0);

  secondController.abort();
  await assert.rejects(second, { name: "AbortError" });
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(upstreamAborts, 1);
});

test("starts fresh work when a request is reopened immediately after every caller leaves", async () => {
  const cache = new InferenceRequestCache();
  const controller = new AbortController();
  let calls = 0;
  const loader = (signal) => new Promise((resolve, reject) => {
    calls += 1;
    if (calls === 2) resolve("fresh");
    signal.addEventListener("abort", () => reject(Object.assign(new Error("aborted"), { name: "AbortError" })), { once: true });
  });
  const abandoned = cache.get("preview", loader, controller.signal);
  controller.abort();
  await assert.rejects(abandoned, { name: "AbortError" });

  assert.equal(await cache.get("preview", loader), "fresh");
  assert.equal(calls, 2);
});

test("invalidation aborts pending upstream work", async () => {
  const cache = new InferenceRequestCache();
  let upstreamAborts = 0;
  const pending = cache.get("project::candidate::preview", (signal) => new Promise((_resolve, reject) => {
    const abort = () => {
      upstreamAborts += 1;
      reject(Object.assign(new Error("aborted"), { name: "AbortError" }));
    };
    if (signal.aborted) abort();
    else signal.addEventListener("abort", abort, { once: true });
  }));

  cache.invalidatePrefix("project::candidate::");
  await assert.rejects(pending, { name: "AbortError" });
  assert.equal(upstreamAborts, 1);
});

test("reset aborts every pending upstream request", async () => {
  const cache = new InferenceRequestCache();
  let upstreamAborts = 0;
  const loader = (signal) => new Promise((_resolve, reject) => {
    const abort = () => {
      upstreamAborts += 1;
      reject(Object.assign(new Error("aborted"), { name: "AbortError" }));
    };
    if (signal.aborted) abort();
    else signal.addEventListener("abort", abort, { once: true });
  });
  const first = cache.get("first", loader);
  const second = cache.get("second", loader);

  cache.reset();
  await Promise.all([assert.rejects(first), assert.rejects(second)]);
  assert.equal(upstreamAborts, 2);
});

test("candidate invalidation is scoped and input identity excludes display fields by construction", async () => {
  const cache = new InferenceRequestCache();
  let calls = 0;
  const load = async () => ++calls;
  const a = inferenceRequestKey("project", "c1", "input-a", "preview");
  const b = inferenceRequestKey("project", "c10", "input-b", "preview");
  assert.equal(await cache.get(a, load), 1);
  assert.equal(await cache.get(b, load), 2);

  cache.invalidatePrefix(candidateInferencePrefix("project", "c1"));

  assert.equal(await cache.get(a, load), 3);
  assert.equal(await cache.get(b, load), 2);
  const inputs = { composition: { C: 0.1 }, process: { speed: 100 }, categorical: {}, heat_pattern: null };
  assert.equal(candidateInputIdentity(inputs), candidateInputIdentity(structuredClone(inputs)));
  assert.equal(
    candidateInputIdentity(inputs),
    candidateInputIdentity({ heat_pattern: null, categorical: {}, process: { speed: 100 }, composition: { C: 0.1 } }),
  );
});

test("a late initial preview cannot overwrite a preview for newer saved inputs", () => {
  const oldInputs = { process: { speed: 100 } };
  const newInputs = { process: { speed: 120 } };
  const oldIdentity = candidateInputIdentity(oldInputs);
  const newIdentity = candidateInputIdentity(newInputs);
  const newer = { canonical_input: newInputs, predictions: { TS: { mean: 500 } } };
  const lateInitial = { canonical_input: oldInputs, predictions: { TS: { mean: 400 } } };
  const current = { candidate: newer };

  const merged = mergePreviewEntryIfCurrent(current, "candidate", oldIdentity, newIdentity, newIdentity, lateInitial);

  assert.equal(merged, current);
  assert.equal(merged.candidate, newer);
});

test("display-only edits preserve pending inference while input edits supersede it", () => {
  const previous = { label: "before", raw: { inputs: { process: { speed: 100 } } } };
  const renamed = { label: "after", raw: { inputs: { process: { speed: 100 } } } };
  const changedInput = { label: "before", raw: { inputs: { process: { speed: 120 } } } };

  assert.equal(candidateInferenceChanged(previous.raw.inputs, renamed.raw.inputs), false);
  assert.equal(candidateInferenceChanged(previous.raw.inputs, changedInput.raw.inputs), true);
});

test("a display-only save refreshes preview when conflict recovery adopts external inputs", () => {
  const baseIdentity = candidateInputIdentity({ process: { speed: 100 } });
  const externalIdentity = candidateInputIdentity({ process: { speed: 140 } });

  assert.equal(shouldRefreshPreviewAfterSave(baseIdentity, baseIdentity, baseIdentity), false);
  assert.equal(shouldRefreshPreviewAfterSave(baseIdentity, externalIdentity, baseIdentity), true);
});
