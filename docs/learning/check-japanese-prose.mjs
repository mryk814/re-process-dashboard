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
  ["カバレッジ", /カバレッジ/gu],

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
  "Wilson", "CALCE", "Percival", "Gregory", "Google",
  // 略語・頭字語
  "JSON", "JSONL", "HTML", "PDF", "CSS", "HTTP", "POST", "API", "URL",
  "HTTPS", "SHA", "UUID", "MCAR", "MAR", "MNAR", "EHS", "UTF",
]);

// 6文字以上の英語名詞＋助詞を検出する。短い語（repo, cell, diff等）は
// 許可リストか文字数で除外し、誤検出を抑える。
const particlePattern =
  /(?<![`\w])([A-Za-z]{6,}(?:\s[A-Za-z]+)*)([のをがはへでに])(?![`])/gu;

const particleChecks = [
  "英語名詞＋日本語助詞の接合",
];

// 本文へ裸で置かれた小文字の英単語を検出する。
// コード識別子、コマンド、画面上の正確なラベルはバッククォートで囲む。
const lowercaseEnglishPattern = /\b([a-z][a-z-]{3,})\b/gu;
const allowedLowercaseProperNames = new Set([
  "skops",
]);
const specificallyProhibitedEnglishWords = new Set([
  "calibration",
  "complacency",
  "fallback",
  "feasibility",
  "heteroscedastic",
  "misspecification",
  "narrowing",
  "quantile",
  "quantiles",
  "robustness",
  "severity",
  "uncertainty",
  "uncertainties",
  "widget",
  "widgets",
]);

function manuscriptFiles(learningRoot) {
  const authoredRootFiles = [
    "index.qmd",
    "references.qmd",
    // concepts.json の読者向けフィールドはこの二つへ生成される。
    // 生成物も走査することで、用語正本から本文へ英語混在が戻る経路を塞ぐ。
    "glossary.qmd",
    "concept-map.qmd",
  ]
    .map((filename) => path.join(learningRoot, filename))
    .filter((filename) => fs.existsSync(filename));
  const roots = [
    path.join(learningRoot, "chapters"),
    path.join(learningRoot, "labs"),
  ];
  const files = [...authoredRootFiles];
  for (const root of roots) {
    if (!fs.existsSync(root)) continue;
    for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
      if (entry.isFile() && /\.qmd$/iu.test(entry.name)) {
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
    // 参考文献案内に置く文献の原題は、正確な検索語として保持する。
    if (/^\*\*.*\[@[^\]]+\]\*\*$/u.test(trimmed)) continue;
    // 用語集の原語と別名は、実装や文献を検索するために意図して残す。
    if (/^\*\*別名と検索語\*\*：/u.test(trimmed)) continue;
    if (trimmed === "$$") {
      inDisplayMath = !inDisplayMath;
      continue;
    }
    if (inDisplayMath) continue;

    line = line
      .replace(/\{\{<[\s\S]*?>\}\}/gu, " ")
      .replace(/`[^`]*`/gu, " ")
      .replace(/（[^（）]*[A-Za-z][^（）]*）/gu, " ")
      .replace(/\$[^$]*\$/gu, " ")
      .replace(/\[\^[^\]]+\]/gu, " ")
      .replace(/\[@[^\]]+\]/gu, " ")
      .replace(/\[[^\]]*\]\([^)]*\)/gu, " ")
      .replace(/\\[A-Za-z]+/gu, " ")
      .replace(/\{[^}]*\}/gu, " ")
      .replace(/https?:\/\/\S+/giu, " ")
      .replace(/<[^>]+>/gu, " ");
    if (/^\s*:::/u.test(line)) continue;
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
            `${relative}:${line}: 「${word}${match[2]}」— 英語名詞に日本語助詞を直接つなげず、日本語で言い直す。正確なコード識別子または画面表示だけは、役割を日本語で示してバッククォートで囲む`,
          );
        }
      }

      if (/^(?:chapters|labs)\//u.test(relative)) {
        lowercaseEnglishPattern.lastIndex = 0;
        while ((match = lowercaseEnglishPattern.exec(text)) !== null) {
          const word = match[1];
          if (
            !allowedLowercaseProperNames.has(word)
            && !specificallyProhibitedEnglishWords.has(word)
          ) {
            errors.push(
              `${relative}:${line}: 「${word}」— 裸の英単語を本文の骨格にせず、日本語で書く。正確な識別子、コマンド、画面表示だけはバッククォートで囲む`,
            );
          }
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
