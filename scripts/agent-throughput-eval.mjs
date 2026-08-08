import { createHash } from "node:crypto";
import { existsSync, lstatSync, mkdirSync, readFileSync, readdirSync, realpathSync, writeFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { dirname, isAbsolute, join, relative, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { checkSkillInventory } from "./check-skill-inventory.mjs";
import { validateVerificationReceipt } from "./verification-receipts.mjs";

export const catalogSchemaVersion = "agent-throughput-catalog/v1";
export const resultSchemaVersion = "agent-throughput-result/v1";
export const comparisonSchemaVersion = "agent-throughput-comparison/v1";

const scriptPath = fileURLToPath(import.meta.url);
const defaultRepoRoot = resolve(dirname(scriptPath), "..");
const defaultCatalogPath = "benchmarks/agent-throughput/cases.json";
const requiredCategories = Object.freeze([
  "pure_docs_typo",
  "backend_one_authority_bug",
  "web_local_interaction",
  "test_only_regression",
  "api_field_addition",
  "model_runtime_scientific_contract",
]);
const allowedSetupCommands = new Set(["node", "npm.cmd", "uv"]);
const forbiddenPayloadKeys = /^(?:(?:raw_)?(?:transcript|conversation|messages|provider_reasoning)|api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|private[_-]?key|password|authorization|cookie|set[_-]?cookie|credential)$/i;
const secretValuePattern = /(?:\b(?:sk-|ghp_|gho_|github_pat_|AKIA)[A-Za-z0-9_-]{8,}|\bBearer\s+[A-Za-z0-9._~+\/-]{8,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|(?:access[_-]?token|refresh[_-]?token|client[_-]?secret|private[_-]?key|cookie|set-cookie|credential|token|secret|password|authorization|api[_-]?key)\s*[:=]\s*[^\s<]+)/i;
const homePathPattern = /(?:[A-Za-z]:[\\/](?:Users|Documents and Settings)[\\/]|\/(?:Users|home|root)\/|%USERPROFILE%|\$HOME|~[\\/])/i;

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function canonicalize(value) {
  if (Array.isArray(value)) return value.map((item) => canonicalize(item));
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value).sort().map((key) => [key, canonicalize(value[key])]),
    );
  }
  return value;
}

export function canonicalJson(value) {
  return JSON.stringify(canonicalize(value));
}

export function redactSensitiveText(value) {
  return String(value)
    .replace(/-----BEGIN ([A-Z ]*PRIVATE KEY)-----[\s\S]*?-----END \1-----/gi, "<redacted-secret>")
    .replace(/(?:\b(?:sk-|ghp_|gho_|github_pat_|AKIA)[A-Za-z0-9_-]{8,}|\bBearer\s+[A-Za-z0-9._~+\/-]{8,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|(?:access[_-]?token|refresh[_-]?token|client[_-]?secret|private[_-]?key|cookie|set-cookie|credential|token|secret|password|authorization|api[_-]?key)\s*[:=]\s*[^\s<]+)/gi, "<redacted-secret>")
    .replace(/(?:[A-Za-z]:[\\/](?:Users|Documents and Settings)[\\/][^\r\n\"'`]+|\/(?:Users|home|root)\/[^\r\n\"'`]+|%USERPROFILE%[^\r\n\"'`]*|\$HOME[^\r\n\"'`]*|~[\\/][^\r\n\"'`]*)/gi, "<redacted-home-path>");
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function isPlainObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function exactKeys(value, allowed, field) {
  assert(isPlainObject(value), `${field} must be an object`);
  const unexpected = Object.keys(value).filter((key) => !allowed.includes(key));
  const missing = allowed.filter((key) => !(key in value));
  assert(unexpected.length === 0, `${field} has unexpected field(s): ${unexpected.join(", ")}`);
  assert(missing.length === 0, `${field} is missing field(s): ${missing.join(", ")}`);
}

function nonEmptyString(value, field) {
  assert(typeof value === "string" && value.trim().length > 0, `${field} must be a non-empty string`);
}

function stringArray(value, field, { minimum = 0 } = {}) {
  assert(Array.isArray(value) && value.length >= minimum, `${field} must contain at least ${minimum} item(s)`);
  value.forEach((item, index) => nonEmptyString(item, `${field}[${index}]`));
}

function readJson(path, maximumBytes = 1024 * 1024) {
  const buffer = readFileSync(path);
  assert(buffer.byteLength <= maximumBytes, `JSON input exceeds ${maximumBytes} bytes: ${path}`);
  return JSON.parse(buffer.toString("utf8"));
}

function lineCount(text) {
  if (text.length === 0) return 0;
  const normalized = text.replaceAll("\r\n", "\n");
  return normalized.endsWith("\n")
    ? normalized.slice(0, -1).split("\n").length
    : normalized.split("\n").length;
}

function walkPayload(value, visitor, path = "$") {
  if (Array.isArray(value)) {
    value.forEach((item, index) => walkPayload(item, visitor, `${path}[${index}]`));
    return;
  }
  if (isPlainObject(value)) {
    for (const [key, item] of Object.entries(value)) {
      visitor({ key, value: item, path: `${path}.${key}` });
      walkPayload(item, visitor, `${path}.${key}`);
    }
  }
}

export function assertRedactedPayload(value) {
  walkPayload(value, ({ key, value: item, path }) => {
    assert(!forbiddenPayloadKeys.test(key), `raw/private payload field is not allowed: ${path}`);
    if (typeof item !== "string") return;
    assert(!secretValuePattern.test(item), `secret-like value is not allowed: ${path}`);
    assert(!homePathPattern.test(item), `HOME/absolute personal path is not allowed: ${path}`);
    assert(!item.startsWith("file://"), `file URL is not allowed: ${path}`);
  });
}

function repositoryRelativePath(value, field) {
  nonEmptyString(value, field);
  const normalized = value.replaceAll("\\", "/");
  assert(!normalized.startsWith("/") && !isAbsolute(normalized), `${field} must be repository-relative`);
  assert(!normalized.split("/").includes(".."), `${field} must not traverse outside the repository`);
  return normalized;
}

function isContainedPath(root, candidate) {
  const difference = relative(resolve(root), resolve(candidate));
  return difference === "" || (!difference.startsWith("..") && !isAbsolute(difference));
}

function resolveRepositoryArtifact(repoRoot, value, field, { mustExist = true } = {}) {
  const normalized = repositoryRelativePath(value, field);
  const candidate = resolve(repoRoot, normalized);
  assert(isContainedPath(repoRoot, candidate), `${field} escapes the repository`);
  if (mustExist) {
    assert(existsSync(candidate), `${field} does not exist: ${normalized}`);
    assert(!lstatSync(candidate).isSymbolicLink(), `${field} must not be a symlink`);
    assert(isContainedPath(repoRoot, realpathSync(candidate)), `${field} resolves outside the repository`);
  }
  return { normalized, candidate };
}

function digestFile(path) {
  return sha256(readFileSync(path));
}

function digestNormalizedTextFile(path) {
  return sha256(readFileSync(path, "utf8").replaceAll("\r\n", "\n"));
}

function validateCase(item, index, repoRoot) {
  const prefix = `cases[${index}]`;
  exactKeys(item, ["id", "version", "category", "expected_lane", "prompt", "allowed_starting_context", "fixture", "success_criteria", "required_focused_evidence", "forbidden_changes", "review_checklist", "resource_ceiling"], prefix);
  nonEmptyString(item.id, `${prefix}.id`);
  assert(/^[a-z0-9-]+$/.test(item.id), `${prefix}.id must be kebab-case`);
  assert(Number.isInteger(item.version) && item.version >= 1, `${prefix}.version must be >= 1`);
  nonEmptyString(item.category, `${prefix}.category`);
  assert(["fast", "deep"].includes(item.expected_lane), `${prefix}.expected_lane must be fast or deep`);
  nonEmptyString(item.prompt, `${prefix}.prompt`);
  stringArray(item.allowed_starting_context, `${prefix}.allowed_starting_context`, { minimum: 1 });
  assert(isPlainObject(item.fixture), `${prefix}.fixture must be an object`);
  exactKeys(item.fixture, ["kind", "patch_path", "patch_digest", "materialized_diff_digest", "changed_paths", "setup_command", "reset_command"], `${prefix}.fixture`);
  assert(item.fixture.kind === "git_apply_patch", `${prefix}.fixture.kind is unsupported`);
  const patch = resolveRepositoryArtifact(repoRoot, item.fixture.patch_path, `${prefix}.fixture.patch_path`);
  assert(/^[0-9a-f]{64}$/.test(item.fixture.patch_digest), `${prefix}.fixture.patch_digest must be SHA-256`);
  assert(digestNormalizedTextFile(patch.candidate) === item.fixture.patch_digest, `${prefix}.fixture.patch_digest does not match the patch`);
  assert(/^[0-9a-f]{64}$/.test(item.fixture.materialized_diff_digest), `${prefix}.fixture.materialized_diff_digest must be SHA-256`);
  stringArray(item.fixture.changed_paths, `${prefix}.fixture.changed_paths`, { minimum: 1 });
  item.fixture.changed_paths.forEach((path, pathIndex) => repositoryRelativePath(path, `${prefix}.fixture.changed_paths[${pathIndex}]`));
  assert(Array.isArray(item.fixture.setup_command) && item.fixture.setup_command.length >= 2, `${prefix}.fixture.setup_command is required`);
  assert(allowedSetupCommands.has(item.fixture.setup_command[0]), `${prefix}.fixture.setup_command is not allow-listed`);
  item.fixture.setup_command.forEach((part, partIndex) => nonEmptyString(part, `${prefix}.fixture.setup_command[${partIndex}]`));
  assert(Array.isArray(item.fixture.reset_command) && item.fixture.reset_command.length >= 2, `${prefix}.fixture.reset_command is required`);
  assert(allowedSetupCommands.has(item.fixture.reset_command[0]), `${prefix}.fixture.reset_command is not allow-listed`);
  item.fixture.reset_command.forEach((part, partIndex) => nonEmptyString(part, `${prefix}.fixture.reset_command[${partIndex}]`));
  stringArray(item.success_criteria, `${prefix}.success_criteria`, { minimum: 1 });
  assert(Array.isArray(item.required_focused_evidence) && item.required_focused_evidence.length > 0, `${prefix}.required_focused_evidence is required`);
  const gateIds = [];
  item.required_focused_evidence.forEach((gate, gateIndex) => {
    const field = `${prefix}.required_focused_evidence[${gateIndex}]`;
    exactKeys(gate, ["gate_id", "command_argv"], field);
    nonEmptyString(gate.gate_id, `${field}.gate_id`);
    assert(/^[a-z0-9-]+$/.test(gate.gate_id), `${field}.gate_id must be kebab-case`);
    stringArray(gate.command_argv, `${field}.command_argv`, { minimum: 1 });
    gate.command_argv.forEach((part) => assert(!/<[^>]+>/.test(part), `${field}.command_argv must not contain placeholders`));
    gateIds.push(gate.gate_id);
  });
  assert(new Set(gateIds).size === gateIds.length, `${prefix}.required_focused_evidence gate IDs must be unique`);
  stringArray(item.forbidden_changes, `${prefix}.forbidden_changes`);
  stringArray(item.review_checklist, `${prefix}.review_checklist`, { minimum: 1 });
  assert(isPlainObject(item.resource_ceiling), `${prefix}.resource_ceiling is required`);
  exactKeys(item.resource_ceiling, ["elapsed_minutes", "tool_calls", "input_tokens", "output_tokens"], `${prefix}.resource_ceiling`);
  assert(Number.isInteger(item.resource_ceiling.elapsed_minutes) && item.resource_ceiling.elapsed_minutes > 0, `${prefix}.resource_ceiling.elapsed_minutes must be positive`);
  assert(Number.isInteger(item.resource_ceiling.tool_calls) && item.resource_ceiling.tool_calls > 0, `${prefix}.resource_ceiling.tool_calls must be positive`);
  assert(Number.isInteger(item.resource_ceiling.input_tokens) && item.resource_ceiling.input_tokens > 0, `${prefix}.resource_ceiling.input_tokens must be positive`);
  assert(Number.isInteger(item.resource_ceiling.output_tokens) && item.resource_ceiling.output_tokens > 0, `${prefix}.resource_ceiling.output_tokens must be positive`);
  assertRedactedPayload(item);
  return {
    ...item,
    prompt_digest: sha256(item.prompt),
    success_criteria_digest: sha256(canonicalJson(item.success_criteria)),
    fixture_digest: sha256(canonicalJson(item.fixture)),
  };
}

export function loadCaseCatalog({ repoRoot = defaultRepoRoot, catalogPath = defaultCatalogPath } = {}) {
  const normalizedPath = repositoryRelativePath(catalogPath, "catalogPath");
  const document = readJson(resolve(repoRoot, normalizedPath));
  assert(document.schema_version === catalogSchemaVersion, `catalog schema must be ${catalogSchemaVersion}`);
  nonEmptyString(document.catalog_id, "catalog_id");
  assert(/^[0-9a-f]{40}$/.test(document.repository_commit), "repository_commit must be a full git SHA");
  assert(isPlainObject(document.comparison_policy), "comparison_policy is required");
  assert(Array.isArray(document.cases), "cases must be an array");
  const cases = document.cases.map((item, index) => validateCase(item, index, repoRoot));
  const ids = cases.map((item) => item.id);
  assert(new Set(ids).size === ids.length, "case IDs must be unique");
  for (const category of requiredCategories) {
    assert(cases.some((item) => item.category === category), `required category is missing: ${category}`);
  }
  assert(cases.length >= 6, "catalog must contain at least six representative cases");
  assert(isPlainObject(document.comparison_policy.maximum_regression_ratio), "comparison_policy.maximum_regression_ratio is required");
  assert(isPlainObject(document.comparison_policy.external_authentication), "comparison_policy.external_authentication is required");
  exactKeys(document.comparison_policy.external_authentication, ["status", "required_for_adoption"], "comparison_policy.external_authentication");
  assert(document.comparison_policy.external_authentication.status === "unsupported", "authenticated external receipts are not implemented yet");
  assert(document.comparison_policy.external_authentication.required_for_adoption === true, "external authentication must remain required for adoption");
  for (const field of ["total_elapsed_ms", "total_tool_call_count", "duplicate_command_count", "local_verification_duration_ms", "input_tokens", "output_tokens"]) {
    const ratio = document.comparison_policy.maximum_regression_ratio[field];
    assert(Number.isFinite(ratio) && ratio >= 1, `comparison_policy.maximum_regression_ratio.${field} must be >= 1`);
    const minimumDelta = document.comparison_policy.minimum_regression_delta?.[field];
    assert(Number.isFinite(minimumDelta) && minimumDelta >= 0, `comparison_policy.minimum_regression_delta.${field} must be non-negative`);
  }
  assertRedactedPayload(document);
  return {
    ...document,
    cases,
    catalog_path: normalizedPath,
    catalog_digest: sha256(canonicalJson(document)),
  };
}

function nullableNonNegative(value, field) {
  assert(value === null || (Number.isFinite(value) && value >= 0), `${field} must be null or non-negative`);
}

function nullableNonNegativeInteger(value, field) {
  assert(value === null || (Number.isInteger(value) && value >= 0), `${field} must be null or a non-negative integer`);
}

function validateProvenance(entries, field = "provenance") {
  assert(Array.isArray(entries) && entries.length > 0, `${field} must not be empty`);
  entries.forEach((entry, index) => {
    assert(isPlainObject(entry), `${field}[${index}] must be an object`);
    exactKeys(entry, ["kind", "locator", "digest"], `${field}[${index}]`);
    nonEmptyString(entry.kind, `${field}[${index}].kind`);
    nonEmptyString(entry.locator, `${field}[${index}].locator`);
    if (!entry.locator.startsWith("git:") && !entry.locator.startsWith("https://github.com/")) {
      repositoryRelativePath(entry.locator, `${field}[${index}].locator`);
    }
    if (entry.digest !== null && entry.digest !== undefined) {
      assert(/^[0-9a-f]{64}$/.test(entry.digest), `${field}[${index}].digest must be SHA-256 or null`);
    }
  });
}

function validateLocalProvenanceArtifact(provenance, repoRoot, field) {
  validateProvenance([provenance], field);
  assert(!provenance.locator.startsWith("git:") && !provenance.locator.startsWith("https://"), `${field} must identify a committed repository artifact`);
  const artifact = resolveRepositoryArtifact(repoRoot, provenance.locator, `${field}.locator`);
  assert(digestFile(artifact.candidate) === provenance.digest, `${field}.digest does not match its artifact`);
  const document = readJson(artifact.candidate);
  assertRedactedPayload(document);
  return document;
}

function expectedGateCommandDigest(gate) {
  return sha256(canonicalJson({ gate_id: gate.gate_id, command_argv: gate.command_argv }));
}

function validateRunReceipt(result, caseDefinition, repoRoot) {
  assert(result.run_receipt.status === "verified", "agent_run requires a verified run/session receipt");
  nonEmptyString(result.run_receipt.locator, "run_receipt.locator");
  assert(/^[0-9a-f]{64}$/.test(result.run_receipt.digest ?? ""), "run_receipt.digest must be SHA-256");
  const artifact = resolveRepositoryArtifact(repoRoot, result.run_receipt.locator, "run_receipt.locator");
  const receipt = readJson(artifact.candidate);
  assertRedactedPayload(receipt);
  exactKeys(receipt, ["schema_version", "source_kind", "run_id", "case_id", "case_version", "catalog_digest", "repository_commit", "prompt_digest", "fixture", "profile", "session", "metrics", "provider_usage", "human_review", "recorded_at", "content_digest"], "run/session receipt");
  assert(receipt.schema_version === "agent-throughput-run-receipt/v1", "run/session receipt schema is invalid");
  assert(receipt.source_kind === "codex_session_receipt", "run/session receipt must be provider/session-derived");
  const { content_digest: contentDigest, ...identity } = receipt;
  assert(/^[0-9a-f]{64}$/.test(contentDigest) && sha256(canonicalJson(identity)) === contentDigest, "run/session receipt content digest is invalid");
  assert(contentDigest === result.run_receipt.digest, "run/session receipt digest does not match the result");
  assert(receipt.run_id === result.run_id && receipt.case_id === result.case.id && receipt.case_version === result.case.version, "run/session receipt case identity does not match the result");
  assert(receipt.catalog_digest === result.case.catalog_digest, "run/session receipt catalog digest does not match the result");
  assert(receipt.repository_commit === result.conditions.repository_commit && receipt.prompt_digest === result.case.prompt_digest, "run/session receipt repository/prompt identity does not match the result");
  exactKeys(receipt.fixture, ["patch_digest", "materialized_diff_digest"], "run/session receipt.fixture");
  assert(receipt.fixture.patch_digest === caseDefinition.fixture.patch_digest, "run/session receipt patch digest does not match the fixture");
  assert(receipt.fixture.materialized_diff_digest === caseDefinition.fixture.materialized_diff_digest, "run/session receipt materialized diff does not match the fixture");
  exactKeys(receipt.profile, ["profile_id", "model_family", "reasoning_setting", "guidance_source_ref", "guidance_digest", "skill_tree_digest"], "run/session receipt.profile");
  assert(receipt.profile.profile_id === result.profile.id, "run/session receipt profile ID does not match the result");
  assert(receipt.profile.model_family === result.profile.model_family && receipt.profile.reasoning_setting === result.profile.reasoning_setting, "run/session receipt model profile does not match the result");
  assert(receipt.profile.guidance_source_ref === result.profile.guidance_identity.source_ref && receipt.profile.guidance_digest === result.profile.guidance_identity.root_instruction_digest, "run/session receipt guidance identity does not match the result");
  assert(receipt.profile.skill_tree_digest === result.profile.skill_inventory_identity.tree_digest, "run/session receipt Skill tree does not match the result");
  exactKeys(receipt.session, ["session_identity", "workspace_identity", "fresh_session", "fresh_workspace"], "run/session receipt.session");
  assert(receipt.session.fresh_session === true && receipt.session.fresh_workspace === true, "run/session receipt must prove fresh execution");
  assert(result.conditions.fresh_session === true && result.conditions.fresh_workspace === true, "agent_run conditions must claim the freshness proven by its receipt");
  assert(receipt.session.session_identity === result.conditions.session_identity && receipt.session.workspace_identity === result.conditions.workspace_identity, "run/session receipt freshness identity does not match the result");
  exactKeys(receipt.metrics, [...requiredAdoptionMetricFields, "command_observations"], "run/session receipt.metrics");
  for (const field of requiredAdoptionMetricFields) {
    assert(receipt.metrics[field] === result.metrics[field], `run/session receipt metric does not match: ${field}`);
  }
  assert(canonicalJson(receipt.metrics.command_observations) === canonicalJson(result.metrics.command_observations), "run/session receipt command observations do not match the result");
  exactKeys(receipt.provider_usage, ["status", "input_tokens", "output_tokens", "provenance"], "run/session receipt.provider_usage");
  assert(["measured", "not_available"].includes(receipt.provider_usage.status), "run/session receipt provider usage status is invalid");
  assert(receipt.provider_usage.input_tokens === result.metrics.input_tokens && receipt.provider_usage.output_tokens === result.metrics.output_tokens, "run/session receipt provider usage does not match token metrics");
  if (receipt.provider_usage.status === "measured") {
    assert(result.metrics.input_tokens !== null && result.metrics.output_tokens !== null, "measured provider usage requires token metrics");
    validateProvenance([receipt.provider_usage.provenance], "run/session receipt.provider_usage.provenance");
    assert(receipt.provider_usage.provenance.kind === "codex_session_usage" && receipt.provider_usage.provenance.digest !== null, "provider usage must carry session provenance");
  } else {
    assert((result.metrics.input_tokens === null || result.metrics.output_tokens === null) && receipt.provider_usage.provenance === null, "unavailable provider usage must preserve unmeasured tokens without provenance claims");
  }
  exactKeys(receipt.human_review, ["reviewer_kind", "completed_at", "artifact_digest"], "run/session receipt.human_review");
  assert(receipt.human_review.reviewer_kind === result.quality.human_review.reviewer_kind, "run/session receipt human reviewer does not match the result");
  assert(receipt.human_review.completed_at === result.quality.human_review.completed_at, "run/session receipt human review time does not match the result");
  assert(receipt.human_review.artifact_digest === result.quality.human_review.provenance?.digest, "run/session receipt human review artifact does not match the result");
  nonEmptyString(receipt.recorded_at, "run/session receipt.recorded_at");
  assert(Number.isFinite(Date.parse(receipt.recorded_at)), "run/session receipt.recorded_at must be an ISO timestamp");
  return receipt;
}

function validatePassedReceipts(result, caseDefinition, repoRoot, runReceipt) {
  assert(runReceipt, "passed verification requires a verified run/session receipt");
  const verification = result.verification;
  const expected = new Map(caseDefinition.required_focused_evidence.map((gate) => [gate.gate_id, gate]));
  assert(verification.receipts.length === expected.size, "passed verification requires one #837 receipt per focused gate");
  const observedGateIds = verification.receipts.map((entry) => entry.gate_id);
  assert(new Set(observedGateIds).size === observedGateIds.length, "verification receipt gate IDs must be unique");
  let totalDurationMs = 0;
  for (const [index, entry] of verification.receipts.entries()) {
    const field = `verification.receipts[${index}]`;
    exactKeys(entry, ["gate_id", "receipt_digest", "receipt_locator", "environment_identity_digest", "duration_ms"], field);
    const gate = expected.get(entry.gate_id);
    assert(gate, `unexpected verification gate receipt: ${entry.gate_id}`);
    nonEmptyString(entry.receipt_locator, `${field}.receipt_locator`);
    const artifact = resolveRepositoryArtifact(repoRoot, entry.receipt_locator, `${field}.receipt_locator`);
    const receipt = validateVerificationReceipt(readJson(artifact.candidate));
    assertRedactedPayload(receipt);
    assert(receipt.status === "passed", `verification gate did not pass: ${entry.gate_id}`);
    assert(receipt.gate_id === gate.gate_id, `verification receipt gate ID does not match: ${entry.gate_id}`);
    assert(receipt.command_digest === expectedGateCommandDigest(gate), `verification receipt command does not match required focused evidence: ${entry.gate_id}`);
    assert(receipt.catalog_digest === runReceipt.content_digest, `verification receipt is not bound to the run/session receipt: ${entry.gate_id}`);
    assert(receipt.content_digest === entry.receipt_digest, `verification receipt digest does not match its content: ${entry.gate_id}`);
    assert(receipt.commit_sha === result.conditions.repository_commit, `verification receipt commit does not match: ${entry.gate_id}`);
    const environmentDigest = sha256(canonicalJson(receipt.environment_identity));
    assert(environmentDigest === entry.environment_identity_digest, `verification receipt environment digest does not match: ${entry.gate_id}`);
    assert(environmentDigest === result.conditions.environment_identity_digest, `verification receipt environment does not match the run condition: ${entry.gate_id}`);
    assert(result.metrics.command_observations.some((command) => command.command_digest === receipt.command_digest), `verification command is absent from session observations: ${entry.gate_id}`);
    assert(Math.abs(receipt.duration_seconds * 1000 - entry.duration_ms) < 1, `verification receipt duration does not match: ${entry.gate_id}`);
    totalDurationMs += entry.duration_ms;
  }
  assert(Math.abs(totalDurationMs - verification.duration_ms) < 1, "verification receipt durations do not match total verification duration");
  assert(Math.abs(verification.duration_ms - result.metrics.local_verification_duration_ms) < 1, "verification duration does not match the run/session receipt metric");
}

function findCase(catalog, result) {
  const matching = catalog.cases.find((item) => item.id === result.case?.id);
  assert(matching, `unknown case: ${result.case?.id}`);
  return matching;
}

export function validateResult(result, catalog, { repoRoot = defaultRepoRoot } = {}) {
  exactKeys(result, ["schema_version", "run_id", "observation_kind", "case", "profile", "conditions", "metrics", "run_receipt", "verification", "quality", "provenance"], "result");
  assert(result.schema_version === resultSchemaVersion, `result schema must be ${resultSchemaVersion}`);
  nonEmptyString(result.run_id, "run_id");
  assert(["context_only", "agent_run"].includes(result.observation_kind), "observation_kind is invalid");
  const caseDefinition = findCase(catalog, result);
  exactKeys(result.case, ["id", "version", "prompt_digest", "prompt_bytes", "prompt_lines", "fixture_digest", "success_criteria_digest", "catalog_digest"], "case");
  assert(result.case.version === caseDefinition.version, "case version does not match catalog");
  assert(result.case.prompt_digest === caseDefinition.prompt_digest, "prompt digest does not match catalog");
  assert(result.case.fixture_digest === caseDefinition.fixture_digest, "fixture digest does not match catalog");
  assert(result.case.success_criteria_digest === caseDefinition.success_criteria_digest, "success criteria digest does not match catalog");
  assert(result.case.catalog_digest === catalog.catalog_digest, "catalog digest does not match current catalog");
  assert(Number.isInteger(result.case.prompt_bytes) && result.case.prompt_bytes > 0, "case.prompt_bytes must be positive");
  assert(Number.isInteger(result.case.prompt_lines) && result.case.prompt_lines > 0, "case.prompt_lines must be positive");
  exactKeys(result.profile, ["id", "expected_lane", "model_family", "reasoning_setting", "guidance_identity", "skill_inventory_identity", "verification_policy_digest"], "profile");
  exactKeys(result.profile.guidance_identity, ["source_ref", "materialization", "root_instruction_bytes", "root_instruction_lines", "root_instruction_digest"], "profile.guidance_identity");
  exactKeys(result.profile.skill_inventory_identity, ["source_ref", "discovery_root", "tree_digest", "typed_inventory_digest", "visible_skill_count", "visible_skill_names"], "profile.skill_inventory_identity");
  nonEmptyString(result.profile.id, "profile.id");
  assert(result.profile.expected_lane === caseDefinition.expected_lane, "profile.expected_lane does not match the case");
  assert(result.profile.model_family === null || typeof result.profile.model_family === "string", "profile.model_family must be string or null");
  assert(result.profile.reasoning_setting === null || typeof result.profile.reasoning_setting === "string", "profile.reasoning_setting must be string or null");
  assert(/^[0-9a-f]{40}$/.test(result.profile.guidance_identity.source_ref), "profile.guidance_identity.source_ref must be a full commit");
  assert(["snapshot_only_not_executed", "repository_context_observed_not_task_executed", "agent_run"].includes(result.profile.guidance_identity.materialization), "profile.guidance_identity.materialization is invalid");
  assert(Number.isInteger(result.profile.guidance_identity.root_instruction_bytes) && result.profile.guidance_identity.root_instruction_bytes > 0, "root instruction bytes must be positive");
  assert(Number.isInteger(result.profile.guidance_identity.root_instruction_lines) && result.profile.guidance_identity.root_instruction_lines > 0, "root instruction lines must be positive");
  assert(/^[0-9a-f]{64}$/.test(result.profile.guidance_identity.root_instruction_digest), "root instruction digest must be SHA-256");
  assert(/^[0-9a-f]{40}$/.test(result.profile.skill_inventory_identity.source_ref), "skill tree source_ref must be a full commit");
  assert(result.profile.skill_inventory_identity.discovery_root === ".agents/skills", "skill discovery root is invalid");
  assert(/^[0-9a-f]{64}$/.test(result.profile.skill_inventory_identity.tree_digest), "skill tree digest must be SHA-256");
  assert(result.profile.skill_inventory_identity.typed_inventory_digest === null || /^[0-9a-f]{64}$/.test(result.profile.skill_inventory_identity.typed_inventory_digest), "typed inventory digest must be SHA-256 or null");
  stringArray(result.profile.skill_inventory_identity.visible_skill_names, "visible_skill_names");
  assert(new Set(result.profile.skill_inventory_identity.visible_skill_names).size === result.profile.skill_inventory_identity.visible_skill_names.length, "visible Skill names must be unique");
  assert(result.profile.skill_inventory_identity.visible_skill_count === result.profile.skill_inventory_identity.visible_skill_names.length, "visible Skill count does not match names");
  assert(/^[0-9a-f]{64}$/.test(result.profile.verification_policy_digest), "verification policy digest must be SHA-256");
  exactKeys(result.conditions, ["repository_commit", "same_success_criteria", "fresh_session", "fresh_workspace", "session_identity", "workspace_identity", "environment_identity_digest"], "conditions");
  assert(/^[0-9a-f]{40}$/.test(result.conditions.repository_commit), "conditions.repository_commit must be a full SHA");
  assert(result.conditions.repository_commit === catalog.repository_commit, "conditions.repository_commit must equal the catalog fixed commit");
  assert(result.conditions.same_success_criteria === true, "same_success_criteria must be true");
  assert([true, false, null].includes(result.conditions.fresh_session), "fresh_session must be boolean or null");
  assert([true, false, null].includes(result.conditions.fresh_workspace), "fresh_workspace must be boolean or null");
  assert(result.conditions.environment_identity_digest === null || /^[0-9a-f]{64}$/.test(result.conditions.environment_identity_digest), "conditions.environment_identity_digest must be SHA-256 or null");
  exactKeys(result.metrics, ["first_edit_elapsed_ms", "first_edit_read_file_count", "first_edit_tool_call_count", "total_tool_call_count", "duplicate_command_count", "local_verification_duration_ms", "total_elapsed_ms", "edit_to_pass_iteration_count", "changed_file_count", "changed_line_count", "input_tokens", "output_tokens", "command_observations", "credit_proxy"], "metrics");
  for (const [field, value] of Object.entries({
    "metrics.first_edit_elapsed_ms": result.metrics.first_edit_elapsed_ms,
    "metrics.local_verification_duration_ms": result.metrics.local_verification_duration_ms,
    "metrics.total_elapsed_ms": result.metrics.total_elapsed_ms,
  })) nullableNonNegative(value, field);
  for (const [field, value] of Object.entries({
    "metrics.first_edit_read_file_count": result.metrics.first_edit_read_file_count,
    "metrics.first_edit_tool_call_count": result.metrics.first_edit_tool_call_count,
    "metrics.total_tool_call_count": result.metrics.total_tool_call_count,
    "metrics.duplicate_command_count": result.metrics.duplicate_command_count,
    "metrics.edit_to_pass_iteration_count": result.metrics.edit_to_pass_iteration_count,
    "metrics.changed_file_count": result.metrics.changed_file_count,
    "metrics.changed_line_count": result.metrics.changed_line_count,
    "metrics.input_tokens": result.metrics.input_tokens,
    "metrics.output_tokens": result.metrics.output_tokens,
  })) nullableNonNegativeInteger(value, field);
  assert(Array.isArray(result.metrics.command_observations), "metrics.command_observations must be an array");
  result.metrics.command_observations.forEach((command, index) => {
    exactKeys(command, ["command_digest", "count", "duration_ms"], `metrics.command_observations[${index}]`);
    assert(/^[0-9a-f]{64}$/.test(command.command_digest), `metrics.command_observations[${index}].command_digest must be SHA-256`);
    assert(Number.isInteger(command.count) && command.count > 0, `metrics.command_observations[${index}].count must be positive`);
    nullableNonNegative(command.duration_ms, `metrics.command_observations[${index}].duration_ms`);
  });
  assert(isPlainObject(result.metrics.credit_proxy), "metrics.credit_proxy is required when provider metrics can be unavailable");
  exactKeys(result.metrics.credit_proxy, ["kind", "value", "unit", "not_provider_token", "provenance"], "metrics.credit_proxy");
  nonEmptyString(result.metrics.credit_proxy.kind, "metrics.credit_proxy.kind");
  nullableNonNegative(result.metrics.credit_proxy.value, "metrics.credit_proxy.value");
  assert(result.metrics.credit_proxy.not_provider_token === true, "credit proxy must not be represented as provider token usage");
  validateProvenance([result.metrics.credit_proxy.provenance], "metrics.credit_proxy.provenance");
  exactKeys(result.run_receipt, ["status", "locator", "digest"], "run_receipt");
  assert(["verified", "not_available"].includes(result.run_receipt.status), "run_receipt.status is invalid");
  if (result.run_receipt.status === "not_available") {
    assert(result.run_receipt.locator === null && result.run_receipt.digest === null, "unavailable run receipt must not claim provenance");
  }
  let runReceipt = null;
  if (result.observation_kind === "agent_run") runReceipt = validateRunReceipt(result, caseDefinition, repoRoot);
  exactKeys(result.verification, ["status", "duration_ms", "receipts", "note"], "verification");
  assert(["passed", "failed", "not_run"].includes(result.verification.status), "verification.status is invalid");
  nullableNonNegative(result.verification.duration_ms, "verification.duration_ms");
  assert(Array.isArray(result.verification.receipts), "verification.receipts must be an array");
  if (result.verification.status === "passed") validatePassedReceipts(result, caseDefinition, repoRoot, runReceipt);
  else assert(result.verification.receipts.length === 0, "non-passed verification must not claim passed receipts");
  exactKeys(result.quality, ["status", "success_criteria_met", "human_review", "findings", "note"], "quality");
  assert(["passed", "failed", "not_evaluated"].includes(result.quality.status), "quality.status is invalid");
  assert(Array.isArray(result.quality.findings), "quality.findings must be an array");
  result.quality.findings.forEach((finding, index) => {
    exactKeys(finding, ["severity", "summary"], `quality.findings[${index}]`);
    assert(["low", "medium", "high", "critical"].includes(finding.severity), `quality.findings[${index}].severity is invalid`);
    nonEmptyString(finding.summary, `quality.findings[${index}].summary`);
  });
  assert(isPlainObject(result.quality.human_review), "quality.human_review is required");
  exactKeys(result.quality.human_review, ["status", "reviewer_kind", "completed_at", "provenance"], "quality.human_review");
  assert(["completed", "not_run"].includes(result.quality.human_review.status), "quality.human_review.status is invalid");
  if (result.quality.status === "passed") {
    assert(result.quality.success_criteria_met === true, "passed quality requires all success criteria");
    assert(result.quality.human_review.status === "completed", "passed quality requires completed human review");
    assert(result.quality.human_review.reviewer_kind === "independent_human", "passed quality requires independent human reviewer provenance");
    nonEmptyString(result.quality.human_review.completed_at, "quality.human_review.completed_at");
    assert(Number.isFinite(Date.parse(result.quality.human_review.completed_at)), "quality.human_review.completed_at must be an ISO timestamp");
    const review = validateLocalProvenanceArtifact(result.quality.human_review.provenance, repoRoot, "quality.human_review.provenance");
    exactKeys(review, ["schema_version", "run_id", "case_id", "reviewer_kind", "reviewer_identity", "completed_at", "verdict", "findings"], "human review artifact");
    assert(review.schema_version === "agent-throughput-human-review/v1", "human review artifact schema is invalid");
    assert(review.run_id === result.run_id && review.case_id === result.case.id, "human review artifact identity does not match the result");
    assert(review.reviewer_kind === "independent_human", "human review artifact must be independently attested");
    nonEmptyString(review.reviewer_identity, "human review artifact.reviewer_identity");
    assert(review.completed_at === result.quality.human_review.completed_at, "human review artifact completion time does not match the result");
    assert(review.verdict === "passed", "human review artifact did not pass");
    assert(canonicalJson(review.findings) === canonicalJson(result.quality.findings), "human review findings do not match the result");
  }
  if (result.observation_kind === "agent_run") {
    nonEmptyString(result.profile.model_family, "agent_run profile.model_family");
    nonEmptyString(result.profile.reasoning_setting, "agent_run profile.reasoning_setting");
    assert(result.profile.guidance_identity.materialization === "agent_run", "agent_run requires materialized guidance identity");
    if (result.conditions.fresh_session === true) nonEmptyString(result.conditions.session_identity, "fresh session identity");
    if (result.conditions.fresh_workspace === true) nonEmptyString(result.conditions.workspace_identity, "fresh workspace identity");
  }
  validateProvenance(result.provenance);
  assertRedactedPayload(result);
  return result;
}

function metricDelta(baseline, candidate, field) {
  const before = baseline.metrics[field];
  const after = candidate.metrics[field];
  return before === null || after === null ? null : after - before;
}

function proxyDelta(baseline, candidate) {
  const before = baseline.metrics.credit_proxy.value;
  const after = candidate.metrics.credit_proxy.value;
  return before === null || after === null ? null : after - before;
}

function identityReasons(baseline, candidate) {
  const reasons = [];
  const pairs = [
    ["case_id", baseline.case.id, candidate.case.id],
    ["case_version", baseline.case.version, candidate.case.version],
    ["prompt_digest", baseline.case.prompt_digest, candidate.case.prompt_digest],
    ["repository_commit", baseline.conditions.repository_commit, candidate.conditions.repository_commit],
    ["fixture_digest", baseline.case.fixture_digest, candidate.case.fixture_digest],
    ["model_family", baseline.profile.model_family, candidate.profile.model_family],
    ["reasoning_setting", baseline.profile.reasoning_setting, candidate.profile.reasoning_setting],
    ["environment_identity_digest", baseline.conditions.environment_identity_digest, candidate.conditions.environment_identity_digest],
    ["success_criteria_digest", baseline.case.success_criteria_digest, candidate.case.success_criteria_digest],
  ];
  for (const [field, before, after] of pairs) {
    if (before === null || after === null) reasons.push(`${field}:unknown`);
    else if (before !== after) reasons.push(`${field}:mismatch`);
  }
  if (baseline.conditions.fresh_session !== true || candidate.conditions.fresh_session !== true) reasons.push("fresh_session:not_proven");
  if (baseline.conditions.fresh_workspace !== true || candidate.conditions.fresh_workspace !== true) reasons.push("fresh_workspace:not_proven");
  if (baseline.conditions.session_identity !== null && baseline.conditions.session_identity === candidate.conditions.session_identity) reasons.push("fresh_session:not_independent");
  if (baseline.conditions.workspace_identity !== null && baseline.conditions.workspace_identity === candidate.conditions.workspace_identity) reasons.push("fresh_workspace:not_independent");
  if (baseline.observation_kind !== "agent_run" || candidate.observation_kind !== "agent_run") reasons.push("agent_run:not_observed");
  if (baseline.quality.status !== "passed") reasons.push(`baseline_quality:${baseline.quality.status}`);
  if (baseline.verification.status !== "passed") reasons.push(`baseline_verification:${baseline.verification.status}`);
  return [...new Set(reasons)];
}

function candidateQualityReasons(candidate) {
  const reasons = [];
  if (candidate.quality.status !== "passed") reasons.push(`quality:${candidate.quality.status}`);
  if (candidate.quality.success_criteria_met !== true) reasons.push("success_criteria:not_met");
  if (candidate.verification.status !== "passed") reasons.push(`verification:${candidate.verification.status}`);
  if (candidate.quality.findings.some((finding) => ["high", "critical"].includes(finding.severity))) {
    reasons.push("human_review:high_or_critical_finding");
  }
  return reasons;
}

const requiredAdoptionMetricFields = Object.freeze([
  "first_edit_elapsed_ms",
  "first_edit_read_file_count",
  "first_edit_tool_call_count",
  "total_tool_call_count",
  "duplicate_command_count",
  "local_verification_duration_ms",
  "total_elapsed_ms",
  "edit_to_pass_iteration_count",
  "changed_file_count",
  "changed_line_count",
  "input_tokens",
  "output_tokens",
]);

function adoptionCompletenessReasons(result, label) {
  const reasons = [];
  if (result.observation_kind !== "agent_run") reasons.push(`${label}_agent_run:not_observed`);
  for (const field of requiredAdoptionMetricFields) {
    if (result.metrics[field] === null) reasons.push(`${label}_metric:${field}:unmeasured`);
  }
  if (result.metrics.command_observations.length === 0) reasons.push(`${label}_commands:unmeasured`);
  if (result.run_receipt.status !== "verified") reasons.push(`${label}_run_receipt:not_verified`);
  if (result.verification.status !== "passed" || result.verification.receipts.length === 0) reasons.push(`${label}_receipt:not_passed`);
  if (result.quality.human_review.status !== "completed") reasons.push(`${label}_human_review:not_completed`);
  return reasons;
}

function exceedsRatio(before, after, maximumRatio, minimumDelta) {
  if (before === null || after === null) return false;
  if (after - before < minimumDelta) return false;
  if (before === 0) return after > 0;
  return after / before > maximumRatio;
}

function resourceRegressionReasons(baseline, candidate, caseDefinition, catalog) {
  const reasons = [];
  const ceiling = caseDefinition.resource_ceiling;
  if (candidate.metrics.total_elapsed_ms !== null && candidate.metrics.total_elapsed_ms > ceiling.elapsed_minutes * 60_000) reasons.push("resource_ceiling:total_elapsed_ms");
  if (candidate.metrics.total_tool_call_count !== null && candidate.metrics.total_tool_call_count > ceiling.tool_calls) reasons.push("resource_ceiling:total_tool_call_count");
  if (candidate.metrics.input_tokens !== null && candidate.metrics.input_tokens > ceiling.input_tokens) reasons.push("resource_ceiling:input_tokens");
  if (candidate.metrics.output_tokens !== null && candidate.metrics.output_tokens > ceiling.output_tokens) reasons.push("resource_ceiling:output_tokens");
  for (const [field, ratio] of Object.entries(catalog.comparison_policy.maximum_regression_ratio)) {
    if (exceedsRatio(baseline.metrics[field], candidate.metrics[field], ratio, catalog.comparison_policy.minimum_regression_delta[field])) reasons.push(`baseline_regression:${field}`);
  }
  return reasons;
}

function gitBytes(repoRoot, args) {
  const result = spawnSync("git", args, { cwd: repoRoot, encoding: null, maxBuffer: 32 * 1024 * 1024 });
  assert(result.status === 0, `git ${args.join(" ")} failed`);
  return result.stdout ?? Buffer.alloc(0);
}

function gitDiffIdentity(workspace) {
  const bytes = gitBytes(workspace, ["diff", "--binary", "--no-ext-diff", "--no-color"]);
  return { digest: sha256(bytes), bytes };
}

function changedWorkspacePaths(workspace) {
  return gitBytes(workspace, ["status", "--porcelain=v1", "-z"])
    .toString("utf8")
    .split("\0")
    .filter(Boolean)
    .map((entry) => entry.slice(3).replaceAll("\\", "/"))
    .sort();
}

function verifiedFixtureWorkspace(workspace, catalog, controllerRoot) {
  nonEmptyString(workspace, "--workspace");
  const workspaceRoot = realpathSync(resolve(workspace));
  const controller = realpathSync(controllerRoot);
  assert(workspaceRoot !== controller, "fixture setup/reset requires a separate fresh worktree");
  const topLevel = gitBytes(workspaceRoot, ["rev-parse", "--show-toplevel"]).toString("utf8").trim();
  assert(realpathSync(topLevel) === workspaceRoot, "--workspace must be a git worktree root");
  const head = gitBytes(workspaceRoot, ["rev-parse", "HEAD"]).toString("utf8").trim();
  assert(head === catalog.repository_commit, `fixture workspace HEAD must equal ${catalog.repository_commit}`);
  return workspaceRoot;
}

function runGitApply(workspace, patchPath, reverse = false) {
  const args = ["apply", ...(reverse ? ["--reverse"] : []), "--unidiff-zero", "--whitespace=nowarn", patchPath];
  const checked = spawnSync("git", ["apply", "--check", ...(reverse ? ["--reverse"] : []), "--unidiff-zero", "--whitespace=nowarn", patchPath], {
    cwd: workspace,
    encoding: "utf8",
  });
  assert(checked.status === 0, `fixture patch cannot be ${reverse ? "reversed" : "applied"}: ${checked.stderr.trim()}`);
  const applied = spawnSync("git", args, { cwd: workspace, encoding: "utf8" });
  assert(applied.status === 0, `fixture patch ${reverse ? "reset" : "setup"} failed: ${applied.stderr.trim()}`);
}

export function materializeFixture({ repoRoot = defaultRepoRoot, workspace, caseId, reset = false } = {}) {
  const catalog = loadCaseCatalog({ repoRoot });
  const item = catalog.cases.find((candidate) => candidate.id === caseId);
  assert(item, `unknown case: ${caseId}`);
  const workspaceRoot = verifiedFixtureWorkspace(workspace, catalog, repoRoot);
  const patch = resolveRepositoryArtifact(repoRoot, item.fixture.patch_path, "fixture.patch_path");
  assert(digestNormalizedTextFile(patch.candidate) === item.fixture.patch_digest, "fixture patch digest drift");
  const beforePaths = changedWorkspacePaths(workspaceRoot);
  if (reset) {
    assert(canonicalJson(beforePaths) === canonicalJson([...item.fixture.changed_paths].sort()), "fixture reset refuses unrelated or incomplete workspace changes");
    assert(gitDiffIdentity(workspaceRoot).digest === item.fixture.materialized_diff_digest, "fixture reset refuses materialized diff drift");
  } else {
    assert(beforePaths.length === 0, "fixture setup requires a clean fresh worktree");
  }
  runGitApply(workspaceRoot, patch.candidate, reset);
  const afterPaths = changedWorkspacePaths(workspaceRoot);
  if (reset) {
    assert(afterPaths.length === 0, "fixture reset did not restore a clean worktree");
  } else {
    assert(canonicalJson(afterPaths) === canonicalJson([...item.fixture.changed_paths].sort()), "fixture changed paths do not match the catalog");
    assert(gitDiffIdentity(workspaceRoot).digest === item.fixture.materialized_diff_digest, "fixture materialized diff digest does not match the catalog");
  }
  return {
    case_id: item.id,
    action: reset ? "reset" : "setup",
    repository_commit: catalog.repository_commit,
    patch_digest: item.fixture.patch_digest,
    materialized_diff_digest: reset ? null : item.fixture.materialized_diff_digest,
    changed_paths: afterPaths,
    clean: afterPaths.length === 0,
  };
}

export function compareResults(baseline, candidate, catalog, options = {}) {
  validateResult(baseline, catalog, options);
  validateResult(candidate, catalog, options);
  const authentication = catalog.comparison_policy.external_authentication.status === "unsupported"
    ? ["authenticated_external_receipt:unsupported"]
    : [];
  const identity = [...identityReasons(baseline, candidate), ...authentication];
  const quality = candidateQualityReasons(candidate);
  const completeness = [
    ...adoptionCompletenessReasons(baseline, "baseline"),
    ...adoptionCompletenessReasons(candidate, "candidate"),
  ];
  const caseDefinition = catalog.cases.find((item) => item.id === candidate.case.id);
  const resource = resourceRegressionReasons(baseline, candidate, caseDefinition, catalog);
  const comparable = identity.length === 0;
  const qualityRegression = candidate.quality.status === "not_evaluated"
    ? null
    : quality.length > 0;
  const rollbackReasons = [...identity, ...quality, ...completeness, ...resource];
  const adoptable = comparable && authentication.length === 0 && quality.length === 0 && completeness.length === 0 && resource.length === 0;
  return {
    schema_version: comparisonSchemaVersion,
    case_id: candidate.case.id,
    baseline_run_id: baseline.run_id,
    candidate_run_id: candidate.run_id,
    comparability: { status: comparable ? "comparable" : "incomparable", reasons: identity },
    external_authentication: {
      status: catalog.comparison_policy.external_authentication.status,
      required_for_adoption: true,
      note: "Content digests prove integrity only; no authenticated external recorder or signature verifier is configured."
    },
    quality_regression: qualityRegression,
    resource_regression: { status: resource.length === 0 ? "within_budget" : "regressed", reasons: resource },
    resource_deltas: {
      first_edit_elapsed_ms: metricDelta(baseline, candidate, "first_edit_elapsed_ms"),
      total_tool_call_count: metricDelta(baseline, candidate, "total_tool_call_count"),
      duplicate_command_count: metricDelta(baseline, candidate, "duplicate_command_count"),
      local_verification_duration_ms: metricDelta(baseline, candidate, "local_verification_duration_ms"),
      total_elapsed_ms: metricDelta(baseline, candidate, "total_elapsed_ms"),
      input_tokens: metricDelta(baseline, candidate, "input_tokens"),
      output_tokens: metricDelta(baseline, candidate, "output_tokens"),
      credit_proxy_value: proxyDelta(baseline, candidate)
    },
    decision: {
      action: adoptable ? "adopt_candidate" : "rollback_candidate_profile",
      rollback_required: !adoptable,
      scope: "evaluation_profile_only",
      reasons: rollbackReasons,
      note: "This decision never mutates repository guidance or user configuration automatically."
    }
  };
}

function gitText(repoRoot, args) {
  const result = spawnSync("git", args, { cwd: repoRoot, encoding: "utf8", maxBuffer: 8 * 1024 * 1024 });
  assert(result.status === 0, `git ${args.join(" ")} failed`);
  return result.stdout;
}

function fileIdentity(text) {
  return { bytes: Buffer.byteLength(text, "utf8"), lines: lineCount(text), digest: sha256(text) };
}

function loadSkillTreeContext(repoRoot, sourceRef, mode) {
  const treeBytes = gitBytes(repoRoot, ["ls-tree", "-r", "--full-tree", sourceRef, ".agents/skills"]);
  const treeText = treeBytes.toString("utf8");
  const visibleSkillNames = treeText
    .split("\n")
    .filter(Boolean)
    .map((line) => line.split("\t")[1])
    .filter((path) => /^\.agents\/skills\/[^/]+\/SKILL\.md$/.test(path))
    .map((path) => path.split("/")[2])
    .sort();
  assert(visibleSkillNames.length > 0, `no visible Skill entries found at ${sourceRef}`);
  const inventoryPath = ".agents/skill-inventory.json";
  const report = mode === "current"
    ? checkSkillInventory({ repoRoot, inventoryPath, strictTarget: true })
    : null;
  if (report) {
    assert(report.ok, "skill inventory must pass its strict checker before it can identify an eval result");
    assert(report.discovery.observed_count === visibleSkillNames.length, "current Skill inventory count disagrees with the fixed git tree");
    assert(canonicalJson(report.discovery.observed_names) === canonicalJson(visibleSkillNames), "current Skill inventory names disagree with the fixed git tree");
  }
  return {
    source_ref: sourceRef,
    discovery_root: ".agents/skills",
    tree_digest: sha256(treeBytes),
    visible_skill_count: visibleSkillNames.length,
    visible_skill_names: visibleSkillNames,
    typed_inventory_digest: report?.inventory_digest ?? null,
    provenance: { kind: "git_tree", locator: `git:${sourceRef}:.agents/skills`, digest: sha256(treeBytes) },
  };
}

function profileConfiguration(mode, catalog) {
  if (mode === "baseline") {
    return {
      id: "pre-throughput-guidance",
      guidance_ref: "06f83a11a1a30695b40bd2be8e4181b17d6bb3dd",
      observation_date: "2026-08-08",
      repository_commit: catalog.repository_commit,
      materialization: "snapshot_only_not_executed",
    };
  }
  if (mode === "current") {
    return {
      id: "router-receipts-skill-visibility",
      guidance_ref: catalog.repository_commit,
      observation_date: "2026-08-08",
      repository_commit: catalog.repository_commit,
      materialization: "repository_context_observed_not_task_executed",
    };
  }
  throw new Error(`unknown observation profile: ${mode}`);
}

export function buildContextObservations({ repoRoot = defaultRepoRoot, mode, catalog = loadCaseCatalog({ repoRoot }) } = {}) {
  const profile = profileConfiguration(mode, catalog);
  const guidance = gitText(repoRoot, ["show", `${profile.guidance_ref}:AGENTS.md`]);
  const guidanceIdentity = fileIdentity(guidance);
  const inventory = loadSkillTreeContext(repoRoot, profile.guidance_ref, mode);
  const policyPath = "docs/operations/verification-policy.md";
  const policyIdentity = fileIdentity(readFileSync(resolve(repoRoot, policyPath), "utf8"));
  return catalog.cases.map((item) => ({
    schema_version: resultSchemaVersion,
    run_id: `${mode}-context-${item.id}-2026-08-08`,
    observation_kind: "context_only",
    case: {
      id: item.id,
      version: item.version,
      prompt_digest: item.prompt_digest,
      prompt_bytes: Buffer.byteLength(item.prompt, "utf8"),
      prompt_lines: lineCount(item.prompt),
      fixture_digest: item.fixture_digest,
      success_criteria_digest: item.success_criteria_digest,
      catalog_digest: catalog.catalog_digest,
    },
    profile: {
      id: profile.id,
      expected_lane: item.expected_lane,
      model_family: null,
      reasoning_setting: null,
      guidance_identity: {
        source_ref: profile.guidance_ref,
        materialization: profile.materialization,
        root_instruction_bytes: guidanceIdentity.bytes,
        root_instruction_lines: guidanceIdentity.lines,
        root_instruction_digest: guidanceIdentity.digest,
      },
      skill_inventory_identity: {
        source_ref: inventory.source_ref,
        discovery_root: inventory.discovery_root,
        tree_digest: inventory.tree_digest,
        typed_inventory_digest: inventory.typed_inventory_digest,
        visible_skill_count: inventory.visible_skill_count,
        visible_skill_names: inventory.visible_skill_names,
      },
      verification_policy_digest: policyIdentity.digest,
    },
    conditions: {
      repository_commit: profile.repository_commit,
      same_success_criteria: true,
      fresh_session: null,
      fresh_workspace: null,
      session_identity: null,
      workspace_identity: null,
      environment_identity_digest: null,
    },
    metrics: {
      first_edit_elapsed_ms: null,
      first_edit_read_file_count: null,
      first_edit_tool_call_count: null,
      total_tool_call_count: null,
      duplicate_command_count: null,
      local_verification_duration_ms: null,
      total_elapsed_ms: null,
      edit_to_pass_iteration_count: null,
      changed_file_count: null,
      changed_line_count: null,
      input_tokens: null,
      output_tokens: null,
      command_observations: [],
      credit_proxy: {
        kind: "repository_root_instruction_bytes",
        value: guidanceIdentity.bytes,
        unit: "bytes",
        not_provider_token: true,
        provenance: { kind: "git_object", locator: `git:${profile.guidance_ref}:AGENTS.md`, digest: guidanceIdentity.digest },
      },
    },
    run_receipt: {
      status: "not_available",
      locator: null,
      digest: null,
    },
    verification: {
      status: "not_run",
      duration_ms: null,
      receipts: [],
      note: "No agent task was run; a verification receipt is not claimed."
    },
    quality: {
      status: "not_evaluated",
      success_criteria_met: null,
      human_review: {
        status: "not_run",
        reviewer_kind: null,
        completed_at: null,
        provenance: null,
      },
      findings: [],
      note: "Context metrics alone cannot approve the fast/deep profile."
    },
    provenance: [
      { kind: "case_catalog", locator: catalog.catalog_path, digest: catalog.catalog_digest },
      { kind: "git_object", locator: `git:${profile.guidance_ref}:AGENTS.md`, digest: guidanceIdentity.digest },
      inventory.provenance,
      { kind: "verification_policy", locator: policyPath, digest: policyIdentity.digest },
    ],
  }));
}

function resultsDirectory(repoRoot, mode) {
  return resolve(repoRoot, "benchmarks", "agent-throughput", "results", mode);
}

function writeJson(path, value) {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, `${JSON.stringify(value, null, 2)}\n`, "utf8");
}

export function validateResultCoverage(results, catalog, { names = results.map((result) => `${result.case.id}.json`), mode = "result" } = {}) {
  assert(results.length === names.length, `${mode} result names do not match result count`);
  results.forEach((result, index) => {
    const name = names[index];
    assert(name === `${result.case.id}.json`, `${mode} result filename does not match case identity: ${name}`);
  });
  const observedIds = results.map((result) => result.case.id).sort();
  const expectedIds = catalog.cases.map((item) => item.id).sort();
  assert(new Set(observedIds).size === observedIds.length, `${mode} results contain duplicate case identities`);
  assert(canonicalJson(observedIds) === canonicalJson(expectedIds), `${mode} results must cover the catalog case set exactly`);
  return results;
}

function loadResults(repoRoot, mode, catalog) {
  const directory = resultsDirectory(repoRoot, mode);
  assert(existsSync(directory), `result directory is missing: ${relative(repoRoot, directory)}`);
  const names = readdirSync(directory).filter((name) => name.endsWith(".json")).sort();
  const results = names.map((name) => validateResult(readJson(join(directory, name)), catalog));
  return validateResultCoverage(results, catalog, { names, mode });
}

function observeCommand(repoRoot, mode) {
  const catalog = loadCaseCatalog({ repoRoot });
  const observations = buildContextObservations({ repoRoot, mode, catalog });
  for (const result of observations) {
    validateResult(result, catalog);
    writeJson(join(resultsDirectory(repoRoot, mode), `${result.case.id}.json`), result);
  }
  return { profile: mode, observations: observations.length };
}

function compareCommand(repoRoot) {
  const catalog = loadCaseCatalog({ repoRoot });
  const baseline = new Map(loadResults(repoRoot, "baseline", catalog).map((item) => [item.case.id, item]));
  const candidate = loadResults(repoRoot, "current", catalog);
  const comparisons = candidate.map((item) => {
    assert(baseline.has(item.case.id), `baseline result is missing: ${item.case.id}`);
    return compareResults(baseline.get(item.case.id), item, catalog);
  });
  const directory = resolve(repoRoot, "benchmarks", "agent-throughput", "comparisons");
  for (const comparison of comparisons) writeJson(join(directory, `${comparison.case_id}.json`), comparison);
  return {
    comparisons: comparisons.length,
    comparable: comparisons.filter((item) => item.comparability.status === "comparable").length,
    rollback_required: comparisons.filter((item) => item.decision.rollback_required).length,
  };
}

export function validateComparisonCoverage(comparisons, catalog, { names = comparisons.map((item) => `${item.case_id}.json`) } = {}) {
  assert(comparisons.length === names.length, "comparison names do not match comparison count");
  comparisons.forEach((comparison, index) => {
    assert(names[index] === `${comparison.case_id}.json`, `comparison filename does not match case identity: ${names[index]}`);
  });
  const observedIds = comparisons.map((comparison) => comparison.case_id).sort();
  const expectedIds = catalog.cases.map((item) => item.id).sort();
  assert(new Set(observedIds).size === observedIds.length, "comparisons contain duplicate case identities");
  assert(canonicalJson(observedIds) === canonicalJson(expectedIds), "comparisons must cover the catalog case set exactly");
  return comparisons;
}

function validateCommand(repoRoot) {
  const catalog = loadCaseCatalog({ repoRoot });
  const baseline = loadResults(repoRoot, "baseline", catalog);
  const current = loadResults(repoRoot, "current", catalog);
  const comparisonDirectory = resolve(repoRoot, "benchmarks", "agent-throughput", "comparisons");
  const comparisonNames = readdirSync(comparisonDirectory).filter((name) => name.endsWith(".json")).sort();
  const storedComparisons = validateComparisonCoverage(
    comparisonNames.map((name) => readJson(join(comparisonDirectory, name))),
    catalog,
    { names: comparisonNames },
  );
  assert(baseline.length === catalog.cases.length, "baseline result count must match catalog");
  assert(current.length === catalog.cases.length, "current result count must match catalog");
  assert(storedComparisons.length === catalog.cases.length, "comparison result count must match catalog");
  const recomputed = new Map(current.map((item) => [item.case.id, compareResults(
    baseline.find((before) => before.case.id === item.case.id),
    item,
    catalog,
  )]));
  for (const stored of storedComparisons) {
    assert(stored.schema_version === comparisonSchemaVersion, "comparison schema is invalid");
    assert(canonicalJson(stored) === canonicalJson(recomputed.get(stored.case_id)), `comparison drift: ${stored.case_id}`);
    assertRedactedPayload(stored);
  }
  return {
    ok: true,
    catalog_id: catalog.catalog_id,
    catalog_digest: catalog.catalog_digest,
    cases: catalog.cases.length,
    baseline_context_observations: baseline.length,
    current_context_observations: current.length,
    comparable_agent_runs: storedComparisons.filter((item) => item.comparability.status === "comparable").length,
    rollback_required: storedComparisons.filter((item) => item.decision.rollback_required).length,
    provider_token_measurements: current.filter((item) => item.metrics.input_tokens !== null || item.metrics.output_tokens !== null).length,
    note: "Context-only observations are real measurements but are not paired agent-run evidence."
  };
}

function argumentValue(args, name) {
  const index = args.indexOf(name);
  return index >= 0 ? args[index + 1] : null;
}

function cli(argv = process.argv.slice(2), repoRoot = defaultRepoRoot) {
  const [command] = argv;
  if (command === "show-case") {
    const caseId = argumentValue(argv, "--case");
    const catalog = loadCaseCatalog({ repoRoot });
    const item = catalog.cases.find((candidate) => candidate.id === caseId);
    assert(item, `unknown case: ${caseId}`);
    return { ...item, repository_commit: catalog.repository_commit, catalog_digest: catalog.catalog_digest };
  }
  if (["setup", "reset"].includes(command)) {
    return materializeFixture({
      repoRoot,
      workspace: argumentValue(argv, "--workspace"),
      caseId: argumentValue(argv, "--case"),
      reset: command === "reset",
    });
  }
  if (command === "observe") {
    const profile = argumentValue(argv, "--profile");
    assert(["baseline", "current"].includes(profile), "observe requires --profile baseline|current");
    return observeCommand(repoRoot, profile);
  }
  if (command === "compare") return compareCommand(repoRoot);
  if (command === "validate") return validateCommand(repoRoot);
  throw new Error("usage: agent-throughput-eval.mjs validate | observe --profile baseline|current | compare | show-case --case <id> | setup|reset --case <id> --workspace <fresh-worktree>");
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  try {
    process.stdout.write(`${JSON.stringify(cli(), null, 2)}\n`);
  } catch (error) {
    process.stderr.write(`${error.message}\n`);
    process.exitCode = 1;
  }
}
