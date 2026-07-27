import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import process from "node:process";

const repositoryRoot = path.resolve(import.meta.dirname, "..");
const exceptions = [
  {
    advisoryId: 1124334,
    ghsa: "GHSA-mh99-v99m-4gvg",
    expiresOn: "2026-08-31",
    owner: "repository maintainers",
  },
  {
    advisoryId: 1123911,
    ghsa: "GHSA-52cp-r559-cp3m",
    expiresOn: "2026-08-31",
    owner: "repository maintainers",
  },
];
const affectedPackages = new Set([
  "@electron/asar",
  "@electron/universal",
  "@redocly/openapi-core",
  "app-builder-lib",
  "brace-expansion",
  "dir-compare",
  "dmg-builder",
  "ejs",
  "electron-builder",
  "electron-builder-squirrel-windows",
  "electron-winstaller",
  "filelist",
  "glob",
  "jake",
  "js-yaml",
  "minimatch",
  "rimraf",
  "temp",
]);

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

function validateAudit({
  report,
  productionReport,
  packageLock,
  today = new Date().toISOString().slice(0, 10),
}) {
  if (productionReport.metadata?.vulnerabilities?.total !== 0) {
    fail("Production dependency audit is not clean.");
  }

  const vulnerabilities = Object.values(report.vulnerabilities ?? {});
  if (vulnerabilities.length === 0) {
    return "Dependency audit passed with no vulnerabilities; remove the stale exceptions.";
  }

  for (const exception of exceptions) {
    if (today > exception.expiresOn) {
      fail(
        `Dependency audit exception ${exception.ghsa} expired on ${exception.expiresOn}; ` +
          `owner: ${exception.owner}.`,
      );
    }
  }

  const seenAdvisories = new Set();
  const seenPackages = new Set();
  for (const vulnerability of vulnerabilities) {
    seenPackages.add(vulnerability.name);
    if (vulnerability.severity === "critical") {
      fail(`Critical vulnerability is never allow-listed: ${vulnerability.name}.`);
    }
    for (const via of vulnerability.via ?? []) {
      if (typeof via === "object") seenAdvisories.add(via.source);
    }
    for (const node of vulnerability.nodes ?? []) {
      const locked = packageLock.packages?.[node];
      if (!locked?.dev) {
        fail(
          `Allow-listed vulnerability reached a non-development dependency: ` +
            `${vulnerability.name} at ${node || "<root>"}.`,
        );
      }
    }
  }

  const expectedAdvisories = new Set(exceptions.map(({ advisoryId }) => advisoryId));
  if (
    seenAdvisories.size !== expectedAdvisories.size ||
    [...seenAdvisories].some((id) => !expectedAdvisories.has(id))
  ) {
    fail(`Unexpected advisory set: ${[...seenAdvisories].sort().join(", ") || "<none>"}.`);
  }

  const unexpectedPackages = [...seenPackages].filter(
    (name) => !affectedPackages.has(name),
  );
  const missingPackages = [...affectedPackages].filter(
    (name) => !seenPackages.has(name),
  );
  if (unexpectedPackages.length > 0 || missingPackages.length > 0) {
    fail(
      `Dependency audit package set changed. Unexpected: ` +
        `${unexpectedPackages.join(", ") || "<none>"}; missing: ` +
        `${missingPackages.join(", ") || "<none>"}.`,
    );
  }

  return (
    `Dependency audit passed: production=0; ${vulnerabilities.length} development-only ` +
    `package records map only to ${exceptions.map(({ ghsa }) => ghsa).join(", ")}; ` +
    `exceptions expire ${exceptions[0].expiresOn} (${exceptions[0].owner}).`
  );
}

function clone(value) {
  return structuredClone(value);
}

function runSelfTests() {
  const vulnerabilities = Object.fromEntries(
    [...affectedPackages].map((name, index) => [
      name,
      {
        name,
        severity: "high",
        via:
          index === 0
            ? [{ source: exceptions[0].advisoryId }]
            : index === 1
              ? [{ source: exceptions[1].advisoryId }]
              : ["transitive dependency"],
        nodes: [`node_modules/${name}`],
      },
    ]),
  );
  const report = { vulnerabilities };
  const productionReport = { metadata: { vulnerabilities: { total: 0 } } };
  const packageLock = {
    packages: Object.fromEntries(
      [...affectedPackages].map((name) => [`node_modules/${name}`, { dev: true }]),
    ),
  };
  const input = { report, productionReport, packageLock, today: "2026-07-28" };
  assert.match(validateAudit(input), /development-only/);

  const expectFailure = (mutate, pattern) => {
    const changed = clone(input);
    mutate(changed);
    assert.throws(() => validateAudit(changed), pattern);
  };
  expectFailure(
    ({ report: changed }) => {
      const first = Object.values(changed.vulnerabilities)[0];
      first.via = [...first.via, { source: 9999999 }];
    },
    /Unexpected advisory set/,
  );
  expectFailure(
    ({ report: changed }) => {
      Object.values(changed.vulnerabilities)[0].severity = "critical";
    },
    /Critical vulnerability/,
  );
  expectFailure(
    ({ productionReport: changed }) => {
      changed.metadata.vulnerabilities.total = 1;
    },
    /Production dependency audit/,
  );
  expectFailure(
    (changed) => {
      changed.today = "2026-09-01";
    },
    /expired/,
  );
  expectFailure(
    ({ report: changed, packageLock: lock }) => {
      const node = Object.values(changed.vulnerabilities).find(
        ({ nodes }) => nodes?.length,
      ).nodes[0];
      lock.packages[node].dev = false;
    },
    /non-development dependency/,
  );
  console.log("Dependency audit policy self-tests passed: 6 cases.");
}

if (process.argv.includes("--self-test")) {
  runSelfTests();
} else {
  const report = npmAudit(["--include=dev"]);
  const productionReport = npmAudit(["--omit=dev"]);
  const packageLock = JSON.parse(
    readFileSync(path.join(repositoryRoot, "package-lock.json"), "utf8"),
  );
  console.log(validateAudit({ report, productionReport, packageLock }));
}
