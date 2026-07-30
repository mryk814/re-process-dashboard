export type CandidateSection = "actuals";

export const candidateQuestionActions = [
  {
    activityId: "robustness-analysis-v1",
    title: "入力ばらつきに強いか",
    description: "条件が少しずれても予測が安定するか確かめる",
  },
  {
    activityId: "candidate-difference-v1",
    title: "2案の差は何が効いているか",
    description: "候補間の予測差を入力項目ごとに分けて見る",
  },
  {
    activityId: "counterfactual-target-reach-v1",
    title: "目標へ届くには何を変えるか",
    description: "目標条件へ近づく最小変更を探す",
  },
] as const;

export function candidateQuestionState(
  candidateId: string | undefined,
  blocked: boolean,
): Readonly<{ disabled: boolean; reason?: string }> {
  if (!candidateId) return { disabled: true, reason: "先に候補が必要です" };
  if (blocked) return { disabled: true };
  return { disabled: false };
}

const activityQuestionLabels = new Map<string, string>(
  candidateQuestionActions.map((item) => [item.activityId, item.title]),
);

export function activityQuestionLabel(activityId: string | undefined): string | undefined {
  return activityId ? activityQuestionLabels.get(activityId) : undefined;
}
