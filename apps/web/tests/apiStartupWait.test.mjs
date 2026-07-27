import assert from "node:assert/strict";
import test from "node:test";

import {
  apiStartupRetryDelayMs,
  apiStartupWaitMs,
  apiStartupWaitText,
  isApiUnreachable,
  shouldKeepWaitingForApi,
} from "../src/features/workbench/apiStartupWait.ts";

test("a connection that never opened is treated as startup, not as a failure", () => {
  assert.equal(isApiUnreachable({ kind: "network", status: 0 }), true);
  for (const status of [502, 503, 504]) {
    assert.equal(isApiUnreachable({ kind: "server", status }), true);
  }
});

test("a served error is a real failure, not a startup wait", () => {
  assert.equal(isApiUnreachable({ kind: "server", status: 500 }), false);
  assert.equal(isApiUnreachable({ kind: "not_found", status: 404 }), false);
  assert.equal(isApiUnreachable(new Error("boom")), false);
  assert.equal(shouldKeepWaitingForApi({ kind: "server", status: 500 }, 0), false);
});

test("the wait ends after the budget so a real outage still reaches the recovery banner", () => {
  const unreachable = { kind: "network", status: 0 };
  assert.equal(shouldKeepWaitingForApi(unreachable, 0), true);
  assert.equal(shouldKeepWaitingForApi(unreachable, apiStartupWaitMs - 1), true);
  assert.equal(shouldKeepWaitingForApi(unreachable, apiStartupWaitMs), false);
});

test("retries back off instead of hammering the port, and stay bounded", () => {
  const delays = [1, 2, 3, 4, 5, 6, 7].map(apiStartupRetryDelayMs);
  assert.deepEqual(delays.slice(0, 4), [300, 600, 1200, 2400]);
  assert.ok(delays.every((delay, index) => index === 0 || delay >= delays[index - 1]));
  assert.ok(delays.every((delay) => delay <= 3000));
});

test("the waiting text names the elapsed time and the bound", () => {
  assert.equal(apiStartupWaitText(0), "ローカルAPIの起動を待っています（経過 0秒 / 最大 20秒）");
  assert.match(apiStartupWaitText(7_400), /経過 7秒/);
});
