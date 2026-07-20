import type { components } from "../generated/api-types";
import type { NavigationIntent } from "./navigation";

export type CandidateProvenance = NonNullable<components["schemas"]["Candidate"]["provenance"]>;

export function provenanceLabel(provenance: CandidateProvenance): string {
  switch (provenance.source_kind) {
    case "lineage":
      return `工程系譜 ${provenance.source_ref.entity_key}`;
    case "screening":
      return `範囲探索 ${provenance.source_ref.run_id.slice(0, 8)} / 点 ${provenance.source_ref.point_id}`;
    case "copy":
      return `候補コピー ${provenance.source_ref.candidate_id.slice(0, 8)}`;
    case "snapshot":
      return `保存済み予測 ${provenance.source_ref.snapshot_id.slice(0, 8)}`;
    case "direct":
      return "直接入力";
    case "manual":
      return "既存の手入力";
  }
}

export function provenanceNavigation(
  provenance: CandidateProvenance,
  projectId: string,
): NavigationIntent | null {
  switch (provenance.source_kind) {
    case "lineage":
      return { view: "lineage", projectId, entityKey: provenance.source_ref.entity_key };
    case "screening":
      return { view: "explore", projectId, screeningRunId: provenance.source_ref.run_id };
    case "copy":
      return {
        view: "candidates",
        projectId: provenance.source_ref.project_id,
        candidateId: provenance.source_ref.candidate_id,
      };
    case "snapshot":
      return { view: "project", projectId, snapshotId: provenance.source_ref.snapshot_id };
    case "direct":
    case "manual":
      return null;
  }
}
