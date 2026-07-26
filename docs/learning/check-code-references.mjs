import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const ts = require("typescript");
const scriptPath = fileURLToPath(import.meta.url);
const learningRoot = path.dirname(scriptPath);
const repositoryRoot = path.resolve(learningRoot, "..", "..");
const allowedRoles = new Set([
  "contract",
  "domain",
  "application",
  "persistence",
  "api",
  "generated",
  "frontend",
  "test",
  "fixture",
  "build",
  "docs",
]);

function unquote(value) {
  const trimmed = value.trim();
  if (
    (trimmed.startsWith("\"") && trimmed.endsWith("\"")) ||
    (trimmed.startsWith("'") && trimmed.endsWith("'"))
  ) {
    return trimmed.slice(1, -1);
  }
  return trimmed;
}

export function parseDocumentMetadata(source, filename = "<memory>") {
  const frontMatter = source.match(/^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/);
  if (!frontMatter) {
    return null;
  }
  const lines = frontMatter[1].split(/\r?\n/);
  const commitLine = lines.find((line) => /^verified_commit:\s*/.test(line));
  const commit = commitLine
    ? unquote(commitLine.replace(/^verified_commit:\s*/, ""))
    : null;
  const start = lines.findIndex((line) => line === "code_references:");
  if (start < 0) {
    return commit ? { filename, source, commit, references: [] } : null;
  }

  const references = [];
  let current = null;
  let readingSymbols = false;
  for (let index = start + 1; index < lines.length; index += 1) {
    const line = lines[index];
    if (/^[^\s]/.test(line)) {
      break;
    }
    const pathMatch = line.match(/^  - path:\s*(.+)$/);
    if (pathMatch) {
      current = {
        id: null,
        path: unquote(pathMatch[1]),
        symbols: [],
        role: null,
      };
      references.push(current);
      readingSymbols = false;
      continue;
    }
    if (/^  -\s+["']/.test(line)) {
      throw new Error(`${filename}: legacy string code_reference is not allowed`);
    }
    if (!current) {
      continue;
    }
    const roleMatch = line.match(/^    role:\s*(.+)$/);
    if (roleMatch) {
      current.role = unquote(roleMatch[1]);
      readingSymbols = false;
      continue;
    }
    const idMatch = line.match(/^    id:\s*(.+)$/);
    if (idMatch) {
      current.id = unquote(idMatch[1]);
      readingSymbols = false;
      continue;
    }
    if (/^    symbols:\s*$/.test(line)) {
      readingSymbols = true;
      continue;
    }
    const symbolMatch = line.match(/^      -\s*(.+)$/);
    if (readingSymbols && symbolMatch) {
      current.symbols.push(unquote(symbolMatch[1]));
    }
  }
  return { filename, source, commit, references };
}

export function buildPermalink(repository, commit, filePath, line = null) {
  const encodedPath = filePath
    .split("/")
    .map((part) => encodeURIComponent(part))
    .join("/");
  const anchor = line === null ? "" : `#L${line}`;
  return `https://github.com/${repository}/blob/${commit}/${encodedPath}${anchor}`;
}

function typeScriptSymbols(source, filename) {
  const kind = filename.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const sourceFile = ts.createSourceFile(
    filename,
    source,
    ts.ScriptTarget.Latest,
    true,
    kind,
  );
  const symbols = new Map();
  const addName = (name, node) => {
    if (!name) return;
    const line = sourceFile.getLineAndCharacterOfPosition(node.getStart()).line + 1;
    symbols.set(name, line);
  };
  for (const node of sourceFile.statements) {
    if (
      ts.isClassDeclaration(node) ||
      ts.isFunctionDeclaration(node) ||
      ts.isInterfaceDeclaration(node) ||
      ts.isTypeAliasDeclaration(node) ||
      ts.isEnumDeclaration(node)
    ) {
      addName(node.name?.text, node);
    } else if (ts.isVariableStatement(node)) {
      for (const declaration of node.declarationList.declarations) {
        if (ts.isIdentifier(declaration.name)) {
          addName(declaration.name.text, declaration);
        }
      }
    }
  }
  return symbols;
}

function pythonSymbols(source) {
  const result = spawnSync(
    "uv",
    ["run", "python", path.join(learningRoot, "check-python-symbols.py")],
    {
      cwd: repositoryRoot,
      encoding: "utf8",
      input: Buffer.from(source, "utf8").toString("base64"),
      shell: false,
    },
  );
  if (result.status !== 0) {
    throw new Error(result.stderr || "Python AST symbol extraction failed");
  }
  return new Map(Object.entries(JSON.parse(result.stdout)));
}

function genericSymbols(source) {
  const symbols = new Map();
  source.split(/\r?\n/).forEach((line, index) => {
    const match = line.match(
      /^\s*(?:export\s+)?(?:default\s+)?(?:class|function|interface|type|enum|const|let|var)\s+([A-Za-z_$][\w$]*)/,
    );
    if (match) symbols.set(match[1], index + 1);
  });
  return symbols;
}

function symbolsFor(source, filename) {
  if (filename.endsWith(".py")) return pythonSymbols(source);
  if (/\.(?:ts|tsx)$/.test(filename)) return typeScriptSymbols(source, filename);
  return genericSymbols(source);
}

export function validateDocument(
  document,
  {
    commitExists,
    commitIsPublished,
    readFileAtCommit,
    extractSymbols = symbolsFor,
  },
) {
  if (!/^[0-9a-f]{40}$/.test(document.commit ?? "")) {
    throw new Error(`${document.filename}: verified_commit must be a full SHA`);
  }
  if (!commitExists(document.commit)) {
    throw new Error(`${document.filename}: unknown commit ${document.commit}`);
  }
  if (!commitIsPublished(document.commit)) {
    throw new Error(
      `${document.filename}: verified_commit is not reachable from an origin ref`,
    );
  }
  if (document.references.length === 0) {
    throw new Error(`${document.filename}: code_references must not be empty`);
  }

  const seenPaths = new Set();
  const symbolOwners = new Map();
  const resolved = [];
  for (const reference of document.references) {
    if (reference.id !== null && !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(reference.id)) {
      throw new Error(`${document.filename}: invalid code reference id ${reference.id}`);
    }
    if (!reference.path || reference.path.includes("\\") || reference.path.includes("..")) {
      throw new Error(`${document.filename}: invalid repository path ${reference.path}`);
    }
    if (seenPaths.has(reference.path)) {
      throw new Error(`${document.filename}: duplicate path ${reference.path}`);
    }
    seenPaths.add(reference.path);
    if (!allowedRoles.has(reference.role)) {
      throw new Error(
        `${document.filename}: invalid role ${reference.role} for ${reference.path}`,
      );
    }
    const source = readFileAtCommit(document.commit, reference.path);
    if (source === null) {
      throw new Error(
        `${document.filename}: missing path ${reference.path} at ${document.commit}`,
      );
    }
    const available = extractSymbols(source, reference.path);
    const seenSymbols = new Set();
    for (const symbol of reference.symbols) {
      if (seenSymbols.has(symbol)) {
        throw new Error(
          `${document.filename}: duplicate symbol ${symbol} in ${reference.path}`,
        );
      }
      seenSymbols.add(symbol);
      const previousPath = symbolOwners.get(symbol);
      if (previousPath && previousPath !== reference.path) {
        throw new Error(
          `${document.filename}: ambiguous symbol ${symbol} in ${previousPath} and ${reference.path}`,
        );
      }
      symbolOwners.set(symbol, reference.path);
      if (!available.has(symbol)) {
        throw new Error(
          `${document.filename}: missing symbol ${symbol} in ${reference.path}`,
        );
      }
      resolved.push({
        id: reference.id,
        commit: document.commit,
        path: reference.path,
        symbol,
        line: Number(available.get(symbol)),
      });
    }
    resolved.push({
      id: reference.id,
      commit: document.commit,
      path: reference.path,
      symbol: null,
      line: null,
    });
  }
  return resolved;
}

function git(...args) {
  return execFileSync("git", args, {
    cwd: repositoryRoot,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  }).trimEnd();
}

function liveDependencies() {
  return {
    commitExists(commit) {
      try {
        git("cat-file", "-e", `${commit}^{commit}`);
        return true;
      } catch {
        return false;
      }
    },
    commitIsPublished(commit) {
      try {
        return git(
          "for-each-ref",
          "--format=%(refname)",
          "--contains",
          commit,
          "refs/remotes/origin",
        ).length > 0;
      } catch {
        return false;
      }
    },
    readFileAtCommit(commit, filePath) {
      try {
        return execFileSync("git", ["show", `${commit}:${filePath}`], {
          cwd: repositoryRoot,
          encoding: "utf8",
          stdio: ["ignore", "pipe", "pipe"],
          maxBuffer: 16 * 1024 * 1024,
        });
      } catch {
        return null;
      }
    },
  };
}

function qmdFiles(root) {
  const results = [];
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    const fullPath = path.join(root, entry.name);
    if (entry.isDirectory() && entry.name !== "_build") {
      results.push(...qmdFiles(fullPath));
    } else if (
      entry.isFile() &&
      entry.name.endsWith(".qmd") &&
      entry.name !== "chapter-template.qmd"
    ) {
      results.push(fullPath);
    }
  }
  return results;
}

function luaString(value) {
  return JSON.stringify(value);
}

function writeManifest(resolved, usages, repository) {
  const entries = new Map();
  for (const item of resolved) {
    if (item.id === null) continue;
    const existing = entries.get(item.id) ?? {
      commit: item.commit,
      path: item.path,
      url: buildPermalink(repository, item.commit, item.path),
      symbols: new Map(),
    };
    if (item.symbol !== null) {
      existing.symbols.set(item.symbol, {
        line: item.line,
        url: buildPermalink(repository, item.commit, item.path, item.line),
      });
    }
    entries.set(item.id, existing);
  }
  const lines = ["return {", "  references = {"];
  for (const [id, entry] of [...entries].sort(([a], [b]) => a.localeCompare(b))) {
    lines.push(`    [${luaString(id)}] = {`);
    lines.push(`      commit = ${luaString(entry.commit)},`);
    lines.push(`      path = ${luaString(entry.path)},`);
    lines.push(`      url = ${luaString(entry.url)},`);
    lines.push("      symbols = {");
    for (const [symbol, symbolReference] of [...entry.symbols].sort(([a], [b]) =>
      a.localeCompare(b)
    )) {
      lines.push(`        [${luaString(symbol)}] = {`);
      lines.push(`          line = ${symbolReference.line},`);
      lines.push(`          url = ${luaString(symbolReference.url)},`);
      lines.push("        },");
    }
    lines.push("      },");
    lines.push("    },");
  }
  lines.push("  },");
  lines.push("}", "");
  const manifestPath = path.join(
    learningRoot,
    "_extensions",
    "code-reference",
    "generated-manifest.lua",
  );
  fs.writeFileSync(manifestPath, lines.join("\n"), "utf8");

  const expectedLinks = usages.map(({ id, symbol }) => {
    const reference = entries.get(id);
    return symbol === null
      ? reference.url
      : reference.symbols.get(symbol).url;
  });
  fs.writeFileSync(
    path.join(
      learningRoot,
      "_extensions",
      "code-reference",
      "generated-expected-links.json",
    ),
    `${JSON.stringify(expectedLinks, null, 2)}\n`,
    "utf8",
  );
}

function selfTest() {
  const commit = "a".repeat(40);
  const metadata = parseDocumentMetadata(`---
verified_commit: "${commit}"
code_references:
  - path: "backend/example.py"
    role: "contract"
    symbols:
      - "Example"
---
`);
  assert.equal(metadata.references[0].symbols[0], "Example");
  assert.equal(
    buildPermalink("owner/repo", commit, "path with space/a.py", 12),
    `https://github.com/owner/repo/blob/${commit}/path%20with%20space/a.py#L12`,
  );

  const validDeps = {
    commitExists: () => true,
    commitIsPublished: () => true,
    readFileAtCommit: () => "class Example:\n    pass\n",
    extractSymbols: () => new Map([["Example", 1]]),
  };
  assert.equal(validateDocument(metadata, validDeps)[0].line, 1);
  assert.throws(
    () => validateDocument({ ...metadata, commit: null }, validDeps),
    /verified_commit must be a full SHA/,
  );
  assert.throws(
    () => validateDocument(metadata, { ...validDeps, commitExists: () => false }),
    /unknown commit/,
  );
  assert.throws(
    () =>
      validateDocument(metadata, {
        ...validDeps,
        readFileAtCommit: () => null,
      }),
    /missing path/,
  );
  assert.throws(
    () =>
      validateDocument(metadata, {
        ...validDeps,
        extractSymbols: () => new Map(),
      }),
    /missing symbol/,
  );
  const duplicatePath = {
    ...metadata,
    references: [metadata.references[0], { ...metadata.references[0], symbols: [] }],
  };
  assert.throws(
    () => validateDocument(duplicatePath, validDeps),
    /duplicate path/,
  );
  const duplicateSymbol = {
    ...metadata,
    references: [
      {
        ...metadata.references[0],
        symbols: ["Example", "Example"],
      },
    ],
  };
  assert.throws(
    () => validateDocument(duplicateSymbol, validDeps),
    /duplicate symbol/,
  );
  console.log("Code reference self-test passed: 9 assertions.");
}

function main() {
  selfTest();
  const config = JSON.parse(
    fs.readFileSync(
      path.join(learningRoot, "code-reference-config.json"),
      "utf8",
    ),
  );
  if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(config.repository ?? "")) {
    throw new Error("code-reference-config.json contains an invalid repository");
  }
  const dependencies = liveDependencies();
  const documents = qmdFiles(learningRoot)
    .map((filename) =>
      parseDocumentMetadata(fs.readFileSync(filename, "utf8"), filename),
    )
    .filter(Boolean);
  const ids = new Map();
  for (const document of documents) {
    for (const reference of document.references) {
      if (reference.id === null) continue;
      if (ids.has(reference.id)) {
        throw new Error(
          `Duplicate code reference id ${reference.id} in ${ids.get(reference.id)} and ${document.filename}`,
        );
      }
      ids.set(reference.id, document.filename);
    }
  }
  const referencesById = new Map();
  for (const document of documents) {
    for (const reference of document.references) {
      if (reference.id !== null) referencesById.set(reference.id, reference);
    }
  }
  const usages = [];
  for (const document of documents) {
    const shortcodePattern =
      /\{\{<\s+code-ref\s+([a-z0-9]+(?:-[a-z0-9]+)*)([^>]*)>\}\}/g;
    for (const match of document.source.matchAll(shortcodePattern)) {
      const reference = referencesById.get(match[1]);
      if (!reference) {
        throw new Error(
          `${document.filename}: shortcode uses unknown code reference id ${match[1]}`,
        );
      }
      const symbolMatch = match[2].match(
        /\bsymbol=(?:"([^"]+)"|'([^']+)'|([A-Za-z_$][\w$]*))/,
      );
      const symbol = symbolMatch?.[1] ?? symbolMatch?.[2] ?? symbolMatch?.[3];
      if (symbol && !reference.symbols.includes(symbol)) {
        throw new Error(
          `${document.filename}: shortcode uses unknown symbol ${symbol} for ${match[1]}`,
        );
      }
      usages.push({ id: match[1], symbol: symbol ?? null });
    }
  }
  const resolved = documents.flatMap((document) =>
    validateDocument(document, dependencies),
  );
  if (process.argv.includes("--write-manifest")) {
    writeManifest(resolved, usages, config.repository);
  }
  console.log(
    `Validated ${documents.length} documents and ${resolved.length} code reference records.`,
  );
}

if (path.resolve(process.argv[1] ?? "") === scriptPath) {
  main();
}
