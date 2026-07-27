import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const defaultLearningRoot = path.dirname(fileURLToPath(import.meta.url));
const profileNames = ["_quarto-reader.yml", "_quarto-site.yml"];

function fail(message) {
  throw new Error(message);
}

function unquote(value) {
  const trimmed = value.trim();
  if (
    trimmed.length >= 2 &&
    ((trimmed.startsWith('"') && trimmed.endsWith('"')) ||
      (trimmed.startsWith("'") && trimmed.endsWith("'")))
  ) {
    return trimmed.slice(1, -1);
  }
  return trimmed;
}

export function parseChapterOrder(source, profileName = "Quarto profile") {
  const chapters = [];
  for (const line of source.split(/\r?\n/)) {
    const match = line.match(/^\s*-\s+(.+?)\s*$/);
    if (!match) continue;
    const chapter = unquote(match[1]);
    if (!/\.(?:qmd|md)$/i.test(chapter)) continue;
    if (
      chapter.includes("\\") ||
      path.isAbsolute(chapter) ||
      chapter.split("/").some((part) => part === "" || part === "." || part === "..")
    ) {
      fail(`${profileName}: invalid chapter path ${chapter}`);
    }
    chapters.push(chapter);
  }
  return chapters;
}

export function parseChapterFrontMatter(source, chapterPath) {
  const match = source.match(/^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/);
  if (!match) return null;
  const lines = match[1].split(/\r?\n/);
  const scalar = (key) => {
    const line = lines.find((candidate) => candidate.startsWith(`${key}:`));
    return line ? unquote(line.slice(key.length + 1)) : null;
  };
  const list = (key) => {
    const inline = lines.find((candidate) => candidate.startsWith(`${key}:`));
    if (!inline) return null;
    const inlineValue = inline.slice(key.length + 1).trim();
    if (inlineValue === "[]") return [];
    if (inlineValue !== "") {
      fail(`${chapterPath}: ${key} must be a YAML list`);
    }
    const start = lines.indexOf(inline);
    const values = [];
    for (let index = start + 1; index < lines.length; index += 1) {
      const line = lines[index];
      if (/^[A-Za-z0-9_-]+:/.test(line)) break;
      const item = line.match(/^\s{2,}-\s+(.+?)\s*$/);
      if (item) values.push(unquote(item[1]));
    }
    return values;
  };

  const chapterId = scalar("chapter_id");
  if (!chapterId) return null;
  const prerequisiteConcepts = list("prerequisite_concepts");
  const introducedConcepts = list("introduced_concepts");
  const bridgedConcepts = list("bridged_concepts") ?? [];
  if (prerequisiteConcepts === null) {
    fail(`${chapterPath}: missing prerequisite_concepts`);
  }
  if (introducedConcepts === null) {
    fail(`${chapterPath}: missing introduced_concepts`);
  }

  return {
    id: chapterId,
    path: chapterPath,
    prerequisiteConcepts,
    introducedConcepts,
    bridgedConcepts,
  };
}

function unique(values) {
  return new Set(values).size === values.length;
}

function validateBridgeContract(chapter) {
  const violations = [];
  if (!unique(chapter.bridgedConcepts)) {
    violations.push(`${chapter.path}: bridged_concepts contains a duplicate`);
  }
  for (const concept of chapter.bridgedConcepts) {
    if (!chapter.prerequisiteConcepts.includes(concept)) {
      violations.push(
        `${chapter.path}: bridged concept "${concept}" must also be listed in prerequisite_concepts`,
      );
    }
    if (chapter.introducedConcepts.includes(concept)) {
      violations.push(
        `${chapter.path}: bridged concept "${concept}" cannot also be listed in introduced_concepts`,
      );
    }
  }
  return violations;
}

export function validateProfileOrder(profileName, orderedChapters) {
  const violations = [];
  const firstIntroduction = new Map();
  orderedChapters.forEach((chapter, index) => {
    for (const concept of chapter.introducedConcepts) {
      if (!firstIntroduction.has(concept)) {
        firstIntroduction.set(concept, { chapter, position: index + 1 });
      }
    }
  });

  const learned = new Set();
  orderedChapters.forEach((chapter, index) => {
    violations.push(...validateBridgeContract(chapter).map((item) => `${profileName}: ${item}`));
    const localBridge = new Set(chapter.bridgedConcepts);
    for (const concept of chapter.prerequisiteConcepts) {
      if (learned.has(concept) || localBridge.has(concept)) continue;
      const introduction = firstIntroduction.get(concept);
      const detail = introduction
        ? `first introduced by ${introduction.chapter.path} at position ${introduction.position}`
        : "no introducing chapter exists in this profile";
      violations.push(
        `${profileName}: ${chapter.path} (${chapter.id}) at position ${
          index + 1
        } requires "${concept}" before it is learned; ${detail}`,
      );
    }
    for (const concept of chapter.introducedConcepts) learned.add(concept);
  });

  return violations;
}

export function validateLearningRoot(learningRoot = defaultLearningRoot) {
  const violations = [];
  const profiles = [];
  for (const profileName of profileNames) {
    const profilePath = path.join(learningRoot, profileName);
    if (!fs.existsSync(profilePath)) fail(`${profileName} is missing`);
    const chapterPaths = parseChapterOrder(
      fs.readFileSync(profilePath, "utf8"),
      profileName,
    );
    const orderedChapters = [];
    for (const chapterPath of chapterPaths) {
      const filename = path.resolve(learningRoot, ...chapterPath.split("/"));
      if (!filename.startsWith(`${path.resolve(learningRoot)}${path.sep}`)) {
        fail(`${profileName}: chapter path escapes learning root: ${chapterPath}`);
      }
      if (!fs.existsSync(filename)) {
        fail(`${profileName}: missing chapter ${chapterPath}`);
      }
      const chapter = parseChapterFrontMatter(
        fs.readFileSync(filename, "utf8"),
        chapterPath,
      );
      // Indexes, generated reference pages, and maintenance guides are not
      // learning units unless they opt in with chapter_id.
      if (chapter) orderedChapters.push(chapter);
    }
    profiles.push({ name: profileName, chapterCount: orderedChapters.length });
    violations.push(...validateProfileOrder(profileName, orderedChapters));
  }
  return { profiles, violations };
}

function main() {
  const rootArgument = process.argv.indexOf("--learning-root");
  const learningRoot =
    rootArgument >= 0
      ? path.resolve(process.argv[rootArgument + 1] ?? fail("--learning-root needs a path"))
      : defaultLearningRoot;
  const result = validateLearningRoot(learningRoot);
  if (result.violations.length > 0) {
    console.error(
      `Concept order validation failed with ${result.violations.length} violation(s):`,
    );
    for (const violation of result.violations) console.error(`- ${violation}`);
    process.exitCode = 1;
    return;
  }
  console.log(
    `Concept order validation passed: ${result.profiles
      .map((profile) => `${profile.name} ${profile.chapterCount} learning units`)
      .join(", ")}.`,
  );
}

if (
  process.argv[1] &&
  path.resolve(process.argv[1]) === path.resolve(fileURLToPath(import.meta.url))
) {
  main();
}
