export type CandidateWorkbenchMode = "comparison" | "review" | "explore";

export function candidateInspectorDefaultCollapsed(mode: CandidateWorkbenchMode) {
  return mode !== "explore";
}
