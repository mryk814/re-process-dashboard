export function decisionActivityIdentity(
  projectId: string,
  candidateId: string,
  candidateRevision: number,
): string {
  return `${projectId}\u001f${candidateId}\u001f${candidateRevision}`;
}

export function acceptsDecisionActivityResponse(
  currentIdentity: string,
  requestedIdentity: string,
): boolean {
  return currentIdentity === requestedIdentity;
}
