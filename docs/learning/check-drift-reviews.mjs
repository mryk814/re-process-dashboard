import { execFileSync, spawnSync } from "node:child_process";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { parseDocumentMetadata } from "./check-code-references.mjs";

const learningRoot = resolve(dirname(fileURLToPath(import.meta.url)));
const repositoryRoot = resolve(learningRoot, "..", "..");
const reviewRoot = join(learningRoot, "drift-reviews");

const classifications = new Set([
  "no_content_impact",
  "editorial_update",
  "conceptual_update",
  "exercise_invalidated",
  "evidence_unavailable",
]);
const semanticImpacts = new Set([
  "none",
  "wording-only",
  "interface-compatible",
  "behavioral",
  "contract-breaking",
  "evidence-invalidating",
]);
const materialImpacts = new Set([
  "learner-text",
  "exercise",
  "solution",
  "figure-screenshot",
  "code-link",
  "test-command",
  "maintainer-guide",
  "bibliography",
]);
const actions = new Set(["updated", "no-change", "deferred"]);

function fail(message) {
  throw new Error(message);
}

function assert(condition, message) {
  if (!condition) fail(message);
}

function git(args, options = {}) {
  return execFileSync("git", args, {
    cwd: repositoryRoot,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
    ...options,
  }).trim();
}

function assertCommit(commit, label) {
  assert(typeof commit === "string" && /^[0-9a-f]{40}$/.test(commit), `${label} must be a full commit SHA.`);
  const result = spawnSync("git", ["cat-file", "-e", `${commit}^{commit}`], {
    cwd: repositoryRoot,
    stdio: "ignore",
  });
  assert(result.status === 0, `${label} does not exist: ${commit}`);
}

function assertAncestor(ancestor, descendant, label) {
  const result = spawnSync(
    "git",
    ["merge-base", "--is-ancestor", ancestor, descendant],
    { cwd: repositoryRoot, stdio: "ignore" },
  );
  assert(result.status === 0, `${label}: ${ancestor} is not an ancestor of ${descendant}.`);
}

function assertUnique(values, label) {
  assert(new Set(values).size === values.length, `${label} contains duplicates.`);
}

function repositoryFile(path, label) {
  assert(typeof path === "string" && path.length > 0, `${label} must name a file.`);
  const absolute = resolve(repositoryRoot, path);
  const repositoryRelative = relative(repositoryRoot, absolute);
  assert(
    repositoryRelative !== "" &&
      repositoryRelative !== ".." &&
      !repositoryRelative.startsWith(`..${sep}`) &&
      !isAbsolute(repositoryRelative),
    `${label} escapes repository root: ${path}`,
  );
  assert(statSync(absolute, { throwIfNoEntry: false })?.isFile(), `${label} does not exist: ${path}`);
  return absolute;
}

function qmdFiles(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) return qmdFiles(path);
    return entry.isFile() && entry.name.endsWith(".qmd") ? [path] : [];
  });
}

const structuredReferencePaths = new Set(
  qmdFiles(learningRoot).flatMap((path) => {
    const metadata = parseDocumentMetadata(readFileSync(path, "utf8"), path);
    return metadata?.references.map((reference) => reference.path) ?? [];
  }),
);

const files = readdirSync(reviewRoot)
  .filter((name) => /^drift-\d{4}-\d{2}-\d{2}-pr-\d+\.json$/.test(name))
  .sort();
assert(files.length > 0, "No drift review records were found.");

for (const file of files) {
  const path = join(reviewRoot, file);
  const record = JSON.parse(readFileSync(path, "utf8"));
  assert(record.schema_version === 1, `${file}: unsupported schema_version.`);
  assert(record.template === false, `${file}: record cannot be a template.`);
  assert(record.review_id === file.replace(/\.json$/, ""), `${file}: review_id must match filename.`);
  assert(["detected", "reviewed-no-change", "update-required", "blocked", "resolved"].includes(record.status), `${file}: invalid status.`);
  assert(record.trigger?.kind === "pull_request", `${file}: trigger.kind must be pull_request.`);
  assert(Number.isInteger(record.trigger.number) && record.trigger.number > 0, `${file}: invalid PR number.`);

  for (const [label, commit] of [
    ["trigger.merge_commit", record.trigger.merge_commit],
    ["reviewed_against", record.reviewed_against],
    ["previous_verified_commit", record.previous_verified_commit],
    ["new_verified_commit", record.new_verified_commit],
    ["implementation_commit", record.implementation_commit],
  ]) {
    if (commit !== null) assertCommit(commit, `${file}: ${label}`);
  }
  assert(record.reviewed_against === record.trigger.merge_commit, `${file}: reviewed_against must equal the merge commit.`);

  const changedPaths = git([
    "diff",
    "--name-only",
    `${record.trigger.merge_commit}^1`,
    record.trigger.merge_commit,
  ]).split(/\r?\n/).filter(Boolean).sort();
  const recordedPaths = record.changes.map((change) => change.path).sort();
  assertUnique(recordedPaths, `${file}: changes`);
  assert(
    JSON.stringify(changedPaths) === JSON.stringify(recordedPaths),
    `${file}: recorded paths differ from the PR merge diff.\nExpected: ${changedPaths.join(", ")}\nFound: ${recordedPaths.join(", ")}`,
  );

  const claimIds = record.claim_assessments.map((claim) => claim.claim_id);
  assertUnique(claimIds, `${file}: claim_assessments`);
  const claimIdSet = new Set(claimIds);
  for (const change of record.changes) {
    assert(semanticImpacts.has(change.semantic_impact), `${file}: invalid semantic impact for ${change.path}.`);
    assert(actions.has(change.action), `${file}: invalid action for ${change.path}.`);
    assert(typeof change.reason === "string" && change.reason.length > 20, `${file}: change reason is too weak for ${change.path}.`);
    assert(Array.isArray(change.material_impact), `${file}: material_impact must be an array.`);
    for (const impact of change.material_impact) {
      assert(materialImpacts.has(impact), `${file}: invalid material impact '${impact}'.`);
    }
    assert(change.affected_claims.length > 0, `${file}: ${change.path} has no affected claim.`);
    for (const claimId of change.affected_claims) {
      assert(claimIdSet.has(claimId), `${file}: ${change.path} refers to unknown claim '${claimId}'.`);
    }
  }

  for (const claim of record.claim_assessments) {
    assert(classifications.has(claim.classification), `${file}: invalid classification for ${claim.claim_id}.`);
    assert(actions.has(claim.action), `${file}: invalid action for ${claim.claim_id}.`);
    assert(typeof claim.statement === "string" && claim.statement.length > 20, `${file}: claim statement is too weak.`);
    assert(typeof claim.reason === "string" && claim.reason.length > 20, `${file}: claim reason is too weak.`);
    assert(Array.isArray(claim.evidence) && claim.evidence.length > 0, `${file}: ${claim.claim_id} has no evidence.`);
    assert(Array.isArray(claim.verification) && claim.verification.length > 0, `${file}: ${claim.claim_id} has no verification.`);
    for (const evidence of claim.evidence) {
      const evidencePath = repositoryFile(
        evidence.path,
        `${file}: evidence for ${claim.claim_id}`,
      );
      if (evidence.symbol !== null) {
        assert(
          typeof evidence.symbol === "string" && evidence.symbol.length > 0,
          `${file}: evidence symbol for ${claim.claim_id} must be null or a non-empty string.`,
        );
        assert(
          readFileSync(evidencePath, "utf8").includes(evidence.symbol),
          `${file}: evidence symbol '${evidence.symbol}' was not found in ${evidence.path}.`,
        );
      }
    }
  }

  if (["resolved", "reviewed-no-change"].includes(record.status)) {
    for (const [key, value] of Object.entries(record.verification)) {
      assert(
        typeof value === "string" &&
          value.length > 10 &&
          !/\b(?:pending|todo|tbd)\b/i.test(value),
        `${file}: verification '${key}' is pending.`,
      );
    }
  }

  if (record.status === "resolved") {
    assert(record.new_verified_commit, `${file}: resolved record needs new_verified_commit.`);
    assert(
      record.new_verified_commit === record.reviewed_against,
      `${file}: resolved new_verified_commit must equal reviewed_against.`,
    );
    assertAncestor(
      record.previous_verified_commit,
      record.reviewed_against,
      `${file}: verified commit history`,
    );
    if (record.implementation_commit) {
      assertAncestor(
        record.reviewed_against,
        record.implementation_commit,
        `${file}: implementation history`,
      );
    }
    assert(
      record.changes.every((change) => change.action !== "deferred"),
      `${file}: resolved record contains a deferred changed file.`,
    );
    assert(
      record.claim_assessments.every(
        (claim) =>
          claim.action !== "deferred" &&
          claim.classification !== "evidence_unavailable",
      ),
      `${file}: resolved record contains an unresolved claim.`,
    );
  }
}

const groupedFiles = readdirSync(reviewRoot)
  .filter((name) => /^grouped-\d{4}-\d{2}-\d{2}-[a-z0-9-]+\.json$/.test(name))
  .sort();

for (const file of groupedFiles) {
  const record = JSON.parse(readFileSync(join(reviewRoot, file), "utf8"));
  assert(record.schema_version === 1, `${file}: unsupported schema_version.`);
  assert(record.review_id === file.replace(/\.json$/, ""), `${file}: review_id must match filename.`);
  assert(record.status === "resolved", `${file}: grouped review must be resolved.`);
  assert(record.recorded_after_the_fact === true, `${file}: grouped review must identify retrospective recording.`);
  assertCommit(record.base_commit, `${file}: base_commit`);
  assertCommit(record.reviewed_against, `${file}: reviewed_against`);
  assertAncestor(record.base_commit, record.reviewed_against, `${file}: review range`);
  assert(Array.isArray(record.pull_requests) && record.pull_requests.length > 0, `${file}: pull_requests must not be empty.`);

  const pullRequestNumbers = record.pull_requests.map((pullRequest) => pullRequest.number);
  assertUnique(pullRequestNumbers, `${file}: pull request numbers`);
  const rangeMergeCommits = git([
    "rev-list",
    "--first-parent",
    "--merges",
    `${record.base_commit}..${record.reviewed_against}`,
  ]).split(/\r?\n/).filter(Boolean).sort();
  const recordedMergeCommits = record.pull_requests
    .map((pullRequest) => pullRequest.merge_commit)
    .sort();
  assertUnique(recordedMergeCommits, `${file}: merge commits`);
  assert(
    JSON.stringify(rangeMergeCommits) === JSON.stringify(recordedMergeCommits),
    `${file}: pull_requests must exactly cover first-parent merges in the review range.\nExpected: ${rangeMergeCommits.join(", ")}\nFound: ${recordedMergeCommits.join(", ")}`,
  );
  for (const pullRequest of record.pull_requests) {
    assert(Number.isInteger(pullRequest.number) && pullRequest.number > 0, `${file}: invalid pull request number.`);
    assertCommit(pullRequest.merge_commit, `${file}: PR #${pullRequest.number} merge_commit`);
    assertAncestor(record.base_commit, pullRequest.merge_commit, `${file}: PR #${pullRequest.number} range`);
    assertAncestor(pullRequest.merge_commit, record.reviewed_against, `${file}: PR #${pullRequest.number} range`);
    const mergeSubject = git(["show", "-s", "--format=%s", pullRequest.merge_commit]);
    assert(
      mergeSubject.includes(`#${pullRequest.number} `),
      `${file}: ${pullRequest.merge_commit} is not the recorded PR #${pullRequest.number} merge.`,
    );
    assert(classifications.has(pullRequest.classification), `${file}: invalid PR #${pullRequest.number} classification.`);
    assert(typeof pullRequest.reason === "string" && pullRequest.reason.length > 20, `${file}: PR #${pullRequest.number} reason is too weak.`);

    const changedPaths = git([
      "diff",
      "--name-only",
      `${pullRequest.merge_commit}^1`,
      pullRequest.merge_commit,
    ]).split(/\r?\n/).filter(Boolean).sort();
    const recordedPaths = [...pullRequest.changed_paths].sort();
    assertUnique(recordedPaths, `${file}: PR #${pullRequest.number} changed_paths`);
    assert(
      JSON.stringify(changedPaths) === JSON.stringify(recordedPaths),
      `${file}: PR #${pullRequest.number} paths differ from the first-parent merge diff.`,
    );
    const expectedReferencedPaths = changedPaths
      .filter((path) => structuredReferencePaths.has(path))
      .sort();
    const recordedReferencedPaths = [...pullRequest.referenced_paths].sort();
    assertUnique(recordedReferencedPaths, `${file}: PR #${pullRequest.number} referenced_paths`);
    assert(
      JSON.stringify(expectedReferencedPaths) === JSON.stringify(recordedReferencedPaths),
      `${file}: PR #${pullRequest.number} referenced_paths must exactly match the current textbook's structured references.\nExpected: ${expectedReferencedPaths.join(", ")}\nFound: ${recordedReferencedPaths.join(", ")}`,
    );
  }

  assert(Array.isArray(record.claim_assessments) && record.claim_assessments.length > 0, `${file}: claim_assessments must not be empty.`);
  const claimIds = record.claim_assessments.map((claim) => claim.claim_id);
  assertUnique(claimIds, `${file}: claim_assessments`);
  for (const claim of record.claim_assessments) {
    assert(classifications.has(claim.classification), `${file}: invalid classification for ${claim.claim_id}.`);
    assert(actions.has(claim.action), `${file}: invalid action for ${claim.claim_id}.`);
    assert(typeof claim.reason === "string" && claim.reason.length > 20, `${file}: claim reason is too weak for ${claim.claim_id}.`);
    assert(Array.isArray(claim.documents) && claim.documents.length > 0, `${file}: ${claim.claim_id} has no documents.`);
    for (const path of claim.documents) repositoryFile(path, `${file}: ${claim.claim_id}`);
  }
  for (const [key, value] of Object.entries(record.verification)) {
    assert(typeof value === "string" && value.length > 10, `${file}: verification '${key}' is too weak.`);
  }
}

console.log(
  `Drift review validation passed: ${files.length} PR record(s), ${groupedFiles.length} grouped record(s).`,
);
