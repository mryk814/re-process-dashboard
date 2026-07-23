import { useEffect, useState } from "react";
import type { TaskDefinitionContract } from "../candidates";
import {
  workbenchApi,
  type ApiModelPackage,
  type ApiModelTrainingDataPage,
} from "../../shared/api/workbench-api";

type Stage = "selected" | "features";

function displayValue(value: string | number | boolean | null | undefined) {
  if (value == null || value === "") return "—";
  if (typeof value === "number") return value.toLocaleString("ja-JP", { maximumFractionDigits: 6 });
  if (typeof value === "boolean") return value ? "はい" : "いいえ";
  return value;
}

export function ModelTrainingDataInspector({
  projectId,
  modelPackage,
  taskDefinition,
}: {
  projectId: string;
  modelPackage: ApiModelPackage;
  taskDefinition: TaskDefinitionContract | null;
}) {
  const [open, setOpen] = useState(false);
  const [stage, setStage] = useState<Stage>("selected");
  const [target, setTarget] = useState(modelPackage.predictors[0]?.target ?? "");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<ApiModelTrainingDataPage | null>(null);
  const [error, setError] = useState("");
  const limit = 25;

  useEffect(() => {
    setTarget(modelPackage.predictors[0]?.target ?? "");
    setStage("selected");
    setOffset(0);
    setPage(null);
  }, [modelPackage.id]);

  useEffect(() => {
    if (!open || !target) return;
    const controller = new AbortController();
    setError("");
    workbenchApi.modelTrainingData(projectId, stage, target, offset, limit, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setPage(value);
      })
      .catch((cause) => {
        if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "学習データを取得できませんでした。");
      });
    return () => controller.abort();
  }, [limit, offset, open, projectId, stage, target]);

  const last = page ? Math.min(page.offset + page.rows.length, page.total) : 0;
  return <details className="model-training-data" open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>
      <span><b>学習データ</b><small>採用行とモデル入力を確認</small></span>
      {page && <em>{page.total.toLocaleString("ja-JP")}行 · {page.parent_conditions.toLocaleString("ja-JP")}条件</em>}
    </summary>
    <div className="training-data-controls">
      <label>目的変数
        <select value={target} onChange={(event) => { setTarget(event.target.value); setOffset(0); }}>
          {modelPackage.predictors.map((predictor) => <option key={predictor.target} value={predictor.target}>
            {taskDefinition?.outputs.find((output) => output.key === predictor.target)?.label ?? predictor.target}
          </option>)}
        </select>
      </label>
      <div className="training-data-tabs" role="tablist" aria-label="学習データの段階">
        <button type="button" role="tab" aria-selected={stage === "selected"} className={stage === "selected" ? "active" : ""} onClick={() => { setStage("selected"); setOffset(0); }}>採用された個々値</button>
        <button type="button" role="tab" aria-selected={stage === "features"} className={stage === "features" ? "active" : ""} onClick={() => { setStage("features"); setOffset(0); }}>モデル入力特徴量</button>
      </div>
    </div>
    <p className="training-data-note">
      {stage === "selected"
        ? "目的変数に実測があり、学習条件を通過した行です。正規化・結合後、特徴量変換前の入力を表示します。"
        : page?.training_unit === "parent_condition_mean"
          ? "Feature Pipelineで変換後、同じ親工程条件の個々値を平均した実際のモデル入力です。個々値数も併記します。"
          : "Feature Pipelineで変換し、個々の観測ごとにモデルへ渡した数値を特徴量順に表示します。"}
    </p>
    {error && <p className="panel-error">{error}</p>}
    {page && !error ? <>
      <div className="training-data-table-wrap">
        <table className="training-data-table">
          <thead><tr>{page.columns.map((column) => <th key={column.key}>
            <small>{column.group}</small>{column.label}{column.unit && <span>{column.unit}</span>}
          </th>)}</tr></thead>
          <tbody>{page.rows.map((row) => <tr key={row.observation_id}>
            {page.columns.map((column) => {
              const value = row.values[column.key];
              return <td key={column.key} title={typeof value === "string" ? value : undefined}>{displayValue(value)}</td>;
            })}
          </tr>)}</tbody>
        </table>
      </div>
      <div className="training-data-footer">
        <span>{page.total ? `${page.offset + 1}–${last} / ${page.total}行` : "0行"}</span>
        <div>
          <button type="button" disabled={page.offset === 0} onClick={() => setOffset(Math.max(0, page.offset - limit))}>前へ</button>
          <button type="button" disabled={last >= page.total} onClick={() => setOffset(page.offset + limit)}>次へ</button>
        </div>
      </div>
      <details className="training-data-identity"><summary>再現情報</summary>
        <dl>
          <div><dt>Feature Pipeline</dt><dd>{page.feature_pipeline_id} · v{page.feature_pipeline_version}</dd></div>
          <div><dt>学習元データ</dt><dd>{page.source_data_digest}</dd></div>
          <div><dt>Feature Dataset</dt><dd>{page.feature_dataset_digest}</dd></div>
        </dl>
      </details>
    </> : !error && <p className="empty-evidence">学習データを組み立てています。</p>}
  </details>;
}
