import fs from "node:fs";
import crypto from "node:crypto";
import path from "node:path";
import process from "node:process";
import {spawnSync} from "node:child_process";
import { fileURLToPath } from "node:url";

const defaultRoot = path.dirname(fileURLToPath(import.meta.url));
const requiredLaneKinds = [
  "real_reader",
  "assistive_technology",
  "materials_domain",
  "security",
];
const requiredTaskIds = ["reader-code-trace", "reader-decision-boundary"];
const requiredAtChecks = [
  "reading-order",
  "math-pronunciation",
  "table-navigation",
  "keyboard",
  "zoom-200",
];
const requiredDomainTopics = [
  "units",
  "composition-basis",
  "process-vocabulary",
  "measurement-vocabulary",
];
const requiredSecurityTopics = ["threat", "control", "residual-risk", "recovery"];
const forbiddenIdentityKeys = new Set([
  "name",
  "email",
  "phone",
  "organization",
  "employee_id",
]);
const supportedSchemaKeywords = new Set([
  "$schema",
  "$id",
  "$defs",
  "title",
  "type",
  "additionalProperties",
  "required",
  "properties",
  "const",
  "enum",
  "pattern",
  "minItems",
  "maxItems",
  "items",
  "oneOf",
  "$ref",
  "minLength",
  "minimum",
  "maximum",
  "format",
]);

function valueMatchesType(value, type) {
  if (type === "null") return value === null;
  if (type === "array") return Array.isArray(value);
  if (type === "object") return value !== null && typeof value === "object" && !Array.isArray(value);
  if (type === "integer") return Number.isInteger(value);
  if (type === "number") return typeof value === "number" && Number.isFinite(value);
  return typeof value === type;
}

function resolveSchemaReference(rootSchema, reference) {
  if (!reference.startsWith("#/")) throw new Error(`unsupported non-local $ref ${reference}`);
  return reference
    .slice(2)
    .split("/")
    .reduce((value, part) => value?.[part.replaceAll("~1", "/").replaceAll("~0", "~")], rootSchema);
}

function validateSchemaValue(value, schema, rootSchema, location, errors) {
  if (!schema || typeof schema !== "object" || Array.isArray(schema)) {
    errors.push(`${location}: schema node must be an object`);
    return;
  }
  if (schema.$ref) {
    const target = resolveSchemaReference(rootSchema, schema.$ref);
    if (!target) {
      errors.push(`${location}: unresolved schema reference ${schema.$ref}`);
      return;
    }
    validateSchemaValue(value, target, rootSchema, location, errors);
  }
  if (schema.oneOf) {
    const matches = schema.oneOf.filter((candidate) => {
      const branchErrors = [];
      validateSchemaValue(value, candidate, rootSchema, location, branchErrors);
      return branchErrors.length === 0;
    }).length;
    if (matches !== 1) errors.push(`${location}: must match exactly one schema branch`);
  }
  if (schema.const !== undefined && value !== schema.const) {
    errors.push(`${location}: must equal ${JSON.stringify(schema.const)}`);
  }
  if (schema.enum && !schema.enum.includes(value)) {
    errors.push(`${location}: unsupported value ${JSON.stringify(value)}`);
  }
  if (schema.type) {
    const types = Array.isArray(schema.type) ? schema.type : [schema.type];
    if (!types.some((type) => valueMatchesType(value, type))) {
      errors.push(`${location}: expected type ${types.join(" or ")}`);
      return;
    }
  }
  if (typeof value === "string") {
    if (schema.minLength !== undefined && value.length < schema.minLength) {
      errors.push(`${location}: must contain at least ${schema.minLength} character(s)`);
    }
    if (schema.pattern && !new RegExp(schema.pattern, "u").test(value)) {
      errors.push(`${location}: does not match required pattern`);
    }
    if (
      schema.format === "date-time" &&
      (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value) ||
        Number.isNaN(Date.parse(value)))
    ) {
      errors.push(`${location}: must be an RFC 3339 date-time`);
    }
  }
  if (typeof value === "number") {
    if (schema.minimum !== undefined && value < schema.minimum) {
      errors.push(`${location}: must be at least ${schema.minimum}`);
    }
    if (schema.maximum !== undefined && value > schema.maximum) {
      errors.push(`${location}: must be at most ${schema.maximum}`);
    }
  }
  if (Array.isArray(value)) {
    if (schema.minItems !== undefined && value.length < schema.minItems) {
      errors.push(`${location}: needs at least ${schema.minItems} item(s)`);
    }
    if (schema.maxItems !== undefined && value.length > schema.maxItems) {
      errors.push(`${location}: allows at most ${schema.maxItems} item(s)`);
    }
    if (schema.items) {
      value.forEach((entry, index) =>
        validateSchemaValue(entry, schema.items, rootSchema, `${location}[${index}]`, errors),
      );
    }
  }
  if (value !== null && typeof value === "object" && !Array.isArray(value)) {
    for (const required of schema.required ?? []) {
      if (!(required in value)) errors.push(`${location}: missing required property ${required}`);
    }
    for (const [key, entry] of Object.entries(value)) {
      if (Object.hasOwn(schema.properties ?? {}, key)) {
        validateSchemaValue(entry, schema.properties[key], rootSchema, `${location}.${key}`, errors);
      } else if (schema.additionalProperties === false) {
        errors.push(`${location}: unknown property ${key}`);
      }
    }
  }
}

function validateSchemaKeywordCoverage(schema, location, errors) {
  if (Array.isArray(schema)) {
    schema.forEach((entry, index) =>
      validateSchemaKeywordCoverage(entry, `${location}[${index}]`, errors),
    );
    return;
  }
  if (!schema || typeof schema !== "object") return;
  for (const [key, value] of Object.entries(schema)) {
    if (!supportedSchemaKeywords.has(key) && location !== "$.properties" && !location.endsWith(".$defs")) {
      errors.push(`observation.schema.json: checker does not implement keyword ${key} at ${location}`);
    }
    if (key === "properties" || key === "$defs") {
      for (const [childKey, child] of Object.entries(value)) {
        validateSchemaKeywordCoverage(child, `${location}.${key}.${childKey}`, errors);
      }
    } else if (["items", "oneOf"].includes(key)) {
      validateSchemaKeywordCoverage(value, `${location}.${key}`, errors);
    }
  }
}

function readJson(filename, errors) {
  try {
    return JSON.parse(fs.readFileSync(filename, "utf8"));
  } catch (error) {
    errors.push(`${path.basename(filename)}: invalid JSON: ${error.message}`);
    return null;
  }
}

function duplicateValues(values) {
  const seen = new Set();
  return values.filter((value) => {
    if (seen.has(value)) return true;
    seen.add(value);
    return false;
  });
}

function requireExactValues(values, required, label, errors) {
  if (!Array.isArray(values)) {
    errors.push(`${label}: must be an array`);
    return;
  }
  for (const duplicate of duplicateValues(values)) {
    errors.push(`${label}: duplicate value ${duplicate}`);
  }
  for (const value of required) {
    if (!values.includes(value)) errors.push(`${label}: missing ${value}`);
  }
  for (const value of values) {
    if (!required.includes(value)) errors.push(`${label}: unsupported value ${value}`);
  }
}

function findForbiddenIdentityKeys(value, location, errors) {
  if (Array.isArray(value)) {
    value.forEach((entry, index) =>
      findForbiddenIdentityKeys(entry, `${location}[${index}]`, errors),
    );
    return;
  }
  if (!value || typeof value !== "object") return;
  for (const [key, entry] of Object.entries(value)) {
    if (forbiddenIdentityKeys.has(key)) {
      errors.push(`${location}: forbidden identity field ${key}`);
    }
    findForbiddenIdentityKeys(entry, `${location}.${key}`, errors);
  }
}

function artifactIsFrozen(artifact) {
  return (
    artifact?.edition === 2 &&
    /^[0-9a-f]{40}$/.test(artifact.commit ?? "") &&
    typeof artifact.html_path === "string" &&
    artifact.html_path.length > 0 &&
    /^[0-9a-f]{64}$/.test(artifact.html_sha256 ?? "") &&
    typeof artifact.pdf_path === "string" &&
    artifact.pdf_path.length > 0 &&
    /^[0-9a-f]{64}$/.test(artifact.pdf_sha256 ?? "")
  );
}

function isRepositoryPath(value) {
  return (
    typeof value === "string" &&
    value.length > 0 &&
    !value.includes("\\") &&
    !path.isAbsolute(value) &&
    value.split("/").every((part) => part && part !== "." && part !== "..")
  );
}

function fileSha256(filename) {
  return crypto.createHash("sha256").update(fs.readFileSync(filename)).digest("hex");
}

export function gitCommitExists(repositoryRoot, commit) {
  const result = spawnSync("git", ["cat-file", "-e", `${commit}^{commit}`], {
    cwd: repositoryRoot,
    stdio: "ignore",
  });
  return result.status === 0;
}

function validateFrozenArtifact(artifact, repositoryRoot, commitExists, label, errors) {
  if (!artifactIsFrozen(artifact)) return;
  if (!commitExists(repositoryRoot, artifact.commit)) {
    errors.push(`${label}: artifact commit does not exist as a Git commit`);
  }
  for (const [kind, expectedExtension] of [
    ["html", ".html"],
    ["pdf", ".pdf"],
  ]) {
    const relative = artifact[`${kind}_path`];
    const expectedDigest = artifact[`${kind}_sha256`];
    if (!isRepositoryPath(relative)) {
      errors.push(`${label}: ${kind}_path must be a repository-relative path`);
      continue;
    }
    if (path.extname(relative).toLowerCase() !== expectedExtension) {
      errors.push(`${label}: ${kind}_path must name a ${expectedExtension} file`);
    }
    const filename = path.join(repositoryRoot, ...relative.split("/"));
    if (!fs.existsSync(filename) || !fs.statSync(filename).isFile()) {
      errors.push(`${label}: artifact file does not exist: ${relative}`);
      continue;
    }
    const resolvedRepositoryRoot = fs.realpathSync(repositoryRoot);
    const resolvedFilename = fs.realpathSync(filename);
    if (
      resolvedFilename !== resolvedRepositoryRoot &&
      !resolvedFilename.startsWith(`${resolvedRepositoryRoot}${path.sep}`)
    ) {
      errors.push(`${label}: artifact path resolves outside repository: ${relative}`);
      continue;
    }
    const actualDigest = fileSha256(filename);
    if (actualDigest !== expectedDigest) {
      errors.push(`${label}: ${kind}_sha256 does not match ${relative}`);
    }
  }
}

function requireParticipant(participant, label, errors) {
  if (
    !participant ||
    typeof participant.pseudonym !== "string" ||
    participant.pseudonym.trim() === "" ||
    typeof participant.role !== "string" ||
    participant.role.trim() === ""
  ) {
    errors.push(`${label}: pseudonym and role are required`);
  }
}

function validateRealReaderLane(lane, errors) {
  if (!Array.isArray(lane.participants) || !Array.isArray(lane.task_observations)) {
    errors.push("real_reader: participants and task_observations must be arrays");
    return;
  }
  if (lane.status !== "completed") return;
  if (lane.participants.length < 2) {
    errors.push("real_reader: completed lane requires at least two real participants");
  }
  lane.participants.forEach((participant, index) =>
    requireParticipant(participant, `real_reader.participants[${index}]`, errors),
  );
  const pseudonyms = new Set(lane.participants.map((participant) => participant?.pseudonym));
  if (pseudonyms.size !== lane.participants.length) {
    errors.push("real_reader: participant pseudonyms must be unique");
  }
  const observedTaskIds = lane.task_observations.map((observation) => observation?.task_id);
  requireExactValues(
    [...new Set(observedTaskIds)],
    requiredTaskIds,
    "real_reader.task_observations task IDs",
    errors,
  );
  if (lane.task_observations.length < 2) {
    errors.push("real_reader: completed lane requires at least two task observations");
  }
  lane.task_observations.forEach((observation, index) => {
    const label = `real_reader.task_observations[${index}]`;
    if (!pseudonyms.has(observation?.participant_pseudonym)) {
      errors.push(`${label}: participant_pseudonym is not registered`);
    }
    if (!Number.isInteger(observation?.duration_seconds) || observation.duration_seconds < 0) {
      errors.push(`${label}: duration_seconds must be a non-negative integer`);
    }
    if (typeof observation?.completed !== "boolean") {
      errors.push(`${label}: completed must be boolean`);
    } else if (observation.completed !== true) {
      errors.push(`${label}: completed reader lane requires completed true`);
    }
    for (const field of ["errors", "confusion_points", "consulted_pages"]) {
      if (!Array.isArray(observation?.[field])) errors.push(`${label}: ${field} must be an array`);
    }
    if (!Array.isArray(observation?.consulted_pages) || observation.consulted_pages.length === 0) {
      errors.push(`${label}: at least one consulted page is required`);
    }
    if (
      !Number.isInteger(observation?.backtracking_count) ||
      observation.backtracking_count < 0
    ) {
      errors.push(`${label}: backtracking_count must be a non-negative integer`);
    }
  });
  for (const pseudonym of pseudonyms) {
    for (const taskId of requiredTaskIds) {
      const matches = lane.task_observations.filter(
        (observation) =>
          observation?.participant_pseudonym === pseudonym &&
          observation?.task_id === taskId,
      );
      if (matches.length !== 1) {
        errors.push(
          `real_reader: participant ${pseudonym} must have exactly one completed observation for ${taskId}`,
        );
      } else if (matches[0].completed !== true) {
        errors.push(
          `real_reader: participant ${pseudonym} did not complete ${taskId}`,
        );
      }
    }
  }
}

function validateAtLane(lane, errors) {
  requireExactValues(
    (lane.matrix ?? []).map((entry) => entry?.check_id),
    requiredAtChecks,
    "assistive_technology.matrix",
    errors,
  );
  if (lane.status !== "completed") return;
  requireParticipant(lane.operator, "assistive_technology.operator", errors);
  for (const field of [
    "operating_system",
    "browser_or_reader",
    "assistive_technology",
    "assistive_technology_version",
  ]) {
    if (typeof lane.environment?.[field] !== "string" || lane.environment[field].trim() === "") {
      errors.push(`assistive_technology.environment: ${field} is required`);
    }
  }
  for (const entry of lane.matrix ?? []) {
    if (!["pass", "fail"].includes(entry?.result)) {
      errors.push(`assistive_technology.${entry?.check_id}: completed lane needs an observed result`);
    }
    if (typeof entry?.observation !== "string" || entry.observation.trim() === "") {
      errors.push(`assistive_technology.${entry?.check_id}: observation is required`);
    }
    if (!Array.isArray(entry?.evidence) || entry.evidence.length === 0) {
      errors.push(`assistive_technology.${entry?.check_id}: evidence is required`);
    }
  }
}

function validateReviewLane(lane, requiredTopics, label, errors) {
  requireExactValues(
    (lane.review_results ?? []).map((entry) => entry?.topic),
    requiredTopics,
    `${label}.review_results`,
    errors,
  );
  if (lane.status !== "completed") return;
  requireParticipant(lane.reviewer, `${label}.reviewer`, errors);
  for (const entry of lane.review_results ?? []) {
    if (!["pass", "changes_requested"].includes(entry?.result)) {
      errors.push(`${label}.${entry?.topic}: completed lane needs a review result`);
    }
    if (typeof entry?.observation !== "string" || entry.observation.trim() === "") {
      errors.push(`${label}.${entry?.topic}: observation is required`);
    }
  }
  if (label === "security" && (!Array.isArray(lane.findings) || lane.findings.length === 0)) {
    errors.push("security: completed lane requires at least one finding with disposition");
  }
}

function validateRecord(record, schema, repositoryRoot, commitExists, label, errors) {
  if (!record || typeof record !== "object" || Array.isArray(record)) {
    errors.push(`${label}: root must be an object`);
    return;
  }
  validateSchemaValue(record, schema, schema, label, errors);
  findForbiddenIdentityKeys(record, label, errors);
  if (record.$schema !== "./observation.schema.json") {
    errors.push(`${label}: $schema must be ./observation.schema.json`);
  }
  if (record.schema_version !== "learning-edition-2-observation/v1") {
    errors.push(`${label}: unsupported schema_version`);
  }
  if (record.source_issue !== 387) errors.push(`${label}: source_issue must be 387`);
  if (record.artifact?.edition !== 2) errors.push(`${label}: artifact edition must be 2`);
  if (!Array.isArray(record.lanes)) {
    errors.push(`${label}: lanes must be an array`);
    return;
  }
  requireExactValues(
    record.lanes.map((lane) => lane?.kind),
    requiredLaneKinds,
    `${label}.lanes`,
    errors,
  );
  const lanes = new Map(record.lanes.map((lane) => [lane?.kind, lane]));
  for (const [kind, lane] of lanes) {
    if (lane?.proxy !== false) errors.push(`${label}.${kind}: proxy must be false`);
    if (
      !["not_run", "awaiting_external_review", "in_progress", "completed"].includes(
        lane?.status,
      )
    ) {
      errors.push(`${label}.${kind}: unsupported status ${lane?.status}`);
    }
    if (lane?.status === "completed" && !artifactIsFrozen(record.artifact)) {
      errors.push(`${label}.${kind}: completed lane requires frozen artifact digests`);
    }
  }
  if (record.lanes.some((lane) => lane?.status === "completed")) {
    validateFrozenArtifact(record.artifact, repositoryRoot, commitExists, label, errors);
  }
  if (lanes.has("real_reader")) validateRealReaderLane(lanes.get("real_reader"), errors);
  if (lanes.has("assistive_technology")) validateAtLane(lanes.get("assistive_technology"), errors);
  if (lanes.has("materials_domain")) {
    const lane = lanes.get("materials_domain");
    if (lane.linked_issue !== 303) errors.push("materials_domain: linked_issue must be 303");
    validateReviewLane(lane, requiredDomainTopics, "materials_domain", errors);
    if (
      lane.status === "completed" &&
      (typeof lane.issue_303_reflection !== "string" ||
        lane.issue_303_reflection.trim() === "")
    ) {
      errors.push("materials_domain: completed lane requires Issue #303 reflection evidence");
    }
  }
  if (lanes.has("security")) {
    validateReviewLane(lanes.get("security"), requiredSecurityTopics, "security", errors);
  }
  if (
    record.overall_status === "complete" &&
    requiredLaneKinds.some((kind) => lanes.get(kind)?.status !== "completed")
  ) {
    errors.push(`${label}: overall complete requires every external lane to be completed`);
  }
  const changes = record.edition_changes;
  if (!Array.isArray(changes?.edition_1_errata)) {
    errors.push(`${label}: edition_1_errata must be an array`);
  }
  if (!Array.isArray(changes?.edition_2_change_proposals)) {
    errors.push(`${label}: edition_2_change_proposals must be an array`);
  }
  for (const duplicate of duplicateValues(
    (changes?.edition_1_errata ?? []).map((erratum) => erratum?.id),
  )) {
    errors.push(`${label}: duplicate Edition 1 erratum ID ${duplicate}`);
  }
  for (const duplicate of duplicateValues(
    (changes?.edition_2_change_proposals ?? []).map((proposal) => proposal?.id),
  )) {
    errors.push(`${label}: duplicate Edition 2 change ID ${duplicate}`);
  }
  for (const erratum of changes?.edition_1_errata ?? []) {
    if (!/^E1-ERR-[0-9]{3}$/.test(erratum?.id ?? "")) {
      errors.push(`${label}: invalid Edition 1 erratum ID ${erratum?.id}`);
    }
  }
  for (const proposal of changes?.edition_2_change_proposals ?? []) {
    if (!/^E2-CHG-[0-9]{3}$/.test(proposal?.id ?? "")) {
      errors.push(`${label}: invalid Edition 2 change ID ${proposal?.id}`);
    }
  }
  const findings = record.lanes.flatMap((lane) =>
    Array.isArray(lane?.findings) ? lane.findings : [],
  );
  const findingIds = findings.map((finding) => finding?.finding_id);
  for (const duplicate of duplicateValues(findingIds)) {
    errors.push(`${label}: duplicate finding ID ${duplicate}`);
  }
  const findingIdSet = new Set(findingIds);
  for (const proposal of changes?.edition_2_change_proposals ?? []) {
    if (!findingIdSet.has(proposal?.source_finding)) {
      errors.push(
        `${label}: Edition 2 proposal ${proposal?.id} references missing finding ${proposal?.source_finding}`,
      );
    }
  }
}

function validateProtocol(protocol, errors) {
  if (protocol?.schema_version !== "learning-edition-2-protocol/v1") {
    errors.push("protocol.json: unsupported schema_version");
  }
  if (protocol?.source_issue !== 387) errors.push("protocol.json: source_issue must be 387");
  requireExactValues(
    (protocol?.real_reader_tasks ?? []).map((task) => task?.task_id),
    requiredTaskIds,
    "protocol.json real_reader_tasks",
    errors,
  );
  requireExactValues(
    (protocol?.assistive_technology_matrix ?? []).map((item) => item?.check_id),
    requiredAtChecks,
    "protocol.json assistive_technology_matrix",
    errors,
  );
  requireExactValues(
    (protocol?.materials_review_packet ?? []).map((item) => item?.topic),
    requiredDomainTopics,
    "protocol.json materials_review_packet",
    errors,
  );
  requireExactValues(
    (protocol?.security_review_packet ?? []).map((item) => item?.topic),
    requiredSecurityTopics,
    "protocol.json security_review_packet",
    errors,
  );
  requireExactValues(
    protocol?.privacy?.allowed_identity_fields,
    ["pseudonym", "role"],
    "protocol.json allowed_identity_fields",
    errors,
  );
  requireExactValues(
    protocol?.privacy?.forbidden_identity_fields,
    [...forbiddenIdentityKeys],
    "protocol.json forbidden_identity_fields",
    errors,
  );
}

function jsonFilesBelow(root) {
  if (!fs.existsSync(root)) return [];
  const files = [];
  const visit = (directory) => {
    for (const entry of fs.readdirSync(directory, {withFileTypes: true})) {
      const filename = path.join(directory, entry.name);
      if (entry.isDirectory()) visit(filename);
      else if (entry.isFile() && entry.name.toLowerCase().endsWith(".json")) files.push(filename);
    }
  };
  visit(root);
  return files;
}

export function validateEdition2Observations({
  root = defaultRoot,
  repositoryRoot = path.resolve(root, "..", "..", "..", ".."),
  commitExists = gitCommitExists,
} = {}) {
  const errors = [];
  const schemaPath = path.join(root, "observation.schema.json");
  const protocolPath = path.join(root, "protocol.json");
  const templatePath = path.join(root, "template.json");
  const protocolDocumentPath = path.join(root, "protocol.qmd");
  for (const filename of [schemaPath, protocolPath, templatePath, protocolDocumentPath]) {
    if (!fs.existsSync(filename)) errors.push(`${path.basename(filename)} is missing`);
  }
  const schema = fs.existsSync(schemaPath) ? readJson(schemaPath, errors) : null;
  const protocol = fs.existsSync(protocolPath) ? readJson(protocolPath, errors) : null;
  if (schema?.properties?.schema_version?.const !== "learning-edition-2-observation/v1") {
    errors.push("observation.schema.json: schema_version contract is missing");
  }
  if (schema) validateSchemaKeywordCoverage(schema, "$", errors);
  const schemaText = schema ? JSON.stringify(schema) : "";
  if (schemaText.includes('"proxy":{"const":true}')) {
    errors.push("observation.schema.json: proxy true is forbidden");
  }
  if (protocol) validateProtocol(protocol, errors);
  const allowedInfrastructure = new Set([
    "observation.schema.json",
    "protocol.json",
    "template.json",
  ]);
  const allJsonPaths = jsonFilesBelow(root);
  const recordPaths = [];
  for (const filename of allJsonPaths) {
    const relative = path.relative(root, filename).replaceAll("\\", "/");
    if (relative.includes("/")) {
      errors.push(`${relative}: JSON files in subdirectories are forbidden`);
      continue;
    }
    if (allowedInfrastructure.has(relative)) {
      if (relative === "template.json") recordPaths.push(filename);
      continue;
    }
    if (/^record-[0-9]{4}-[0-9]{2}-[0-9]{2}-[a-z0-9-]+\.json$/.test(relative)) {
      recordPaths.push(filename);
    } else {
      errors.push(`${relative}: unknown JSON filename`);
    }
  }
  for (const recordPath of recordPaths) {
    const record = readJson(recordPath, errors);
    if (record && schema) {
      validateRecord(
        record,
        schema,
        repositoryRoot,
        commitExists,
        path.basename(recordPath),
        errors,
      );
    }
  }
  return {errors, recordCount: recordPaths.length};
}

function main() {
  const rootIndex = process.argv.indexOf("--root");
  if (rootIndex >= 0 && !process.argv[rootIndex + 1]) {
    throw new Error("--root needs a path");
  }
  const root = rootIndex >= 0 ? path.resolve(process.argv[rootIndex + 1]) : defaultRoot;
  const result = validateEdition2Observations({root});
  if (result.errors.length > 0) {
    console.error(`Edition 2 observation validation failed with ${result.errors.length} error(s):`);
    result.errors.forEach((error) => console.error(`- ${error}`));
    process.exitCode = 1;
    return;
  }
  console.log(
    `Edition 2 observation validation passed: ${result.recordCount} template or record file(s).`,
  );
}

if (
  process.argv[1] &&
  path.resolve(process.argv[1]) === path.resolve(fileURLToPath(import.meta.url))
) {
  main();
}
