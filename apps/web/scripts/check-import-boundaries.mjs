import { readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import ts from "typescript";

const sourceRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../src");
const rootModuleAllowList = new Set(["App.tsx", "main.tsx"]);

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

  if (importerPath.startsWith("shared/") && !targetPath.startsWith("shared/") && !targetPath.startsWith("generated/")) {
    errors.push("shared may only depend on shared or generated modules");
  }
  if (importerPath.startsWith("features/") && (targetPath.startsWith("app/") || !targetPath.includes("/"))) {
    errors.push("features must not depend on app or root modules");
  }
  if (importerPath.startsWith("features/candidates/") && targetPath.startsWith("features/") && !targetPath.startsWith("features/candidates/")) {
    errors.push("candidates must not depend on another feature");
  }
  if (!importerPath.startsWith("features/candidates/") && targetPath.startsWith("features/candidates/") && targetPath !== "features/candidates") {
    errors.push("candidate feature consumers must use its public index");
  }
  return errors;
}

export function checkSourceTree() {
  const errors = [];
  for (const file of sourceFiles(sourceRoot)) {
    const importer = posix(path.relative(sourceRoot, file));
    if (!importer.includes("/") && /\.(?:ts|tsx)$/.test(importer) && !rootModuleAllowList.has(importer)) {
      errors.push(`${importer}: new root modules are not allowed; place the module under app, features, or shared`);
    }
    for (const specifier of importedSpecifiers(readFileSync(file, "utf8"), importer)) {
      const target = resolvedSourcePath(importer, specifier);
      if (!target) continue;
      for (const message of validateImport(importer, target)) errors.push(`${importer} -> ${specifier}: ${message}`);
    }
  }
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
