import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { resolve } from "node:path";
import {
  catalogPath,
  evaluateAcceptanceApplicability,
} from "./verification-gates.mjs";

function git(args, { allowFailure = false } = {}) {
  const result = spawnSync("git", args, { encoding: "utf8" });
  if (result.status !== 0 && !allowFailure) {
    throw new Error(`git ${args.join(" ")} failed: ${result.stderr.trim()}`);
  }
  return {
    status: result.status,
    stdout: result.stdout.trim(),
  };
}

export function inspectAcceptanceReport(
  report,
  {
    currentCommit,
    commitsAhead,
    commitsBehind,
    changedPaths,
    dirtyPaths,
    currentCatalogSha256,
  },
) {
  const base = evaluateAcceptanceApplicability({
    testedCommit: report.testedCommit,
    currentCommit,
    commitsAhead,
    commitsBehind,
    changedPaths,
  });
  const catalogChanged =
    report.verificationCatalogSha256 !== undefined
    && report.verificationCatalogSha256 !== currentCatalogSha256;
  let applicability = base.applicability;
  if (catalogChanged) applicability = "invalid";
  else if (dirtyPaths.length > 0 && applicability === "current") {
    applicability = "partial";
  }
  return {
    schemaVersion: "acceptance-status/v1",
    reportSchemaVersion: report.schemaVersion,
    reportStatus: report.status,
    testedCommit: report.testedCommit,
    currentCommit,
    commitsAhead,
    commitsBehind,
    freshness: base.freshness,
    applicability,
    changedRiskCategories: base.changedRiskCategories,
    changedPaths,
    dirtyPaths,
    catalogChanged,
    omittedGates: report.omittedGates ?? [],
  };
}

if (process.argv[1] && import.meta.filename === resolve(process.argv[1])) {
  const reportPath = resolve(
    process.argv[2] ?? "artifacts/main-acceptance/latest.json",
  );
  if (!existsSync(reportPath)) {
    process.stderr.write(`Acceptance report not found: ${reportPath}\n`);
    process.exit(2);
  }
  const report = JSON.parse(readFileSync(reportPath, "utf8"));
  const currentCommit = git(["rev-parse", "HEAD"]).stdout;
  const counts = git([
    "rev-list",
    "--left-right",
    "--count",
    `${report.testedCommit}...${currentCommit}`,
  ]).stdout
    .split(/\s+/)
    .map(Number);
  const changedPaths =
    report.testedCommit === currentCommit
      ? []
      : git([
          "diff",
          "--name-only",
          "--find-renames",
          `${report.testedCommit}...${currentCommit}`,
        ]).stdout
          .split(/\r?\n/)
          .filter(Boolean);
  const dirtyPaths = git(["status", "--porcelain"]).stdout
    .split(/\r?\n/)
    .filter(Boolean)
    .map((line) => line.slice(3));
  const currentCatalogSha256 = createHash("sha256")
    .update(readFileSync(catalogPath))
    .digest("hex");
  const status = inspectAcceptanceReport(report, {
    currentCommit,
    commitsAhead: counts[1],
    commitsBehind: counts[0],
    changedPaths,
    dirtyPaths,
    currentCatalogSha256,
  });
  const asJson = process.argv.includes("--json");
  if (asJson) {
    process.stdout.write(`${JSON.stringify(status, null, 2)}\n`);
  } else {
    process.stdout.write(
      `Acceptance ${status.applicability}: tested=${status.testedCommit} current=${status.currentCommit} ahead=${status.commitsAhead} behind=${status.commitsBehind}\n`,
    );
    if (status.changedRiskCategories.length > 0) {
      process.stdout.write(
        `Changed risk categories: ${status.changedRiskCategories.join(", ")}\n`,
      );
    }
    if (status.dirtyPaths.length > 0) {
      process.stdout.write(`Dirty paths: ${status.dirtyPaths.join(", ")}\n`);
    }
  }
  process.exit(
    ["current", "still_applicable"].includes(status.applicability) ? 0 : 1,
  );
}
