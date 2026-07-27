import type { ApiChainEvaluation } from "../../shared/api/workbench-api";
import { chainEvaluationPresentation } from "./chainEvaluationPresentation";

type Props = {
  evaluation: ApiChainEvaluation;
  stagePath: string;
};

const familyLabels: Record<string, string> = {
  tensile: "引張",
  charpy: "シャルピー",
  corrosion: "腐食",
};

const metric = (value: number) => value.toLocaleString("ja-JP", {
  maximumFractionDigits: Math.abs(value) < 0.1 ? 4 : 2,
});

export function ChainEvaluationPanel({ evaluation, stagePath }: Props) {
  const { report } = evaluation;
  const presentation = chainEvaluationPresentation(evaluation, stagePath);
  return <section className="chain-evaluation-panel" aria-labelledby="chain-evaluation-title">
    <header className="chain-evaluation-header">
      <div>
        <span className="overline">CHAIN EVALUATION</span>
        <h3 id="chain-evaluation-title">段単体と通しを分けて評価</h3>
      </div>
      <span className="chain-evaluation-folds">
        {report.split.folds}-fold · {report.split.group_key}
      </span>
    </header>
    <div className="chain-evaluation-kinds">
      <div>
        <strong>{presentation.stageOnlyLabel}</strong>
        <span>{presentation.stageOnlyDescription}</span>
      </div>
      <div>
        <strong>{presentation.endToEndLabel}</strong>
        <span>学習側はinner OOF、評価側はouter-trainだけで作った上流予測を入力</span>
      </div>
    </div>
    <div className="chain-evaluation-table-wrap">
      <table className="chain-evaluation-table">
        <thead>
          <tr>
            <th rowSpan={2}>特性</th>
            <th rowSpan={2}>評価母集団</th>
            <th colSpan={2}>段単体</th>
            <th colSpan={2}>通し</th>
          </tr>
          <tr>
            <th>RMSE</th>
            <th>MAE</th>
            <th>RMSE</th>
            <th>MAE</th>
          </tr>
        </thead>
        <tbody>
          {report.targets.map((target) => <tr key={target.target}>
            <th>
              <strong>{target.label}</strong>
              <span>{familyLabels[target.observation_family] ?? target.observation_family}</span>
            </th>
            <td>
              <strong>n={target.observations.toLocaleString("ja-JP")}</strong>
              <span>{target.split_groups.toLocaleString("ja-JP")} group</span>
              <small>{target.cohort}</small>
            </td>
            <td>{metric(target.stage_only.rmse)} <small>{target.unit}</small></td>
            <td>{metric(target.stage_only.mae)} <small>{target.unit}</small></td>
            <td>{metric(target.end_to_end.rmse)} <small>{target.unit}</small></td>
            <td>{metric(target.end_to_end.mae)} <small>{target.unit}</small></td>
          </tr>)}
        </tbody>
      </table>
    </div>
    <footer className="chain-evaluation-footer">
      <p>
        欠測を一括除外せず、特性ごとの利用可能な観測で評価しています。
        二つの値は同じ outer split・同じ尺度ですが、一つの精度には合成しません。
      </p>
      <details>
        <summary>評価方法と固定identity</summary>
        <dl>
          <div><dt>MAE</dt><dd>{report.metric_definitions.mae}</dd></div>
          <div><dt>RMSE</dt><dd>{report.metric_definitions.rmse}</dd></div>
          <div><dt>分割</dt><dd>{report.split.strategy} / {report.split.assignment_policy}</dd></div>
          <div><dt>評価成果物</dt><dd title={evaluation.artifact_digest}>{evaluation.artifact_digest.slice(0, 20)}…</dd></div>
          <div><dt>Chain Revision</dt><dd>{evaluation.chain_revision_id} / <span title={evaluation.chain_revision_digest}>{evaluation.chain_revision_digest.slice(0, 20)}…</span></dd></div>
        </dl>
      </details>
    </footer>
  </section>;
}
