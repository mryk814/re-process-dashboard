import type { ApiChainEvaluation } from "../../shared/api/workbench-api";

export function chainEvaluationPresentation(
  evaluation: ApiChainEvaluation,
  stagePath: string,
) {
  const terminalStage = evaluation.report.stages.at(-1)?.stage_id ?? "終端Stage";
  return {
    stageOnlyLabel: `段単体 ${terminalStage}`,
    stageOnlyDescription: `上流Stageの実測値を${terminalStage}へ入力`,
    endToEndLabel: `通し ${stagePath}`,
  };
}
