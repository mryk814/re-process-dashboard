import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const learningRoot = path.dirname(fileURLToPath(import.meta.url));
const repositoryRoot = path.resolve(learningRoot, "..", "..");
const sourcePath = path.join(learningRoot, "concepts", "concepts.json");
const schemaPath = path.join(learningRoot, "concepts", "concept.schema.json");
const glossaryPath = path.join(learningRoot, "glossary.qmd");
const mapPath = path.join(learningRoot, "concept-map.qmd");
const writeMode = process.argv.includes("--write");
const selfTestMode = process.argv.includes("--self-test");

const allowedStatuses = new Set(["current", "future", "historical"]);
const allowedScopes = new Set(["repo-wide", "subsystem", "pattern"]);
const allowedRoles = new Set([
  "docs",
  "contract",
  "domain",
  "application",
  "persistence",
  "api",
  "generated",
  "frontend",
  "test",
  "build",
]);
const idPattern = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

function fail(message) {
  throw new Error(message);
}

function normalizedGeneratedText(value) {
  return value.replace(/^\uFEFF/, "").replace(/\r\n?/g, "\n");
}

function readJson(filename) {
  return JSON.parse(fs.readFileSync(filename, "utf8"));
}

function schemaPointer(root, reference) {
  if (!reference.startsWith("#/")) fail(`unsupported schema reference ${reference}`);
  return reference
    .slice(2)
    .split("/")
    .map((part) => part.replaceAll("~1", "/").replaceAll("~0", "~"))
    .reduce((value, part) => value?.[part], root);
}

function jsonType(value) {
  if (value === null) return "null";
  if (Array.isArray(value)) return "array";
  if (Number.isInteger(value)) return "integer";
  return typeof value;
}

function validateSchemaNode(value, schema, root, location, errors) {
  if (schema.$ref) {
    const target = schemaPointer(root, schema.$ref);
    if (!target) {
      errors.push(`${location}: unresolved schema reference ${schema.$ref}`);
      return;
    }
    validateSchemaNode(value, target, root, location, errors);
    return;
  }
  if ("const" in schema && JSON.stringify(value) !== JSON.stringify(schema.const)) {
    errors.push(`${location}: must equal ${JSON.stringify(schema.const)}`);
  }
  if (
    schema.enum &&
    !schema.enum.some((candidate) => JSON.stringify(candidate) === JSON.stringify(value))
  ) {
    errors.push(`${location}: must be one of ${schema.enum.join(", ")}`);
  }
  if (schema.type) {
    const allowed = Array.isArray(schema.type) ? schema.type : [schema.type];
    if (!allowed.includes(jsonType(value))) {
      errors.push(`${location}: expected ${allowed.join(" or ")}, found ${jsonType(value)}`);
      return;
    }
  }
  if (typeof value === "string") {
    if (schema.minLength !== undefined && value.length < schema.minLength) {
      errors.push(`${location}: string is shorter than ${schema.minLength}`);
    }
    if (schema.pattern && !new RegExp(schema.pattern).test(value)) {
      errors.push(`${location}: does not match ${schema.pattern}`);
    }
  }
  if (Array.isArray(value)) {
    if (schema.minItems !== undefined && value.length < schema.minItems) {
      errors.push(`${location}: needs at least ${schema.minItems} items`);
    }
    if (schema.items) {
      value.forEach((item, index) =>
        validateSchemaNode(item, schema.items, root, `${location}[${index}]`, errors),
      );
    }
  }
  if (value !== null && typeof value === "object" && !Array.isArray(value)) {
    for (const required of schema.required ?? []) {
      if (!(required in value)) errors.push(`${location}: missing required property ${required}`);
    }
    const properties = schema.properties ?? {};
    for (const [key, item] of Object.entries(value)) {
      if (properties[key]) {
        validateSchemaNode(item, properties[key], root, `${location}.${key}`, errors);
      } else if (schema.additionalProperties === false) {
        errors.push(`${location}: unknown property ${key}`);
      }
    }
  }
}

function validateAgainstSchema(value, schema) {
  const errors = [];
  validateSchemaNode(value, schema, schema, "$", errors);
  return errors;
}

function assertArray(value, label) {
  if (!Array.isArray(value)) fail(`${label} must be an array`);
  return value;
}

function assertUnique(values, label) {
  const seen = new Set();
  for (const value of values) {
    if (seen.has(value)) fail(`${label} contains duplicate ${value}`);
    seen.add(value);
  }
}

function repositoryPath(reference, owner) {
  const relative = reference.path;
  if (
    typeof relative !== "string" ||
    relative.length === 0 ||
    relative.includes("\\") ||
    path.isAbsolute(relative) ||
    relative.split("/").some((part) => part === "" || part === "." || part === "..")
  ) {
    fail(`${owner} has invalid repository path ${relative}`);
  }
  if (!allowedRoles.has(reference.role)) {
    fail(`${owner} has invalid repository role ${reference.role}`);
  }
  const resolved = path.resolve(repositoryRoot, ...relative.split("/"));
  if (!resolved.startsWith(`${repositoryRoot}${path.sep}`)) {
    fail(`${owner} repository path escapes the repository: ${relative}`);
  }
  if (!fs.existsSync(resolved) || !fs.statSync(resolved).isFile()) {
    fail(`${owner} repository path does not exist: ${relative}`);
  }
  return relative;
}

function parseFrontMatter(filename) {
  const source = fs.readFileSync(filename, "utf8");
  const match = source.match(/^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/);
  if (!match) fail(`${filename} has no YAML front matter`);
  const lines = match[1].split(/\r?\n/);
  const scalar = (key) => {
    const line = lines.find((candidate) => candidate.startsWith(`${key}:`));
    if (!line) return null;
    return line.slice(key.length + 1).trim().replace(/^["']|["']$/g, "");
  };
  const list = (key) => {
    if (lines.some((candidate) => candidate === `${key}: []`)) return [];
    const start = lines.findIndex((candidate) => candidate === `${key}:`);
    if (start === -1) return null;
    const values = [];
    for (let index = start + 1; index < lines.length; index += 1) {
      const line = lines[index];
      const item = line.match(/^  -\s+"?([^"]+?)"?\s*$/);
      if (item) {
        values.push(item[1]);
        continue;
      }
      if (/^\S/.test(line)) break;
      if (line.trim() !== "") fail(`${filename}: unsupported ${key} item: ${line}`);
    }
    return values;
  };
  return {
    chapterId: scalar("chapter_id"),
    prerequisiteConcepts: list("prerequisite_concepts"),
    introducedConcepts: list("introduced_concepts"),
    reinforcedConcepts: list("reinforced_concepts"),
    futureConcepts: list("future_concepts"),
    historicalConcepts: list("historical_concepts"),
  };
}

function directCycle(conceptsById) {
  const visiting = new Set();
  const visited = new Set();
  const trail = [];
  function visit(id) {
    if (visiting.has(id)) {
      const start = trail.indexOf(id);
      return [...trail.slice(start), id];
    }
    if (visited.has(id)) return null;
    visiting.add(id);
    trail.push(id);
    for (const prerequisite of conceptsById.get(id).prerequisites) {
      const cycle = visit(prerequisite);
      if (cycle) return cycle;
    }
    trail.pop();
    visiting.delete(id);
    visited.add(id);
    return null;
  }
  for (const id of conceptsById.keys()) {
    const cycle = visit(id);
    if (cycle) return cycle;
  }
  return null;
}

function validateConceptRelations(concepts, conceptsById) {
  for (const concept of concepts) {
    for (const field of ["not_same_as", "prerequisites", "related"]) {
      for (const target of concept[field]) {
        if (!conceptsById.has(target)) fail(`${concept.id}.${field}: missing ${target}`);
        if (target === concept.id) fail(`${concept.id}.${field}: self reference`);
      }
    }
    for (const prerequisite of concept.prerequisites) {
      const prerequisiteStatus = conceptsById.get(prerequisite).status;
      if (concept.status === "current" && prerequisiteStatus !== "current") {
        fail(`${concept.id}: current concept depends on ${prerequisiteStatus} ${prerequisite}`);
      }
      if (concept.status === "future" && prerequisiteStatus === "historical") {
        fail(`${concept.id}: future concept depends on historical ${prerequisite}`);
      }
      if (concept.status === "historical" && prerequisiteStatus === "future") {
        fail(`${concept.id}: historical concept depends on future ${prerequisite}`);
      }
    }
  }
}

function markdownLink(label, anchor) {
  return `[${label}](#${anchor})`;
}

function renderStatus(status) {
  return {
    current: "現行",
    future: "将来候補",
    historical: "履歴",
  }[status];
}

function renderConceptList(ids, conceptsById, page = "") {
  if (ids.length === 0) return "なし";
  return ids
    .map((id) => `[${conceptsById.get(id).term_ja}](${page}#concept-${id})`)
    .join("、");
}

function permalink(repository, commit, reference) {
  const encodedPath = reference.path
    .split("/")
    .map((part) => encodeURIComponent(part))
    .join("/");
  return `https://github.com/${repository}/blob/${commit}/${encodedPath}`;
}

function renderGlossary(data, concepts, conceptsById, chaptersById) {
  const lines = [
    "<!-- generated by check-concepts.mjs; edit concepts/concepts.json instead -->",
    "",
    "# 用語集 {#sec-glossary .unnumbered}",
    "",
    "画面に表示された「改訂」と、保存競合を防ぐ`revision`は、同じ語で呼べそうに見えても答える問いが違います。",
    "この用語集は日本語と英語の対応だけでなく、何を指し、何と混同しやすく、現行実装のどこへ着地するかをまとめます。",
    "",
    "状態は、現行実装で確認した概念を「現行」、実装前の候補を「将来候補」、現在は採用していない過去の案を「履歴」として区別します。",
    "",
  ];
  for (const category of data.categories) {
    lines.push(`## ${category.title}`, "");
    const members = concepts
      .filter((concept) => concept.category === category.id)
      .sort((left, right) => left.term_ja.localeCompare(right.term_ja, "ja"));
    for (const concept of members) {
      lines.push(
        `### ${concept.term_ja}（${concept.term_en}） {#concept-${concept.id}}`,
        "",
        `${concept.definition}`,
        "",
        `**状態**：${renderStatus(concept.status)}`,
        "",
      );
      if (concept.aliases.length > 0) {
        lines.push(`**別名と検索語**：${concept.aliases.join("、")}`, "");
      }
      lines.push(`**適用範囲**：${concept.scope}`, "");
      lines.push(
        `**同じではない概念**：${renderConceptList(concept.not_same_as, conceptsById)}`,
        "",
        `**前提概念**：${renderConceptList(concept.prerequisites, conceptsById)}`,
        "",
      );
      if (concept.anti_definitions.length > 0) {
        lines.push(
          "**この語が意味しないもの**：",
          "",
          ...concept.anti_definitions.map((item) => `- ${item}`),
          "",
        );
      }
      if (concept.status_note) {
        lines.push(`**状態の注記**：${concept.status_note}`, "");
      }
      if (concept.math_connections.length > 0) {
        lines.push(`**数理との接続**：${concept.math_connections.join("。")}。`, "");
      }
      const chapterLinks = concept.chapters.map((id) => {
        const chapter = chaptersById.get(id);
        return `[${chapter.title}](${chapter.path})`;
      });
      lines.push(
        `**学ぶ章**：${
          chapterLinks.length > 0 ? chapterLinks.join("、") : "今後の章候補（未配置）"
        }`,
        "",
      );
      const repoLinks = concept.repo_references.map(
        (reference) =>
          `[${reference.path}](${permalink(data.repository, data.verified_commit, reference)})`,
      );
      lines.push(
        `**このrepoの実装**：${repoLinks.length > 0 ? repoLinks.join("、") : "現行実装なし"}`,
        "",
      );
      if (concept.common_misconceptions.length > 0) {
        lines.push(
          "**よくある誤解**：",
          "",
          ...concept.common_misconceptions.map((item) => `- ${item}`),
          "",
        );
      }
      if (concept.ui_mapping) {
        lines.push(
          "**表示と言葉の対応**：",
          "",
          `- 一般概念：${concept.ui_mapping.general}`,
          `- internal term：${concept.ui_mapping.internal}`,
          `- UI表現：${concept.ui_mapping.ui}`,
          "",
        );
      }
    }
  }
  return `${lines.join("\n").trim()}\n`;
}

function renderMap(data, concepts, conceptsById, chapters) {
  const uniqueReferences = new Map();
  for (const concept of concepts) {
    for (const reference of concept.repo_references) {
      if (!uniqueReferences.has(reference.path)) {
        uniqueReferences.set(reference.path, reference);
      }
    }
  }
  const lines = [
    "---",
    `verified_commit: "${data.verified_commit}"`,
    "code_references:",
    ...[...uniqueReferences.values()]
      .sort((left, right) => left.path.localeCompare(right.path))
      .flatMap((reference) => [
        `  - path: "${reference.path}"`,
        `    role: "${reference.role}"`,
      ]),
    "---",
    "",
    "<!-- generated by check-concepts.mjs; edit concepts/concepts.json and chapter front matter instead -->",
    "",
    "# 概念の依存から読む {#sec-concept-map}",
    "",
    "用語集を五十音順に引けても、どの概念を先に理解すればよいかは分かりません。",
    "ここでは、概念そのものと表示上の集まりを分け、直接の前提だけを辺として示します。^[W3C SKOSはconcept、label、collectionを分け、`broader`と`narrower`を直接の階層関係として扱います [@w3c-skos-reference; @w3c-skos-primer]。本教材はRDFを導入せず、この分離だけを小さなJSON schemaへ借りています。]",
    "",
    "この表の`A → B`は「Aを理解してからBを読む」を表します。",
    "関連があるだけの概念は前提にしません。^[Referenceは調べるための情報として簡潔で一貫した形にそろえ、説明や手順を混ぜないという分離はDiátaxisのReferenceに倣いました [@diataxis]。Googleの開発者向けstyle guideも、用語の定義ではdescription listと正確な語の選択を勧めています [@google-dev-word-list; @google-dev-lists]。]",
    "",
    "## 章を読む前に確かめる概念",
    "",
    "| 章 | 前提 | この章で導入 | 繰り返し使う |",
    "|---|---|---|---|",
  ];
  for (const chapter of chapters) {
    lines.push(
      `| [${chapter.title}](${chapter.path}) | ${renderConceptList(
        chapter.prerequisiteConcepts,
        conceptsById,
        "glossary.qmd",
      )} | ${renderConceptList(
        chapter.introducedConcepts,
        conceptsById,
        "glossary.qmd",
      )} | ${renderConceptList(
        chapter.reinforcedConcepts,
        conceptsById,
        "glossary.qmd",
      )} |`,
    );
  }
  const statusUnits = chapters.filter(
    (chapter) => chapter.futureConcepts.length > 0 || chapter.historicalConcepts.length > 0,
  );
  if (statusUnits.length > 0) {
    lines.push(
      "",
      "## 将来候補と履歴を置く章",
      "",
      "| 章 | 将来候補 | 履歴 |",
      "|---|---|---|",
    );
    for (const chapter of statusUnits) {
      lines.push(
        `| [${chapter.title}](${chapter.path}) | ${renderConceptList(
          chapter.futureConcepts,
          conceptsById,
          "glossary.qmd",
        )} | ${renderConceptList(
          chapter.historicalConcepts,
          conceptsById,
          "glossary.qmd",
        )} |`,
      );
    }
  }
  lines.push(
    "",
    "## 直接の前提関係",
    "",
    "| 先に理解する概念 | 次に理解する概念 | 入口となる章 |",
    "|---|---|---|",
  );
  for (const concept of concepts) {
    for (const prerequisite of concept.prerequisites) {
      const chapter = concept.chapters.length > 0 ? chapters.find((item) => item.id === concept.chapters[0]) : null;
      lines.push(
        `| [${conceptsById.get(prerequisite).term_ja}](glossary.qmd#concept-${prerequisite}) | [${
          concept.term_ja
        }](glossary.qmd#concept-${concept.id}) | ${
          chapter ? `[${chapter.title}](${chapter.path})` : "未配置"
        } |`,
      );
    }
  }
  lines.push(
    "",
    "## 比較して理解する関係",
    "",
    "| 概念 | 関係 | 相手 |",
    "|---|---|---|",
  );
  const renderedRelations = new Set();
  for (const concept of concepts) {
    for (const target of concept.related) {
      const key = [concept.id, target].sort().join(":");
      if (renderedRelations.has(`related:${key}`)) continue;
      renderedRelations.add(`related:${key}`);
      lines.push(
        `| [${concept.term_ja}](glossary.qmd#concept-${concept.id}) | 関連する | [${
          conceptsById.get(target).term_ja
        }](glossary.qmd#concept-${target}) |`,
      );
    }
    for (const target of concept.not_same_as) {
      const key = [concept.id, target].sort().join(":");
      if (renderedRelations.has(`not-same:${key}`)) continue;
      renderedRelations.add(`not-same:${key}`);
      lines.push(
        `| [${concept.term_ja}](glossary.qmd#concept-${concept.id}) | 同じではない | [${
          conceptsById.get(target).term_ja
        }](glossary.qmd#concept-${target}) |`,
      );
    }
  }
  lines.push(
    "",
    "## 状態を混ぜない",
    "",
    "| 状態 | 件数 | 読み方 |",
    "|---|---:|---|",
  );
  for (const status of ["current", "future", "historical"]) {
    lines.push(
      `| ${renderStatus(status)} | ${concepts.filter((concept) => concept.status === status).length} | ${
        {
          current: "verified commitの実装または契約へリンクする",
          future: "未実装の候補として読み、現行機能へ格上げしない",
          historical: "過去の案または不採用判断として読む",
        }[status]
      } |`,
    );
  }
  lines.push(
    "",
    "## UI labelとinternal termを分ける",
    "",
    "| 一般概念 | internal term | UI表現 |",
    "|---|---|---|",
  );
  for (const concept of concepts.filter((item) => item.ui_mapping)) {
    lines.push(
      `| [${concept.ui_mapping.general}](glossary.qmd#concept-${concept.id}) | \`${concept.ui_mapping.internal}\` | ${concept.ui_mapping.ui} |`,
    );
  }
  lines.push(
    "",
    "この対応表は生のenumを日本語へ置換する辞書ではありません。",
    "利用者が判断するときの語と、保存や通信で使う識別子が答える問いを分けます。",
    "",
  );
  return `${lines.join("\n").trim()}\n`;
}

function validateAndRender() {
  if (!fs.existsSync(schemaPath)) fail("concept schema is missing");
  const schema = readJson(schemaPath);
  const data = readJson(sourcePath);
  const schemaErrors = validateAgainstSchema(data, schema);
  if (schemaErrors.length > 0) {
    fail(`concept schema validation failed:\n${schemaErrors.join("\n")}`);
  }
  if (data.$schema !== "./concept.schema.json") {
    fail("concepts.json must declare $schema as ./concept.schema.json");
  }
  if (data.schema_version !== 1) fail("concept schema_version must be 1");
  if (!/^[0-9a-f]{40}$/.test(data.verified_commit ?? "")) {
    fail("verified_commit must be a full SHA");
  }
  if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(data.repository ?? "")) {
    fail("repository must be owner/name");
  }
  const categories = assertArray(data.categories, "categories");
  const categoryIds = categories.map((item) => item.id);
  assertUnique(categoryIds, "categories");
  for (const category of categories) {
    if (!idPattern.test(category.id) || typeof category.title !== "string" || !category.title) {
      fail(`invalid category ${JSON.stringify(category)}`);
    }
  }
  const concepts = assertArray(data.concepts, "concepts");
  if (concepts.length < 50) {
    fail(`expected at least 50 core concepts, found ${concepts.length}`);
  }
  const conceptsById = new Map();
  const japaneseLabels = new Set();
  const englishLabels = new Set();
  for (const concept of concepts) {
    if (!idPattern.test(concept.id ?? "")) fail(`invalid concept id ${concept.id}`);
    if (conceptsById.has(concept.id)) fail(`duplicate concept id ${concept.id}`);
    conceptsById.set(concept.id, concept);
    if (!categoryIds.includes(concept.category)) fail(`${concept.id}: unknown category`);
    if (!allowedStatuses.has(concept.status)) fail(`${concept.id}: invalid status`);
    if (!allowedScopes.has(concept.scope)) fail(`${concept.id}: invalid scope`);
    if (
      (concept.status === "current" && concept.status_note !== null) ||
      (concept.status !== "current" &&
        (typeof concept.status_note !== "string" || concept.status_note.trim() === ""))
    ) {
      fail(`${concept.id}: status_note does not match status ${concept.status}`);
    }
    for (const field of ["term_ja", "term_en", "definition"]) {
      if (typeof concept[field] !== "string" || concept[field].trim() === "") {
        fail(`${concept.id}: ${field} must be non-empty`);
      }
    }
    if (japaneseLabels.has(concept.term_ja)) fail(`duplicate Japanese preferred label ${concept.term_ja}`);
    if (englishLabels.has(concept.term_en)) fail(`duplicate English preferred label ${concept.term_en}`);
    japaneseLabels.add(concept.term_ja);
    englishLabels.add(concept.term_en);
    for (const field of [
      "aliases",
      "anti_definitions",
      "not_same_as",
      "prerequisites",
      "related",
      "math_connections",
      "repo_references",
      "common_misconceptions",
    ]) {
      assertArray(concept[field], `${concept.id}.${field}`);
    }
    for (const field of ["aliases", "not_same_as", "prerequisites", "related"]) {
      assertUnique(concept[field], `${concept.id}.${field}`);
    }
    if (concept.status === "current" && concept.repo_references.length === 0) {
      fail(`${concept.id}: current concept must have a repository reference`);
    }
    for (const reference of concept.repo_references) repositoryPath(reference, concept.id);
  }
  validateConceptRelations(concepts, conceptsById);
  const cycle = directCycle(conceptsById);
  if (cycle) fail(`concept prerequisite cycle: ${cycle.join(" -> ")}`);
  const misconceptionConcepts = concepts.filter(
    (concept) => concept.anti_definitions.length > 0,
  );
  if (misconceptionConcepts.length < 10) {
    fail(`at least 10 concepts need anti-definitions; found ${misconceptionConcepts.length}`);
  }
  for (const status of allowedStatuses) {
    if (!concepts.some((concept) => concept.status === status)) {
      fail(`concept inventory has no ${status} entry`);
    }
  }

  const chapterSpecs = assertArray(data.chapters, "chapters");
  const chapters = [];
  const chaptersById = new Map();
  for (const spec of chapterSpecs) {
    if (!idPattern.test(spec.id ?? "")) fail(`invalid chapter id ${spec.id}`);
    const filename = path.join(learningRoot, ...spec.path.split("/"));
    if (!fs.existsSync(filename)) fail(`missing chapter ${spec.path}`);
    const frontMatter = parseFrontMatter(filename);
    if (frontMatter.chapterId !== spec.id) {
      fail(`${spec.path}: chapter_id must be ${spec.id}`);
    }
    for (const field of [
      "prerequisiteConcepts",
      "introducedConcepts",
      "reinforcedConcepts",
      "futureConcepts",
      "historicalConcepts",
    ]) {
      if (frontMatter[field] === null) fail(`${spec.path}: missing ${field}`);
      assertUnique(frontMatter[field], `${spec.path}.${field}`);
      for (const id of frontMatter[field]) {
        if (!conceptsById.has(id)) fail(`${spec.path}.${field}: missing concept ${id}`);
      }
    }
    const groups = [
      ...frontMatter.prerequisiteConcepts,
      ...frontMatter.introducedConcepts,
      ...frontMatter.reinforcedConcepts,
      ...frontMatter.futureConcepts,
      ...frontMatter.historicalConcepts,
    ];
    assertUnique(groups, `${spec.path} concept roles`);
    for (const id of [
      ...frontMatter.prerequisiteConcepts,
      ...frontMatter.introducedConcepts,
      ...frontMatter.reinforcedConcepts,
    ]) {
      if (conceptsById.get(id).status !== "current") {
        fail(`${spec.path}: ${id} must be current in a current concept role`);
      }
    }
    for (const id of frontMatter.futureConcepts) {
      if (conceptsById.get(id).status !== "future") {
        fail(`${spec.path}: ${id} must be future`);
      }
    }
    for (const id of frontMatter.historicalConcepts) {
      if (conceptsById.get(id).status !== "historical") {
        fail(`${spec.path}: ${id} must be historical`);
      }
    }
    const chapter = { ...spec, ...frontMatter };
    chapters.push(chapter);
    chaptersById.set(spec.id, chapter);
  }
  assertUnique(chapters.map((chapter) => chapter.id), "chapters");
  for (const concept of concepts) concept.chapters = [];
  for (const chapter of chapters) {
    for (const id of [
      ...chapter.prerequisiteConcepts,
      ...chapter.introducedConcepts,
      ...chapter.reinforcedConcepts,
      ...chapter.futureConcepts,
      ...chapter.historicalConcepts,
    ]) {
      conceptsById.get(id).chapters.push(chapter.id);
    }
  }

  return {
    glossary: renderGlossary(data, concepts, conceptsById, chaptersById),
    map: renderMap(data, concepts, conceptsById, chapters),
    conceptCount: concepts.length,
    misconceptionCount: misconceptionConcepts.length,
    edgeCount: concepts.reduce((total, concept) => total + concept.prerequisites.length, 0),
    chapterCount: chapters.length,
  };
}

function synchronize(filename, expected) {
  if (writeMode) {
    fs.writeFileSync(filename, expected, "utf8");
    return;
  }
  const actual = fs.existsSync(filename) ? fs.readFileSync(filename, "utf8") : null;
  if (actual === null || normalizedGeneratedText(actual) !== normalizedGeneratedText(expected)) {
    fail(`${path.relative(learningRoot, filename)} is stale; run node docs/learning/check-concepts.mjs --write`);
  }
}

function runSelfTests() {
  const expectFailure = (operation, fragment) => {
    try {
      operation();
    } catch (error) {
      if (error.message.includes(fragment)) return;
      fail(`self-test: expected ${fragment}, found ${error.message}`);
    }
    fail(`self-test: expected failure containing ${fragment}`);
  };
  const concept = (prerequisites) => ({ prerequisites });
  const acyclic = new Map([
    ["evidence", concept([])],
    ["snapshot", concept(["evidence"])],
  ]);
  if (directCycle(acyclic) !== null) fail("self-test: acyclic graph was rejected");

  const cyclic = new Map([
    ["revision", concept(["digest"])],
    ["digest", concept(["revision"])],
  ]);
  const cycle = directCycle(cyclic);
  if (!cycle || cycle[0] !== cycle.at(-1)) {
    fail("self-test: prerequisite cycle was not detected");
  }

  let traversalRejected = false;
  try {
    repositoryPath({ path: "../outside", role: "docs" }, "self-test");
  } catch (error) {
    traversalRejected = error.message.includes("invalid repository path");
  }
  if (!traversalRejected) fail("self-test: repository path traversal was accepted");
  expectFailure(() => assertUnique(["duplicate", "duplicate"], "self-test"), "duplicate");

  const current = {
    id: "current",
    status: "current",
    not_same_as: [],
    prerequisites: ["future"],
    related: [],
  };
  const future = {
    id: "future",
    status: "future",
    not_same_as: [],
    prerequisites: [],
    related: [],
  };
  expectFailure(
    () => validateConceptRelations([current, future], new Map([["current", current], ["future", future]])),
    "current concept depends on future",
  );
  const missing = { ...current, prerequisites: ["absent"] };
  expectFailure(
    () => validateConceptRelations([missing], new Map([["current", missing]])),
    "missing absent",
  );

  const linked = renderConceptList(
    ["evidence"],
    new Map([["evidence", { term_ja: "証拠" }]]),
    "glossary.qmd",
  );
  if (linked !== "[証拠](glossary.qmd#concept-evidence)") {
    fail("self-test: cross-page glossary link is incorrect");
  }
  if (normalizedGeneratedText("\uFEFFline 1\r\nline 2\r\n") !== normalizedGeneratedText("line 1\nline 2\n")) {
    fail("self-test: generated text normalization did not ignore BOM and line endings");
  }
  const schema = readJson(schemaPath);
  const invalid = readJson(sourcePath);
  invalid.concepts[0].unexpected = true;
  if (!validateAgainstSchema(invalid, schema).some((error) => error.includes("unknown property"))) {
    fail("self-test: schema did not reject an unknown property");
  }
  console.log(
    "Concept checker self-tests passed: schema, duplicate, relation, graph, path, link, and line-ending invariants.",
  );
}

if (selfTestMode) {
  runSelfTests();
} else {
  const rendered = validateAndRender();
  synchronize(glossaryPath, rendered.glossary);
  synchronize(mapPath, rendered.map);
  console.log(
    `Concept validation passed: ${rendered.conceptCount} concepts, ${rendered.edgeCount} prerequisite edges, ${rendered.misconceptionCount} anti-definitions, ${rendered.chapterCount} chapters.`,
  );
}
