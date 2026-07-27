export function collectTrainingTargetFields(
  steps: ReadonlyArray<{ kind: string; fields?: string[] }>,
): string[] {
  return [...new Set(
    steps
      .filter((step) => step.kind === "target_eligibility_v1")
      .flatMap((step) => step.fields ?? []),
  )];
}

export function trainingRecipeIdForRevision(
  curationRunId: string | undefined,
  runs: ReadonlyArray<{ id: string; recipe_id: string }>,
): string | undefined {
  return runs.find((run) => run.id === curationRunId)?.recipe_id;
}
