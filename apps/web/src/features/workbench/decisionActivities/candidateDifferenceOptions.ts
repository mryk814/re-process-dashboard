import type { CandidateViewModel } from "../../candidates";

export type CandidateDifferenceOption = {
  key: string;
  candidateId: string;
  revision: number;
  label: string;
  kind: "history" | "candidate";
};

function optionKey(candidateId: string, revision: number): string {
  return `${candidateId}@${revision}`;
}

export function candidateDifferenceOptions(
  candidate: CandidateViewModel,
  candidates: CandidateViewModel[],
): CandidateDifferenceOption[] {
  const history = Array.from(
    { length: Math.max(candidate.raw.revision - 1, 0) },
    (_, index) => candidate.raw.revision - index - 1,
  ).map((revision) => ({
    key: optionKey(candidate.id, revision),
    candidateId: candidate.id,
    revision,
    label: `この候補の過去版 r${revision}`,
    kind: "history" as const,
  }));
  const others = candidates
    .filter((item) => item.id !== candidate.id)
    .map((item) => ({
      key: optionKey(item.id, item.raw.revision),
      candidateId: item.id,
      revision: item.raw.revision,
      label: `${item.label}（r${item.raw.revision}）`,
      kind: "candidate" as const,
    }));
  return [...history, ...others];
}
