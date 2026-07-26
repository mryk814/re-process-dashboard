import type { components } from "../generated/api-types";

export type CandidateProvenance = NonNullable<components["schemas"]["Candidate"]["provenance"]>;

export function provenanceLabel(provenance: CandidateProvenance): string {
  switch (provenance.source_kind) {
    case "lineage": return `工程系譜 ${provenance.source_ref.entity_key}`;
    case "screening": return `範囲探索 ${provenance.source_ref.run_id.slice(0, 8)} / 点 ${provenance.source_ref.point_id}`;
    case "copy": return `候補コピー ${provenance.source_ref.candidate_id.slice(0, 8)}`;
    case "blend_optimization": return `配合逆算 ${provenance.source_ref.method} / 基準 ${provenance.source_ref.baseline_candidate_id.slice(0, 8)}`;
    case "decision_activity": return `検討案 ${provenance.source_ref.run_id.slice(0, 8)} / 基準 ${provenance.source_ref.base_candidate_id.slice(0, 8)}`;
    case "snapshot": return `保存済み予測 ${provenance.source_ref.snapshot_id.slice(0, 8)}`;
    case "direct": return "直接入力";
    case "manual": return "既存の手入力";
  }
}
