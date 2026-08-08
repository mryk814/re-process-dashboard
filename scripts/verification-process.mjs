import { spawn } from "node:child_process";

export const defaultProcessCaptureBytes = 64 * 1024;

class TailCapture {
  constructor(maximumBytes) {
    this.maximumBytes = maximumBytes;
    this.chunks = [];
    this.bytes = 0;
    this.totalBytes = 0;
  }

  append(value) {
    const chunk = Buffer.isBuffer(value) ? value : Buffer.from(value);
    this.totalBytes += chunk.byteLength;
    if (chunk.byteLength >= this.maximumBytes) {
      this.chunks = [chunk.subarray(chunk.byteLength - this.maximumBytes)];
      this.bytes = this.maximumBytes;
      return;
    }
    this.chunks.push(chunk);
    this.bytes += chunk.byteLength;
    while (this.bytes > this.maximumBytes && this.chunks.length > 0) {
      const first = this.chunks[0];
      const remove = Math.min(first.byteLength, this.bytes - this.maximumBytes);
      if (remove === first.byteLength) this.chunks.shift();
      else this.chunks[0] = first.subarray(remove);
      this.bytes -= remove;
    }
  }

  toString() {
    return Buffer.concat(this.chunks, this.bytes).toString("utf8");
  }
}

export function classifyProcessResult(result) {
  if (result.error?.code === "ETIMEDOUT") return "timeout";
  if (result.signal) return "interrupted";
  return result.error || result.status !== 0 ? "failed" : "passed";
}

export function runStreamingCommand({
  command,
  args = [],
  env = process.env,
  cwd = process.cwd(),
  timeoutMs = 0,
  abortSignal = null,
  stdout = process.stdout,
  stderr = process.stderr,
  captureBytes = defaultProcessCaptureBytes,
} = {}) {
  if (!command) throw new Error("streaming command is required");
  if (!Number.isInteger(captureBytes) || captureBytes <= 0) {
    throw new Error("streaming captureBytes must be a positive integer");
  }
  return new Promise((resolve) => {
    const stdoutCapture = new TailCapture(captureBytes);
    const stderrCapture = new TailCapture(captureBytes);
    let child;
    let spawnError = null;
    let timedOut = false;
    let interrupted = false;
    let settled = false;
    let timeoutHandle = null;
    let forceKillHandle = null;
    let abort = null;

    const finish = (status, signal) => {
      if (settled) return;
      settled = true;
      if (timeoutHandle) clearTimeout(timeoutHandle);
      if (forceKillHandle) clearTimeout(forceKillHandle);
      if (abortSignal?.removeEventListener && abort) abortSignal.removeEventListener("abort", abort);
      resolve({
        status,
        signal: signal ?? (interrupted ? "SIGTERM" : null),
        error: timedOut
          ? Object.assign(new Error(`command timed out after ${timeoutMs}ms`), { code: "ETIMEDOUT" })
          : spawnError,
        stdout: stdoutCapture.toString(),
        stderr: stderrCapture.toString(),
        stdoutBytes: stdoutCapture.totalBytes,
        stderrBytes: stderrCapture.totalBytes,
      });
    };

    const terminate = (kind) => {
      if (settled || !child) return;
      if (kind === "timeout") timedOut = true;
      if (kind === "interrupt") interrupted = true;
      child.kill("SIGTERM");
      forceKillHandle = setTimeout(() => {
        if (!settled) child.kill("SIGKILL");
      }, 250);
    };

    abort = () => terminate("interrupt");

    try {
      child = spawn(command, args, {
        cwd,
        env,
        stdio: ["ignore", "pipe", "pipe"],
        windowsHide: true,
      });
    } catch (error) {
      spawnError = error;
      finish(null, null);
      return;
    }

    child.stdout.on("data", (chunk) => {
      stdout.write(chunk);
      stdoutCapture.append(chunk);
    });
    child.stderr.on("data", (chunk) => {
      stderr.write(chunk);
      stderrCapture.append(chunk);
    });
    child.once("error", (error) => {
      spawnError = error;
    });
    child.once("close", (status, signal) => finish(status, signal));

    if (timeoutMs > 0) timeoutHandle = setTimeout(() => terminate("timeout"), timeoutMs);
    if (abortSignal?.aborted) abort();
    else if (abortSignal?.addEventListener) abortSignal.addEventListener("abort", abort, { once: true });
  });
}
