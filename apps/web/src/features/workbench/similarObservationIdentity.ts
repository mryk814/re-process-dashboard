import type { ApiSimilarObservation } from "../../shared/api/workbench-api";

export function similarObservationRowKey(item: ApiSimilarObservation) {
  const observationIdentity = item.observation_id
    || [...(item.observation_ids ?? [])].sort().join(",")
    || [...(item.relation_context_ids ?? [])].sort().join(",");
  return [
    item.layer ?? "training",
    item.source_scope ?? "",
    item.source,
    item.parent_key,
    item.melt_key ?? "",
    item.process_key ?? "",
    observationIdentity,
  ].join("\u001f");
}
