import fs from "node:fs";
import path from "node:path";
import process from "node:process";

const reviewRoot = path.resolve("docs/learning/reviews");
const schema = JSON.parse(
  fs.readFileSync(path.join(reviewRoot, "review-record.schema.json"), "utf8"),
);
const records = fs
  .readdirSync(reviewRoot)
  .filter((name) => /^acceptance-\d{4}-\d{2}-\d{2}-issue-\d+\.json$/.test(name))
  .sort();

const allowedRoles = new Set(
  schema.$defs.roleReview.properties.role.enum,
);
const allowedStatuses = new Set(schema.properties.status.enum);
const allowedResults = new Set(["pass", "pass_with_followup", "fail"]);
const errors = [];
const seenReviewIds = new Set();

function requireText(value, location) {
  if (typeof value !== "string" || value.trim() === "") {
    errors.push(`${location} must be a non-empty string`);
  }
}

for (const fileName of records) {
  const filePath = path.join(reviewRoot, fileName);
  const record = JSON.parse(fs.readFileSync(filePath, "utf8"));
  const prefix = `docs/learning/reviews/${fileName}`;

  if (record.schema_version !== 1) {
    errors.push(`${prefix}: schema_version must be 1`);
  }
  if (seenReviewIds.has(record.review_id)) {
    errors.push(`${prefix}: duplicate review_id ${record.review_id}`);
  }
  seenReviewIds.add(record.review_id);
  if (!allowedStatuses.has(record.status)) {
    errors.push(`${prefix}: unsupported status ${record.status}`);
  }
  if (!/^[0-9a-f]{40}$/.test(record.reviewed_against ?? "")) {
    errors.push(`${prefix}: reviewed_against must be a full commit SHA`);
  }

  const chapterPaths = record.chapter?.paths ?? [];
  if (chapterPaths.length === 0) {
    errors.push(`${prefix}: chapter.paths must not be empty`);
  }
  for (const chapterPath of chapterPaths) {
    if (!chapterPath.startsWith("docs/learning/")) {
      errors.push(`${prefix}: chapter path must stay below docs/learning: ${chapterPath}`);
      continue;
    }
    if (!fs.existsSync(path.resolve(chapterPath))) {
      errors.push(`${prefix}: chapter path does not exist: ${chapterPath}`);
    }
  }

  const roles = record.roles ?? [];
  const roleNames = new Set();
  for (const [index, role] of roles.entries()) {
    const location = `${prefix}: roles[${index}]`;
    if (!allowedRoles.has(role.role)) {
      errors.push(`${location}: unsupported role ${role.role}`);
    }
    if (roleNames.has(role.role)) {
      errors.push(`${location}: duplicate role ${role.role}`);
    }
    roleNames.add(role.role);
    if (!allowedResults.has(role.result)) {
      errors.push(`${location}: unsupported result ${role.result}`);
    }
    requireText(role.reviewer, `${location}.reviewer`);
    requireText(role.method, `${location}.method`);
    requireText(role.notes, `${location}.notes`);
    if (!Array.isArray(role.evidence) || role.evidence.length === 0) {
      errors.push(`${location}.evidence must not be empty`);
    }
  }
  for (const requiredRole of ["implementation", "pedagogy", "accessibility"]) {
    if (!roleNames.has(requiredRole)) {
      errors.push(`${prefix}: missing required role ${requiredRole}`);
    }
  }
  if (record.issue?.number === 307) {
    for (const requiredRole of ["statistics", "domain"]) {
      if (!roleNames.has(requiredRole)) {
        errors.push(`${prefix}: Issue #307 requires role ${requiredRole}`);
      }
    }
  }

  const readerTasks = record.reader_task_tests ?? [];
  if (readerTasks.length < 2) {
    errors.push(`${prefix}: at least two reader task tests are required`);
  }
  for (const [index, readerTask] of readerTasks.entries()) {
    const location = `${prefix}: reader_task_tests[${index}]`;
    if (readerTask.proxy !== true) {
      errors.push(`${location}.proxy must explicitly be true`);
    }
    if (!allowedResults.has(readerTask.result)) {
      errors.push(`${location}: unsupported result ${readerTask.result}`);
    }
    for (const key of [
      "profile",
      "starting_context",
      "task",
      "limitations",
    ]) {
      requireText(readerTask[key], `${location}.${key}`);
    }
    for (const key of ["success_criteria", "observations"]) {
      if (!Array.isArray(readerTask[key]) || readerTask[key].length === 0) {
        errors.push(`${location}.${key} must not be empty`);
      }
    }
  }

  const findingIds = new Set();
  for (const [index, finding] of (record.findings ?? []).entries()) {
    const location = `${prefix}: findings[${index}]`;
    if (findingIds.has(finding.finding_id)) {
      errors.push(`${location}: duplicate finding_id ${finding.finding_id}`);
    }
    findingIds.add(finding.finding_id);
    requireText(finding.summary, `${location}.summary`);
    if (!Array.isArray(finding.evidence) || finding.evidence.length === 0) {
      errors.push(`${location}.evidence must not be empty`);
    }
    if (
      finding.requires_textbook_change &&
      finding.disposition !== "fixed_in_review"
    ) {
      errors.push(
        `${location}: unresolved textbook change cannot be accepted`,
      );
    }
  }

  if (
    record.status !== "changes_required" &&
    roles.some((role) => role.result === "fail")
  ) {
    errors.push(`${prefix}: accepted record contains a failed role`);
  }
  if (
    record.issue_close_recommendation !== "not_ready" &&
    (record.findings ?? []).some(
      (finding) =>
        finding.severity === "blocking" &&
        finding.disposition !== "fixed_in_review",
    )
  ) {
    errors.push(`${prefix}: close recommendation contains a blocking finding`);
  }
}

if (records.length < 2) {
  errors.push("At least two acceptance review records are required.");
}

if (errors.length > 0) {
  for (const error of errors) {
    console.error(error);
  }
  process.exit(1);
}

console.log(
  `Learning acceptance review validation passed: ${records.length} records.`,
);
