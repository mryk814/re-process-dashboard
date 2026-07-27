import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const defaultLearningRoot = path.dirname(fileURLToPath(import.meta.url));
const requiredProtectedPaths = new Set([
  "data/source",
  "data/workbench.db",
  "models/active-packages.json",
  "models/active-transforms.json",
  "models/packages",
  "apps/web/src/generated",
]);
const allowedModes = new Set(["guided", "executable"]);
const allowedCategories = new Set([
  "contract",
  "data",
  "frontend",
  "math",
  "persistence",
  "security",
]);
const allowedRequirements = new Set(["node", "python"]);
const idPattern = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const manifestKeys = new Set([
  "$schema",
  "schema_version",
  "allowed_write_root",
  "protected_paths",
  "labs",
]);
const labKeys = new Set([
  "lab_id",
  "document",
  "mode",
  "category",
  "title",
  "verified_commit",
  "expected_minutes",
  "requires",
  "fixtures",
  "commands",
  "writes",
  "must_not_write",
  "network",
  "secrets",
  "timeout_seconds",
  "expected_outcomes",
]);
const commandKeys = new Set(["setup", "run", "verify", "reset"]);

function normalized(value) {
  return value.replace(/\s+/g, " ").trim();
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

function overlaps(left, right) {
  return left === right || left.startsWith(`${right}/`) || right.startsWith(`${left}/`);
}

function duplicates(values) {
  const seen = new Set();
  return values.filter((value) => {
    if (seen.has(value)) return true;
    seen.add(value);
    return false;
  });
}

function frontMatter(source, filename, errors) {
  const match = source.match(/^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/);
  if (!match) {
    errors.push(`${filename}: missing YAML front matter`);
    return null;
  }
  const lines = match[1].split(/\r?\n/);
  const scalar = (key) => {
    const line = lines.find((candidate) => candidate.startsWith(`${key}:`));
    if (!line) return null;
    return line
      .slice(key.length + 1)
      .trim()
      .replace(/^["']|["']$/g, "");
  };
  return {
    chapterId: scalar("chapter_id"),
    estimatedMinutes: Number(scalar("estimated_minutes")),
    verifiedCommit: scalar("verified_commit"),
  };
}

function validateCommand(command, lab, field, repositoryRoot, protectedPaths, errors) {
  if (command === null) return;
  if (typeof command !== "string" || normalized(command) === "") {
    errors.push(`${lab.lab_id}.commands.${field}: must be a non-empty string or null`);
    return;
  }
  const normalizedCommand = command.replaceAll("\\", "/").toLowerCase();
  for (const protectedPath of protectedPaths) {
    if (normalizedCommand.includes(protectedPath.toLowerCase())) {
      errors.push(
        `${lab.lab_id}.commands.${field}: command names protected path ${protectedPath}`,
      );
    }
  }
  if (/(?:^|\s)(?:curl|wget|invoke-webrequest|irm|iwr)(?:\s|$)/i.test(command) || /https?:\/\//i.test(command)) {
    errors.push(`${lab.lab_id}.commands.${field}: network command is forbidden`);
  }
  const nodeScript = command.match(/^node\s+([^\s]+\.mjs)(?:\s|$)/);
  if (lab.mode === "executable" && !nodeScript) {
    errors.push(
      `${lab.lab_id}.commands.${field}: executable command must use a reviewed Node Lab script`,
    );
  }
  if (nodeScript) {
    const relative = nodeScript[1].replaceAll("\\", "/");
    if (!isRepositoryPath(relative)) {
      errors.push(`${lab.lab_id}.commands.${field}: invalid script path ${relative}`);
    } else {
      if (!relative.startsWith("docs/learning/labs/scripts/")) {
        errors.push(
          `${lab.lab_id}.commands.${field}: executable script must be under docs/learning/labs/scripts`,
        );
      }
      const filename = path.join(repositoryRoot, ...relative.split("/"));
      if (!fs.existsSync(filename)) {
        errors.push(`${lab.lab_id}.commands.${field}: script does not exist: ${relative}`);
      } else {
        const script = fs.readFileSync(filename, "utf8");
        if (
          /(?:node:)?https?\b|(?:^|[^\w])fetch\s*\(|node:child_process|(?:^|[^\w])spawn\s*\(|(?:^|[^\w])exec(?:File|Sync)?\s*\(/m.test(
            script,
          )
        ) {
          errors.push(`${lab.lab_id}.commands.${field}: Lab script contains network or child-process access`);
        }
      }
    }
  }
  if (
    /\b(?:api:generate|model:activate|models:build|data:build)\b/i.test(command) ||
    /\bnpm(?:\.cmd)?\s+run\s+dev\b/i.test(command) ||
    /\buvicorn\b/i.test(command)
  ) {
    errors.push(`${lab.lab_id}.commands.${field}: production-mutating command is forbidden`);
  }
}

export function validateLabs({
  learningRoot = defaultLearningRoot,
  repositoryRoot = path.resolve(learningRoot, "..", ".."),
} = {}) {
  const errors = [];
  const labsRoot = path.join(learningRoot, "labs");
  const schemaPath = path.join(labsRoot, "lab.schema.json");
  const manifestPath = path.join(labsRoot, "manifest.json");
  if (!fs.existsSync(schemaPath)) {
    return { errors: ["labs/lab.schema.json is missing"], labCount: 0 };
  }
  if (!fs.existsSync(manifestPath)) {
    return { errors: ["labs/manifest.json is missing"], labCount: 0 };
  }

  let schema;
  let manifest;
  try {
    schema = JSON.parse(fs.readFileSync(schemaPath, "utf8"));
  } catch (error) {
    errors.push(`labs/lab.schema.json is invalid JSON: ${error.message}`);
  }
  try {
    manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  } catch (error) {
    return { errors: [...errors, `labs/manifest.json is invalid JSON: ${error.message}`], labCount: 0 };
  }
  if (!manifest || typeof manifest !== "object" || Array.isArray(manifest)) {
    return { errors: [...errors, "labs/manifest.json: root must be an object"], labCount: 0 };
  }
  for (const key of Object.keys(manifest)) {
    if (!manifestKeys.has(key)) errors.push(`labs/manifest.json: unknown property ${key}`);
  }
  if (schema?.properties?.schema_version?.const !== "learning-labs/v1") {
    errors.push("labs/lab.schema.json: schema_version contract is missing");
  }
  if (manifest.$schema !== "./lab.schema.json") {
    errors.push("labs/manifest.json: $schema must be ./lab.schema.json");
  }
  if (manifest.schema_version !== "learning-labs/v1") {
    errors.push("labs/manifest.json: schema_version must be learning-labs/v1");
  }
  if (manifest.allowed_write_root !== "artifacts/learning-labs") {
    errors.push("labs/manifest.json: allowed_write_root must be artifacts/learning-labs");
  }
  if (!Array.isArray(manifest.protected_paths)) {
    errors.push("labs/manifest.json: protected_paths must be an array");
  }
  const protectedPaths = Array.isArray(manifest.protected_paths)
    ? manifest.protected_paths
    : [];
  for (const protectedPath of protectedPaths) {
    if (!isRepositoryPath(protectedPath)) {
      errors.push(`labs/manifest.json: invalid protected path ${JSON.stringify(protectedPath)}`);
    }
  }
  for (const required of requiredProtectedPaths) {
    if (!protectedPaths.includes(required)) {
      errors.push(`labs/manifest.json: protected_paths is missing ${required}`);
    }
  }
  for (const duplicate of duplicates(protectedPaths)) {
    errors.push(`labs/manifest.json: duplicate protected path ${duplicate}`);
  }
  if (!Array.isArray(manifest.labs)) {
    return { errors: [...errors, "labs/manifest.json: labs must be an array"], labCount: 0 };
  }

  const labsById = new Map();
  const labsByDocument = new Map();
  for (const [index, lab] of manifest.labs.entries()) {
    const owner = `labs[${index}]`;
    if (!lab || typeof lab !== "object" || Array.isArray(lab)) {
      errors.push(`${owner}: lab must be an object`);
      continue;
    }
    for (const key of Object.keys(lab)) {
      if (!labKeys.has(key)) errors.push(`${owner}: unknown property ${key}`);
    }
    if (!idPattern.test(lab.lab_id ?? "")) {
      errors.push(`${owner}: invalid lab_id ${JSON.stringify(lab.lab_id)}`);
    } else if (labsById.has(lab.lab_id)) {
      errors.push(`${owner}: duplicate lab_id ${lab.lab_id}`);
    } else {
      labsById.set(lab.lab_id, lab);
    }
    if (!allowedModes.has(lab.mode)) errors.push(`${lab.lab_id}: invalid mode ${lab.mode}`);
    if (!allowedCategories.has(lab.category)) {
      errors.push(`${lab.lab_id}: invalid category ${lab.category}`);
    }
    if (typeof lab.title !== "string" || normalized(lab.title) === "") {
      errors.push(`${lab.lab_id}: title must be non-empty`);
    }
    if (!/^[0-9a-f]{40}$/.test(lab.verified_commit ?? "")) {
      errors.push(`${lab.lab_id}: verified_commit must be a full SHA`);
    }
    if (
      !Number.isInteger(lab.expected_minutes) ||
      lab.expected_minutes < 1 ||
      lab.expected_minutes > 180
    ) {
      errors.push(`${lab.lab_id}: expected_minutes must be between 1 and 180`);
    }
    if (!Number.isInteger(lab.timeout_seconds) || lab.timeout_seconds < 1 || lab.timeout_seconds > 600) {
      errors.push(`${lab.lab_id}: timeout_seconds must be between 1 and 600`);
    }
    if (!Array.isArray(lab.requires)) {
      errors.push(`${lab.lab_id}: requires must be an array`);
    } else {
      for (const requirement of lab.requires) {
        if (!allowedRequirements.has(requirement)) {
          errors.push(`${lab.lab_id}: unsupported requirement ${requirement}`);
        }
      }
      for (const duplicate of duplicates(lab.requires)) {
        errors.push(`${lab.lab_id}: duplicate requirement ${duplicate}`);
      }
    }
    if (!isRepositoryPath(lab.document) || !lab.document.startsWith("labs/")) {
      errors.push(`${lab.lab_id}: invalid document ${JSON.stringify(lab.document)}`);
    } else {
      if (labsByDocument.has(lab.document)) {
        errors.push(`${lab.lab_id}: duplicate document ${lab.document}`);
      }
      labsByDocument.set(lab.document, lab);
      const filename = path.join(learningRoot, ...lab.document.split("/"));
      if (!fs.existsSync(filename)) {
        errors.push(`${lab.lab_id}: document does not exist: ${lab.document}`);
      } else {
        const metadata = frontMatter(fs.readFileSync(filename, "utf8"), lab.document, errors);
        if (metadata && metadata.chapterId !== lab.lab_id) {
          errors.push(`${lab.lab_id}: document chapter_id is ${metadata.chapterId}`);
        }
        if (metadata && metadata.estimatedMinutes !== lab.expected_minutes) {
          errors.push(
            `${lab.lab_id}: document estimated_minutes is ${metadata.estimatedMinutes}, expected ${lab.expected_minutes}`,
          );
        }
        if (metadata && metadata.verifiedCommit !== lab.verified_commit) {
          errors.push(`${lab.lab_id}: document verified_commit differs from manifest`);
        }
      }
    }
    if (!Array.isArray(lab.fixtures)) {
      errors.push(`${lab.lab_id}: fixtures must be an array`);
    } else {
      for (const duplicate of duplicates(lab.fixtures)) {
        errors.push(`${lab.lab_id}: duplicate fixture ${duplicate}`);
      }
      for (const fixture of lab.fixtures) {
        if (!isRepositoryPath(fixture)) {
          errors.push(`${lab.lab_id}: invalid fixture path ${JSON.stringify(fixture)}`);
        } else if (!fs.existsSync(path.join(repositoryRoot, ...fixture.split("/")))) {
          errors.push(`${lab.lab_id}: fixture does not exist: ${fixture}`);
        } else if (
          lab.mode === "executable" &&
          !fixture.startsWith("docs/learning/labs/fixtures/")
        ) {
          errors.push(`${lab.lab_id}: executable fixture must be under docs/learning/labs/fixtures`);
        }
      }
    }
    if (!lab.commands || typeof lab.commands !== "object" || Array.isArray(lab.commands)) {
      errors.push(`${lab.lab_id}: commands must be an object`);
    } else {
      for (const key of Object.keys(lab.commands)) {
        if (!commandKeys.has(key)) errors.push(`${lab.lab_id}.commands: unknown property ${key}`);
      }
      for (const field of ["setup", "run", "verify", "reset"]) {
        if (!(field in lab.commands)) errors.push(`${lab.lab_id}.commands: missing ${field}`);
        else validateCommand(lab.commands[field], lab, field, repositoryRoot, protectedPaths, errors);
      }
      if (
        lab.mode === "executable" &&
        ["setup", "run", "verify", "reset"].some(
          (field) => typeof lab.commands[field] !== "string" || normalized(lab.commands[field]) === "",
        )
      ) {
        errors.push(`${lab.lab_id}: executable lab needs setup, run, verify, and reset commands`);
      }
    }
    if (!Array.isArray(lab.writes)) {
      errors.push(`${lab.lab_id}: writes must be an array`);
    } else {
      for (const duplicate of duplicates(lab.writes)) {
        errors.push(`${lab.lab_id}: duplicate write path ${duplicate}`);
      }
      const expectedWriteRoot = `${manifest.allowed_write_root}/${lab.lab_id}`;
      if (lab.mode === "guided" && lab.writes.length !== 0) {
        errors.push(`${lab.lab_id}: guided lab must not declare writes`);
      }
      if (lab.mode === "executable" && !lab.writes.includes(expectedWriteRoot)) {
        errors.push(`${lab.lab_id}: executable lab must write to ${expectedWriteRoot}`);
      }
      for (const writePath of lab.writes) {
        if (
          !isRepositoryPath(writePath) ||
          (writePath !== expectedWriteRoot && !writePath.startsWith(`${expectedWriteRoot}/`))
        ) {
          errors.push(`${lab.lab_id}: write path is outside lab sandbox: ${writePath}`);
        }
        for (const protectedPath of protectedPaths) {
          if (overlaps(writePath, protectedPath)) {
            errors.push(`${lab.lab_id}: write path overlaps protected path ${protectedPath}`);
          }
        }
      }
    }
    if (!Array.isArray(lab.must_not_write)) {
      errors.push(`${lab.lab_id}: must_not_write must be an array`);
    } else {
      for (const duplicate of duplicates(lab.must_not_write)) {
        errors.push(`${lab.lab_id}: duplicate must_not_write path ${duplicate}`);
      }
      for (const protectedPath of protectedPaths) {
        if (!lab.must_not_write.includes(protectedPath)) {
          errors.push(`${lab.lab_id}: must_not_write is missing ${protectedPath}`);
        }
      }
    }
    if (lab.network !== "forbidden") errors.push(`${lab.lab_id}: network must be forbidden`);
    if (lab.secrets !== "forbidden") errors.push(`${lab.lab_id}: secrets must be forbidden`);
    if (
      !Array.isArray(lab.expected_outcomes) ||
      lab.expected_outcomes.length === 0 ||
      lab.expected_outcomes.some((item) => typeof item !== "string" || normalized(item) === "")
    ) {
      errors.push(`${lab.lab_id}: expected_outcomes must contain non-empty strings`);
    }
  }

  const labDocuments = fs
    .readdirSync(labsRoot, { withFileTypes: true })
    .filter((entry) => entry.isFile() && entry.name.endsWith(".qmd"))
    .map((entry) => `labs/${entry.name}`);
  for (const document of labDocuments) {
    if (!labsByDocument.has(document)) errors.push(`${document}: Lab document is not registered`);
  }
  for (const document of labsByDocument.keys()) {
    if (!labDocuments.includes(document)) errors.push(`${document}: registered document is not a top-level Lab QMD`);
  }

  return { errors, labCount: manifest.labs.length };
}

function main() {
  const rootIndex = process.argv.indexOf("--learning-root");
  if (rootIndex >= 0 && !process.argv[rootIndex + 1]) {
    throw new Error("--learning-root needs a path");
  }
  const learningRoot =
    rootIndex >= 0 ? path.resolve(process.argv[rootIndex + 1]) : defaultLearningRoot;
  const result = validateLabs({ learningRoot });
  if (result.errors.length > 0) {
    console.error(`Lab validation failed with ${result.errors.length} error(s):`);
    result.errors.forEach((error) => console.error(`- ${error}`));
    process.exitCode = 1;
    return;
  }
  console.log(`Lab validation passed: ${result.labCount} registered Labs.`);
}

if (
  process.argv[1] &&
  path.resolve(process.argv[1]) === path.resolve(fileURLToPath(import.meta.url))
) {
  main();
}
