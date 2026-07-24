import type { ApiModelPackageRef } from "../api/workbench-api";
import { modelPackageDecisionSummary } from "../dataLibraryPresentation";

export function ModelPackageDecisionCard({
  modelPackage,
}: {
  modelPackage: ApiModelPackageRef | undefined;
}) {
  const summary = modelPackageDecisionSummary(modelPackage);
  if (!summary) return null;
  return (
    <aside className="model-decision-card" aria-label={`${summary.label}の選択判断`}>
      <header>
        <strong>{summary.label}</strong>
        {summary.experimental && <span>試験モデル</span>}
      </header>
      <dl>
        <div><dt>使いどころ</dt><dd>{summary.useCase}</dd></div>
        <div><dt>学習単位</dt><dd>{summary.trainingUnit}</dd></div>
        <div><dt>不確かさ</dt><dd>{summary.uncertainty}</dd></div>
      </dl>
      <p>{summary.caution}</p>
    </aside>
  );
}
