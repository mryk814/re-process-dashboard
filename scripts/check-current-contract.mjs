import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

const DOCUMENT_STATUSES = new Set([
  "current",
  "decision",
  "compatibility",
  "historical",
  "learning",
]);

function sourceText(repositoryRoot, relativePath) {
  return readFileSync(resolve(repositoryRoot, relativePath), "utf8");
}

function documentMetadata(text) {
  const status = text.match(/document-status:\s*([a-z-]+)/)?.[1];
  const verifiedCommit = text.match(/verified-commit:\s*([0-9a-f]{7,40})/)?.[1];
  const owner = text.match(/owner:\s*(.+)/)?.[1]?.trim();
  const sourceOfTruth = text.match(/source-of-truth:\s*(.+)/)?.[1]?.trim();
  return { status, verifiedCommit, owner, sourceOfTruth };
}

function gitCommitExists(repositoryRoot, commit) {
  try {
    execFileSync("git", ["rev-parse", "--verify", `${commit}^{commit}`], {
      cwd: repositoryRoot,
      stdio: "ignore",
    });
    return true;
  } catch {
    return false;
  }
}

function quotedItems(body) {
  return [...body.matchAll(/"([^"]+)"/g)].map((match) => match[1]);
}

export function navigationContractFromSource(source) {
  const viewsBlock = source.match(/export const WORKBENCH_VIEWS\s*=\s*\[([\s\S]*?)\]\s*as const;/);
  const views = viewsBlock ? quotedItems(viewsBlock[1]) : [];
  const queries = [...source.matchAll(/params\.(?:get|has)\("([^"]+)"\)/g)]
    .map((match) => match[1])
    .filter((value, index, values) => values.indexOf(value) === index)
    .sort();
  const fallback = source.match(/VIEW_SET\.has\(requestedView\)[\s\S]*?:\s*"([^"]+)";/)?.[1];
  return { views, queries, fallback };
}

function markerItems(document, marker) {
  const match = document.match(new RegExp(`<!--\\s*${marker}:([^>]+)-->`));
  return match ? match[1].trim().split(",").filter(Boolean) : [];
}

export function validateCurrentContract(repositoryRoot) {
  const failures = [];
  const authorityPath = "docs/inventory/document-authority.json";
  if (!existsSync(resolve(repositoryRoot, authorityPath))) {
    return [`current document authority registry is missing: ${authorityPath}`];
  }
  const authority = JSON.parse(sourceText(repositoryRoot, authorityPath));
  if (authority.schemaVersion !== "document-authority/v1") {
    failures.push("document authority registry schemaVersion must be document-authority/v1");
  }
  for (const document of authority.documents ?? []) {
    if (!document.path || !document.status || !document.owner || !document.sourceOfTruth) {
      failures.push(`document authority entry is incomplete: ${document.path ?? "unknown"}`);
      continue;
    }
    if (!DOCUMENT_STATUSES.has(document.status)) {
      failures.push(`document authority entry has invalid status: ${document.path}`);
    }
    const absolutePath = resolve(repositoryRoot, document.path);
    if (!existsSync(absolutePath)) {
      failures.push(`document authority target does not exist: ${document.path}`);
      continue;
    }
    const metadata = documentMetadata(readFileSync(absolutePath, "utf8"));
    if (metadata.status !== document.status) {
      failures.push(`document status metadata drift: ${document.path}`);
    }
    if (metadata.owner !== document.owner) {
      failures.push(`document owner metadata drift: ${document.path}`);
    }
    if (metadata.sourceOfTruth !== document.sourceOfTruth) {
      failures.push(`document source-of-truth metadata drift: ${document.path}`);
    }
    if (!metadata.verifiedCommit || !gitCommitExists(repositoryRoot, metadata.verifiedCommit)) {
      failures.push(`document verified commit is missing or unknown: ${document.path}`);
    }
  }

  const navigationSource = sourceText(repositoryRoot, "apps/web/src/app/navigation.ts");
  const navigationDocument = sourceText(repositoryRoot, "docs/product/navigation-intent.md");
  const navigation = navigationContractFromSource(navigationSource);
  if (navigation.views.length === 0) failures.push("could not parse WORKBENCH_VIEWS");
  if (markerItems(navigationDocument, "current-contract:navigation-views").join(",") !== navigation.views.join(",")) {
    failures.push("navigation view list drift: docs/product/navigation-intent.md");
  }
  if (markerItems(navigationDocument, "current-contract:navigation-query").sort().join(",") !== navigation.queries.join(",")) {
    failures.push("navigation query list drift: docs/product/navigation-intent.md");
  }
  const fallback = navigationDocument.match(/<!--\s*current-contract:navigation-fallback:([^>]+)-->/)?.[1]?.trim();
  if (!fallback || fallback !== navigation.fallback) {
    failures.push("navigation fallback drift: docs/product/navigation-intent.md");
  }

  const packageJson = JSON.parse(sourceText(repositoryRoot, "package.json"));
  const developerGuide = sourceText(repositoryRoot, "docs/developer-start-here.md");
  const requiredCommands = ["verify:edit", "verify:pr", "verify:checkpoint", "acceptance:release"];
  for (const command of requiredCommands) {
    if (!packageJson.scripts?.[command]) failures.push(`required verification command is missing: ${command}`);
  }
  if (markerItems(developerGuide, "current-contract:verification-commands").join(",") !== requiredCommands.join(",")) {
    failures.push("verification command marker drift: docs/developer-start-here.md");
  }

  const onboardingSource = sourceText(repositoryRoot, "backend/src/decision_workbench/api/csv_task_onboarding.py");
  const baseline = sourceText(repositoryRoot, "docs/product/current-system-baseline.md");
  for (const token of ["/api/data-library/csv-onboarding", "build_standard_package", "verify_model_package", "promote_personal_package"]) {
    if (!onboardingSource.includes(token)) failures.push(`CSV onboarding implementation marker is missing: ${token}`);
  }
  if (!baseline.includes("<!-- current-contract:csv-onboarding:standard-builder-build-verify-promote -->")) {
    failures.push("CSV onboarding contract marker is missing: docs/product/current-system-baseline.md");
  }
  for (const path of ["docs/contracts/task-inventory.json", "apps/web/src/generated/openapi.json", "apps/web/src/generated/api-types.ts"]) {
    if (!existsSync(resolve(repositoryRoot, path))) failures.push(`generated current-contract artifact is missing: ${path}`);
  }
  return failures;
}

export function main(repositoryRoot = resolve(import.meta.dirname, "..")) {
  const failures = validateCurrentContract(repositoryRoot);
  if (failures.length > 0) {
    process.stderr.write(`Current contract errors:\n${failures.join("\n")}\n`);
    return 1;
  }
  process.stdout.write("Current contract passed: document authority, navigation, verification commands, CSV onboarding, and generated API artifacts are aligned.\n");
  return 0;
}

if (process.argv[1] && import.meta.filename === resolve(process.argv[1])) {
  process.exitCode = main();
}
