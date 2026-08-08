import assert from "node:assert/strict";
import {
  mkdtempSync,
  readFileSync,
  rmSync,
  statSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";
import {
  boundRedactedReceiptOutput,
  createEnvironmentIdentity,
  createVerificationReceipt,
  createVerificationReceiptIdentity,
  findReusableVerificationReceipt,
  maximumReceiptOutputBytes,
  normalizeReceiptRelativePath,
  validateVerificationReceipt,
  writeReceiptOutput,
  writeVerificationReceipt,
} from "./verification-receipts.mjs";

const repoRoot = resolve(import.meta.dirname, "..");
const baseIdentity = () => createVerificationReceiptIdentity({
  repoRoot,
  commitSha: "a".repeat(40),
  gateId: "focused-pytest",
  commandArgv: [process.execPath, "scripts/verify.mjs", "edit", "--", "backend/tests/test_api.py"],
  inputPaths: ["scripts/verification-receipts.mjs"],
  catalogDigest: "b".repeat(64),
  environment: createEnvironmentIdentity({
    env: { CI: "false", NODE_ENV: "test", GITHUB_TOKEN: "gho_should-not-be-stored" },
    platform: "win32",
    arch: "x64",
    nodeVersion: "v22.20.0",
    pythonVersion: "Python 3.13.5",
    uvVersion: "uv 0.8.3",
    lockfileDigests: { "package-lock.json": "c".repeat(64) },
  }),
  gitState: {
    tracked_diff_digest: "d".repeat(64),
    status_digest: "e".repeat(64),
    untracked_input_digest: "f".repeat(64),
  },
});

test("result-affecting environment and actual Python identity prevent false reuse", () => {
  const base = createEnvironmentIdentity({
    env: {
      CI: "false",
      PYTHONPATH: "C:\\work\\backend",
      WORKBENCH_DB_PATH: "C:\\work\\one.db",
      DECISION_WORKBENCH_API_TOKEN: "secret-one",
      IRRELEVANT_EDITOR_SETTING: "one",
    },
    platform: "win32",
    arch: "x64",
    nodeVersion: "v22.20.0",
    pythonVersion: "Python 3.13.5",
    uvVersion: "uv 0.8.3",
  });
  const changedPythonPath = createEnvironmentIdentity({
    env: {
      CI: "false",
      PYTHONPATH: "C:\\work\\other-backend",
      WORKBENCH_DB_PATH: "C:\\work\\one.db",
      DECISION_WORKBENCH_API_TOKEN: "secret-one",
      IRRELEVANT_EDITOR_SETTING: "two",
    },
    platform: "win32",
    arch: "x64",
    nodeVersion: "v22.20.0",
    pythonVersion: "Python 3.13.5",
    uvVersion: "uv 0.8.3",
  });
  const changedPython = createEnvironmentIdentity({
    env: {
      CI: "false",
      PYTHONPATH: "C:\\work\\backend",
      WORKBENCH_DB_PATH: "C:\\work\\one.db",
      DECISION_WORKBENCH_API_TOKEN: "secret-one",
    },
    platform: "win32",
    arch: "x64",
    nodeVersion: "v22.20.0",
    pythonVersion: "Python 3.12.10",
    uvVersion: "uv 0.8.3",
  });

  assert.notDeepEqual(base, changedPythonPath);
  assert.notDeepEqual(base, changedPython);
  assert.doesNotMatch(JSON.stringify(base), /secret-one|work|backend|one\.db/);
  assert.equal(base.python, "Python 3.13.5");
  assert.equal(base.uv, "uv 0.8.3");
});

test("verification-receipt/v1 stores a safe exact identity and reuses only passed evidence", () => {
  const root = mkdtempSync(join(tmpdir(), "verification-receipt-test-"));
  try {
    const identity = baseIdentity();
    const receipt = createVerificationReceipt({
      identity,
      status: "passed",
      exitCode: 0,
      durationSeconds: 1.25,
    });
    const locator = writeVerificationReceipt({ receipt, receiptsDirectory: root });
    const stored = JSON.parse(readFileSync(join(root, locator), "utf8"));
    assert.equal(stored.schema_version, "verification-receipt/v1");
    assert.equal(stored.receipt_id, identity.receipt_identity_digest);
    assert.deepEqual(stored.input_paths, identity.input_paths);
    const runtimeName = process.execPath.replaceAll("\\", "/").split("/").at(-1);
    assert.equal(stored.command_argv[0], `<runtime>/${runtimeName}`);
    assert.doesNotMatch(JSON.stringify(stored), /gho_should-not-be-stored/);
    assert.doesNotMatch(JSON.stringify(stored), /[A-Za-z]:\\\\/);

    const reuse = findReusableVerificationReceipt({ identity, receiptsDirectory: root });
    assert.equal(reuse.kind, "reused");
    assert.equal(reuse.receipt_id, receipt.receipt_id);
    assert.deepEqual(reuse.identity_matches, [
      "commit_sha",
      "gate_id",
      "command_argv",
      "command_digest",
      "input_paths",
      "input_digests",
      "dirty_tree_digest",
      "untracked_input_digest",
      "environment_identity",
      "catalog_digest",
    ]);

    const changedEnvironment = createVerificationReceiptIdentity({
      repoRoot,
      commitSha: identity.commit_sha,
      gateId: identity.gate_id,
      commandArgv: identity.command_argv,
      inputPaths: identity.input_paths,
      catalogDigest: identity.catalog_digest,
      gitState: {
        tracked_diff_digest: identity.dirty_tree_digest,
        status_digest: identity.dirty_tree_digest,
        untracked_input_digest: identity.untracked_input_digest,
      },
      environment: { ...identity.environment_identity, node: "v22.20.1" },
    });
    assert.equal(findReusableVerificationReceipt({ identity: changedEnvironment, receiptsDirectory: root }).kind, "executed");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("Windows absolute command arguments remain redacted without basename collisions", () => {
  const identity = baseIdentity();
  const options = {
    repoRoot,
    commitSha: identity.commit_sha,
    gateId: identity.gate_id,
    inputPaths: identity.input_paths,
    catalogDigest: identity.catalog_digest,
    environment: identity.environment_identity,
    gitState: {
      tracked_diff_digest: identity.dirty_tree_digest,
      status_digest: identity.dirty_tree_digest,
      untracked_input_digest: identity.untracked_input_digest,
    },
  };
  const first = createVerificationReceiptIdentity({
    ...options,
    commandArgv: ["C:\\Users\\alice\\tools\\same-name.mjs"],
  });
  const second = createVerificationReceiptIdentity({
    ...options,
    commandArgv: ["D:\\build\\tools\\same-name.mjs"],
  });
  assert.notEqual(first.command_digest, second.command_digest);
  assert.match(first.command_argv[0], /^<external-path>\/[a-f0-9]{64}$/);
  assert.doesNotMatch(JSON.stringify(first), /Users|alice|build/);
});

test("failed, not_run, timeout, and interrupted receipts are never reusable", () => {
  for (const status of ["failed", "not_run", "timeout", "interrupted"]) {
    const root = mkdtempSync(join(tmpdir(), `verification-receipt-${status}-`));
    try {
      const identity = baseIdentity();
      writeVerificationReceipt({
        receipt: createVerificationReceipt({ identity, status, exitCode: status === "not_run" ? null : 1 }),
        receiptsDirectory: root,
      });
      const result = findReusableVerificationReceipt({ identity, receiptsDirectory: root });
      assert.equal(result.kind, "executed", status);
      assert.ok(result.rejected.some((item) => item.reason.includes(`status ${status}`)), status);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  }
});

test("tampered and oversized receipt artifacts fail closed", () => {
  const root = mkdtempSync(join(tmpdir(), "verification-receipt-tamper-"));
  try {
    const identity = baseIdentity();
    const receipt = createVerificationReceipt({ identity, status: "passed", exitCode: 0 });
    const locator = writeVerificationReceipt({ receipt, receiptsDirectory: root });
    for (const mutate of [
      (value) => { value.status = "failed"; value.result = "failed"; value.exit_code = 1; },
      (value) => { value.status = "passed"; value.result = "passed"; value.exit_code = 7; },
      (value) => { value.artifacts.stdout = "other.stdout.log"; },
    ]) {
      const tampered = JSON.parse(readFileSync(join(root, locator), "utf8"));
      mutate(tampered);
      writeFileSync(join(root, locator), JSON.stringify(tampered));
      const rejected = findReusableVerificationReceipt({ identity, receiptsDirectory: root });
      assert.equal(rejected.kind, "executed");
      assert.ok(rejected.rejected.some((item) => item.reason.includes("content digest")));
    }

    writeFileSync(join(root, "f".repeat(64) + ".json"), "x".repeat(300_000));
    assert.equal(findReusableVerificationReceipt({ identity, receiptsDirectory: root }).kind, "executed");

    const legacy = { ...receipt };
    delete legacy.content_digest;
    writeFileSync(join(root, "e".repeat(64) + ".json"), JSON.stringify(legacy));
    const legacyResult = findReusableVerificationReceipt({ identity, receiptsDirectory: root });
    assert.equal(legacyResult.kind, "executed");
    assert.ok(legacyResult.rejected.some((item) => item.reason.includes("content_digest")));
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("receipt writers reject existing symlink targets instead of following them", () => {
  const root = mkdtempSync(join(tmpdir(), "verification-receipt-symlink-"));
  const outside = mkdtempSync(join(tmpdir(), "verification-receipt-outside-"));
  try {
    const identity = baseIdentity();
    const receipt = createVerificationReceipt({ identity, status: "passed", exitCode: 0 });
    const outputTarget = join(root, `${"1".repeat(64)}.stdout.log`);
    const outsideOutput = join(outside, "outside.log");
    writeFileSync(outsideOutput, "outside");
    symlinkSync(outsideOutput, outputTarget, "file");
    assert.throws(
      () => writeReceiptOutput({
        receiptsDirectory: root,
        receiptId: "1".repeat(64),
        kind: "stdout",
        output: "must not overwrite outside",
      }),
      /symlink/,
    );
    assert.equal(readFileSync(outsideOutput, "utf8"), "outside");

    const receiptTarget = join(root, `${receipt.receipt_id}.json`);
    const outsideReceipt = join(outside, "outside-receipt.json");
    writeFileSync(outsideReceipt, "outside-receipt");
    symlinkSync(outsideReceipt, receiptTarget, "file");
    assert.throws(
      () => writeVerificationReceipt({ receipt, receiptsDirectory: root }),
      /symlink/,
    );
    assert.equal(readFileSync(outsideReceipt, "utf8"), "outside-receipt");

    const brokenOutputTarget = join(root, `${"1".repeat(64)}.stderr.log`);
    symlinkSync(join(outside, "missing.log"), brokenOutputTarget, "file");
    assert.throws(
      () => writeReceiptOutput({
        receiptsDirectory: root,
        receiptId: "1".repeat(64),
        kind: "stderr",
        output: "must not follow a broken symlink",
      }),
      /symlink/,
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
    rmSync(outside, { recursive: true, force: true });
  }
});

test("receipt paths reject traversal and output artifacts are bounded and redacted", () => {
  assert.throws(() => normalizeReceiptRelativePath("../outside.json"), /traversal/);
  assert.throws(() => normalizeReceiptRelativePath("C:\\Users\\someone\\secret.json"), /repository-relative/);
  const output = `${"safe ".repeat(30_000)} token=secret-value C:\\Users\\someone\\private.txt`;
  const bounded = boundRedactedReceiptOutput(output);
  const sensitive = boundRedactedReceiptOutput("AWS_SECRET_ACCESS_KEY=secret-key Authorization: Bearer bearer-token sk-live-value /home/someone/private.txt");
  assert.doesNotMatch(sensitive, /secret-key|bearer-token|sk-live-value|\/home\/someone/);
  assert.ok(Buffer.byteLength(bounded, "utf8") <= maximumReceiptOutputBytes);
  assert.doesNotMatch(bounded, /secret-value|C:\\Users/);

  const root = mkdtempSync(join(tmpdir(), "verification-receipt-output-"));
  try {
    const locator = writeReceiptOutput({
      receiptsDirectory: root,
      receiptId: "1".repeat(64),
      kind: "stdout",
      output,
    });
    assert.equal(locator, `${"1".repeat(64)}.stdout.log`);
    assert.ok(statSync(join(root, locator)).size <= maximumReceiptOutputBytes);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("receipt validation rejects absolute input and artifact locators", () => {
  const identity = baseIdentity();
  const receipt = createVerificationReceipt({ identity, status: "passed", exitCode: 0 });
  assert.throws(
    () => validateVerificationReceipt({ ...receipt, input_paths: ["C:/Users/someone/secret.txt"] }),
    /identity digest|content digest|repository-relative/,
  );
  assert.throws(
    () => validateVerificationReceipt({ ...receipt, artifacts: { stdout: "../escape.log", stderr: null } }),
    /identity digest|content digest|traversal/,
  );
});
