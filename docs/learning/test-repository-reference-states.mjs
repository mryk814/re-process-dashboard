import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { compareObserved, validateRegistry } from "./check-repository-reference-states.mjs";

const learningRoot = resolve(dirname(fileURLToPath(import.meta.url)));
const registry = validateRegistry(
  JSON.parse(readFileSync(resolve(learningRoot, "repository-reference-states.json"), "utf8")),
);
const fixtureRoot = resolve(learningRoot, "fixtures", "repository-reference-states");
const matching = JSON.parse(readFileSync(resolve(fixtureRoot, "matching.json"), "utf8"));
const mismatch = JSON.parse(readFileSync(resolve(fixtureRoot, "mismatch.json"), "utf8"));

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const matched = compareObserved(registry, matching);
assert(matched.every((result) => result.matches), "Matching fixture must match every recorded state.");
assert(matched.every((result) => !result.blocking), "Matching fixture cannot block.");

const mismatched = compareObserved(registry, mismatch);
assert(
  mismatched.filter((result) => result.blocking).map((result) => result.key).join(",") === "issue:335",
  "Only the current-state mismatch may block.",
);
assert(
  compareObserved(registry, { ...matching, "issue:279": "open" })
    .find((result) => result.key === "issue:279").blocking === false,
  "Historical state drift must remain a dated record rather than a current assertion.",
);

console.log("Repository-reference fixtures passed: matching, current mismatch, and historical snapshot behavior.");
