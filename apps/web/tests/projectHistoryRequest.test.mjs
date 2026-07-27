import assert from "node:assert/strict";
import test from "node:test";
import { isCurrentProjectHistoryRequest } from "../src/features/projects/projectHistoryRequest.ts";

test("a delayed Project history failure cannot replace the next Project state", () => {
  assert.equal(isCurrentProjectHistoryRequest("project-a", "project-b"), false);
  assert.equal(isCurrentProjectHistoryRequest("project-b", "project-b"), true);
  assert.equal(isCurrentProjectHistoryRequest("project-b", "project-b", true), false);
});
