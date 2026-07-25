import type {
  ApiBlendMaterial,
  ApiCandidate,
} from "../../shared/api/workbench-api";


export function blendCost(
  candidate: ApiCandidate,
  materials: ReadonlyMap<string, ApiBlendMaterial>,
) {
  return (candidate.blend?.items ?? []).reduce(
    (total, item) =>
      total
      + item.ratio
      * (materials.get(item.material_id)?.unit_price_yen_per_kg_core ?? 0)
      / 100,
    0,
  );
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
