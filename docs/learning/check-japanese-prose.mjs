import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const defaultLearningRoot = path.dirname(fileURLToPath(import.meta.url));
const prohibitedTerms = [
  ["nominal coverage", /\bnominal\s+coverage\b/giu],
  ["empirical coverage", /\bempirical\s+coverage\b/giu],
  ["quantile", /\bquantiles?\b/giu],
  ["uncertainty", /\buncertaint(?:y|ies)\b/giu],
  ["calibration", /\bcalibrat(?:e|ed|es|ing|ion)\b/giu],
  ["robustness", /\brobustness\b/giu],
  ["クアンタイル", /クアンタイル/gu],
  ["アンサーテンティー", /アンサーテンティー/gu],
  ["ロバストネス", /ロバストネス/gu],
];

function manuscriptFiles(learningRoot) {
  const roots = [
    path.join(learningRoot, "chapters"),
    path.join(learningRoot, "labs"),
  ];
  const files = [];
  for (const root of roots) {
    if (!fs.existsSync(root)) continue;
    for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
      if (entry.isFile() && /\.(?:qmd|md)$/iu.test(entry.name)) {
        files.push(path.join(root, entry.name));
      }
    }
  }
  return files.sort();
}

export function visibleProse(source) {
  const lines = source.replaceAll("\r\n", "\n").split("\n");
  const output = [];
  let inFence = false;
  let inFrontMatter = lines[0]?.trim() === "---";
  let inDisplayMath = false;
  let inFurtherReading = false;

  for (let index = 0; index < lines.length; index += 1) {
    const lineNumber = index + 1;
    let line = lines[index];
    const trimmed = line.trim();

    if (inFrontMatter) {
      if (index > 0 && trimmed === "---") inFrontMatter = false;
      continue;
    }
    if (/^```/.test(trimmed)) {
      inFence = !inFence;
      continue;
    }
    if (inFence) continue;
    if (/^##\s+(?:Further Reading|参考文献を読む)\s*$/iu.test(trimmed)) {
      inFurtherReading = true;
      continue;
    }
    if (inFurtherReading && /^##\s+/.test(trimmed)) inFurtherReading = false;
    if (inFurtherReading) continue;
    if (trimmed === "$$") {
      inDisplayMath = !inDisplayMath;
      continue;
    }
    if (inDisplayMath) continue;

    line = line
      .replace(/\{\{<[\s\S]*?>\}\}/gu, " ")
      .replace(/`[^`]*`/gu, " ")
      .replace(/\$[^$]*\$/gu, " ")
      .replace(/\[\^[^\]]+\]/gu, " ")
      .replace(/\[[^\]]*\]\([^)]*\)/gu, " ")
      .replace(/\{#[^}]*\}/gu, " ")
      .replace(/https?:\/\/\S+/giu, " ")
      .replace(/<[^>]+>/gu, " ");
    output.push({ line: lineNumber, text: line });
  }
  return output;
}

export function inspectJapaneseProse({
  learningRoot = defaultLearningRoot,
} = {}) {
  const errors = [];
  for (const filename of manuscriptFiles(learningRoot)) {
    const relative = path
      .relative(learningRoot, filename)
      .replaceAll(path.sep, "/");
    const source = fs.readFileSync(filename, "utf8");
    const prose = visibleProse(source);

    for (const { line, text } of prose) {
      for (const [label, pattern] of prohibitedTerms) {
        pattern.lastIndex = 0;
        if (pattern.test(text)) {
          errors.push(
            `${relative}:${line}: 日本語本文では「${label}」を日本語の用語へ置き換える`,
          );
        }
      }
    }

  }
  return { errors };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const { errors } = inspectJapaneseProse();
  if (errors.length > 0) {
    for (const error of errors) console.error(`- ${error}`);
    process.exitCode = 1;
  } else {
    console.log("日本語本文に既知の英語混在パターンはありません。");
  }
}
