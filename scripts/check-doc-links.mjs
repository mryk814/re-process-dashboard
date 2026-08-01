import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, extname, join, resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const ignoredDirectories = new Set([".git", ".book-tools", "_build", "node_modules"]);
const extensions = new Set([".md", ".qmd"]);

function collect(directory) {
  const files = [];
  for (const entry of readdirSync(directory)) {
    if (ignoredDirectories.has(entry)) continue;
    const path = join(directory, entry);
    if (statSync(path).isDirectory()) files.push(...collect(path));
    else if (extensions.has(extname(path))) files.push(path);
  }
  return files;
}

const failures = [];
const linkPattern = /!?\[[^\]]*]\(([^)\s]+)(?:\s+["'][^"']*["'])?\)/g;

for (const file of collect(root)) {
  const text = readFileSync(file, "utf8");
  for (const match of text.matchAll(linkPattern)) {
    const target = match[1].replace(/^<|>$/g, "");
    if (
      target.startsWith("#")
      || target.startsWith("http://")
      || target.startsWith("https://")
      || target.startsWith("mailto:")
      || target.includes("{{")
    ) {
      continue;
    }
    const path = target.split("#", 1)[0];
    if (path && !existsSync(resolve(dirname(file), decodeURIComponent(path)))) {
      failures.push(`${file.slice(root.length + 1)} -> ${target}`);
    }
  }
}

if (failures.length > 0) {
  process.stderr.write(`Broken relative documentation links:\n${failures.join("\n")}\n`);
  process.exit(1);
}

console.log("Relative documentation link check passed.");
