import assert from "node:assert/strict";
import test from "node:test";

import { runWorkspaceRestoreTransaction } from "../dist/workspaceRestoreTransaction.js";

test("restored API health failure rolls back before restarting the original Workspace", async () => {
  const calls = [];
  let startAttempt = 0;
  const healthFailure = new Error("restored API health check failed");

  await assert.rejects(
    runWorkspaceRestoreTransaction("restore-token", 8765, {
      stopSidecar: async () => {
        calls.push("stop");
      },
      commit: async (token) => {
        calls.push(`commit:${token}`);
      },
      startAndVerifyHealth: async (port) => {
        startAttempt += 1;
        calls.push(`start-and-health:${port}:${startAttempt}`);
        if (startAttempt === 1) throw healthFailure;
      },
      finalize: async (token) => {
        calls.push(`finalize:${token}`);
      },
      rollback: async (token) => {
        calls.push(`rollback:${token}`);
      },
      cancel: async (token) => {
        calls.push(`cancel:${token}`);
      },
      rollbackFailed: (error) => {
        throw error;
      },
      restartFailed: (error) => {
        throw error;
      },
    }),
    healthFailure,
  );

  assert.deepEqual(calls, [
    "stop",
    "commit:restore-token",
    "start-and-health:8765:1",
    "stop",
    "rollback:restore-token",
    "start-and-health:8765:2",
  ]);
});
