import test from "node:test";
import assert from "node:assert/strict";
import { loadSelectedFirstBounded } from "../src/features/workbench/boundedPreviewLoader.ts";

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

test("finishes selected preview before starting a bounded background pool", async () => {
  const selected = deferred();
  const background = deferred();
  const gates = new Map([["A", selected], ["B", background], ["C", background], ["D", background]]);
  const started = [];
  const events = [];
  let active = 0;
  let peak = 0;
  const promise = loadSelectedFirstBounded({
    items: [{ id: "A" }, { id: "B" }, { id: "C" }, { id: "D" }],
    selectedId: "A",
    concurrency: 2,
    onSelectedLoaded: (item, result) => {
      events.push(`publish-${item.id}-${result}`);
    },
    load: async (item) => {
      started.push(item.id);
      events.push(`load-${item.id}`);
      active += 1;
      peak = Math.max(peak, active);
      await (gates.get(item.id)?.promise ?? Promise.resolve());
      active -= 1;
      return `preview-${item.id}`;
    },
  });

  await Promise.resolve();
  assert.deepEqual(started, ["A"]);
  selected.resolve();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(events.slice(0, 4), ["load-A", "publish-A-preview-A", "load-B", "load-C"]);
  assert.deepEqual(started, ["A", "B", "C"]);
  background.resolve();
  const result = await promise;
  assert.deepEqual(result, [["A", "preview-A"], ["B", "preview-B"], ["C", "preview-C"], ["D", "preview-D"]]);
  assert.equal(peak, 2);
});

test("omits failed loads and stops without publishing partial results after abort", async () => {
  const controller = new AbortController();
  const selected = deferred();
  const promise = loadSelectedFirstBounded({
    items: [{ id: "A" }, { id: "B" }],
    selectedId: "A",
    signal: controller.signal,
    load: async (item) => item.id === "A" ? selected.promise : `preview-${item.id}`,
  });
  controller.abort();
  selected.resolve("preview-A");
  assert.deepEqual(await promise, []);

  assert.deepEqual(await loadSelectedFirstBounded({
    items: [{ id: "A" }, { id: "B" }],
    selectedId: "A",
    load: async (item) => item.id === "A" ? null : `preview-${item.id}`,
  }), [["B", "preview-B"]]);
});
