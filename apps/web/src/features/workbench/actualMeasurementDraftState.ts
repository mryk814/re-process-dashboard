export type ActualDraft = {
  property: string;
  mean: string;
  std: string;
  replicates: string;
  experimentNo: string;
  measuredAt: string;
  note: string;
};

export type ActualDraftRevisionState = {
  targetRevision: number;
  pendingRevision: number | null;
};

export const emptyActualDraft = (property: string): ActualDraft => ({
  property,
  mean: "",
  std: "0",
  replicates: "1",
  experimentNo: "",
  measuredAt: "",
  note: "",
});

export function actualDraftHasUserInput(draft: ActualDraft, firstOutput: string): boolean {
  const empty = emptyActualDraft(firstOutput);
  return Object.keys(empty).some((key) => (
    draft[key as keyof ActualDraft] !== empty[key as keyof ActualDraft]
  ));
}

export function reconcileActualDraftRevision(
  current: ActualDraftRevisionState,
  candidateRevision: number,
  dirty: boolean,
): ActualDraftRevisionState {
  if (candidateRevision === current.targetRevision) {
    return current.pendingRevision === null
      ? current
      : { ...current, pendingRevision: null };
  }
  return dirty
    ? { ...current, pendingRevision: candidateRevision }
    : { targetRevision: candidateRevision, pendingRevision: null };
}
