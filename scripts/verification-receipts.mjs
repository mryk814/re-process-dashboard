import { createHash } from "node:crypto";
import {
  closeSync,
  existsSync,
  lstatSync,
  mkdirSync,
  openSync,
  readFileSync,
  readlinkSync,
  readSync,
  readdirSync,
  realpathSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { spawnSync } from "node:child_process";
import { dirname, isAbsolute, join, relative, resolve } from "node:path";

export const verificationReceiptSchemaVersion = "verification-receipt/v1";
export const reusableReceiptResult = "passed";
export const receiptResultStatuses = Object.freeze([
  "passed",
  "failed",
  "not_run",
  "timeout",
  "interrupted",
]);

const receiptIdentityFields = Object.freeze([
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
const defaultInputPaths = Object.freeze([
  "package.json",
  "package-lock.json",
  "uv.lock",
  "scripts/verify.mjs",
  "scripts/verification-ci.mjs",
  "scripts/verification-gates.mjs",
  "scripts/verification-gates.json",
  "scripts/verification-process.mjs",
  "scripts/verification-receipts.mjs",
]);
const safeEnvironmentKeys = Object.freeze([
  "CI",
  "GITHUB_ACTIONS",
  "GITHUB_EVENT_NAME",
  "GITHUB_JOB",
  "GITHUB_WORKFLOW",
  "NODE_ENV",
  "NPM_CONFIG_USER_AGENT",
  "npm_config_user_agent",
  "PLAYWRIGHT_CI_DIAGNOSTICS",
  "PLAYWRIGHT_RETRIES",
  "PLAYWRIGHT_WORKERS",
  "RUNNER_ARCH",
  "RUNNER_OS",
  "UV_VERSION",
  "VERIFY_BASE_REF",
]);
const maximumInputFiles = 20_000;
export const maximumReceiptOutputBytes = 64 * 1024;
const maximumReceiptFileBytes = 256 * 1024;

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function canonicalize(value) {
  if (Array.isArray(value)) return value.map((item) => canonicalize(item));
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, canonicalize(value[key])]),
    );
  }
  return value;
}

export function canonicalJson(value) {
  return JSON.stringify(canonicalize(value));
}

function isDriveAbsolute(value) {
  return /^[A-Za-z]:\//.test(value);
}

export function normalizeReceiptRelativePath(value) {
  const candidate = String(value).replaceAll("\\", "/");
  if (
    candidate.length === 0
    || candidate.includes("\0")
    || candidate.startsWith("/")
    || isDriveAbsolute(candidate)
  ) {
    throw new Error(`receipt path must be repository-relative: ${value}`);
  }
  const parts = candidate
    .split("/")
    .filter((part) => part.length > 0);
  if (parts.includes("..")) {
    throw new Error(`receipt path traversal is not allowed: ${value}`);
  }
  const normalized = parts.join("/");
  return normalized;
}

function isContainedPath(root, candidate) {
  const rootPath = resolve(root);
  const candidatePath = resolve(candidate);
  const difference = relative(rootPath, candidatePath);
  return difference === "" || (
    !difference.startsWith("..")
    && !isAbsolute(difference)
  );
}

function resolveInputPath(repoRoot, inputPath) {
  const normalized = normalizeReceiptRelativePath(inputPath);
  const candidate = resolve(repoRoot, normalized);
  if (!isContainedPath(repoRoot, candidate)) {
    throw new Error(`receipt input path escapes repository: ${inputPath}`);
  }
  if (existsSync(candidate)) {
    const real = realpathSync(candidate);
    if (!isContainedPath(repoRoot, real)) {
      throw new Error(`receipt input path resolves outside repository: ${inputPath}`);
    }
  }
  return { normalized, candidate };
}

function digestFile(path) {
  const hash = createHash("sha256");
  const descriptor = openSync(path, "r");
  const buffer = Buffer.allocUnsafe(1024 * 1024);
  try {
    let bytesRead = 0;
    do {
      bytesRead = readSync(descriptor, buffer, 0, buffer.length, null);
      if (bytesRead > 0) hash.update(buffer.subarray(0, bytesRead));
    } while (bytesRead > 0);
    return hash.digest("hex");
  } finally {
    // Node's descriptor is deliberately closed here even when hashing fails.
    // The receipt never stores file contents.
    closeSync(descriptor);
  }
}

function digestSymlink(path) {
  return sha256(`symlink\0${readlinkSync(path, "utf8")}`);
}

function filesBelow(repoRoot, relativePath) {
  const { normalized, candidate } = resolveInputPath(repoRoot, relativePath);
  if (!existsSync(candidate)) return [{ path: normalized, digest: "missing" }];
  const entry = lstatSync(candidate);
  if (entry.isSymbolicLink()) return [{ path: normalized, digest: digestSymlink(candidate) }];
  if (entry.isFile()) return [{ path: normalized, digest: digestFile(candidate) }];
  if (!entry.isDirectory()) return [{ path: normalized, digest: "unsupported-file-type" }];
  const entries = readdirSync(candidate, { withFileTypes: true })
    .sort((left, right) => left.name.localeCompare(right.name))
    .flatMap((child) => filesBelow(repoRoot, join(normalized, child.name)));
  if (entries.length > maximumInputFiles) {
    throw new Error(`receipt input expansion exceeds ${maximumInputFiles} files: ${normalized}`);
  }
  return entries;
}

export function digestRepositoryInputs({ repoRoot = process.cwd(), inputPaths = [] } = {}) {
  const normalizedPaths = [...new Set(inputPaths.map((path) => normalizeReceiptRelativePath(path)))].sort();
  const files = normalizedPaths.flatMap((path) => filesBelow(repoRoot, path));
  if (files.length > maximumInputFiles) {
    throw new Error(`receipt input set exceeds ${maximumInputFiles} files`);
  }
  const uniqueFiles = [...new Map(files.map((entry) => [entry.path, entry])).values()]
    .sort((left, right) => left.path.localeCompare(right.path));
  return {
    paths: uniqueFiles.map((entry) => entry.path),
    digests: uniqueFiles,
    digest: sha256(canonicalJson(uniqueFiles)),
  };
}

function gitBytes(repoRoot, args) {
  const result = spawnSync("git", args, {
    cwd: repoRoot,
    encoding: null,
    // Dirty diffs are part of identity only; do not persist their contents,
    // but allow a large working tree to remain representable.
    maxBuffer: 64 * 1024 * 1024,
  });
  if (result.status !== 0) {
    throw new Error(`cannot inspect git state: git ${args.join(" ")}`);
  }
  return result.stdout ?? Buffer.alloc(0);
}

function untrackedPaths(repoRoot) {
  return gitBytes(repoRoot, ["ls-files", "--others", "--exclude-standard", "-z"])
    .toString("utf8")
    .split("\0")
    .filter(Boolean)
    .map((path) => normalizeReceiptRelativePath(path));
}

function digestUntrackedInputs(repoRoot, paths) {
  return digestRepositoryInputs({ repoRoot, inputPaths: paths }).digest;
}

export function captureWorkingTreeIdentity({
  repoRoot = process.cwd(),
  gitState = null,
} = {}) {
  if (gitState) {
    const normalized = {
      tracked_diff_digest: gitState.tracked_diff_digest,
      status_digest: gitState.status_digest,
      untracked_input_digest: gitState.untracked_input_digest,
    };
    return {
      ...normalized,
      dirty_tree_digest: gitState.dirty_tree_digest
        ?? sha256(canonicalJson(normalized)),
    };
  }
  const trackedDiff = gitBytes(repoRoot, ["diff", "HEAD", "--binary", "--no-ext-diff", "--"]);
  const status = gitBytes(repoRoot, ["status", "--porcelain=v1", "--untracked-files=all", "-z"]);
  const paths = untrackedPaths(repoRoot);
  const identity = {
    tracked_diff_digest: sha256(trackedDiff),
    status_digest: sha256(status),
    untracked_input_digest: digestUntrackedInputs(repoRoot, paths),
  };
  return {
    ...identity,
    dirty_tree_digest: sha256(canonicalJson(identity)),
  };
}

function redactSensitiveText(value) {
  return String(value ?? "")
    .replaceAll("\0", "")
    .replace(/(authorization)\s*[:=]\s*[^\r\n]+/gi, "$1=<redacted>")
    .replace(/([A-Za-z0-9_.-]*(?:token|secret|password|authorization|api[_-]?key)[A-Za-z0-9_.-]*)\s*[:=]\s*[^\s]+/gi, "$1=<redacted>")
    .replace(/\b(?:gho_|github_pat_|sk-|AKIA)[A-Za-z0-9_-]+\b/gi, "<redacted-token>")
    .replace(/[A-Za-z]:[\\/][^\r\n\s"'`]+/g, "<absolute-path>")
    .replace(/\/(?:Users|home|private\/var|mnt\/c)\/[^\r\n\s"'`]+/g, "<absolute-path>");
}

function safeEnvironmentValue(value) {
  if (value === undefined || value === null) return null;
  return redactSensitiveText(value).replaceAll("\\", "/");
}

export function createEnvironmentIdentity({
  env = process.env,
  platform = process.platform,
  arch = process.arch,
  nodeVersion = process.version,
  lockfileDigests = {},
} = {}) {
  const variables = Object.fromEntries(
    safeEnvironmentKeys
      .filter((key) => env[key] !== undefined)
      .map((key) => [key, safeEnvironmentValue(env[key])]),
  );
  return {
    schema_version: "verification-environment/v1",
    os: platform,
    arch,
    node: nodeVersion,
    python: safeEnvironmentValue(env.PYTHON_VERSION ?? env.pythonVersion),
    variables,
    lockfile_digests: lockfileDigests,
  };
}

function normalizeCommandArg(repoRoot, value) {
  const raw = String(value).replaceAll("\\", "/");
  if (isDriveAbsolute(raw)) {
    if (process.platform === "win32") {
      const candidate = resolve(raw);
      if (isContainedPath(repoRoot, candidate)) {
        return `<repo>/${relative(repoRoot, candidate).replaceAll("\\", "/")}`;
      }
    }
    const basename = raw.split("/").at(-1);
    const runtimeExecutable = process.execPath.replaceAll("\\", "/");
    if (raw.toLowerCase() === runtimeExecutable.toLowerCase()) {
      return `<runtime>/${basename}`;
    }
    return `<external-path>/${sha256(`command-path\0${process.platform === "win32" ? raw.toLowerCase() : raw}`)}`;
  }
  if (raw.startsWith("/")) {
    const candidate = resolve(raw);
    if (isContainedPath(repoRoot, candidate)) {
      return `<repo>/${relative(repoRoot, candidate).replaceAll("\\", "/")}`;
    }
    if (resolve(raw) === resolve(process.execPath)) {
      return `<runtime>/${raw.split("/").at(-1)}`;
    }
    return `<external-path>/${sha256(`command-path\0${raw}`)}`;
  }
  return raw;
}

export function createVerificationReceiptIdentity({
  repoRoot = process.cwd(),
  commitSha,
  gateId = null,
  commandArgv,
  inputPaths = [],
  catalogDigest,
  environment = null,
  environmentOptions = {},
  gitState = null,
} = {}) {
  if (!commitSha) throw new Error("receipt identity requires commit_sha");
  if (!Array.isArray(commandArgv) || commandArgv.length === 0) {
    throw new Error("receipt identity requires resolved command argv");
  }
  if (!catalogDigest) throw new Error("receipt identity requires catalog_digest");
  const inputs = digestRepositoryInputs({
    repoRoot,
    inputPaths: [...defaultInputPaths, ...inputPaths],
  });
  const lockfileDigests = Object.fromEntries(
    inputs.digests
      .filter((entry) => ["package-lock.json", "uv.lock"].includes(entry.path))
      .map((entry) => [entry.path, entry.digest]),
  );
  const workingTree = captureWorkingTreeIdentity({ repoRoot, gitState });
  const command = commandArgv.map((value) => normalizeCommandArg(repoRoot, value));
  const identity = {
    commit_sha: commitSha,
    gate_id: gateId,
    command_argv: command,
    command_digest: sha256(canonicalJson({ gate_id: gateId, command_argv: command })),
    input_paths: inputs.paths,
    input_digests: inputs.digests,
    dirty_tree_digest: workingTree.dirty_tree_digest,
    untracked_input_digest: workingTree.untracked_input_digest,
    environment_identity: environment ?? createEnvironmentIdentity({
      ...environmentOptions,
      lockfileDigests,
    }),
    catalog_digest: catalogDigest,
  };
  return {
    ...identity,
    receipt_identity_digest: sha256(canonicalJson(identity)),
  };
}

function identityFromReceipt(receipt) {
  return Object.fromEntries(receiptIdentityFields.map((field) => [field, receipt[field]]));
}

function contentFromReceipt(receipt) {
  return Object.fromEntries(
    Object.entries(receipt).filter(([key]) => key !== "content_digest"),
  );
}

function receiptContentDigest(receipt) {
  return sha256(canonicalJson(contentFromReceipt(receipt)));
}

function containsUnsafeReceiptString(value) {
  if (typeof value === "string") {
    return isDriveAbsolute(value) || value.startsWith("/") || value.includes("\0")
      || /(?:gho_|github_pat_|sk-[A-Za-z0-9]|AKIA[A-Z0-9]{12,})/.test(value);
  }
  if (Array.isArray(value)) return value.some((item) => containsUnsafeReceiptString(item));
  if (value && typeof value === "object") {
    return Object.entries(value).some(([key, child]) => (
      /(?:token|secret|password|authorization|api[_-]?key)/i.test(key)
      || containsUnsafeReceiptString(child)
    ));
  }
  return false;
}

export function validateVerificationReceipt(receipt) {
  if (!receipt || typeof receipt !== "object") throw new Error("verification receipt must be an object");
  if (receipt.schema_version !== verificationReceiptSchemaVersion) {
    throw new Error(`verification receipt schema must be ${verificationReceiptSchemaVersion}`);
  }
  if (!receiptResultStatuses.includes(receipt.status)) {
    throw new Error(`verification receipt has invalid status: ${receipt.status}`);
  }
  if (receipt.result !== receipt.status) {
    throw new Error("verification receipt result does not match its status");
  }
  if (!/^[a-f0-9]{64}$/.test(receipt.content_digest ?? "")) {
    throw new Error("verification receipt has an invalid content_digest");
  }
  if (receiptContentDigest(receipt) !== receipt.content_digest) {
    throw new Error("verification receipt content digest does not match its contents");
  }
  for (const field of receiptIdentityFields) {
    if (receipt[field] === undefined) throw new Error(`verification receipt is missing ${field}`);
  }
  if (!/^[a-f0-9]{64}$/.test(receipt.receipt_id ?? "")) {
    throw new Error("verification receipt has an invalid receipt_id");
  }
  if (sha256(canonicalJson(identityFromReceipt(receipt))) !== receipt.receipt_id) {
    throw new Error("verification receipt identity digest does not match its contents");
  }
  if (!Array.isArray(receipt.input_paths) || !Array.isArray(receipt.input_digests)) {
    throw new Error("verification receipt input identity is invalid");
  }
  receipt.input_paths.forEach(normalizeReceiptRelativePath);
  receipt.input_digests.forEach((entry) => normalizeReceiptRelativePath(entry.path));
  for (const locator of [receipt.artifacts?.stdout, receipt.artifacts?.stderr]) {
    if (locator !== null && locator !== undefined) normalizeReceiptRelativePath(locator);
  }
  if (containsUnsafeReceiptString(receipt)) {
    throw new Error("verification receipt contains an unsafe or secret value");
  }
  return receipt;
}

export function createVerificationReceipt({
  identity,
  status,
  exitCode = null,
  signal = null,
  durationSeconds = 0,
  createdAt = new Date().toISOString(),
  artifacts = { stdout: null, stderr: null },
} = {}) {
  if (!receiptResultStatuses.includes(status)) throw new Error(`invalid receipt status: ${status}`);
  const receipt = {
    schema_version: verificationReceiptSchemaVersion,
    ...identity,
    receipt_id: identity.receipt_identity_digest,
    status,
    result: status,
    exit_code: exitCode,
    signal,
    duration: durationSeconds,
    duration_seconds: durationSeconds,
    created_at: createdAt,
    artifacts,
  };
  receipt.content_digest = receiptContentDigest(receipt);
  return validateVerificationReceipt(receipt);
}

function assertNoSymlinkPath(path) {
  let cursor = resolve(path);
  while (true) {
    try {
      const entry = lstatSync(cursor);
      if (entry.isSymbolicLink()) {
        throw new Error(`receipt path cannot use a symlink: ${path}`);
      }
      const real = realpathSync(cursor);
      const same = process.platform === "win32"
        ? real.toLowerCase() === cursor.toLowerCase()
        : real === cursor;
      if (!same) throw new Error(`receipt path resolves through a symlink: ${path}`);
      return;
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
    const parent = dirname(cursor);
    if (parent === cursor) return;
    cursor = parent;
  }
}

function resolveReceiptArtifact(root, relativePath) {
  const normalized = normalizeReceiptRelativePath(relativePath);
  const rootPath = resolve(root);
  const target = resolve(rootPath, normalized);
  if (!isContainedPath(rootPath, target)) throw new Error(`receipt artifact escapes directory: ${relativePath}`);
  assertNoSymlinkPath(rootPath);
  assertNoSymlinkPath(target);
  return { normalized, target };
}

export function boundRedactedReceiptOutput(value, maximumBytes = maximumReceiptOutputBytes) {
  const redacted = redactSensitiveText(value);
  const buffer = Buffer.from(redacted, "utf8");
  if (buffer.byteLength <= maximumBytes) return redacted;
  const marker = `[earlier ${buffer.byteLength - maximumBytes} bytes omitted]\n`;
  const tailBytes = Math.max(0, maximumBytes - Buffer.byteLength(marker, "utf8"));
  const tail = buffer.subarray(buffer.byteLength - tailBytes).toString("utf8");
  return `${marker}${tail}`;
}

export function writeReceiptOutput({
  receiptsDirectory,
  receiptId,
  kind,
  output,
  maximumBytes = maximumReceiptOutputBytes,
} = {}) {
  if (!/^[a-f0-9]{64}$/.test(receiptId ?? "")) throw new Error("receipt output requires a safe receipt id");
  if (!["stdout", "stderr"].includes(kind)) throw new Error(`invalid receipt output kind: ${kind}`);
  const relativePath = `${receiptId}.${kind}.log`;
  const { target } = resolveReceiptArtifact(receiptsDirectory, relativePath);
  mkdirSync(receiptsDirectory, { recursive: true });
  writeFileSync(target, boundRedactedReceiptOutput(output, maximumBytes), "utf8");
  return relativePath;
}

export function writeVerificationReceipt({
  receipt,
  receiptsDirectory = resolve("artifacts", "verification", "receipts"),
} = {}) {
  validateVerificationReceipt(receipt);
  mkdirSync(receiptsDirectory, { recursive: true });
  const relativePath = `${receipt.receipt_id}.json`;
  const { target } = resolveReceiptArtifact(receiptsDirectory, relativePath);
  writeFileSync(target, `${JSON.stringify(receipt, null, 2)}\n`, "utf8");
  return relativePath;
}

function readReceiptFile(path) {
  if (statSync(path).size > maximumReceiptFileBytes) return { receipt: null, reason: "receipt is oversized" };
  try {
    return { receipt: validateVerificationReceipt(JSON.parse(readFileSync(path, "utf8"))), reason: null };
  } catch (error) {
    return { receipt: null, reason: error.message };
  }
}

export function readVerificationReceipts({
  receiptsDirectory = resolve("artifacts", "verification", "receipts"),
} = {}) {
  if (!existsSync(receiptsDirectory)) return { receipts: [], rejected: [] };
  const receipts = [];
  const rejected = [];
  for (const entry of readdirSync(receiptsDirectory, { withFileTypes: true })) {
    if (!entry.isFile() || !entry.name.endsWith(".json")) continue;
    try {
      const path = resolveReceiptArtifact(receiptsDirectory, entry.name).target;
      const result = readReceiptFile(path);
      if (result.receipt) receipts.push(result.receipt);
      else rejected.push({ path: entry.name, reason: result.reason });
    } catch (error) {
      rejected.push({ path: entry.name, reason: error.message });
    }
  }
  return { receipts, rejected };
}

function identityMatches(left, right) {
  return receiptIdentityFields.every((field) => canonicalJson(left[field]) === canonicalJson(right[field]));
}

export function findReusableVerificationReceipt({
  identity,
  receiptsDirectory = resolve("artifacts", "verification", "receipts"),
} = {}) {
  const candidates = readVerificationReceipts({ receiptsDirectory });
  const rejected = [...candidates.rejected];
  for (const receipt of candidates.receipts) {
    if (receipt.status !== reusableReceiptResult) {
      rejected.push({ path: `${receipt.receipt_id}.json`, reason: `status ${receipt.status} is not reusable` });
      continue;
    }
    if (identityMatches(receipt, identity)) {
      return {
        kind: "reused",
        receipt,
        receipt_id: receipt.receipt_id,
        created_at: receipt.created_at,
        identity_matches: receiptIdentityFields,
        rejected,
      };
    }
  }
  return { kind: "executed", receipt: null, receipt_id: null, created_at: null, identity_matches: [], rejected };
}
