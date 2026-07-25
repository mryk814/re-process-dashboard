import type { components } from "../../generated/api-types";

export type SparseBlend = components["schemas"]["SparseBlend"];
export type BlendEditorContext = components["schemas"]["BlendEditorContext"];
export type BlendEditorMaterial = components["schemas"]["BlendEditorMaterial"];

export type BlendEditResult = {
  blend: SparseBlend;
  message: string;
};

export type PasteRow = {
  row: number;
  materialId: string;
  ratioText: string;
  error: string;
};

const roundRatio = (value: number) => Math.round(value * 1_000_000) / 1_000_000;

export function sameRevisionRef(
  left: components["schemas"]["RevisionRef"],
  right: components["schemas"]["RevisionRef"],
) {
  return left.resource_id === right.resource_id
    && left.revision === right.revision
    && left.digest === right.digest;
}

export function compatibleBlendContext(blend: SparseBlend, context: BlendEditorContext) {
  return sameRevisionRef(blend.scientific_master, context.scientific_master)
    && sameRevisionRef(blend.commercial_catalog, context.commercial_catalog)
    && blend.design_space.resource_id === context.design_space.resource_id
    && blend.design_space.revision === context.design_space.revision;
}

export function blendScientificIdentity(blend: SparseBlend) {
  return JSON.stringify({
    items: [...blend.items]
      .filter((item) => item.ratio !== 0)
      .sort((left, right) => left.material_id.localeCompare(right.material_id))
      .map((item) => [item.material_id, item.ratio]),
    hoop: blend.hoop_id,
    fill: blend.fill_ratio,
    scientific: blend.scientific_master,
  });
}

function lowerBound(context: BlendEditorContext, materialId: string) {
  return context.design_space.material_bounds
    .find((bound) => bound.material_id === materialId)?.lower ?? 0;
}

export function editBlendRatio(
  blend: SparseBlend,
  lockedMaterialIds: string[],
  context: BlendEditorContext,
  materialId: string,
  ratio: number,
): BlendEditResult {
  const previous = blend.items.find((item) => item.material_id === materialId);
  if (!previous || !Number.isFinite(ratio)) return { blend, message: "有限の配合比を入力してください" };
  const items = blend.items.map((item) => item.material_id === materialId
    ? { ...item, ratio: roundRatio(ratio) }
    : item);
  if (materialId === blend.balance_material_id) {
    return { blend: { ...blend, items }, message: "" };
  }
  const balanceIndex = items.findIndex((item) => item.material_id === blend.balance_material_id);
  if (balanceIndex < 0 || lockedMaterialIds.includes(blend.balance_material_id)) {
    return {
      blend: { ...blend, items },
      message: "残部原料がlock中のため合計は自動調整していません",
    };
  }
  const delta = ratio - previous.ratio;
  const currentBalance = items[balanceIndex].ratio;
  const minimum = lowerBound(context, blend.balance_material_id);
  const requestedBalance = roundRatio(currentBalance - delta);
  if (requestedBalance >= minimum) {
    items[balanceIndex] = { ...items[balanceIndex], ratio: requestedBalance };
    return { blend: { ...blend, items }, message: "" };
  }
  items[balanceIndex] = { ...items[balanceIndex], ratio: minimum };
  return {
    blend: { ...blend, items },
    message: `残部原料は下限 ${minimum}% で止めました。ほかの行は変更していません`,
  };
}

export function addBlendMaterial(blend: SparseBlend, materialId: string): BlendEditResult {
  if (blend.items.some((item) => item.material_id === materialId)) {
    return { blend, message: `${materialId} は配合済みです` };
  }
  return {
    blend: { ...blend, items: [...blend.items, { material_id: materialId, ratio: 0 }] },
    message: "",
  };
}

export function replaceBlendMaterial(
  blend: SparseBlend,
  fromMaterialId: string,
  toMaterialId: string,
): BlendEditResult {
  if (fromMaterialId === blend.balance_material_id) {
    return { blend, message: "残部原料は置換できません" };
  }
  if (blend.items.some((item) => item.material_id === toMaterialId)) {
    return { blend, message: `${toMaterialId} は配合済みです` };
  }
  return {
    blend: {
      ...blend,
      items: blend.items.map((item) => item.material_id === fromMaterialId
        ? { ...item, material_id: toMaterialId }
        : item),
    },
    message: "",
  };
}

export function removeBlendMaterial(
  blend: SparseBlend,
  materialId: string,
  lockedMaterialIds: string[] = [],
): BlendEditResult {
  if (materialId === blend.balance_material_id) {
    return { blend, message: "残部原料は削除できません" };
  }
  const removed = blend.items.find((item) => item.material_id === materialId);
  const balance = blend.items.find((item) => item.material_id === blend.balance_material_id);
  if (!removed || !balance) return { blend, message: "" };
  if (lockedMaterialIds.includes(blend.balance_material_id)) {
    return {
      blend: {
        ...blend,
        items: blend.items.filter((item) => item.material_id !== materialId),
      },
      message: "残部原料がlock中のため削除分は自動調整していません",
    };
  }
  return {
    blend: {
      ...blend,
      items: blend.items
        .filter((item) => item.material_id !== materialId)
        .map((item) => item.material_id === blend.balance_material_id
          ? { ...item, ratio: roundRatio(item.ratio + removed.ratio) }
          : item),
    },
    message: "",
  };
}

export function validatePasteRows(
  rows: Array<Omit<PasteRow, "error">>,
  materials: BlendEditorMaterial[],
  existingMaterialIds: string[],
): PasteRow[] {
  const known = new Set(materials.map((item) => item.material_id));
  const existing = new Set(existingMaterialIds);
  const counts = new Map<string, number>();
  for (const row of rows) {
    const id = row.materialId.trim();
    if (id) counts.set(id, (counts.get(id) ?? 0) + 1);
  }
  return rows.map((row) => {
    const materialId = row.materialId.trim();
    const ratioText = row.ratioText.trim();
    const ratio = Number(ratioText);
    const error = !materialId || !ratioText
      ? "原料コードと比率の2列が必要です"
      : !known.has(materialId)
        ? "未知の原料コードです"
        : existing.has(materialId)
          ? "すでに配合にある原料です"
          : (counts.get(materialId) ?? 0) > 1
            ? "貼り付け内で重複しています"
            : !Number.isFinite(ratio)
              ? "比率を数値で入力してください"
              : "";
    return { ...row, materialId, ratioText, error };
  });
}

export function parseBlendPaste(
  text: string,
  materials: BlendEditorMaterial[],
  existingMaterialIds: string[],
) {
  const rows = text
    .split(/\r?\n/)
    .filter((line) => line.trim())
    .map((line, index) => {
      const cells = line.trim().split(/[\t,\s]+/);
      return {
        row: index + 1,
        materialId: cells[0] ?? "",
        ratioText: cells[1] ?? "",
      };
    });
  return validatePasteRows(rows, materials, existingMaterialIds);
}

export function filterBlendMaterials(
  materials: BlendEditorMaterial[],
  query: string,
  group: string,
  materialType: string,
  includeRetired: boolean,
) {
  const needle = query.trim().toLocaleLowerCase("ja");
  return materials.filter((material) => (
    (includeRetired || material.procurement !== "廃止予定")
    && (!group || material.group === group)
    && (!materialType || material.material_type === materialType)
    && (!needle || [
      material.material_id,
      material.name,
      material.group,
      material.material_type,
      ...material.main_components,
    ].some((value) => value.toLocaleLowerCase("ja").includes(needle)))
  ));
}
