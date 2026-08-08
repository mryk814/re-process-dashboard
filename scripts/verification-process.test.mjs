import assert from "node:assert/strict";
import test from "node:test";
import { classifyProcessResult, runStreamingCommand } from "./verification-process.mjs";

const silentSink = () => ({ write() {} });

test("streaming runner keeps a successful 9MiB output command successful and bounds capture", async () => {
  const result = await runStreamingCommand({
    command: process.execPath,
    args: ["-e", "process.stdout.write('x'.repeat(9 * 1024 * 1024))"],
    stdout: silentSink(),
    stderr: silentSink(),
  });
  assert.equal(result.status, 0);
  assert.equal(result.error, null);
  assert.equal(classifyProcessResult(result), "passed");
  assert.equal(result.stdoutBytes, 9 * 1024 * 1024);
  assert.ok(Buffer.byteLength(result.stdout, "utf8") <= 64 * 1024);
});

test("streaming runner records timeout and abort signal paths", async () => {
  const timedOut = await runStreamingCommand({
    command: process.execPath,
    args: ["-e", "setTimeout(() => {}, 5000)"],
    timeoutMs: 40,
    stdout: silentSink(),
    stderr: silentSink(),
  });
  assert.equal(classifyProcessResult(timedOut), "timeout");
  assert.equal(timedOut.error?.code, "ETIMEDOUT");

  const controller = new AbortController();
  const abortTimer = setTimeout(() => controller.abort(), 40);
  const interrupted = await runStreamingCommand({
    command: process.execPath,
    args: ["-e", "setTimeout(() => {}, 5000)"],
    abortSignal: controller.signal,
    stdout: silentSink(),
    stderr: silentSink(),
  });
  clearTimeout(abortTimer);
  assert.equal(classifyProcessResult(interrupted), "interrupted");
  assert.equal(interrupted.signal, "SIGTERM");
});
