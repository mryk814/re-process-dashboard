import { existsSync, readFileSync, readdirSync } from "node:fs";
import { createHash } from "node:crypto";
import { resolve } from "node:path";
import { validateCurrentContract } from "./check-current-contract.mjs";

export const allowedDocsRootFiles = new Set([
  "README.md",
  "developer-start-here.md",
]);

export function validateDocsRoot(entries, allowed = allowedDocsRootFiles) {
  const directFiles = entries
    .filter((entry) => entry.isFile() || entry.isSymbolicLink?.())
    .map((entry) => entry.name)
    .sort();
  return {
    unexpected: directFiles.filter((name) => !allowed.has(name)),
    missing: [...allowed].filter((name) => !directFiles.includes(name)).sort(),
  };
}

export function validateDocumentInventory(inventory, repositoryRoot) {
  const failures = [];
  if (inventory.schemaVersion !== "document-inventory/v1") {
    failures.push("inventory schemaVersion must be document-inventory/v1");
  }
  const seenSources = new Set();
  const seenTargets = new Set();
  for (const [index, document] of (inventory.documents ?? []).entries()) {
    for (const field of [
      "sourcePath",
      "path",
      "role",
      "authority",
      "lifecycle",
      "owner",
      "updateTrigger",
      "generated",
    ]) {
      if (document[field] === undefined || document[field] === "") {
        failures.push(`documents[${index}] is missing ${field}`);
      }
    }
    if (seenSources.has(document.sourcePath)) {
      failures.push(`duplicate sourcePath: ${document.sourcePath}`);
    }
    if (seenTargets.has(document.path)) {
      failures.push(`duplicate path: ${document.path}`);
    }
    seenSources.add(document.sourcePath);
    seenTargets.add(document.path);
    if (!existsSync(resolve(repositoryRoot, document.path))) {
      failures.push(`inventory target does not exist: ${document.path}`);
    }
  }
  const documentSetSha256 = createHash("sha256")
    .update(
      (inventory.documents ?? [])
        .map((document) => `${document.sourcePath}->${document.path}`)
        .sort()
        .join("\n"),
    )
    .digest("hex");
  if (inventory.documentSetSha256 !== documentSetSha256) {
    failures.push(
      `inventory document set digest mismatch: expected ${inventory.documentSetSha256 ?? "missing"}, calculated ${documentSetSha256}`,
    );
  }
  return failures;
}

function main() {
  const repositoryRoot = resolve(import.meta.dirname, "..");
  const docsRoot = resolve(repositoryRoot, "docs");
  const rootResult = validateDocsRoot(
    readdirSync(docsRoot, { withFileTypes: true }),
  );
  const inventoryPath = resolve(
    docsRoot,
    "inventory",
    "root-documents.json",
  );
  const failures = [
    ...rootResult.unexpected.map(
      (name) => `docs root file is not allowed: ${name}`,
    ),
    ...rootResult.missing.map(
      (name) => `required docs root entry is missing: ${name}`,
    ),
  ];
  if (!existsSync(inventoryPath)) {
    failures.push("document inventory is missing: docs/inventory/root-documents.json");
  } else {
    failures.push(
      ...validateDocumentInventory(
        JSON.parse(readFileSync(inventoryPath, "utf8")),
        repositoryRoot,
      ),
    );
  }
  failures.push(...validateCurrentContract(repositoryRoot));
  if (failures.length > 0) {
    process.stderr.write(`Documentation structure errors:\n${failures.join("\n")}\n`);
    process.exit(1);
  }
  process.stdout.write(
    "Documentation structure passed: root placement, inventory targets, and current contract markers are aligned.\n",
  );
}

if (process.argv[1] && import.meta.filename === resolve(process.argv[1])) {
  main();
}
