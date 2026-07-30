import assert from "node:assert/strict";
import test from "node:test";

import {
  devProxyStartupPayload,
  devProxyStartupWindowMs,
  isDevProxyStartupLog,
  isDevProxyStartupRefusal,
} from "../devProxyStartup.ts";
import { apiStartupWaitMs } from "../src/features/workbench/apiStartupWait.ts";

test("connection refusal is quiet only during the bounded API startup window", () => {
  assert.equal(devProxyStartupWindowMs, apiStartupWaitMs);
  const refusal = Object.assign(new Error("connect ECONNREFUSED"), {
    code: "ECONNREFUSED",
  });
  assert.equal(isDevProxyStartupRefusal(refusal, 0), true);
  assert.equal(isDevProxyStartupRefusal(refusal, devProxyStartupWindowMs - 1), true);
  assert.equal(isDevProxyStartupRefusal(refusal, devProxyStartupWindowMs), false);
  assert.equal(isDevProxyStartupRefusal({ code: "ECONNRESET" }, 0), false);
});

test("only the matching Vite proxy stack is condensed during startup", () => {
  const proxyMessage = "http proxy error: /api/projects\nError: connect ECONNREFUSED 127.0.0.1:8769";
  assert.equal(isDevProxyStartupLog(proxyMessage, 1_000), true);
  assert.equal(isDevProxyStartupLog(proxyMessage, devProxyStartupWindowMs), false);
  assert.equal(isDevProxyStartupLog("unrelated ECONNREFUSED", 1_000), false);
});

test("startup refusal becomes an explicit retryable API response", () => {
  assert.deepEqual(JSON.parse(devProxyStartupPayload()), {
    message: "ローカルAPIを起動しています。",
    code: "dev_api_starting",
    field_errors: [],
  });
});
