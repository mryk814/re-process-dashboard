import { readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import ts from "typescript";

const sourceRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../src");
const rootModuleAllowList = new Set(["main.tsx"]);
const allowedFeatureDependencies = new Set([
  "admin->candidates",
  "admin->quality",
  "lineage->candidates",
  "projects->candidates",
  "screening->candidates",
  "workbench->candidates",
]);

const posix = (value) => value.split(path.sep).join("/");

function sourceFiles(directory) {
  return readdirSync(directory).flatMap((name) => {
    const target = path.join(directory, name);
    if (statSync(target).isDirectory()) return sourceFiles(target);
    return /\.(?:ts|tsx)$/.test(name) ? [target] : [];
  });
}

export function importedSpecifiers(source, fileName = "source.ts") {
  const sourceFile = ts.createSourceFile(
    fileName,
    source,
    ts.ScriptTarget.Latest,
    true,
    fileName.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  );
  const specifiers = [];
  const visit = (node) => {
    if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) && node.moduleSpecifier && ts.isStringLiteralLike(node.moduleSpecifier)) {
      specifiers.push(node.moduleSpecifier.text);
    }
    if (ts.isCallExpression(node) && node.expression.kind === ts.SyntaxKind.ImportKeyword && node.arguments.length === 1 && ts.isStringLiteralLike(node.arguments[0])) {
      specifiers.push(node.arguments[0].text);
    }
    ts.forEachChild(node, visit);
  };
  visit(sourceFile);
  return specifiers;
}

function resolvedSourcePath(importer, specifier) {
  if (!specifier.startsWith(".")) return null;
  return posix(path.relative(sourceRoot, path.resolve(sourceRoot, path.dirname(importer), specifier)))
    .replace(/\.(?:ts|tsx)$/, "")
    .replace(/\/index$/, "");
}

export function validateImport(importer, target) {
  const errors = [];
  const importerPath = posix(importer).replace(/\.(?:ts|tsx)$/, "");
  const targetPath = posix(target).replace(/\.(?:ts|tsx)$/, "").replace(/\/index$/, "");
  const isStyleEntryImport = importerPath === "app/styles" && targetPath.endsWith(".css");

  if (importerPath.startsWith("shared/") && !targetPath.startsWith("shared/") && !targetPath.startsWith("generated/")) {
    errors.push("shared may only depend on shared or generated modules");
  }
  if (importerPath.startsWith("features/") && (targetPath.startsWith("app/") || !targetPath.includes("/"))) {
    errors.push("features must not depend on app or root modules");
  }
  const importerFeature = importerPath.match(/^features\/([^/]+)/)?.[1];
  const targetFeature = targetPath.match(/^features\/([^/]+)/)?.[1];
  if (importerFeature && targetFeature && importerFeature !== targetFeature && !allowedFeatureDependencies.has(`${importerFeature}->${targetFeature}`)) {
    errors.push(`feature dependency ${importerFeature}->${targetFeature} is not allowed`);
  }
  if (targetFeature && importerFeature !== targetFeature && targetPath !== `features/${targetFeature}` && !isStyleEntryImport && errors.length === 0) {
    errors.push(`${targetFeature} consumers must use its public index`);
  }
  return errors;
}

export function featureCycle(edges) {
  const visited = new Set();
  const active = [];
  const visit = (feature) => {
    const cycleAt = active.indexOf(feature);
    if (cycleAt >= 0) return [...active.slice(cycleAt), feature];
    if (visited.has(feature)) return null;
    active.push(feature);
    for (const target of edges.get(feature) ?? []) {
      const cycle = visit(target);
      if (cycle) return cycle;
    }
    active.pop();
    visited.add(feature);
    return null;
  };
  for (const feature of edges.keys()) {
    const cycle = visit(feature);
    if (cycle) return cycle;
  }
  return null;
}

export function checkSourceTree() {
  const errors = [];
  const featureEdges = new Map();
  for (const file of sourceFiles(sourceRoot)) {
    const importer = posix(path.relative(sourceRoot, file));
    if (!importer.includes("/") && /\.(?:ts|tsx)$/.test(importer) && !rootModuleAllowList.has(importer)) {
      errors.push(`${importer}: new root modules are not allowed; place the module under app, features, or shared`);
    }
    for (const specifier of importedSpecifiers(readFileSync(file, "utf8"), importer)) {
      const target = resolvedSourcePath(importer, specifier);
      if (!target) continue;
      for (const message of validateImport(importer, target)) errors.push(`${importer} -> ${specifier}: ${message}`);
      const importerFeature = posix(importer).match(/^features\/([^/]+)/)?.[1];
      const targetFeature = target.match(/^features\/([^/]+)/)?.[1];
      if (importerFeature && targetFeature && importerFeature !== targetFeature) {
        if (!featureEdges.has(importerFeature)) featureEdges.set(importerFeature, new Set());
        featureEdges.get(importerFeature).add(targetFeature);
      }
    }
  }
  const cycle = featureCycle(featureEdges);
  if (cycle) errors.push(`feature dependency cycle: ${cycle.join(" -> ")}`);
  return errors;
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) {
  const errors = checkSourceTree();
  if (errors.length > 0) {
    process.stderr.write(`Frontend import boundary violations:\n${errors.map((error) => `- ${error}`).join("\n")}\n`);
    process.exit(1);
  }
  process.stdout.write("Frontend import boundaries: OK\n");
}
