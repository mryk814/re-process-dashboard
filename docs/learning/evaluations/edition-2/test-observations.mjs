import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {spawnSync} from "node:child_process";
import {fileURLToPath} from "node:url";

import {
  gitCommitExists,
  validateEdition2Observations,
} from "./check-observations.mjs";

const sourceRoot = path.dirname(fileURLToPath(import.meta.url));
const repositoryRoot = path.resolve(sourceRoot, "..", "..", "..", "..");
const sourceFiles = [
  "observation.schema.json",
  "protocol.json",
  "protocol.qmd",
  "template.json",
];

function withFixture(operation) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "edition-2-observations-"));
  try {
    for (const filename of sourceFiles) {
      fs.copyFileSync(path.join(sourceRoot, filename), path.join(root, filename));
    }
    return operation(root);
  } finally {
    fs.rmSync(root, {recursive: true, force: true});
  }
}

function digest(content) {
  return crypto.createHash("sha256").update(content).digest("hex");
}

function completedRecord(root) {
  const record = JSON.parse(fs.readFileSync(path.join(root, "template.json"), "utf8"));
  const html = "<!doctype html><title>Edition 2 fixture</title>\n";
  const pdf = Buffer.from("%PDF-1.7\nEdition 2 fixture\n");
  fs.mkdirSync(path.join(root, "artifacts"), {recursive: true});
  fs.writeFileSync(path.join(root, "artifacts", "book.html"), html);
  fs.writeFileSync(path.join(root, "artifacts", "book.pdf"), pdf);
  record.record_id = "edition-2-observation-2099-01-02-completed-fixture";
  record.artifact = {
    edition: 2,
    commit: "0123456789abcdef0123456789abcdef01234567",
    html_path: "artifacts/book.html",
    html_sha256: digest(html),
    pdf_path: "artifacts/book.pdf",
    pdf_sha256: digest(pdf),
  };
  record.overall_status = "complete";
  const reader = record.lanes.find((lane) => lane.kind === "real_reader");
  reader.status = "completed";
  reader.participants = [
    {pseudonym: "reader-a", role: "backend developer"},
    {pseudonym: "reader-b", role: "quality engineer"},
  ];
  reader.task_observations = reader.participants.flatMap((participant, participantIndex) =>
    ["reader-code-trace", "reader-decision-boundary"].map((taskId, taskIndex) => ({
      participant_pseudonym: participant.pseudonym,
      task_id: taskId,
      started_at: `2099-01-02T0${participantIndex * 2 + taskIndex}:00:00Z`,
      duration_seconds: 120 + participantIndex * 10 + taskIndex,
      completed: true,
      errors: [],
      confusion_points: [],
      backtracking_count: 0,
      consulted_pages: [{artifact: "html", location: "chapters/example.html"}],
      confidence: 4,
    })),
  );
  const at = record.lanes.find((lane) => lane.kind === "assistive_technology");
  at.status = "completed";
  at.operator = {pseudonym: "at-reader", role: "screen reader user"};
  at.environment = {
    operating_system: "Fixture OS",
    browser_or_reader: "Fixture Browser",
    assistive_technology: "Fixture Screen Reader",
    assistive_technology_version: "1.0",
  };
  at.matrix.forEach((entry) => {
    entry.result = "pass";
    entry.observation = `${entry.check_id} observed`;
    entry.evidence = [`fixture:${entry.check_id}`];
  });
  const domain = record.lanes.find((lane) => lane.kind === "materials_domain");
  domain.status = "completed";
  domain.reviewer = {pseudonym: "domain-reader", role: "materials researcher"};
  domain.issue_303_reflection = "Issue #303へreview結果を反映した。";
  domain.review_results.forEach((entry) => {
    entry.result = "pass";
    entry.observation = `${entry.topic} reviewed`;
  });
  const security = record.lanes.find((lane) => lane.kind === "security");
  security.status = "completed";
  security.reviewer = {pseudonym: "security-reader", role: "security reviewer"};
  security.review_results.forEach((entry) => {
    entry.result = "pass";
    entry.observation = `${entry.topic} reviewed`;
  });
  security.findings = [
    {
      finding_id: "E2-F-001",
      severity: "note",
      summary: "review scope recorded",
      evidence: ["fixture security review"],
      disposition: "no_change",
      tracking: null,
    },
  ];
  record.limitations = ["Fixture record for checker tests."];
  return record;
}

function writeRecord(root, record) {
  fs.writeFileSync(
    path.join(root, "record-2099-01-02-completed-fixture.json"),
    `${JSON.stringify(record, null, 2)}\n`,
  );
}

withFixture((root) => {
  const result = validateEdition2Observations({root});
  assert.deepEqual(result.errors, []);
  assert.equal(result.recordCount, 1);
});

withFixture((root) => {
  const templatePath = path.join(root, "template.json");
  const template = JSON.parse(fs.readFileSync(templatePath, "utf8"));
  template.lanes[0].proxy = true;
  fs.writeFileSync(templatePath, `${JSON.stringify(template, null, 2)}\n`);
  const errors = validateEdition2Observations({root}).errors.join("\n");
  assert.match(errors, /real_reader: proxy must be false/);
});

withFixture((root) => {
  const templatePath = path.join(root, "template.json");
  const template = JSON.parse(fs.readFileSync(templatePath, "utf8"));
  template.lanes[3] = structuredClone(template.lanes[0]);
  fs.writeFileSync(templatePath, `${JSON.stringify(template, null, 2)}\n`);
  const errors = validateEdition2Observations({root}).errors.join("\n");
  assert.match(errors, /duplicate value real_reader/);
  assert.match(errors, /missing security/);
});

withFixture((root) => {
  const templatePath = path.join(root, "template.json");
  const template = JSON.parse(fs.readFileSync(templatePath, "utf8"));
  template.overall_status = "complete";
  template.lanes.forEach((lane) => {
    lane.status = "completed";
  });
  fs.writeFileSync(templatePath, `${JSON.stringify(template, null, 2)}\n`);
  const errors = validateEdition2Observations({root}).errors.join("\n");
  assert.match(errors, /completed lane requires frozen artifact digests/);
  assert.match(errors, /requires at least two real participants/);
  assert.match(errors, /operator: pseudonym and role are required/);
  assert.match(errors, /materials_domain\.reviewer: pseudonym and role are required/);
  assert.match(errors, /requires Issue #303 reflection evidence/);
  assert.match(errors, /security: completed lane requires at least one finding/);
});

withFixture((root) => {
  const protocolPath = path.join(root, "protocol.json");
  const protocol = JSON.parse(fs.readFileSync(protocolPath, "utf8"));
  protocol.assistive_technology_matrix.pop();
  protocol.privacy.allowed_identity_fields.push("email");
  fs.writeFileSync(protocolPath, `${JSON.stringify(protocol, null, 2)}\n`);
  const errors = validateEdition2Observations({root}).errors.join("\n");
  assert.match(errors, /assistive_technology_matrix: missing zoom-200/);
  assert.match(errors, /allowed_identity_fields: unsupported value email/);
});

withFixture((root) => {
  const templatePath = path.join(root, "template.json");
  const template = JSON.parse(fs.readFileSync(templatePath, "utf8"));
  template.schema_bypass = true;
  fs.writeFileSync(templatePath, `${JSON.stringify(template, null, 2)}\n`);
  const errors = validateEdition2Observations({root}).errors.join("\n");
  assert.match(errors, /template\.json: unknown property schema_bypass/);
});

withFixture((root) => {
  const templatePath = path.join(root, "template.json");
  const templateText = fs.readFileSync(templatePath, "utf8").trimEnd();
  const injected = templateText.replace(
    /\n}$/,
    ',\n  "__proto__": {"email": "must-not-bypass@example.invalid"}\n}',
  );
  fs.writeFileSync(templatePath, `${injected}\n`);
  const errors = validateEdition2Observations({root}).errors.join("\n");
  assert.match(errors, /template\.json: unknown property __proto__/);
  assert.match(errors, /template\.json\.__proto__: forbidden identity field email/);
});

withFixture((root) => {
  const schemaPath = path.join(root, "observation.schema.json");
  const schema = JSON.parse(fs.readFileSync(schemaPath, "utf8"));
  schema.minProperties = 1;
  fs.writeFileSync(schemaPath, `${JSON.stringify(schema, null, 2)}\n`);
  const errors = validateEdition2Observations({root}).errors.join("\n");
  assert.match(errors, /checker does not implement keyword minProperties/);
});

withFixture((root) => {
  const record = completedRecord(root);
  writeRecord(root, record);
  const result = validateEdition2Observations({
    root,
    repositoryRoot: root,
    commitExists: () => true,
  });
  assert.deepEqual(result.errors, []);
  assert.equal(result.recordCount, 2);
});

withFixture((root) => {
  const record = completedRecord(root);
  const reader = record.lanes.find((lane) => lane.kind === "real_reader");
  reader.task_observations = reader.task_observations.filter(
    (observation) =>
      !(
        observation.participant_pseudonym === "reader-b" &&
        observation.task_id === "reader-decision-boundary"
      ),
  );
  reader.task_observations[0].completed = false;
  writeRecord(root, record);
  const errors = validateEdition2Observations({
    root,
    repositoryRoot: root,
    commitExists: () => true,
  }).errors.join("\n");
  assert.match(errors, /completed reader lane requires completed true/);
  assert.match(
    errors,
    /participant reader-b must have exactly one completed observation for reader-decision-boundary/,
  );
});

withFixture((root) => {
  fs.writeFileSync(path.join(root, "unexpected.json"), "{}\n");
  fs.writeFileSync(path.join(root, "record-2099-01-02-uppercase.JSON"), "{}\n");
  fs.mkdirSync(path.join(root, "hidden"), {recursive: true});
  fs.writeFileSync(path.join(root, "hidden", "record-2099-01-02-hidden.json"), "{}\n");
  const errors = validateEdition2Observations({root}).errors.join("\n");
  assert.match(errors, /unexpected\.json: unknown JSON filename/);
  assert.match(errors, /record-2099-01-02-uppercase\.JSON: unknown JSON filename/);
  assert.match(errors, /hidden\/record-2099-01-02-hidden\.json: JSON files in subdirectories are forbidden/);
});

withFixture((root) => {
  const record = completedRecord(root);
  writeRecord(root, record);
  const errors = validateEdition2Observations({
    root,
    repositoryRoot: root,
    commitExists: () => false,
  }).errors.join("\n");
  assert.match(errors, /artifact commit does not exist as a Git commit/);
});

{
  const head = spawnSync("git", ["rev-parse", "HEAD"], {
    cwd: repositoryRoot,
    encoding: "utf8",
  }).stdout.trim();
  assert.match(head, /^[0-9a-f]{40}$/);
  assert.equal(gitCommitExists(repositoryRoot, head), true);
  assert.equal(gitCommitExists(repositoryRoot, "0".repeat(40)), false);
}

withFixture((root) => {
  const record = completedRecord(root);
  record.artifact.html_sha256 = "0".repeat(64);
  writeRecord(root, record);
  const errors = validateEdition2Observations({
    root,
    repositoryRoot: root,
    commitExists: () => true,
  }).errors.join("\n");
  assert.match(errors, /html_sha256 does not match artifacts\/book\.html/);
});

withFixture((root) => {
  const record = completedRecord(root);
  const domain = record.lanes.find((lane) => lane.kind === "materials_domain");
  domain.findings.push({
    finding_id: "E2-F-001",
    severity: "minor",
    summary: "duplicate fixture finding",
    evidence: ["fixture"],
    disposition: "edition_2_change_proposed",
    tracking: null,
  });
  record.edition_changes.edition_2_change_proposals.push({
    id: "E2-CHG-001",
    source_finding: "E2-F-999",
    proposal: "Missing finding reference fixture.",
    tracking: null,
  });
  record.edition_changes.edition_2_change_proposals.push({
    id: "E2-CHG-002",
    proposal: "Missing source_finding property fixture.",
    tracking: null,
  });
  writeRecord(root, record);
  const errors = validateEdition2Observations({
    root,
    repositoryRoot: root,
    commitExists: () => true,
  }).errors.join("\n");
  assert.match(errors, /duplicate finding ID E2-F-001/);
  assert.match(errors, /proposal E2-CHG-001 references missing finding E2-F-999/);
  assert.match(errors, /missing required property source_finding/);
});

withFixture((root) => {
  const record = completedRecord(root);
  record.edition_changes.edition_1_errata = [
    {
      id: "E1-ERR-001",
      location: "page 1",
      correction: "first correction",
      tracking: null,
    },
    {
      id: "E1-ERR-001",
      location: "page 2",
      correction: "duplicate correction",
      tracking: null,
    },
  ];
  record.edition_changes.edition_2_change_proposals = [
    {
      id: "E2-CHG-001",
      source_finding: "E2-F-001",
      proposal: "first proposal",
      tracking: null,
    },
    {
      id: "E2-CHG-001",
      source_finding: "E2-F-001",
      proposal: "duplicate proposal",
      tracking: null,
    },
  ];
  writeRecord(root, record);
  const errors = validateEdition2Observations({
    root,
    repositoryRoot: root,
    commitExists: () => true,
  }).errors.join("\n");
  assert.match(errors, /duplicate Edition 1 erratum ID E1-ERR-001/);
  assert.match(errors, /duplicate Edition 2 change ID E2-CHG-001/);
});

console.log(
  "Edition 2 observation fixture tests passed: schema execution, record discovery, honest pending state, proxy rejection, participant-task completion, artifact identity, finding references, protocol coverage, and privacy fields.",
);
