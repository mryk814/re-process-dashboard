import assert from "node:assert/strict";
import test from "node:test";
import { navigationContractFromSource } from "./check-current-contract.mjs";

test("navigation contract parser reads views, queries, and unknown-view fallback", () => {
  const contract = navigationContractFromSource(`
    export const WORKBENCH_VIEWS = ["project", "candidates"] as const;
    const first = params.get("view");
    const second = params.has("activity");
    const normalized = VIEW_SET.has(requestedView) ? requestedView : "project";
  `);
  assert.deepEqual(contract.views, ["project", "candidates"]);
  assert.deepEqual(contract.queries, ["activity", "view"]);
  assert.equal(contract.fallback, "project");
});

test("navigation contract parser leaves an absent fallback observable", () => {
  const contract = navigationContractFromSource(`
    export const WORKBENCH_VIEWS = ["project"] as const;
    const requested = params.get("view");
  `);
  assert.equal(contract.fallback, undefined);
});
