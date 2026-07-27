import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const defaultLearningRoot = path.dirname(fileURLToPath(import.meta.url));
const allowedKinds = new Set([
  "architecture-flow",
  "contract-diagram",
  "data-flow",
  "state-diagram",
  "timeline",
]);
const idPattern = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

function posixPath(value) {
  return value.split(path.sep).join("/");
}

function normalizedText(value) {
  return value.replace(/\s+/g, " ").trim();
}

function safeRelativePath(value, owner, errors) {
  if (
    typeof value !== "string" ||
    value.length === 0 ||
    value.includes("\\") ||
    path.isAbsolute(value) ||
    value.split("/").some((part) => part === "" || part === "." || part === "..")
  ) {
    errors.push(`${owner}: invalid relative path ${JSON.stringify(value)}`);
    return null;
  }
  return value;
}

function filesUnder(root, predicate) {
  if (!fs.existsSync(root)) return [];
  const files = [];
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    if (entry.name === "_build" || entry.name === "node_modules") continue;
    const filename = path.join(root, entry.name);
    if (entry.isDirectory()) files.push(...filesUnder(filename, predicate));
    else if (entry.isFile() && predicate(filename)) files.push(filename);
  }
  return files;
}

export function markdownFigureReferences(source, chapterPath) {
  const references = [];
  const pattern = /!\[([^\]]*)\]\(([^)\s]+)(?:\s+["'][^"']*["'])?\)(?:\{([^}]*)\})?/g;
  const prose = source.replace(
    /^(?<fence>`{3,}|~{3,}).*?^\k<fence>\s*$/gms,
    "",
  );
  for (const match of prose.matchAll(pattern)) {
    const target = match[2];
    if (!/\.svg(?:[#?].*)?$/i.test(target)) continue;
    const cleanTarget = target.replace(/[#?].*$/, "");
    const resolved = path.posix.normalize(
      path.posix.join(path.posix.dirname(chapterPath), cleanTarget),
    );
    const attributes = match[3] ?? "";
    const longAlt =
      attributes.match(/\bfig-alt=(?:"([^"]*)"|'([^']*)')/)?.slice(1).find((value) => value !== undefined) ??
      null;
    references.push({
      chapter: chapterPath,
      source: resolved,
      alt: normalizedText(match[1]),
      longDescription: longAlt === null ? null : normalizedText(longAlt),
    });
  }
  return references;
}

function validateSvg(filename, sourcePath, errors) {
  const source = fs.readFileSync(filename, "utf8");
  const title = source.match(/<title(?:\s[^>]*)?>([\s\S]*?)<\/title>/i)?.[1] ?? "";
  const description = source.match(/<desc(?:\s[^>]*)?>([\s\S]*?)<\/desc>/i)?.[1] ?? "";
  if (!/<svg\b[^>]*\brole=["']img["']/i.test(source)) {
    errors.push(`${sourcePath}: SVG must declare role="img"`);
  }
  if (!normalizedText(title)) errors.push(`${sourcePath}: SVG title is empty or missing`);
  if (!normalizedText(description)) errors.push(`${sourcePath}: SVG desc is empty or missing`);
  if (/<image\b/i.test(source)) {
    errors.push(`${sourcePath}: SVG must not embed a raster image`);
  }
}

export function validateFigures({
  learningRoot = defaultLearningRoot,
  repositoryRoot = path.resolve(learningRoot, "..", ".."),
} = {}) {
  const errors = [];
  const registryPath = path.join(learningRoot, "figures", "registry.json");
  if (!fs.existsSync(registryPath)) {
    return { errors: ["figures/registry.json is missing"], figureCount: 0, referenceCount: 0 };
  }

  let registry;
  try {
    registry = JSON.parse(fs.readFileSync(registryPath, "utf8"));
  } catch (error) {
    return {
      errors: [`figures/registry.json is invalid JSON: ${error.message}`],
      figureCount: 0,
      referenceCount: 0,
    };
  }
  if (registry.schema_version !== 1) {
    errors.push("figures/registry.json: schema_version must be 1");
  }
  if (!Array.isArray(registry.figures)) {
    return {
      errors: [...errors, "figures/registry.json: figures must be an array"],
      figureCount: 0,
      referenceCount: 0,
    };
  }

  const figuresById = new Map();
  const figuresBySource = new Map();
  for (const [index, figure] of registry.figures.entries()) {
    const owner = `figures[${index}]`;
    if (!figure || typeof figure !== "object" || Array.isArray(figure)) {
      errors.push(`${owner}: figure metadata must be an object`);
      continue;
    }
    if (!idPattern.test(figure.id ?? "")) {
      errors.push(`${owner}: invalid id ${JSON.stringify(figure.id)}`);
    } else if (figuresById.has(figure.id)) {
      errors.push(`${owner}: duplicate id ${figure.id}`);
    } else {
      figuresById.set(figure.id, figure);
    }
    if (!allowedKinds.has(figure.kind)) {
      errors.push(`${owner}: unsupported kind ${JSON.stringify(figure.kind)}`);
    }
    const sourcePath = safeRelativePath(figure.source, owner, errors);
    const chapterPath = safeRelativePath(figure.chapter, owner, errors);
    if (sourcePath && sourcePath !== `figures/${figure.id}.svg`) {
      errors.push(`${owner}: source must be figures/${figure.id}.svg`);
    }
    if (sourcePath) {
      if (figuresBySource.has(sourcePath)) {
        errors.push(`${owner}: duplicate source ${sourcePath}`);
      } else {
        figuresBySource.set(sourcePath, figure);
      }
      const filename = path.join(learningRoot, ...sourcePath.split("/"));
      if (!fs.existsSync(filename)) errors.push(`${owner}: source does not exist: ${sourcePath}`);
      else validateSvg(filename, sourcePath, errors);
    }
    if (chapterPath) {
      const filename = path.join(learningRoot, ...chapterPath.split("/"));
      if (!fs.existsSync(filename)) errors.push(`${owner}: chapter does not exist: ${chapterPath}`);
    }
    if (!/^[0-9a-f]{40}$/.test(figure.verified_commit ?? "")) {
      errors.push(`${owner}: verified_commit must be a full SHA`);
    }
    const alt = typeof figure.alt === "string" ? normalizedText(figure.alt) : "";
    const longDescription =
      typeof figure.long_description === "string"
        ? normalizedText(figure.long_description)
        : "";
    if (!alt && !longDescription) {
      errors.push(`${owner}: alt or long_description must be non-empty`);
    }
    if (!Array.isArray(figure.drift_refs) || figure.drift_refs.length === 0) {
      errors.push(`${owner}: drift_refs must be a non-empty array`);
    } else {
      const seen = new Set();
      for (const driftRef of figure.drift_refs) {
        const relative = safeRelativePath(driftRef, `${owner}.drift_refs`, errors);
        if (!relative) continue;
        if (seen.has(relative)) errors.push(`${owner}.drift_refs: duplicate ${relative}`);
        seen.add(relative);
        const filename = path.join(repositoryRoot, ...relative.split("/"));
        if (!fs.existsSync(filename) || !fs.statSync(filename).isFile()) {
          errors.push(`${owner}.drift_refs: path does not exist: ${relative}`);
        }
      }
    }
  }

  const chapterFiles = filesUnder(
    learningRoot,
    (filename) => /\.(?:qmd|md)$/i.test(filename),
  );
  const references = chapterFiles.flatMap((filename) => {
    const chapterPath = posixPath(path.relative(learningRoot, filename));
    return markdownFigureReferences(fs.readFileSync(filename, "utf8"), chapterPath);
  });
  const referencesBySource = new Map();
  for (const reference of references) {
    const list = referencesBySource.get(reference.source) ?? [];
    list.push(reference);
    referencesBySource.set(reference.source, list);
    const figure = figuresBySource.get(reference.source);
    if (!figure) {
      errors.push(`${reference.chapter}: unregistered figure reference ${reference.source}`);
      continue;
    }
    if (figure.chapter !== reference.chapter) {
      errors.push(
        `${reference.chapter}: ${reference.source} is registered for ${figure.chapter}`,
      );
    }
    if (!reference.alt) {
      errors.push(`${reference.chapter}: ${reference.source} has empty Markdown alt`);
    } else if (
      typeof figure.alt === "string" &&
      normalizedText(figure.alt) !== reference.alt
    ) {
      errors.push(`${reference.chapter}: ${reference.source} alt differs from registry`);
    }
    if (
      reference.longDescription !== null &&
      normalizedText(figure.long_description ?? "") !== reference.longDescription
    ) {
      errors.push(
        `${reference.chapter}: ${reference.source} fig-alt differs from registry long_description`,
      );
    }
  }

  for (const [sourcePath, figure] of figuresBySource) {
    const matching = referencesBySource.get(sourcePath) ?? [];
    if (!matching.some((reference) => reference.chapter === figure.chapter)) {
      errors.push(`${sourcePath}: not referenced by registered chapter ${figure.chapter}`);
    }
  }

  const svgFiles = filesUnder(
    path.join(learningRoot, "figures"),
    (filename) => filename.toLowerCase().endsWith(".svg"),
  );
  for (const filename of svgFiles) {
    const sourcePath = posixPath(path.relative(learningRoot, filename));
    if (!figuresBySource.has(sourcePath)) {
      errors.push(`${sourcePath}: SVG source is not registered`);
    }
  }

  return {
    errors,
    figureCount: registry.figures.length,
    referenceCount: references.length,
  };
}

function main() {
  const rootIndex = process.argv.indexOf("--learning-root");
  const learningRoot =
    rootIndex >= 0
      ? path.resolve(process.argv[rootIndex + 1] ?? "")
      : defaultLearningRoot;
  if (rootIndex >= 0 && !process.argv[rootIndex + 1]) {
    throw new Error("--learning-root needs a path");
  }
  const result = validateFigures({ learningRoot });
  if (result.errors.length > 0) {
    console.error(`Figure validation failed with ${result.errors.length} error(s):`);
    result.errors.forEach((error) => console.error(`- ${error}`));
    process.exitCode = 1;
    return;
  }
  console.log(
    `Figure validation passed: ${result.figureCount} registered figures, ${result.referenceCount} chapter references.`,
  );
}

if (
  process.argv[1] &&
  path.resolve(process.argv[1]) === path.resolve(fileURLToPath(import.meta.url))
) {
  main();
}
