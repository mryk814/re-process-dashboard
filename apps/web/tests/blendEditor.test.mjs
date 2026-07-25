import test from "node:test";
import assert from "node:assert/strict";
import { build } from "esbuild";
import path from "node:path";
import { createRequire } from "node:module";

const sourceRoot = path.resolve(import.meta.dirname, "../src");
const bundle = await build({
  stdin: {
    contents: `export * from "./features/workbench/blendEditor.ts";`,
    resolveDir: sourceRoot,
    loader: "ts",
  },
  bundle: true,
  format: "cjs",
  platform: "node",
  write: false,
});
const module = { exports: {} };
new Function("module", "exports", "require", bundle.outputFiles[0].text)(
  module,
  module.exports,
  createRequire(import.meta.url),
);
const {
  blendScientificIdentity,
  editBlendRatio,
  filterBlendMaterials,
  parseBlendPaste,
  removeBlendMaterial,
  validatePasteRows,
} = module.exports;

const ref = (resourceId) => ({ resource_id: resourceId, revision: 1, digest: `sha256:${"1".repeat(64)}` });
const blend = {
  schema_version: "sparse-blend/v1",
  items: [
    { material_id: "RM-BAL", ratio: 70 },
    { material_id: "RM-A", ratio: 20 },
    { material_id: "RM-B", ratio: 10 },
  ],
  hoop_id: "HP-01",
  fill_ratio: 19,
  balance_material_id: "RM-BAL",
  scientific_master: ref("science"),
  commercial_catalog: ref("catalog"),
  design_space: ref("space"),
};
const context = {
  transform_id: "stage-a",
  scientific_master: blend.scientific_master,
  commercial_catalog: blend.commercial_catalog,
  design_space: {
    schema_version: "sparse-blend-design-space/v1",
    resource_id: "space",
    revision: 1,
    scientific_master: blend.scientific_master,
    commercial_catalog: blend.commercial_catalog,
    allowed_material_ids: ["RM-BAL", "RM-A", "RM-B"],
    material_bounds: [{ material_id: "RM-BAL", lower: 60, upper: 100 }],
    group_totals: [],
    group_cardinalities: [],
    selection_count: { minimum: 1, maximum: 20 },
    total: 100,
    tolerance: 1e-6,
    fixed_hoop_id: "HP-01",
    fixed_fill_ratio: 19,
    balance_material_id: "RM-BAL",
  },
  materials: [],
};

test("ordinary edits are absorbed only by the remainder line", () => {
  const result = editBlendRatio(blend, [], context, "RM-A", 25);
  assert.deepEqual(result.blend.items, [
    { material_id: "RM-BAL", ratio: 65 },
    { material_id: "RM-A", ratio: 25 },
    { material_id: "RM-B", ratio: 10 },
  ]);
  assert.equal(result.message, "");
});

test("remainder lower bound leaves the requested row invalid without moving another row", () => {
  const result = editBlendRatio(blend, [], context, "RM-A", 40);
  assert.deepEqual(result.blend.items, [
    { material_id: "RM-BAL", ratio: 60 },
    { material_id: "RM-A", ratio: 40 },
    { material_id: "RM-B", ratio: 10 },
  ]);
  assert.match(result.message, /ほかの行は変更していません/);
  assert.equal(result.blend.items.reduce((sum, item) => sum + item.ratio, 0), 110);
});

test("locking the remainder changes no scientific row except the edited one", () => {
  const result = editBlendRatio(blend, ["RM-BAL"], context, "RM-A", 25);
  assert.equal(result.blend.items[0].ratio, 70);
  assert.equal(result.blend.items[1].ratio, 25);
  assert.match(result.message, /lock中/);
});

test("deleting a row does not bypass a locked remainder", () => {
  const result = removeBlendMaterial(blend, "RM-A", ["RM-BAL"]);
  assert.equal(result.blend.items.find((item) => item.material_id === "RM-BAL").ratio, 70);
  assert.match(result.message, /lock中/);
});

test("commercial revisions and editor locks are excluded from Stage A identity", () => {
  const changedCatalog = {
    ...blend,
    commercial_catalog: { ...blend.commercial_catalog, revision: 99 },
  };
  assert.equal(blendScientificIdentity(blend), blendScientificIdentity(changedCatalog));
});

const materials = [
  { material_id: "RM-A", name: "低炭素鉄粉", group: "鉄粉", material_type: "鉄粉", main_components: ["Fe", "C"], procurement: "常用" },
  { material_id: "RM-B", name: "試作ニッケル", group: "純金属粉", material_type: "Ni粉", main_components: ["Ni"], procurement: "試作限定" },
  { material_id: "RM-C", name: "旧マンガン", group: "合金鉄", material_type: "FeMn", main_components: ["Mn", "Fe"], procurement: "廃止予定" },
];

test("paste import reports unknown, duplicate, and malformed rows independently", () => {
  const rows = parseBlendPaste("RM-X\t2\nRM-B\tabc\nRM-B\t3\nRM-A", materials, ["RM-A"]);
  assert.deepEqual(rows.map((row) => row.error), [
    "未知の原料コードです",
    "貼り付け内で重複しています",
    "貼り付け内で重複しています",
    "原料コードと比率の2列が必要です",
  ]);
  const corrected = validatePasteRows([
    { row: 1, materialId: "RM-B", ratioText: "2" },
    { row: 2, materialId: "RM-C", ratioText: "3" },
  ], materials, ["RM-A"]);
  assert.deepEqual(corrected.map((row) => row.error), ["", ""]);
});

test("catalog search includes main components and hides retired materials by default", () => {
  assert.deepEqual(filterBlendMaterials(materials, "Ni", "", "", false).map((item) => item.material_id), ["RM-B"]);
  assert.deepEqual(filterBlendMaterials(materials, "Mn", "", "", false), []);
  assert.deepEqual(filterBlendMaterials(materials, "Mn", "", "", true).map((item) => item.material_id), ["RM-C"]);
});
