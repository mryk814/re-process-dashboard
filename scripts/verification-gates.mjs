import { readFileSync } from "node:fs";
import { resolve } from "node:path";

export const catalogPath = resolve(
  import.meta.dirname,
  "verification-gates.json",
);

const requiredGateFields = [
  "command",
  "purpose",
  "estimatedMinutes",
  "platform",
  "riskCategories",
];

export function loadVerificationCatalog(path = catalogPath) {
  const catalog = JSON.parse(readFileSync(path, "utf8"));
  validateVerificationCatalog(catalog);
  return catalog;
}

export function validateVerificationCatalog(catalog) {
  if (catalog.schemaVersion !== "verification-gates/v1") {
    throw new Error("verification catalog schemaVersion must be verification-gates/v1");
  }
  if (!Array.isArray(catalog.levels) || catalog.levels.length !== 4) {
    throw new Error("verification catalog must declare exactly four levels");
  }
  const levelIds = new Set();
  for (const level of catalog.levels) {
    if (levelIds.has(level.id)) throw new Error(`duplicate level: ${level.id}`);
    levelIds.add(level.id);
    for (const field of [
      "label",
      "purpose",
      "estimatedMinutes",
      "platform",
      "gates",
      "evidence",
    ]) {
      if (level[field] === undefined) {
        throw new Error(`level ${level.id} is missing ${field}`);
      }
    }
  }
  if (
    [...levelIds].join(",") !== "edit,pr,checkpoint,release"
  ) {
    throw new Error("verification levels must be edit, pr, checkpoint, release");
  }
  for (const [id, gate] of Object.entries(catalog.gates ?? {})) {
    for (const field of requiredGateFields) {
      if (gate[field] === undefined) {
        throw new Error(`gate ${id} is missing ${field}`);
      }
    }
    if (!gate.manual && !gate.runner) {
      throw new Error(`gate ${id} must declare runner or manual`);
    }
  }
  for (const level of catalog.levels) {
    for (const gateId of level.gates) {
      if (!catalog.gates[gateId]) {
        throw new Error(`level ${level.id} references unknown gate ${gateId}`);
      }
      if (catalog.gates[gateId].manual) {
        throw new Error(`automated level ${level.id} cannot run manual gate ${gateId}`);
      }
    }
  }
  for (const rule of catalog.riskMatrix ?? []) {
    if (!levelIds.has(rule.minimumLevel)) {
      throw new Error(`risk ${rule.risk} references unknown level ${rule.minimumLevel}`);
    }
    for (const gateId of [
      ...(rule.requiredGates ?? []),
      ...(rule.checkpointOnly ?? []),
    ]) {
      if (!catalog.gates[gateId]) {
        throw new Error(`risk ${rule.risk} references unknown gate ${gateId}`);
      }
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
  { focusedArgs = [], baseRef = "origin/main" } = {},
) {
  if (gate.manual) throw new Error(`manual gate cannot be executed: ${gate.command}`);
  const args = gate.runner.args.flatMap((argument) =>
    argument === "$BASE...HEAD" ? [`${baseRef}...HEAD`] : [argument],
  );
  if (gate.runner.appendFocusedArgs) args.push(...focusedArgs);
  return { executable: gate.runner.executable, args };
}

export function classifyChangedPath(path) {
  const normalized = path.replaceAll("\\", "/");
  if (
    normalized.startsWith("docs/reports/")
    || normalized.startsWith("artifacts/")
  ) {
    return "evidence";
  }
  if (normalized.startsWith("docs/learning/")) return "textbook";
  if (normalized.startsWith("docs/")) return "docs";
  if (
    normalized.startsWith("compose.")
    || normalized.startsWith("infrastructure/compose/")
    || normalized.includes("/shared_lab/")
    || normalized.startsWith("scripts/run-shared-lab")
    || normalized.startsWith("scripts/run-compose")
  ) {
    return "compose-shared-lab";
  }
  if (
    normalized.startsWith("models/packages/")
    || normalized.includes("model_package")
    || normalized.includes("model-package")
  ) {
    return "model-package";
  }
  if (
    normalized === "backend/src/decision_workbench/api/security.py"
    || normalized === "backend/tests/test_launch_token.py"
    || normalized.includes("/security/")
  ) {
    return "security";
  }
  if (
    normalized.includes("migration")
    || normalized.includes("workspace_bundle")
    || normalized.includes("workspace-backup")
    || normalized.startsWith("backend/src/decision_workbench/persistence/")
  ) {
    return "persistence";
  }
  if (
    normalized === "package.json"
    || normalized === "package-lock.json"
    || normalized === "uv.lock"
    || normalized.startsWith(".github/workflows/")
    || normalized.includes("package-windows")
    || normalized.includes("smoke-windows")
  ) {
    return "dependency-packaging";
  }
  if (normalized.startsWith("apps/web/") || normalized.startsWith("e2e/")) {
    return "frontend";
  }
  if (normalized.startsWith("apps/desktop/")) return "distribution";
  if (normalized.startsWith("backend/")) return "backend";
  if (
    normalized.startsWith("scripts/")
    || normalized.startsWith("data/")
    || normalized.startsWith("models/")
  ) {
    return "contracts";
  }
  return "unknown";
}

export function classifyChangedPaths(paths) {
  return [...new Set(paths.map(classifyChangedPath))].sort();
}

export function requiresBackendPytest(riskCategories) {
  return riskCategories.some((risk) =>
    ["backend", "persistence", "model-package", "contracts", "security"].includes(risk)
  );
}

export function appendNotRunResults(selectedGateIds, results, catalog) {
  const completed = new Set(results.map((result) => result.id));
  return [
    ...results,
    ...selectedGateIds
      .filter((gateId) => !completed.has(gateId))
      .map((gateId) => ({
        id: gateId,
        status: "not_run",
        command: catalog.gates[gateId].command,
        exitCode: null,
        durationSeconds: 0,
        error: "an earlier selected gate failed",
      })),
  ];
}

export function evaluateAcceptanceApplicability({
  testedCommit,
  currentCommit,
  commitsAhead,
  commitsBehind,
  changedPaths,
}) {
  const changedRiskCategories = classifyChangedPaths(changedPaths);
  if (testedCommit === currentCommit) {
    return {
      freshness: "current",
      applicability: "current",
      changedRiskCategories: [],
    };
  }
  if (commitsBehind > 0) {
    return {
      freshness: "diverged",
      applicability: "partial",
      changedRiskCategories,
    };
  }
  if (
    changedRiskCategories.length === 0
    || changedRiskCategories.every((category) => category === "evidence")
  ) {
    return {
      freshness: "ahead",
      applicability: "still_applicable",
      changedRiskCategories,
    };
  }
  if (changedRiskCategories.includes("unknown")) {
    return {
      freshness: "ahead",
      applicability: "partial",
      changedRiskCategories,
    };
  }
  return {
    freshness: commitsAhead > 0 ? "ahead" : "diverged",
    applicability: "stale",
    changedRiskCategories,
  };
}
