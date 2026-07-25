import type {
  ApiBlendMaterial,
  ApiCandidate,
} from "../../shared/api/workbench-api";


export function blendCost(
  candidate: ApiCandidate,
  materials: ReadonlyMap<string, ApiBlendMaterial>,
) {
  let total = 0;
  for (const item of candidate.blend?.items ?? []) {
    if (item.ratio === 0) continue;
    const price = materials.get(item.material_id)?.unit_price_yen_per_kg_core;
    if (price == null) return null;
    total += item.ratio * price / 100;
  }
  return total;
}

export function blendComparisonRows(
  candidates: ApiCandidate[],
  showAll: boolean,
) {
  const materialIds = Array.from(new Set(candidates.flatMap(
    (candidate) => candidate.blend?.items.map((item) => item.material_id) ?? [],
  ))).sort((left, right) => left.localeCompare(right, "ja", { numeric: true }));
  if (showAll) return materialIds;
  return materialIds.filter((materialId) => {
    const values = candidates.map((candidate) =>
      candidate.blend?.items.find((item) => item.material_id === materialId)?.ratio ?? 0
    );
    return new Set(values.map((value) => value.toFixed(9))).size > 1;
  });
}
