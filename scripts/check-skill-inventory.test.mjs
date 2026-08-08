import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { checkSkillInventory } from "./check-skill-inventory.mjs";

const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");

function safety() {
  return { network: false, write: false, execute: false, vendor_scripts: false, automatic: false };
}

function clientResolution() {
  return { codex: "observed", chatgpt: "unknown", claude: "unknown" };
}

function fixtureInventory({ publicContent = "[internal](../../references/internal/SKILL.md)", internalPath = ".agents/references/internal/SKILL.md", aliases = [] } = {}) {
  const root = mkdtempSync(join(tmpdir(), "skill-inventory-test-"));
  const publicDir = join(root, ".agents", "skills", "public");
  const internalDir = join(root, ".agents", "references", "internal");
  mkdirSync(publicDir, { recursive: true });
  mkdirSync(join(publicDir, "agents"), { recursive: true });
  mkdirSync(internalDir, { recursive: true });
  writeFileSync(join(publicDir, "SKILL.md"), `---\nname: public\ndescription: test\n---\n\n${publicContent}\n`, "utf8");
  writeFileSync(join(publicDir, "agents", "openai.yaml"), "interface:\n  display_name: Test\n", "utf8");
  writeFileSync(join(internalDir, "SKILL.md"), "---\nname: internal\ndescription: test\n---\n", "utf8");

  const inventory = {
    schema_version: "skill-inventory/v1",
    inventory_id: "fixture/skill-inventory",
    inventory_revision: 1,
    observed_commit: "a".repeat(40),
    discovery: {
      root: ".agents/skills",
      baseline: { visible_count: 1, visible_names: ["public"] },
      current: { visible_count: 1, visible_names: ["public"] },
      target: { visible_count: 1, visible_names: ["public"] },
      transition: { strict_target_requires_root_public_only: true },
    },
    clients: {
      codex: {
        status: "observed",
        version: "unknown",
        discovery_root: ".agents/skills",
        visible_count: 1,
        visible_names: ["public"],
        explicit_skill_resolution: "unknown",
        root_external_reference: "unknown",
        evidence: "fixture",
      },
      chatgpt: {
        status: "unknown",
        version: "unknown",
        discovery_root: null,
        visible_count: null,
        visible_names: null,
        explicit_skill_resolution: "unknown",
        root_external_reference: "unknown",
        evidence: "fixture",
      },
      claude: {
        status: "unknown",
        version: "unknown",
        discovery_root: null,
        visible_count: null,
        visible_names: null,
        explicit_skill_resolution: "unknown",
        root_external_reference: "unknown",
        evidence: "fixture",
      },
    },
    entries: [
      {
        id: "public:public",
        public_name: "public",
        version: "fixture",
        visibility: "public_entry",
        path: ".agents/skills/public",
        source: { kind: "repo", owner: "test", purpose: "fixture" },
        wrapper: { required_files: ["SKILL.md", "agents/openai.yaml"] },
        references: ["internal:internal"],
        required_capabilities: ["read_repository"],
        client_resolution: clientResolution(),
        safety: safety(),
      },
      {
        id: "internal:internal",
        reference_name: "internal",
        version: "fixture",
        visibility: "internal_reference",
        path: internalPath,
        source: { kind: "repo", owner: "test", purpose: "fixture" },
        references: [],
        required_capabilities: ["read_guidance"],
        client_resolution: { codex: "unknown", chatgpt: "unknown", claude: "unknown" },
        safety: safety(),
      },
    ],
    migration: { status: "active", aliases, policy: "fixture" },
  };
  writeFileSync(join(root, ".agents", "skill-inventory.json"), `${JSON.stringify(inventory, null, 2)}\n`, "utf8");
  return { root, inventory };
}

function cleanup(root) {
  rmSync(root, { recursive: true, force: true });
}

test("repository inventory exposes six public entries and a historical twelve-entry baseline", () => {
  const report = checkSkillInventory({ repoRoot: repositoryRoot, strictTarget: true });
  assert.equal(report.ok, true, JSON.stringify(report.findings, null, 2));
  assert.equal(report.discovery.baseline_count, 12);
  assert.equal(report.discovery.observed_count, 6);
  assert.equal(report.discovery.target_count, 6);
  assert.equal(report.discovery.legacy_visible_internal_count, 0);
  assert.equal(report.counts.public_entries, 6);
  assert.equal(report.counts.internal_references, 10);
  assert.equal(report.warnings.length, 0);
});

test("unknown clients do not become false support claims", () => {
  const report = checkSkillInventory({ repoRoot: repositoryRoot });
  assert.equal(report.ok, true);
  const inventory = JSON.parse(readFileSync(join(repositoryRoot, ".agents", "skill-inventory.json"), "utf8"));
  assert.equal(inventory.clients.chatgpt.root_external_reference, "unknown");
  assert.equal(inventory.clients.claude.visible_count, null);
});

test("allows repo-internal absolute inventory paths and rejects repo escapes", () => {
  const internalPath = join(repositoryRoot, ".agents", "skill-inventory.json");
  const internalReport = checkSkillInventory({ repoRoot: repositoryRoot, inventoryPath: internalPath, strictTarget: true });
  assert.equal(internalReport.ok, true, JSON.stringify(internalReport.findings, null, 2));

  for (const inventoryPath of [
    resolve(repositoryRoot, "..", "outside-skill-inventory.json"),
    "../outside-skill-inventory.json",
  ]) {
    const report = checkSkillInventory({ repoRoot: repositoryRoot, inventoryPath });
    assert.equal(report.ok, false);
    assert.ok(report.findings.some((item) => item.code === "inventory-path-escape"), JSON.stringify(report.findings));
  }
});

test("rejects duplicate migration aliases", () => {
  const fixture = fixtureInventory({ aliases: [
    { old_name: "old-skill", replacement: "public", status: "active" },
    { old_name: "old-skill", replacement: "public", status: "active" },
  ] });
  try {
    const report = checkSkillInventory({ repoRoot: fixture.root });
    assert.ok(report.findings.some((item) => item.code === "duplicate-migration-alias"));
  } finally {
    cleanup(fixture.root);
  }
});

test("rejects a broken relative link and a path escape", () => {
  for (const content of [
    "[missing](../../references/internal/missing.md)",
    "[escape](../../../../outside.md)",
  ]) {
    const fixture = fixtureInventory({ publicContent: content });
    try {
      const report = checkSkillInventory({ repoRoot: fixture.root });
      assert.ok(report.findings.some((item) => item.code === (content.includes("escape") ? "path-escape" : "broken-link")), JSON.stringify(report.findings));
    } finally {
      cleanup(fixture.root);
    }
  }
});

test("rejects duplicate local references in a public wrapper", () => {
  const link = "[internal](../../references/internal/SKILL.md)";
  const fixture = fixtureInventory({ publicContent: `${link}\n${link}` });
  try {
    const report = checkSkillInventory({ repoRoot: fixture.root });
    assert.ok(report.findings.some((item) => item.code === "duplicate-link"));
  } finally {
    cleanup(fixture.root);
  }
});

test("rejects a symlinked internal reference when the platform permits symlink creation", (t) => {
  const fixture = fixtureInventory({ internalPath: ".agents/references/internal/linked.md", publicContent: "[internal](../../references/internal/linked.md)" });
  const outside = join(fixture.root, "..", `${fixture.root.split(/[\\/]/).pop()}-outside.md`);
  try {
    writeFileSync(outside, "outside\n", "utf8");
    try {
      symlinkSync(outside, join(fixture.root, ".agents", "references", "internal", "linked.md"));
    } catch {
      t.skip("symlink creation is unavailable in this Windows environment");
      return;
    }
    const report = checkSkillInventory({ repoRoot: fixture.root });
    assert.ok(report.findings.some((item) => item.code === "symlink-path" || item.code === "symlink-link" || item.code === "path-escape"));
  } finally {
    cleanup(fixture.root);
    rmSync(outside, { force: true });
  }
});

test("detects a manually entered provenance digest drift", () => {
  const temporaryRoot = mkdtempSync(join(repositoryRoot, ".tmp-skill-inventory-digest-"));
  const inventoryPath = join(temporaryRoot, "skill-inventory.json");
  try {
    const inventory = JSON.parse(readFileSync(join(repositoryRoot, ".agents", "skill-inventory.json"), "utf8"));
    const domainModeling = inventory.entries.find((entry) => entry.id === "internal:domain-modeling");
    domainModeling.provenance.digest.value = "0".repeat(64);
    writeFileSync(inventoryPath, `${JSON.stringify(inventory, null, 2)}\n`, "utf8");
    const report = checkSkillInventory({ repoRoot: repositoryRoot, inventoryPath });
    assert.ok(report.findings.some((item) => item.code === "digest-drift"), JSON.stringify(report.findings));
  } finally {
    cleanup(temporaryRoot);
  }
});

test("checker source has no process execution, network, or filesystem write primitive", () => {
  const source = readFileSync(join(repositoryRoot, "scripts", "check-skill-inventory.mjs"), "utf8");
  assert.doesNotMatch(source, /node:child_process|node:http|node:https|fetch\s*\(|spawn\s*\(|execFile\s*\(|writeFileSync|appendFileSync/);
});
