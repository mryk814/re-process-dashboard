import { readFileSync } from "node:fs";
import { resolve } from "node:path";

export const catalogPath = resolve(import.meta.dirname, "verification-gates.json");

const requiredGateFields = [
  "command",
  "purpose",
  "estimatedMinutes",
  "platform",
  "riskCategories",
];
const levelOrder = ["edit", "pr", "checkpoint", "release"];

export function loadVerificationCatalog(path = catalogPath) {
  const catalog = JSON.parse(readFileSync(path, "utf8"));
  validateVerificationCatalog(catalog);
  return catalog;
}

export function validateVerificationCatalog(catalog) {
  if (catalog.schemaVersion !== "verification-gates/v2") {
    throw new Error("verification catalog schemaVersion must be verification-gates/v2");
  }
  if (!Array.isArray(catalog.levels) || catalog.levels.length !== 4) {
    throw new Error("verification catalog must declare exactly four levels");
  }
  const levelIds = new Set();
  for (const level of catalog.levels) {
    if (levelIds.has(level.id)) throw new Error(`duplicate level: ${level.id}`);
    levelIds.add(level.id);
    for (const field of ["label", "purpose", "estimatedMinutes", "platform", "gates", "evidence"]) {
      if (level[field] === undefined) throw new Error(`level ${level.id} is missing ${field}`);
    }
  }
  if ([...levelIds].join(",") !== levelOrder.join(",")) {
    throw new Error("verification levels must be edit, pr, checkpoint, release");
  }
  for (const [id, gate] of Object.entries(catalog.gates ?? {})) {
    for (const field of requiredGateFields) {
      if (gate[field] === undefined) throw new Error(`gate ${id} is missing ${field}`);
    }
    if (!gate.manual && !gate.runner) throw new Error(`gate ${id} must declare runner or manual`);
  }
  for (const level of catalog.levels) {
    for (const gateId of level.gates) {
      if (!catalog.gates[gateId]) throw new Error(`level ${level.id} references unknown gate ${gateId}`);
      if (catalog.gates[gateId].manual) throw new Error(`automated level ${level.id} cannot run manual gate ${gateId}`);
    }
  }
  const risks = new Set();
  for (const rule of catalog.riskMatrix ?? []) {
    if (risks.has(rule.risk)) throw new Error(`duplicate risk rule: ${rule.risk}`);
    risks.add(rule.risk);
    if (!levelIds.has(rule.minimumLevel)) {
      throw new Error(`risk ${rule.risk} references unknown level ${rule.minimumLevel}`);
    }
    for (const gateId of [...(rule.requiredGates ?? []), ...(rule.checkpointOnly ?? [])]) {
      if (!catalog.gates[gateId]) throw new Error(`risk ${rule.risk} references unknown gate ${gateId}`);
    }
  }
  if (!catalog.planning || !Array.isArray(catalog.planning.pathRules) || catalog.planning.pathRules.length === 0) {
    throw new Error("verification catalog must declare planning.pathRules");
  }
  for (const rule of catalog.planning.pathRules) {
    if (!risks.has(rule.risk)) throw new Error(`path rule references unknown risk: ${rule.risk}`);
    if (!Array.isArray(rule.matches) || rule.matches.length === 0) {
      throw new Error(`path rule for ${rule.risk} needs matches`);
    }
  }
  for (const rule of catalog.planning.focusedTestAuthority ?? []) {
    if (!Array.isArray(rule.tests) || rule.tests.length === 0) {
      throw new Error("focused test authority needs tests");
    }
  }
  return catalog;
}

export function getVerificationLevel(catalog, levelId) {
  const level = catalog.levels.find((candidate) => candidate.id === levelId);
  if (!level) throw new Error(`unknown verification level: ${levelId}`);
  return level;
}

export function resolveRunner(gate, { focusedArgs = [], baseRef = "origin/main" } = {}) {
  if (gate.manual) throw new Error(`manual gate cannot be executed: ${gate.command}`);
  const args = gate.runner.args.flatMap((argument) =>
    argument === "$BASE...HEAD" ? [`${baseRef}...HEAD`] : [argument],
  );
  if (gate.runner.appendFocusedArgs) args.push(...focusedArgs);
  return { executable: gate.runner.executable, args };
}

function pathMatches(path, matcher) {
  if (matcher.startsWith("prefix:")) return path.startsWith(matcher.slice("prefix:".length));
  if (matcher.startsWith("exact:")) return path === matcher.slice("exact:".length);
  if (matcher.startsWith("contains:")) return path.includes(matcher.slice("contains:".length));
  throw new Error(`unsupported path matcher: ${matcher}`);
}

export function classifyChangedPath(path, catalog) {
  const normalized = path.replaceAll("\\", "/");
  const rule = catalog.planning.pathRules.find((candidate) =>
    candidate.matches.some((matcher) => pathMatches(normalized, matcher)),
  );
  return rule?.risk ?? "unknown";
}

export function classifyChangedPaths(paths, catalog) {
  return [...new Set(paths.map((path) => classifyChangedPath(path, catalog)))].sort();
}

export function requiresBackendPytest(riskCategories) {
  return riskCategories.some((risk) =>
    ["backend-application", "api-contract", "migration-workspace", "model-runtime", "security", "unknown"].includes(risk),
  );
}

function riskRuleMap(catalog) {
  return new Map(catalog.riskMatrix.map((rule) => [rule.risk, rule]));
}

function levelRank(level) {
  return levelOrder.indexOf(level);
}

function strongestMinimumLevel(risks, rules, requestedLevel) {
  return risks.reduce((strongest, risk) =>
    levelRank(rules.get(risk).minimumLevel) > levelRank(strongest)
      ? rules.get(risk).minimumLevel
      : strongest,
  requestedLevel);
}

export function resolveFocusedTests({ catalog, changedPaths, focusedArgs = [] }) {
  if (focusedArgs.length > 0) {
    return { tests: focusedArgs, source: "explicit", fallback: false };
  }
  const normalizedPaths = changedPaths.map((path) => path.replaceAll("\\", "/"));
  const tests = new Set();
  for (const authority of catalog.planning.focusedTestAuthority ?? []) {
    if (normalizedPaths.some((path) => authority.matches.some((matcher) => pathMatches(path, matcher)))) {
      authority.tests.forEach((testPath) => tests.add(testPath));
    }
  }
  if (tests.size > 0) return { tests: [...tests].sort(), source: "authority-map", fallback: false };
  return {
    tests: catalog.planning.unresolvedBackendFallback.tests,
    source: "unresolved-backend-fallback",
    fallback: true,
  };
}

export function buildVerificationPlan({
  catalog,
  requestedLevel,
  changedPaths,
  focusedArgs = [],
  manualRiskOverrides = [],
  manualOverrideReason = null,
  baseRef = "origin/main",
  commitSha = null,
  ci = false,
}) {
  if (manualRiskOverrides.length > 0 && !manualOverrideReason) {
    throw new Error("manual risk override requires --reason");
  }
  const rules = riskRuleMap(catalog);
  for (const risk of manualRiskOverrides) {
    if (!rules.has(risk)) throw new Error(`unknown manual risk override: ${risk}`);
  }
  const detectedRiskCategories = classifyChangedPaths(changedPaths, catalog);
  const riskCategories = [...new Set([...detectedRiskCategories, ...manualRiskOverrides])].sort();
  const effectiveRisks = riskCategories.length > 0 ? riskCategories : ["unknown"];
  const requested = getVerificationLevel(catalog, requestedLevel);
  const minimumRequiredLevel = strongestMinimumLevel(effectiveRisks, rules, requestedLevel);
  const incomplete = levelRank(requestedLevel) < levelRank(minimumRequiredLevel);
  const selectedGateReasons = new Map();
  const select = (gateId, reason) => {
    if (!selectedGateReasons.has(gateId)) selectedGateReasons.set(gateId, []);
    selectedGateReasons.get(gateId).push(reason);
  };
  for (const gateId of requested.gates) select(gateId, `baseline for ${requestedLevel}`);
  for (const risk of effectiveRisks) {
    for (const gateId of rules.get(risk).requiredGates ?? []) {
      select(gateId, `required for ${risk}`);
    }
    if (levelRank(requestedLevel) >= levelRank("checkpoint")) {
      for (const gateId of rules.get(risk).checkpointOnly ?? []) {
        select(gateId, `checkpoint evidence for ${risk}`);
      }
    }
  }
  const backendRiskDetected = requiresBackendPytest(effectiveRisks);
  const focused = backendRiskDetected
    ? resolveFocusedTests({ catalog, changedPaths, focusedArgs })
    : { tests: focusedArgs, source: focusedArgs.length > 0 ? "explicit" : "not-needed", fallback: false };
  const ciOwnsBackendFullSuite = ci && backendRiskDetected && !effectiveRisks.includes("unknown");
  if (ciOwnsBackendFullSuite) {
    selectedGateReasons.delete("focused-pytest");
    select("full-pytest", "CI is the full-suite owner for backend risk on this commit");
  } else if (backendRiskDetected && !selectedGateReasons.has("focused-pytest")) {
    select("focused-pytest", "backend risk requires focused evidence");
  }
  if (!ciOwnsBackendFullSuite && selectedGateReasons.has("focused-pytest") && focused.tests.length > 0) {
    selectedGateReasons.get("focused-pytest").push(`${focused.source}: ${focused.tests.join(", ")}`);
  }
  if (effectiveRisks.includes("unknown")) {
    select("full-pytest", "unknown path is handled conservatively");
  }
  const selectedGates = [...selectedGateReasons].map(([id, reasons]) => ({
    id,
    command: catalog.gates[id].command,
    manual: Boolean(catalog.gates[id].manual),
    reasons,
  }));
  const selectedGateIds = selectedGates.filter((gate) => !gate.manual).map((gate) => gate.id);
  const requiredManualGates = selectedGates.filter((gate) => gate.manual);
  const skippedGates = Object.keys(catalog.gates)
    .filter((gateId) => !selectedGateReasons.has(gateId))
    .map((id) => ({
      id,
      status: "not_run",
      reason: id === "focused-pytest" && ciOwnsBackendFullSuite
        ? "CI full-pytest is the sole full-suite evidence for this backend-risk commit"
        : `not required by ${requestedLevel} baseline or detected risks: ${effectiveRisks.join(", ")}`,
    }));
  const fullSuiteSelected = selectedGateIds.includes("full-pytest");
  const fullSuiteOwner = {
    owner: fullSuiteSelected ? (ci ? "ci" : "local") : "ci",
    commitSha,
    reason: fullSuiteSelected
      ? (ci ? "CI executes the required full suite for this commit" : "unknown paths require a local conservative full suite")
      : "CI is the sole full-suite owner; local verification records focused evidence only",
  };
  return {
    schemaVersion: "verification-plan/v1",
    requestedLevel,
    executionLevel: requestedLevel,
    selectedLevel: minimumRequiredLevel,
    minimumRequiredLevel,
    completion: incomplete ? "incomplete" : "ready",
    incompleteReasons: incomplete
      ? [`${requestedLevel} is below the ${minimumRequiredLevel} evidence level required by ${effectiveRisks.join(", ")}`]
      : [],
    requiredFollowUp: incomplete
      ? minimumRequiredLevel === "release"
        ? { level: "release", command: "npm run acceptance:release", reason: "release evidence is required before this plan can pass" }
        : { level: "checkpoint", command: "npm run verify:checkpoint", reason: "checkpoint evidence is required before this plan can pass" }
      : null,
    baseRef,
    changedPaths,
    detectedRiskCategories,
    riskCategories: effectiveRisks,
    manualOverrides: manualRiskOverrides.map((risk) => ({ risk, reason: manualOverrideReason })),
    focusedTests: focused,
    requiredGates: selectedGates,
    selectedGateIds,
    requiredManualGates,
    skippedGates,
    fullSuiteOwner,
  };
}

export function appendNotRunResults(selectedGateIds, results, catalog) {
  const completed = new Set(results.map((result) => result.id));
  return [
    ...results,
    ...selectedGateIds.filter((gateId) => !completed.has(gateId)).map((gateId) => ({
      id: gateId,
      status: "not_run",
      command: catalog.gates[gateId].command,
      exitCode: null,
      durationSeconds: 0,
      error: "an earlier selected gate failed",
    })),
  ];
}

export function evaluateAcceptanceApplicability({ testedCommit, currentCommit, commitsAhead, commitsBehind, changedPaths, catalog = loadVerificationCatalog() }) {
  const changedRiskCategories = classifyChangedPaths(changedPaths, catalog);
  if (testedCommit === currentCommit) return { freshness: "current", applicability: "current", changedRiskCategories: [] };
  if (commitsBehind > 0) return { freshness: "diverged", applicability: "partial", changedRiskCategories };
  if (changedRiskCategories.length === 0 || changedRiskCategories.every((category) => category === "evidence")) {
    return { freshness: "ahead", applicability: "still_applicable", changedRiskCategories };
  }
  if (changedRiskCategories.includes("unknown")) return { freshness: "ahead", applicability: "partial", changedRiskCategories };
  return { freshness: commitsAhead > 0 ? "ahead" : "diverged", applicability: "stale", changedRiskCategories };
}
