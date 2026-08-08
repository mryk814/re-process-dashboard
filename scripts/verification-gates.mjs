import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";

export const catalogPath = resolve(import.meta.dirname, "verification-gates.json");

export function normalizedTextSha256(value) {
  const normalized = String(value)
    .replace(/^\uFEFF/, "")
    .replace(/\r\n?/g, "\n");
  return createHash("sha256").update(normalized, "utf8").digest("hex");
}

export function verificationCatalogSha256(path = catalogPath) {
  return normalizedTextSha256(readFileSync(path, "utf8"));
}

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
    if (!["direct", "follow_up"].includes(rule.higherLevelDisposition)) {
      throw new Error(`risk ${rule.risk} must declare higherLevelDisposition`);
    }
    if (rule.higherLevelDisposition === "follow_up" && !rule.followUpOwner) {
      throw new Error(`follow-up risk ${rule.risk} must declare followUpOwner`);
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
  for (const rule of catalog.planning.focusedNodeTestAuthority ?? []) {
    if (!Array.isArray(rule.tests) || rule.tests.length === 0) {
      throw new Error("focused Node test authority needs tests");
    }
  }
  return catalog;
}

export function getVerificationLevel(catalog, levelId) {
  const level = catalog.levels.find((candidate) => candidate.id === levelId);
  if (!level) throw new Error(`unknown verification level: ${levelId}`);
  return level;
}

export function resolveRunner(
  gate,
  {
    focusedArgs = [],
    focusedNodeArgs = [],
    changedPaths = [],
    baseRef = "origin/main",
  } = {},
) {
  if (gate.manual) throw new Error(`manual gate cannot be executed: ${gate.command}`);
  const args = gate.runner.args.flatMap((argument) =>
    argument === "$BASE...HEAD" ? [`${baseRef}...HEAD`] : [argument],
  );
  if (gate.runner.appendFocusedArgs) args.push(...focusedArgs);
  if (gate.runner.appendFocusedNodeArgs) args.push(...focusedNodeArgs);
  if (gate.runner.appendChangedPaths) {
    args.push(...changedPaths.filter((path) => /\.spec\.(?:[cm]?js|jsx|ts|tsx)$/i.test(path)));
  }
  return { executable: gate.runner.executable, args };
}

export function resolveExecutable(
  name,
  {
    platform = process.platform,
    execPath = process.execPath,
    npmExecPath = process.env.npm_execpath,
  } = {},
) {
  const nodeHostedNpmExecPath = npmExecPath
    ?? (platform === "win32"
      ? join(dirname(execPath), "node_modules", "npm", "bin", "npm-cli.js")
      : null);
  if (name === "npm" && nodeHostedNpmExecPath) {
    return { command: execPath, prefix: [nodeHostedNpmExecPath] };
  }
  if (name === "npx" && nodeHostedNpmExecPath) {
    return { command: execPath, prefix: [nodeHostedNpmExecPath, "exec", "--"] };
  }
  if (name === "npm") {
    return { command: platform === "win32" ? "npm.cmd" : "npm", prefix: [] };
  }
  if (name === "npx") {
    return { command: platform === "win32" ? "npx.cmd" : "npx", prefix: [] };
  }
  if (name === "powershell") {
    return { command: platform === "win32" ? "powershell.exe" : "pwsh", prefix: [] };
  }
  return { command: name, prefix: [] };
}

export function parseVerificationArguments(args) {
  const parsed = { planOnly: false, asJson: false, risks: [], reason: null, focusedArgs: [] };
  const separator = args.indexOf("--");
  const options = separator >= 0 ? args.slice(0, separator) : args;
  if (separator >= 0) parsed.focusedArgs = args.slice(separator + 1);
  for (let index = 0; index < options.length; index += 1) {
    const option = options[index];
    if (!option.startsWith("--")) {
      parsed.focusedArgs.push(...options.slice(index));
      break;
    }
    if (option === "--plan") parsed.planOnly = true;
    else if (option === "--json") parsed.asJson = true;
    else if (option === "--risk") {
      const risk = options[index + 1];
      if (!risk) throw new Error("--risk requires a risk category");
      parsed.risks.push(risk);
      index += 1;
    } else if (option === "--reason") {
      const reason = options[index + 1];
      if (!reason) throw new Error("--reason requires text");
      parsed.reason = reason;
      index += 1;
    } else {
      throw new Error(`unknown verification option: ${option}`);
    }
  }
  return parsed;
}

function pathMatches(path, matcher) {
  if (matcher.startsWith("prefix:")) return path.startsWith(matcher.slice("prefix:".length));
  if (matcher.startsWith("exact:")) return path === matcher.slice("exact:".length);
  if (matcher.startsWith("contains:")) return path.includes(matcher.slice("contains:".length));
  throw new Error(`unsupported path matcher: ${matcher}`);
}

const semanticRiskMap = Object.freeze({
  "instruction-only": "instruction-only",
  "docs-only": "pure-docs",
  "backend-test-only": "backend-test-only",
  "web-test-only": "web-test-only",
  "e2e-product-spec": "e2e-product-spec",
  "dependency-manifest": "dependency-manifest",
  "actual-distribution-change": "actual-distribution-change",
  "script-only": "script-only",
});
const manifestDependencyKeys = new Set([
  "dependencies",
  "devDependencies",
  "peerDependencies",
  "optionalDependencies",
  "overrides",
  "resolutions",
  "packageManager",
  "engines",
]);
const manifestDistributionKeys = new Set([
  "build",
  "files",
  "main",
  "bin",
  "directories",
  "publishConfig",
  "name",
  "version",
  "workspaces",
]);
const maximumManifestInspectionBytes = 256 * 1024;

function semanticRules(catalog) {
  return catalog.planning.semanticClassification ?? {};
}

function semanticRuleMatches(path, matchers = []) {
  return matchers.some((matcher) => pathMatches(path, matcher));
}

function changedManifestKeys(before, after) {
  return [...new Set([...Object.keys(before ?? {}), ...Object.keys(after ?? {})])]
    .filter((key) => JSON.stringify(before?.[key]) !== JSON.stringify(after?.[key]));
}

function inspectPackageManifest({ beforeText, afterText }) {
  if (
    typeof beforeText !== "string"
    || typeof afterText !== "string"
    || Buffer.byteLength(beforeText, "utf8") > maximumManifestInspectionBytes
    || Buffer.byteLength(afterText, "utf8") > maximumManifestInspectionBytes
  ) {
    return {
      classification: "classification-required",
      reason: "package manifest diff exceeds the bounded inspector or is unavailable",
      confidence: "unresolved",
    };
  }
  let before;
  let after;
  try {
    before = JSON.parse(beforeText);
    after = JSON.parse(afterText);
  } catch {
    return {
      classification: "classification-required",
      reason: "package manifest is not valid JSON for bounded structural inspection",
      confidence: "unresolved",
    };
  }
  const changedKeys = changedManifestKeys(before, after);
  if (changedKeys.length === 0) {
    return { classification: "script-only", reason: "no semantic package manifest change", confidence: "resolved" };
  }
  if (changedKeys.every((key) => key === "scripts")) {
    return { classification: "script-only", reason: "only package scripts changed", confidence: "resolved" };
  }
  if (changedKeys.some((key) => manifestDistributionKeys.has(key))) {
    return {
      classification: "actual-distribution-change",
      reason: `distribution-sensitive package fields changed: ${changedKeys.filter((key) => manifestDistributionKeys.has(key)).join(", ")}`,
      confidence: "resolved",
    };
  }
  if (changedKeys.some((key) => manifestDependencyKeys.has(key))) {
    return {
      classification: "dependency-manifest",
      reason: `dependency-sensitive package fields changed: ${changedKeys.filter((key) => manifestDependencyKeys.has(key)).join(", ")}`,
      confidence: "resolved",
    };
  }
  return {
    classification: "classification-required",
    reason: `package manifest fields need explicit classification: ${changedKeys.join(", ")}`,
    confidence: "unresolved",
  };
}

function semanticClassificationForPath(path, catalog, { diffs = {} } = {}) {
  const normalized = path.replaceAll("\\", "/");
  const candidateRisk = classifyChangedPath(normalized, catalog);
  const rules = semanticRules(catalog);
  const diff = diffs[normalized] ?? diffs[path] ?? null;
  if (normalized === "package.json" || normalized.endsWith("/package.json")) {
    const inspected = inspectPackageManifest(diff ?? {});
    const semanticRisk = semanticRiskMap[inspected.classification] ?? "unknown";
    return {
      path: normalized,
      ...inspected,
      risk: inspected.classification === "actual-distribution-change" && candidateRisk !== "unknown"
        ? candidateRisk
        : semanticRisk,
      candidateRisk,
      authority: "dependency-and-distribution-manifest",
      candidates: inspected.classification === "classification-required"
        ? ["script-only", "dependency-manifest", "actual-distribution-change"]
        : [],
    };
  }
  if (semanticRuleMatches(normalized, rules.instructionOnly)) {
    return { path: normalized, classification: "instruction-only", risk: "instruction-only", candidateRisk, confidence: "resolved", reason: "instruction file only", authority: "developer-instructions", candidates: [] };
  }
  if (semanticRuleMatches(normalized, rules.docsOnly) && candidateRisk !== "evidence" && candidateRisk !== "textbook-edition") {
    return { path: normalized, classification: "docs-only", risk: "pure-docs", candidateRisk, confidence: "resolved", reason: "documentation-only path", authority: "docs", candidates: [] };
  }
  if (semanticRuleMatches(normalized, rules.dependencyManifest)) {
    return { path: normalized, classification: "dependency-manifest", risk: "dependency-manifest", candidateRisk, confidence: "resolved", reason: "lockfile or dependency manifest", authority: "dependency-manifest", candidates: [] };
  }
  if (semanticRuleMatches(normalized, rules.backendTestOnly)) {
    return { path: normalized, classification: "backend-test-only", risk: "backend-test-only", candidateRisk, confidence: "resolved", reason: "backend test authority only", authority: "backend-tests", candidates: [] };
  }
  if (semanticRuleMatches(normalized, rules.webTestOnly)) {
    return { path: normalized, classification: "web-test-only", risk: "web-test-only", candidateRisk, confidence: "resolved", reason: "Web test authority only", authority: "web-tests", candidates: [] };
  }
  if (semanticRuleMatches(normalized, rules.actualDistribution)) {
    return { path: normalized, classification: "actual-distribution-change", risk: candidateRisk === "unknown" ? "actual-distribution-change" : candidateRisk, candidateRisk, confidence: "resolved", reason: "distribution artifact or target path", authority: "distribution", candidates: [] };
  }
  if (semanticRuleMatches(normalized, rules.e2eProductSpec)) {
    if (["e2e-test-infrastructure", "chain-degraded-test-infrastructure", "shared-recovery-test-infrastructure"].includes(candidateRisk)) {
      return { path: normalized, classification: candidateRisk, risk: candidateRisk, candidateRisk, confidence: "resolved", reason: "E2E infrastructure path", authority: "e2e-infrastructure", candidates: [] };
    }
    return { path: normalized, classification: "e2e-product-spec", risk: "e2e-product-spec", candidateRisk, confidence: "resolved", reason: "product journey spec with a bounded changed path", authority: "e2e-product-spec", candidates: [] };
  }
  if (candidateRisk !== "unknown") {
    return { path: normalized, classification: candidateRisk, risk: candidateRisk, candidateRisk, confidence: "path-candidate", reason: "catalog path rule resolved the authority", authority: candidateRisk, candidates: [] };
  }
  return {
    path: normalized,
    classification: "classification-required",
    risk: "unknown",
    candidateRisk,
    confidence: "unresolved",
    reason: "no bounded semantic classifier resolved this path",
    authority: null,
    candidates: ["instruction-only", "docs-only", "backend-test-only", "web-test-only", "e2e-product-spec", "dependency-manifest", "actual-distribution-change"],
  };
}

export function classifyChangedPathSemantically(path, catalog, options = {}) {
  return semanticClassificationForPath(path, catalog, options);
}

export function classifyChangedPathsSemantically(paths, catalog, options = {}) {
  return paths.map((path) => semanticClassificationForPath(path, catalog, options));
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
    ["backend-application", "backend-test-only", "api-contract", "migration-workspace", "model-runtime-artifact", "model-runtime", "security", "unknown"].includes(risk),
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

const pytestOptionsWithSeparateValue = new Set([
  "-k",
  "-m",
  "-o",
  "--assert",
  "--basetemp",
  "--capture",
  "--color",
  "--confcutdir",
  "--deselect",
  "--doctest-glob",
  "--doctest-report",
  "--durations",
  "--durations-min",
  "--ignore",
  "--ignore-glob",
  "--import-mode",
  "--junit-prefix",
  "--junitxml",
  "--last-failed-no-failures",
  "--log-cli-date-format",
  "--log-cli-format",
  "--log-cli-level",
  "--log-date-format",
  "--log-file",
  "--log-file-date-format",
  "--log-file-format",
  "--log-file-level",
  "--log-format",
  "--log-level",
  "--maxfail",
  "--override-ini",
  "--pastebin",
  "--pdbcls",
  "--rootdir",
  "--tb",
]);

function isPythonPytestTarget(value) {
  return /(?:^|\/)tests(?:\/|$)/.test(value)
    || /\.py(?:::.+)?$/i.test(value);
}

function isNodeTestTarget(value) {
  return /\.(?:[cm]?js|jsx|ts|tsx)$/i.test(value);
}

function focusedPytestArgs(args) {
  const normalized = args.map((value) => value.replaceAll("\\", "/"));
  const hasPythonTarget = normalized.some((value) => (
    !isNodeTestTarget(value) && isPythonPytestTarget(value)
  ));
  if (!hasPythonTarget) return [];
  let preserveNextValue = false;
  return args.filter((value, index) => {
    if (preserveNextValue) {
      preserveNextValue = false;
      return true;
    }
    const normalizedValue = normalized[index];
    if (normalizedValue.startsWith("-")) {
      const option = normalizedValue.split("=", 1)[0];
      preserveNextValue = !normalizedValue.includes("=")
        && pytestOptionsWithSeparateValue.has(option);
      return true;
    }
    return !isNodeTestTarget(normalizedValue);
  });
}

export function resolveFocusedTests({
  catalog,
  changedPaths,
  focusedArgs = [],
  semanticClassifications = [],
}) {
  if (focusedArgs.length > 0) {
    return { tests: focusedPytestArgs(focusedArgs), source: "explicit", fallback: false };
  }
  const normalizedPaths = changedPaths.map((path) => path.replaceAll("\\", "/"));
  const semanticBackendTests = semanticClassifications
    .filter((item) => item.classification === "backend-test-only")
    .map((item) => item.path)
    .filter((path) => /\.py$/i.test(path));
  if (semanticBackendTests.length > 0) {
    return {
      tests: [...new Set(semanticBackendTests)].sort(),
      source: "semantic-backend-test-only",
      fallback: false,
      classificationRequired: false,
    };
  }
  const tests = new Set();
  for (const authority of catalog.planning.focusedTestAuthority ?? []) {
    if (normalizedPaths.some((path) => authority.matches.some((matcher) => pathMatches(path, matcher)))) {
      focusedPytestArgs(authority.tests).forEach((testPath) => tests.add(testPath));
    }
  }
  if (tests.size > 0) return { tests: [...tests].sort(), source: "authority-map", fallback: false };
  return {
    tests: [],
    source: "classification-required",
    fallback: false,
    classificationRequired: true,
    candidates: catalog.planning.unresolvedBackendFallback?.tests ?? [],
  };
}

export function resolveFocusedNodeTests({ catalog, changedPaths }) {
  const normalizedPaths = changedPaths.map((path) => path.replaceAll("\\", "/"));
  const tests = new Set();
  for (const authority of catalog.planning.focusedNodeTestAuthority ?? []) {
    if (normalizedPaths.some((path) => authority.matches.some((matcher) => pathMatches(path, matcher)))) {
      authority.tests
        .filter((path) => /\.(?:[cm]?js|jsx|ts|tsx)$/i.test(path))
        .forEach((path) => tests.add(path));
    }
  }
  return [...tests].sort();
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
  semanticClassifications = null,
  semanticDiffs = {},
}) {
  if (manualRiskOverrides.length > 0 && !manualOverrideReason) {
    throw new Error("manual risk override requires --reason");
  }
  const rules = riskRuleMap(catalog);
  for (const risk of manualRiskOverrides) {
    if (!rules.has(risk)) throw new Error(`unknown manual risk override: ${risk}`);
  }
  const classifications = semanticClassifications
    ?? classifyChangedPathsSemantically(changedPaths, catalog, { diffs: semanticDiffs });
  const detectedRiskCategories = [...new Set(classifications.map((item) => item.risk))].sort();
  const riskCategories = [...new Set([...detectedRiskCategories, ...manualRiskOverrides])].sort();
  const effectiveRisks = riskCategories.length > 0 ? riskCategories : ["unknown"];
  const requested = getVerificationLevel(catalog, requestedLevel);
  const backendRiskDetected = requiresBackendPytest(effectiveRisks);
  const focused = backendRiskDetected || focusedArgs.length > 0
    ? resolveFocusedTests({ catalog, changedPaths, focusedArgs, semanticClassifications: classifications })
    : { tests: [], source: "not-needed", fallback: false, classificationRequired: false };
  const focusedNodeTests = resolveFocusedNodeTests({ catalog, changedPaths });
  const unresolvedClassificationPaths = classifications.filter(
    (item) => item.classification === "classification-required",
  );
  const classificationRequired = (
    focused.classificationRequired === true
    || (focusedArgs.length === 0 && unresolvedClassificationPaths.length > 0)
  );
  const classificationRequirements = [];
  const classificationRequirementKeys = new Set();
  const addClassificationRequirement = (requirement) => {
    const key = requirement.path ?? "<backend-authority>";
    if (classificationRequirementKeys.has(key)) return;
    classificationRequirementKeys.add(key);
    classificationRequirements.push(requirement);
  };
  for (const item of unresolvedClassificationPaths) {
    addClassificationRequirement({
      path: item.path,
      reason: item.reason,
      candidates: item.candidates,
    });
  }
  if (focused.classificationRequired && unresolvedClassificationPaths.length === 0) {
    addClassificationRequirement({
      path: null,
      reason: "focused backend authority is unresolved; provide an explicit --test path",
      candidates: focused.candidates ?? [],
    });
  }
  const minimumRequiredLevel = strongestMinimumLevel(effectiveRisks, rules, requestedLevel);
  const incomplete = levelRank(requestedLevel) < levelRank(minimumRequiredLevel);
  const unmetRiskRequirements = effectiveRisks
    .filter((risk) => (
      !(classificationRequired && risk === "unknown")
      &&
      levelRank(requestedLevel) < levelRank(rules.get(risk).minimumLevel)
    ))
    .map((risk) => {
      const rule = rules.get(risk);
      const level = rule.minimumLevel;
      return {
        risk,
        level,
        command: level === "release"
          ? "npm run acceptance:release"
          : "npm run verify:checkpoint",
        gateId: level === "release"
          ? "release-acceptance"
          : "checkpoint-acceptance",
        disposition: rule.higherLevelDisposition,
        owner: rule.followUpOwner ?? null,
      };
    });
  const unmetByRisk = new Map(
    unmetRiskRequirements.map((requirement) => [requirement.risk, requirement]),
  );
  const editLoopDefersHigherEvidence = requestedLevel === "edit";
  const strongestDirectRequirement = unmetRiskRequirements
    .filter((requirement) => (
      requirement.disposition === "direct"
      && !editLoopDefersHigherEvidence
    ))
    .sort((left, right) => levelRank(right.level) - levelRank(left.level))[0] ?? null;
  const directAggregateRequired = strongestDirectRequirement !== null;
  const deferredManualFollowUps = [];
  const selectedGateReasons = new Map();
  const select = (gateId, reason) => {
    if (!selectedGateReasons.has(gateId)) selectedGateReasons.set(gateId, []);
    selectedGateReasons.get(gateId).push(reason);
  };
  for (const gateId of requested.gates) select(gateId, `baseline for ${requestedLevel}`);
  for (const risk of effectiveRisks) {
    if (classificationRequired && risk === "unknown") continue;
    if (editLoopDefersHigherEvidence && risk === "verification-tooling") {
      select("verification-policy-tests", "verification-tooling edit evidence");
      continue;
    }
    const unmet = unmetByRisk.get(risk);
    if (unmet && editLoopDefersHigherEvidence) {
      for (const gateId of rules.get(risk).requiredGates ?? []) {
        if (!catalog.gates[gateId].manual) continue;
        deferredManualFollowUps.push({
          level: unmet.level,
          command: catalog.gates[gateId].command,
          reason: `manual evidence deferred for ${risk}`,
          risks: [risk],
          owner: unmet.disposition === "direct"
            ? "pr-verification"
            : unmet.owner,
        });
      }
      continue;
    }
    if (unmet?.disposition === "direct") {
      select(
        strongestDirectRequirement.gateId,
        `${strongestDirectRequirement.level} evidence covers the direct requirement for ${risk}`,
      );
      continue;
    }
    for (const gateId of rules.get(risk).requiredGates ?? []) {
      if (
        unmet?.disposition === "follow_up"
        && catalog.gates[gateId].manual
      ) {
        deferredManualFollowUps.push({
          level: unmet.level,
          command: catalog.gates[gateId].command,
          reason: `manual evidence deferred for ${risk}`,
          risks: [risk],
          owner: unmet.owner,
        });
      } else {
        select(gateId, `required for ${risk}`);
      }
    }
    if (levelRank(requestedLevel) >= levelRank("checkpoint")) {
      for (const gateId of rules.get(risk).checkpointOnly ?? []) {
        select(gateId, `checkpoint evidence for ${risk}`);
      }
    }
  }
  if (focusedNodeTests.length > 0) {
    select("focused-node", `Node authority: ${focusedNodeTests.join(", ")}`);
  }
  const ciOwnsBackendFullSuite = ci
    && backendRiskDetected
    && !classificationRequired
    && !effectiveRisks.includes("unknown")
    && !directAggregateRequired;
  if (ciOwnsBackendFullSuite) {
    selectedGateReasons.delete("focused-pytest");
    select("full-pytest", "CI is the full-suite owner for backend risk on this commit");
  } else if (!classificationRequired && !directAggregateRequired && focused.tests.length > 0 && !selectedGateReasons.has("focused-pytest")) {
    select("focused-pytest", "explicit focused tests were supplied");
  } else if (!classificationRequired && !directAggregateRequired && backendRiskDetected && !selectedGateReasons.has("focused-pytest")) {
    select("focused-pytest", "backend risk requires focused evidence");
  }
  if (!ciOwnsBackendFullSuite && selectedGateReasons.has("focused-pytest") && focused.tests.length > 0) {
    selectedGateReasons.get("focused-pytest").push(`${focused.source}: ${focused.tests.join(", ")}`);
  }
  if (
    effectiveRisks.includes("unknown")
    && focusedArgs.length === 0
    && !classificationRequired
    && !directAggregateRequired
  ) {
    select("full-pytest", "unknown path is handled conservatively");
  }
  if (classificationRequired && focused.classificationRequired) {
    selectedGateReasons.delete("focused-pytest");
  }
  if (strongestDirectRequirement) {
    const aggregateGateIds = new Set(
      getVerificationLevel(catalog, strongestDirectRequirement.level).gates,
    );
    const requestedBaselineGateIds = new Set(requested.gates);
    for (const gateId of aggregateGateIds) {
      if (!requestedBaselineGateIds.has(gateId)) {
        selectedGateReasons.delete(gateId);
      }
    }
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
    owner: classificationRequired
      ? "classification-required"
      : fullSuiteSelected || directAggregateRequired ? (ci ? "ci" : "local") : "ci",
    commitSha,
    reason: classificationRequired
      ? "classification must be resolved before local or CI broad evidence is selected"
      : directAggregateRequired
      ? `${ci ? "CI" : "local"} aggregate acceptance owns the full suite for this commit`
      : fullSuiteSelected
      ? (ci ? "CI executes the required full suite for this commit" : "unknown paths require a local conservative full suite")
      : "CI is the sole full-suite owner; local verification records focused evidence only",
  };
  const mergeRequirements = (requirements, keyFor) => [
    ...requirements.reduce((merged, requirement) => {
      const key = keyFor(requirement);
      const current = merged.get(key);
      if (current) {
        current.risks = [...new Set([...current.risks, ...requirement.risks])];
        current.reason = `${current.level} evidence is required by ${current.risks.join(", ")}`;
      } else {
        merged.set(key, { ...requirement });
      }
      return merged;
    }, new Map()).values(),
  ];
  const directEvidenceRequirements = mergeRequirements(
    unmetRiskRequirements
      .filter((requirement) => (
        requirement.disposition === "direct"
        && !editLoopDefersHigherEvidence
      ))
      .map((requirement) => ({
        level: strongestDirectRequirement.level,
        command: strongestDirectRequirement.command,
        gate_id: strongestDirectRequirement.gateId,
        reason: `${strongestDirectRequirement.level} evidence is required by ${requirement.risk}`,
        risks: [requirement.risk],
      })),
    (requirement) => requirement.gate_id,
  );
  const requiredFollowUps = mergeRequirements(
    [
      ...unmetRiskRequirements
        .filter((requirement) => (
          (
            requirement.disposition === "follow_up"
            || (
              editLoopDefersHigherEvidence
              && requirement.disposition === "direct"
            )
          )
          && (
            !strongestDirectRequirement
            || levelRank(requirement.level) > levelRank(strongestDirectRequirement.level)
          )
        ))
        .map((requirement) => ({
          level: requirement.level,
          command: requirement.command,
          reason: `${requirement.level} evidence is required by ${requirement.risk}`,
          risks: [requirement.risk],
          owner: requirement.disposition === "direct"
            ? "pr-verification"
            : requirement.owner,
        })),
      ...deferredManualFollowUps.filter((requirement) => (
        !strongestDirectRequirement
        || levelRank(requirement.level) > levelRank(strongestDirectRequirement.level)
      )),
    ],
    (requirement) => `${requirement.command}\0${requirement.owner}`,
  );
  return {
    schemaVersion: "verification-plan/v1",
    requestedLevel,
    executionLevel: requestedLevel,
    selectedLevel: minimumRequiredLevel,
    minimumRequiredLevel,
    completion: classificationRequired
      ? "classification_required"
      : directEvidenceRequirements.length > 0
      ? "direct_evidence_required"
      : requiredFollowUps.length > 0
        ? "follow_up"
        : "ready",
    incompleteReasons: [
      ...(classificationRequired
        ? ["semantic authority classification is required before selecting a broad gate"]
        : []),
      ...(incomplete
        ? [`${requestedLevel} is below the ${minimumRequiredLevel} evidence level required by ${effectiveRisks.join(", ")}`]
        : []),
    ],
    requiredFollowUp: requiredFollowUps[0] ?? null,
    directEvidenceRequirements,
    requiredFollowUps,
    followUpOwner: requiredFollowUps.length > 0
      ? [...new Set(requiredFollowUps.map((item) => item.owner))].join(", ")
      : null,
    baseRef,
    changedPaths,
    detectedRiskCategories,
    riskCategories: effectiveRisks,
    classifications,
    classificationRequired,
    classificationRequirements,
    focusedNodeTests,
    manualOverrides: manualRiskOverrides.map((risk) => ({ risk, reason: manualOverrideReason })),
    focusedTests: focused,
    requiredGates: selectedGates,
    selectedGateIds,
    requiredManualGates,
    skippedGates,
    fullSuiteOwner,
  };
}

export function gateRunsOnPlatform(gatePlatform, currentPlatform) {
  return gatePlatform === "any"
    || gatePlatform === currentPlatform
    || gatePlatform === "docker";
}

export function verificationGateEnvironment(
  baseEnvironment,
  gateId,
  executionGateIds,
) {
  const environment = { ...baseEnvironment };
  delete environment.VERIFICATION_SKIP_STANDARD_FAILURE_SPECS;
  if (
    gateId === "failure-state-e2e"
    && executionGateIds.includes("default-playwright")
  ) {
    environment.VERIFICATION_SKIP_STANDARD_FAILURE_SPECS = "1";
  }
  return environment;
}

export function evaluateVerificationOutcome({ plan, gateResults }) {
  const byId = new Map(gateResults.map((result) => [result.id, result]));
  const failedGates = gateResults
    .filter((result) => ["failed", "timeout", "interrupted"].includes(result.status))
    .map((result) => ({
      kind: "gate",
      id: result.id,
      command: result.command ?? null,
      exit_code: result.exitCode ?? null,
      reason: result.error ?? "command failed",
    }));
  const plannedClassificationFailures = (plan.classificationRequired ? plan.classificationRequirements ?? [] : [])
    .map((requirement) => ({
      kind: "classification_required",
      id: requirement.path ?? "classification-required",
      command: null,
      exit_code: null,
      reason: requirement.reason,
      candidates: requirement.candidates ?? [],
    }));
  const classificationFailures = (plannedClassificationFailures.length > 0
    ? plannedClassificationFailures
    : gateResults
      .filter((result) => result.status === "classification_required")
      .map((result) => ({
        kind: "classification_required",
        id: result.id,
        command: result.command ?? null,
        exit_code: result.exitCode ?? null,
        reason: result.error ?? "semantic authority classification is required",
        candidates: result.candidates ?? [],
      })))
    .filter((failure, index, failures) => failures.findIndex((candidate) => (
      candidate.kind === failure.kind && candidate.id === failure.id
    )) === index);
  const missingPlannedEvidence = (plan.directEvidenceRequirements ?? [])
    .filter((requirement) => (
      byId.get(requirement.gate_id)?.status === "not_run"
    ));
  const missingDirectEvidence = [
    ...missingPlannedEvidence,
    ...(plan.requiredManualGates ?? []).map((gate) => ({
      level: plan.minimumRequiredLevel,
      command: gate.command,
      reason: gate.reasons.join("; "),
      risks: plan.riskCategories,
      status: "not_run",
    })),
  ].map((requirement) => ({
    kind: "required_evidence",
    id: requirement.level,
    command: requirement.command,
    exit_code: null,
    reason: requirement.reason,
  }));
  const directFailures = [...classificationFailures, ...failedGates, ...missingDirectEvidence];
  const pendingGates = plan.selectedGateIds.filter((gateId) => {
    const result = byId.get(gateId);
    return !result || ["pending", "not_run", "classification_required"].includes(result.status);
  });
  const outcome = directFailures.length > 0
    ? "failed"
    : pendingGates.length > 0
      ? "pending"
      : (plan.requiredFollowUps ?? []).length > 0
        ? "passed_with_follow_up"
        : "passed";
  const evidenceSummary = {
    executed: gateResults.filter((result) => result.execution === "executed").length,
    reused: gateResults.filter((result) => result.execution === "reused").length,
    not_run: gateResults.filter((result) => result.status === "not_run").length,
    classification_required: classificationFailures.length,
  };
  return {
    outcome_schema_version: "verification-outcome/v1",
    outcome,
    direct_failures: directFailures,
    required_follow_ups: plan.requiredFollowUps ?? [],
    follow_up_owner: plan.followUpOwner ?? null,
    full_suite_owner: plan.fullSuiteOwner,
    commit_sha: plan.fullSuiteOwner.commitSha,
    pending_gates: pendingGates,
    evidence_summary: evidenceSummary,
  };
}

export function verificationEvidenceMarkdown(outcome) {
  const direct = outcome.direct_failures.length === 0
    ? "none"
    : outcome.direct_failures.map((failure) => `${failure.id}: ${failure.reason}`).join("; ");
  const followUp = outcome.required_follow_ups.length === 0
    ? "none"
    : outcome.required_follow_ups
      .map((item) => `${item.command} (${item.owner})`)
      .join("; ");
  return [
    "## Verification evidence",
    `- Outcome: \`${outcome.outcome}\``,
    `- Commit: \`${outcome.commit_sha ?? "unknown"}\``,
    `- Direct failures: ${direct}`,
    `- Follow-up: ${followUp}`,
    `- Full-suite owner: ${outcome.full_suite_owner.owner}`,
    `- Gate evidence: executed=${outcome.evidence_summary?.executed ?? 0}, reused=${outcome.evidence_summary?.reused ?? 0}, not_run=${outcome.evidence_summary?.not_run ?? 0}, classification_required=${outcome.evidence_summary?.classification_required ?? 0}`,
  ].join("\n");
}

export function appendNotRunResults(selectedGateIds, results, catalog) {
  const completed = new Set(results.map((result) => result.id));
  return [
    ...results,
    ...selectedGateIds.filter((gateId) => !completed.has(gateId)).map((gateId) => ({
      id: gateId,
      status: "not_run",
      execution: "not_run",
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
