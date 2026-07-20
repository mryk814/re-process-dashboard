import test from "node:test";
import assert from "node:assert/strict";
import {
  emptyInferenceSurface,
  inferenceSurfaceStatus,
  rejectInferenceSurface,
  requestInferenceSurface,
  resolveInferenceSurface,
} from "../src/features/workbench/inferenceSurfaceState.ts";

test("keeps prior data and exposes stale while a new identity is requested", () => {
  let state = requestInferenceSurface(emptyInferenceSurface(), "candidate@1");
  state = resolveInferenceSurface(state, state.requestSequence, "candidate@1", { value: 410 });
  assert.equal(inferenceSurfaceStatus(state), "latest");

  state = requestInferenceSurface(state, "candidate@2");
  assert.deepEqual(state.data, { value: 410 });
  assert.equal(state.pending, true);
  assert.equal(inferenceSurfaceStatus(state), "stale");
});

test("rejects a response that arrives after a newer request", () => {
  const first = requestInferenceSurface(emptyInferenceSurface(), "candidate@1");
  const second = requestInferenceSurface(first, "candidate@2");
  const late = resolveInferenceSurface(second, first.requestSequence, "candidate@1", { value: 410 });
  assert.equal(late, second);

  const current = resolveInferenceSurface(late, second.requestSequence, "candidate@2", { value: 430 });
  assert.deepEqual(current.data, { value: 430 });
  assert.equal(current.currentIdentity, "candidate@2");
  assert.equal(inferenceSurfaceStatus(current), "latest");
});

test("retains stale data when the current refresh fails", () => {
  let state = requestInferenceSurface(emptyInferenceSurface(), "candidate@1");
  state = resolveInferenceSurface(state, state.requestSequence, "candidate@1", { value: 410 });
  state = requestInferenceSurface(state, "candidate@2");
  state = rejectInferenceSurface(state, state.requestSequence, "candidate@2", new Error("offline"));

  assert.deepEqual(state.data, { value: 410 });
  assert.equal(inferenceSurfaceStatus(state), "error");
});

test("reports refreshing when no stale identity is being displayed", () => {
  const initial = requestInferenceSurface(emptyInferenceSurface(), "candidate@1");
  assert.equal(inferenceSurfaceStatus(initial), "refreshing");
  const current = resolveInferenceSurface(initial, initial.requestSequence, "candidate@1", 42);
  const refresh = requestInferenceSurface(current, "candidate@1");
  assert.equal(inferenceSurfaceStatus(refresh), "refreshing");
});
