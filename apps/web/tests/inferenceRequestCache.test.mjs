import test from "node:test";
import assert from "node:assert/strict";
import {
  InferenceRequestCache,
  candidateInferencePrefix,
  candidateInputIdentity,
  inferenceRequestKey,
} from "../src/inferenceRequestCache.ts";

test("coalesces identical inference work and keeps shared work alive when one caller aborts", async () => {
  const cache = new InferenceRequestCache();
  const controller = new AbortController();
  let calls = 0;
  let complete;
  const loader = () => {
    calls += 1;
    return new Promise((resolve) => { complete = resolve; });
  };
  const first = cache.get("preview", loader, controller.signal);
  const second = cache.get("preview", loader);

  controller.abort();
  await assert.rejects(first, { name: "AbortError" });
  complete({ value: 42 });

  assert.deepEqual(await second, { value: 42 });
  assert.equal(calls, 1);
  assert.deepEqual(cache.stats(), { hits: 1, misses: 1, coalesced: 1, invalidations: 0 });
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
