import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import path from "node:path";
import process from "node:process";

const repositoryRoot = path.resolve(import.meta.dirname, "..");

function fail(message) {
  throw new Error(message);
}

function npmAudit(args) {
  const npmCli = process.env.npm_execpath;
  if (!npmCli) {
    fail("npm_execpath is unavailable; run this check through npm run security:audit.");
  }
  const env = { ...process.env };
  delete env.npm_config_include;
  delete env.npm_config_omit;
  try {
    return JSON.parse(
      execFileSync(process.execPath, [npmCli, "audit", "--json", ...args], {
        cwd: repositoryRoot,
        encoding: "utf8",
        env,
        stdio: ["ignore", "pipe", "pipe"],
      }),
    );
  } catch (error) {
    const output = error.stdout?.toString();
    if (!output) throw error;
    return JSON.parse(output);
  }
}

function vulnerabilityCount(report) {
  return report.metadata?.vulnerabilities?.total
    ?? Object.keys(report.vulnerabilities ?? {}).length;
}

function vulnerabilityNames(report) {
  return Object.keys(report.vulnerabilities ?? {}).sort().join(", ") || "<unknown>";
}

function validateAudit({ report, productionReport }) {
  const productionCount = vulnerabilityCount(productionReport);
  if (productionCount !== 0) {
    fail(
      `Production dependency audit is not clean: ${productionCount} record(s): ` +
        vulnerabilityNames(productionReport),
    );
  }

  const allCount = vulnerabilityCount(report);
  if (allCount !== 0) {
    fail(
      `Dependency audit is not clean: ${allCount} record(s): ` +
        vulnerabilityNames(report),
    );
  }

  return "Dependency audit passed with no production or development vulnerabilities.";
}

function runSelfTests() {
  const clean = {
    report: { metadata: { vulnerabilities: { total: 0 } }, vulnerabilities: {} },
    productionReport: {
      metadata: { vulnerabilities: { total: 0 } },
      vulnerabilities: {},
    },
  };
  assert.match(validateAudit(clean), /no production or development vulnerabilities/);
  assert.throws(
    () => validateAudit({
      ...clean,
      report: {
        metadata: { vulnerabilities: { total: 1 } },
        vulnerabilities: { example: { severity: "high" } },
      },
    }),
    /Dependency audit is not clean.*example/,
  );
  assert.throws(
    () => validateAudit({
      ...clean,
      productionReport: {
        metadata: { vulnerabilities: { total: 1 } },
        vulnerabilities: { production: { severity: "high" } },
      },
    }),
    /Production dependency audit is not clean.*production/,
  );
  console.log("Dependency audit policy self-tests passed: 3 cases.");
}

if (process.argv.includes("--self-test")) {
  runSelfTests();
} else {
  const report = npmAudit(["--include=dev"]);
  const productionReport = npmAudit(["--omit=dev"]);
  console.log(validateAudit({ report, productionReport }));
}
