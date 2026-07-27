import { readFileSync, statSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { dirname, isAbsolute, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const learningRoot = resolve(dirname(fileURLToPath(import.meta.url)));
const repositoryRoot = resolve(learningRoot, "..", "..");
const registryPath = resolve(learningRoot, "repository-reference-states.json");

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function repositoryFile(path) {
  const absolute = resolve(repositoryRoot, path);
  const repositoryRelative = relative(repositoryRoot, absolute);
  assert(
    repositoryRelative !== "" &&
      repositoryRelative !== ".." &&
      !repositoryRelative.startsWith(`..${sep}`) &&
      !isAbsolute(repositoryRelative),
    `Document escapes repository root: ${path}`,
  );
  assert(statSync(absolute, { throwIfNoEntry: false })?.isFile(), `Document does not exist: ${path}`);
  return absolute;
}

export function referenceKey(reference) {
  return `${reference.kind}:${reference.number}`;
}

export function validateRegistry(registry) {
  assert(registry.schema_version === "learning-repository-reference-states/v1", "Unsupported registry schema.");
  assert(/^[^/]+\/[^/]+$/.test(registry.repository), "Repository must be owner/name.");
  assert(!Number.isNaN(Date.parse(registry.verified_at)), "verified_at must be an ISO timestamp.");
  const keys = registry.references.map(referenceKey);
  assert(new Set(keys).size === keys.length, "Repository references must be unique.");
  for (const reference of registry.references) {
    assert(["issue", "pull_request"].includes(reference.kind), `${referenceKey(reference)}: invalid kind.`);
    assert(Number.isInteger(reference.number) && reference.number > 0, `${referenceKey(reference)}: invalid number.`);
    assert(["current", "historical"].includes(reference.mode), `${referenceKey(reference)}: invalid mode.`);
    assert(["open", "closed", "merged"].includes(reference.state), `${referenceKey(reference)}: invalid state.`);
    assert(reference.kind === "pull_request" || reference.state !== "merged", `${referenceKey(reference)}: issue cannot be merged.`);
    assert(Array.isArray(reference.documents) && reference.documents.length > 0, `${referenceKey(reference)}: no documents.`);
    for (const path of reference.documents) {
      const text = readFileSync(repositoryFile(path), "utf8");
      assert(text.includes(reference.url), `${referenceKey(reference)}: URL is absent from ${path}.`);
    }
  }
  return registry;
}

export function compareObserved(registry, observed) {
  return registry.references.map((reference) => {
    const key = referenceKey(reference);
    const actual = observed[key];
    return {
      key,
      mode: reference.mode,
      expected: reference.state,
      actual: actual ?? null,
      matches: actual === reference.state,
      blocking: reference.mode === "current" && actual !== reference.state,
    };
  });
}

async function fetchObserved(registry) {
  const headers = {
    Accept: "application/vnd.github+json",
    "User-Agent": "material-decision-workbench-learning-check",
  };
  if (process.env.GITHUB_TOKEN) headers.Authorization = `Bearer ${process.env.GITHUB_TOKEN}`;
  const observed = {};
  for (const reference of registry.references) {
    const endpoint = reference.kind === "issue" ? "issues" : "pulls";
    let payload;
    if (!process.env.GITHUB_TOKEN) {
      payload = JSON.parse(
        execFileSync(
          "gh",
          ["api", `repos/${registry.repository}/${endpoint}/${reference.number}`],
          { cwd: repositoryRoot, encoding: "utf8" },
        ),
      );
    } else {
      const response = await fetch(
        `https://api.github.com/repos/${registry.repository}/${endpoint}/${reference.number}`,
        { headers },
      );
      if (!response.ok) throw new Error(`GitHub ${referenceKey(reference)} returned ${response.status}.`);
      payload = await response.json();
    }
    observed[referenceKey(reference)] =
      reference.kind === "pull_request" && payload.merged_at ? "merged" : payload.state;
  }
  return observed;
}

export async function main(args = process.argv.slice(2)) {
  const registry = validateRegistry(JSON.parse(readFileSync(registryPath, "utf8")));
  if (!args.includes("--online")) {
    console.log(
      `Offline repository-reference check passed. Last observed ${registry.verified_at}; current references were not confirmed live.`,
    );
    return 0;
  }
  const observed = await fetchObserved(registry);
  const comparisons = compareObserved(registry, observed);
  for (const result of comparisons) {
    console.log(`${result.key}: recorded=${result.expected}, live=${result.actual}, mode=${result.mode}`);
  }
  const mismatches = comparisons.filter((result) => result.blocking);
  if (mismatches.length > 0) {
    console.error(`Current repository-reference mismatch: ${mismatches.map((result) => result.key).join(", ")}`);
    return 2;
  }
  console.log(`Online repository-reference check passed: ${comparisons.length} references observed.`);
  return 0;
}

if (process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url))) {
  process.exitCode = await main();
}
