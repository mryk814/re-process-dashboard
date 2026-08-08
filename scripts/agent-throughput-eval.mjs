import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { dirname, isAbsolute, join, relative, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { checkSkillInventory } from "./check-skill-inventory.mjs";

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
const forbiddenPayloadKeys = /^(?:raw_)?(?:transcript|conversation|messages|provider_reasoning|api_key|password|authorization)$/i;
const secretValuePattern = /(?:\b(?:sk-|ghp_|gho_|github_pat_|AKIA)[A-Za-z0-9_-]{8,}|(?:token|secret|password|authorization|api[_-]?key)\s*[:=]\s*[^\s<]+)/i;
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
    .replace(/(?:\b(?:sk-|ghp_|gho_|github_pat_|AKIA)[A-Za-z0-9_-]{8,}|(?:token|secret|password|authorization|api[_-]?key)\s*[:=]\s*[^\s<]+)/gi, "<redacted-secret>")
    .replace(/(?:[A-Za-z]:[\\/](?:Users|Documents and Settings)[\\/][^\r\n\"'`]+|\/(?:Users|home|root)\/[^\r\n\"'`]+|%USERPROFILE%[^\r\n\"'`]*|\$HOME[^\r\n\"'`]*|~[\\/][^\r\n\"'`]*)/gi, "<redacted-home-path>");
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function isPlainObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
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

function validateCase(item, index) {
  const prefix = `cases[${index}]`;
  assert(isPlainObject(item), `${prefix} must be an object`);
  nonEmptyString(item.id, `${prefix}.id`);
  assert(/^[a-z0-9-]+$/.test(item.id), `${prefix}.id must be kebab-case`);
  assert(Number.isInteger(item.version) && item.version >= 1, `${prefix}.version must be >= 1`);
  nonEmptyString(item.category, `${prefix}.category`);
  assert(["fast", "deep"].includes(item.expected_lane), `${prefix}.expected_lane must be fast or deep`);
  nonEmptyString(item.prompt, `${prefix}.prompt`);
  stringArray(item.allowed_starting_context, `${prefix}.allowed_starting_context`, { minimum: 1 });
  assert(isPlainObject(item.fixture), `${prefix}.fixture must be an object`);
  assert(item.fixture.kind === "versioned_task_contract", `${prefix}.fixture.kind is unsupported`);
  assert(Array.isArray(item.fixture.setup_command) && item.fixture.setup_command.length >= 2, `${prefix}.fixture.setup_command is required`);
  assert(allowedSetupCommands.has(item.fixture.setup_command[0]), `${prefix}.fixture.setup_command is not allow-listed`);
  item.fixture.setup_command.forEach((part, partIndex) => nonEmptyString(part, `${prefix}.fixture.setup_command[${partIndex}]`));
  stringArray(item.success_criteria, `${prefix}.success_criteria`, { minimum: 1 });
  stringArray(item.required_focused_evidence, `${prefix}.required_focused_evidence`, { minimum: 1 });
  stringArray(item.forbidden_changes, `${prefix}.forbidden_changes`);
  stringArray(item.review_checklist, `${prefix}.review_checklist`, { minimum: 1 });
  assert(isPlainObject(item.resource_ceiling), `${prefix}.resource_ceiling is required`);
  assert(Number.isInteger(item.resource_ceiling.elapsed_minutes) && item.resource_ceiling.elapsed_minutes > 0, `${prefix}.resource_ceiling.elapsed_minutes must be positive`);
  assert(Number.isInteger(item.resource_ceiling.tool_calls) && item.resource_ceiling.tool_calls > 0, `${prefix}.resource_ceiling.tool_calls must be positive`);
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
  const cases = document.cases.map(validateCase);
  const ids = cases.map((item) => item.id);
  assert(new Set(ids).size === ids.length, "case IDs must be unique");
  for (const category of requiredCategories) {
    assert(cases.some((item) => item.category === category), `required category is missing: ${category}`);
  }
  assert(cases.length >= 6, "catalog must contain at least six representative cases");
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

function validateProvenance(entries, field = "provenance") {
  assert(Array.isArray(entries) && entries.length > 0, `${field} must not be empty`);
  entries.forEach((entry, index) => {
    assert(isPlainObject(entry), `${field}[${index}] must be an object`);
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

function findCase(catalog, result) {
  const matching = catalog.cases.find((item) => item.id === result.case?.id);
  assert(matching, `unknown case: ${result.case?.id}`);
  return matching;
}

export function validateResult(result, catalog) {
  assert(isPlainObject(result), "result must be an object");
  assert(result.schema_version === resultSchemaVersion, `result schema must be ${resultSchemaVersion}`);
  nonEmptyString(result.run_id, "run_id");
  assert(["context_only", "agent_run"].includes(result.observation_kind), "observation_kind is invalid");
  const caseDefinition = findCase(catalog, result);
  assert(result.case.version === caseDefinition.version, "case version does not match catalog");
  assert(result.case.prompt_digest === caseDefinition.prompt_digest, "prompt digest does not match catalog");
  assert(result.case.fixture_digest === caseDefinition.fixture_digest, "fixture digest does not match catalog");
  assert(result.case.success_criteria_digest === caseDefinition.success_criteria_digest, "success criteria digest does not match catalog");
  assert(result.case.catalog_digest === catalog.catalog_digest, "catalog digest does not match current catalog");
  assert(isPlainObject(result.profile), "profile is required");
  nonEmptyString(result.profile.id, "profile.id");
  assert(result.profile.model_family === null || typeof result.profile.model_family === "string", "profile.model_family must be string or null");
  assert(result.profile.reasoning_setting === null || typeof result.profile.reasoning_setting === "string", "profile.reasoning_setting must be string or null");
  assert(isPlainObject(result.conditions), "conditions are required");
  assert(/^[0-9a-f]{40}$/.test(result.conditions.repository_commit), "conditions.repository_commit must be a full SHA");
  assert([true, false, null].includes(result.conditions.fresh_session), "fresh_session must be boolean or null");
  assert([true, false, null].includes(result.conditions.fresh_workspace), "fresh_workspace must be boolean or null");
  assert(isPlainObject(result.metrics), "metrics are required");
  for (const [field, value] of Object.entries({
    "metrics.first_edit_elapsed_ms": result.metrics.first_edit_elapsed_ms,
    "metrics.first_edit_read_file_count": result.metrics.first_edit_read_file_count,
    "metrics.first_edit_tool_call_count": result.metrics.first_edit_tool_call_count,
    "metrics.total_tool_call_count": result.metrics.total_tool_call_count,
    "metrics.duplicate_command_count": result.metrics.duplicate_command_count,
    "metrics.local_verification_duration_ms": result.metrics.local_verification_duration_ms,
    "metrics.total_elapsed_ms": result.metrics.total_elapsed_ms,
    "metrics.edit_to_pass_iteration_count": result.metrics.edit_to_pass_iteration_count,
    "metrics.changed_file_count": result.metrics.changed_file_count,
    "metrics.changed_line_count": result.metrics.changed_line_count,
    "metrics.input_tokens": result.metrics.input_tokens,
    "metrics.output_tokens": result.metrics.output_tokens,
  })) nullableNonNegative(value, field);
  assert(Array.isArray(result.metrics.command_observations), "metrics.command_observations must be an array");
  result.metrics.command_observations.forEach((command, index) => {
    assert(/^[0-9a-f]{64}$/.test(command.command_digest), `metrics.command_observations[${index}].command_digest must be SHA-256`);
    assert(Number.isInteger(command.count) && command.count > 0, `metrics.command_observations[${index}].count must be positive`);
    nullableNonNegative(command.duration_ms, `metrics.command_observations[${index}].duration_ms`);
  });
  assert(isPlainObject(result.metrics.credit_proxy), "metrics.credit_proxy is required when provider metrics can be unavailable");
  nonEmptyString(result.metrics.credit_proxy.kind, "metrics.credit_proxy.kind");
  nullableNonNegative(result.metrics.credit_proxy.value, "metrics.credit_proxy.value");
  assert(result.metrics.credit_proxy.not_provider_token === true, "credit proxy must not be represented as provider token usage");
  validateProvenance([result.metrics.credit_proxy.provenance], "metrics.credit_proxy.provenance");
  assert(isPlainObject(result.verification), "verification is required");
  assert(["passed", "failed", "not_run"].includes(result.verification.status), "verification.status is invalid");
  nullableNonNegative(result.verification.duration_ms, "verification.duration_ms");
  assert(result.verification.receipt_digest === null || /^[0-9a-f]{64}$/.test(result.verification.receipt_digest), "verification.receipt_digest must be SHA-256 or null");
  if (result.verification.receipt_digest !== null) repositoryRelativePath(result.verification.receipt_locator, "verification.receipt_locator");
  assert(isPlainObject(result.quality), "quality is required");
  assert(["passed", "failed", "not_evaluated"].includes(result.quality.status), "quality.status is invalid");
  assert(Array.isArray(result.quality.findings), "quality.findings must be an array");
  result.quality.findings.forEach((finding, index) => {
    assert(["low", "medium", "high", "critical"].includes(finding.severity), `quality.findings[${index}].severity is invalid`);
    nonEmptyString(finding.summary, `quality.findings[${index}].summary`);
  });
  if (result.quality.status === "passed") {
    assert(result.quality.success_criteria_met === true, "passed quality requires all success criteria");
  }
  if (result.observation_kind === "agent_run") {
    nonEmptyString(result.profile.model_family, "agent_run profile.model_family");
    nonEmptyString(result.profile.reasoning_setting, "agent_run profile.reasoning_setting");
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

export function compareResults(baseline, candidate, catalog) {
  validateResult(baseline, catalog);
  validateResult(candidate, catalog);
  const identity = identityReasons(baseline, candidate);
  const quality = candidateQualityReasons(candidate);
  const comparable = identity.length === 0;
  const qualityRegression = candidate.quality.status === "not_evaluated"
    ? null
    : quality.length > 0;
  const rollbackReasons = [...identity, ...quality];
  return {
    schema_version: comparisonSchemaVersion,
    case_id: candidate.case.id,
    baseline_run_id: baseline.run_id,
    candidate_run_id: candidate.run_id,
    comparability: { status: comparable ? "comparable" : "incomparable", reasons: identity },
    quality_regression: qualityRegression,
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
      action: comparable && quality.length === 0 ? "adopt_candidate" : "rollback_candidate_profile",
      rollback_required: !(comparable && quality.length === 0),
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

function loadInventoryContext(repoRoot, mode) {
  const inventoryPath = ".agents/skill-inventory.json";
  const document = readJson(resolve(repoRoot, inventoryPath));
  const report = checkSkillInventory({ repoRoot, inventoryPath, strictTarget: true });
  assert(report.ok, "skill inventory must pass its strict checker before it can identify an eval result");
  const observation = mode === "baseline" ? document.discovery.baseline : document.discovery.current;
  return {
    inventory_id: document.inventory_id,
    inventory_digest: report.inventory_digest,
    visible_skill_count: observation.visible_count,
    visible_skill_names: observation.visible_names,
    provenance: { kind: "skill_inventory", locator: inventoryPath, digest: report.inventory_digest },
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
  const inventory = loadInventoryContext(repoRoot, mode);
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
        inventory_id: inventory.inventory_id,
        inventory_digest: inventory.inventory_digest,
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
    verification: {
      status: "not_run",
      duration_ms: null,
      receipt_digest: null,
      receipt_locator: null,
      note: "No agent task was run; a verification receipt is not claimed."
    },
    quality: {
      status: "not_evaluated",
      success_criteria_met: null,
      human_review_status: "not_run",
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

function loadResults(repoRoot, mode, catalog) {
  const directory = resultsDirectory(repoRoot, mode);
  assert(existsSync(directory), `result directory is missing: ${relative(repoRoot, directory)}`);
  return readdirSync(directory)
    .filter((name) => name.endsWith(".json"))
    .sort()
    .map((name) => validateResult(readJson(join(directory, name)), catalog));
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

function validateCommand(repoRoot) {
  const catalog = loadCaseCatalog({ repoRoot });
  const baseline = loadResults(repoRoot, "baseline", catalog);
  const current = loadResults(repoRoot, "current", catalog);
  const comparisonDirectory = resolve(repoRoot, "benchmarks", "agent-throughput", "comparisons");
  const storedComparisons = readdirSync(comparisonDirectory)
    .filter((name) => name.endsWith(".json"))
    .sort()
    .map((name) => readJson(join(comparisonDirectory, name)));
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
  if (command === "observe") {
    const profile = argumentValue(argv, "--profile");
    assert(["baseline", "current"].includes(profile), "observe requires --profile baseline|current");
    return observeCommand(repoRoot, profile);
  }
  if (command === "compare") return compareCommand(repoRoot);
  if (command === "validate") return validateCommand(repoRoot);
  throw new Error("usage: agent-throughput-eval.mjs validate | observe --profile baseline|current | compare | show-case --case <id>");
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  try {
    process.stdout.write(`${JSON.stringify(cli(), null, 2)}\n`);
  } catch (error) {
    process.stderr.write(`${error.message}\n`);
    process.exitCode = 1;
  }
}
