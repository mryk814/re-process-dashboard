export async function archiveAfterCandidateSettlement({
  active,
  settlePending,
  archive,
}: {
  active: boolean;
  settlePending: () => Promise<boolean>;
  archive: () => Promise<void>;
}): Promise<boolean> {
  if (active && !(await settlePending())) return false;
  await archive();
  return true;
}
