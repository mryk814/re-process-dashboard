import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const defaultLearningRoot = path.dirname(fileURLToPath(import.meta.url));
const prohibitedTerms = [
  // --- 訳語が確定している統計・ML用語 ---
  ["nominal coverage", /\bnominal\s+coverage\b/giu],
  ["empirical coverage", /\bempirical\s+coverage\b/giu],
  ["quantile", /\bquantiles?\b/giu],
  ["uncertainty", /\buncertaint(?:y|ies)\b/giu],
  ["calibration", /\bcalibrat(?:e|ed|es|ing|ion)\b/giu],
  ["robustness", /\brobustness\b/giu],
  ["クアンタイル", /クアンタイル/gu],
  ["アンサーテンティー", /アンサーテンティー/gu],
  ["ロバストネス", /ロバストネス/gu],

  // --- 日本語で書ける概念語 ---
  ["scope（→「範囲」「扱わない範囲」）", /(?:非|対象|扱わない)scope/giu],
  ["widget（→「表示要素」「部品」）", /\bwidgets?\b/giu],
  ["progressive disclosure（→「段階的開示」）", /\bprogressive\s+disclosure\b/giu],
  ["feasibility（→「実行可能性」）", /\bfeasibilit(?:y|ies)\b/giu],
  ["tolerance（→「許容度」）", /(?:リスク|risk)\s*tolerance/giu],
  ["human oversight（→「人間による監視」）", /\bhuman\s+oversight\b/giu],
  ["complacency（→「過信」）", /\bcomplacenc(?:y|ies)\b/giu],
  ["narrowing（→「型の絞り込み」）", /\bnarrowing\b/giu],
  ["fallback（→「代替手段」「退避表示」）", /\bfallbacks?\b/giu],
  ["bias（認知の文脈）", /認知bias/gu],
  ["context of use（→「利用文脈」）", /\bcontext\s+of\s+use\b/giu],
  ["severity（→「深刻度」）", /\bseverit(?:y|ies)\b/giu],
  ["actual measurement（→「実測」）", /\bactual\s+measurements?\b/giu],
  ["misspecification（→「モデルの誤り」「定式化の誤り」）", /\bmisspecification\b/giu],
  ["heteroscedastic（→「分散不均一」）", /\bheteroscedastic\b/giu],

  // --- 英語＋日本語助詞の接合（コード識別子以外） ---
];

// バッククォート外の英語名詞＋日本語助詞の接合を検出する。
// 製品名、略語、固有名詞など許容する語は許可リストへ入れる。
const allowedEnglishWords = new Set([
  // 製品名・ライブラリ名・ツール名
  "Pydantic", "FastAPI", "React", "TypeScript", "JavaScript", "Python",
  "SQLite", "Quarto", "PowerShell", "OpenAPI", "GitHub", "NIST", "WCAG",
  "Playwright", "Material", "Decision", "Workbench", "Ousterhout",
  "Wilson", "CALCE", "Percival", "Gregory",
  // 略語・頭字語
  "JSON", "JSONL", "HTML", "PDF", "CSS", "HTTP", "POST", "API", "URL",
  "HTTPS", "SHA", "UUID", "MCAR", "MAR", "MNAR", "EHS", "UTF",
  // 学術用語で原語を残す合意がある語
  "Screening", "Counterfactual",
  // 教材固有の概念で日本語名が章内で先に定義されている語
  "Curation", "Connector", "Recipe", "Training", "Canonical",
  "Raw", "Actual",
  // git・CI・ツールの固有語
  "main", "pytest", "build", "diff",
  // コード上の識別子として本文に頻出する語（本来バッククォートで囲むべきだが、
  // 既存章で大量に使われており段階的に修正する）
  "discriminator", "validator", "router", "endpoint", "literal",
  "body", "wrapper", "renderer", "command", "method",
  "override", "format", "location", "hint", "definition",
  "target", "binary", "ordinal", "presentation", "validation",
  "precondition", "kind", "plugin", "loader", "inspection",
  "application", "pattern", "document", "tutorial", "mapping",
  "generate", "check", "store", "alert", "goal",
  // Web API・TypeScript固有名
  "AbortSignal", "AbortController", "Prediction",
]);

// 6文字以上の英語名詞＋助詞を検出する。短い語（repo, cell, diff等）は
// 許可リストか文字数で除外し、誤検出を抑える。
const particlePattern =
  /(?<![`\w])([A-Za-z]{6,}(?:\s[A-Za-z]+)*)([のをがはへでに])(?![`])/gu;

const particleChecks = [
  "英語名詞＋日本語助詞の接合",
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

      particlePattern.lastIndex = 0;
      let match;
      while ((match = particlePattern.exec(text)) !== null) {
        const word = match[1].trim();
        const firstWord = word.split(/\s/u)[0];
        if (!allowedEnglishWords.has(firstWord) && !/^[A-Z]{2,}$/u.test(firstWord)) {
          errors.push(
            `${relative}:${line}: 「${word}${match[2]}」— 英語名詞に日本語助詞を直接つなげず、日本語で言い直すかバッククォートで囲む`,
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
