import type { NavigationIntent } from "./navigation";
export { provenanceLabel, type CandidateProvenance } from "../shared/candidateProvenance";
import type { CandidateProvenance } from "../shared/candidateProvenance";

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
    case "blend_optimization":
      return {
        view: "candidates",
        projectId: provenance.source_ref.project_id,
        candidateId: provenance.source_ref.baseline_candidate_id,
      };
    case "decision_activity":
      return {
        view: "candidate-review",
        projectId,
        candidateId: provenance.source_ref.base_candidate_id,
        activityRunId: provenance.source_ref.run_id,
      };
    case "snapshot":
      return { view: "project", projectId, snapshotId: provenance.source_ref.snapshot_id };
    case "direct":
    case "manual":
      return null;
  }
}
