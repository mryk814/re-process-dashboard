import { useEffect, useRef, useState } from "react";
import type { TaskDefinitionContract } from "../candidates";
import {
  workbenchApi,
  type ApiModelPackage,
  type ApiModelTrainingDataPage,
} from "../../shared/api/workbench-api";

type Stage = "curation" | "selected" | "features";

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
  const [loading, setLoading] = useState(false);
  const tableWrapRef = useRef<HTMLDivElement>(null);
  const limit = 25;

  useEffect(() => {
    setTarget(modelPackage.predictors[0]?.target ?? "");
    setStage("selected");
    setOffset(0);
    setPage(null);
    tableWrapRef.current?.scrollTo({ top: 0 });
  }, [modelPackage.id, projectId]);

  useEffect(() => {
    if (!open || !target) return;
    const controller = new AbortController();
    setError("");
    setLoading(true);
    workbenchApi.modelTrainingData(projectId, stage, target, offset, limit, controller.signal)
      .then((value) => {
        if (controller.signal.aborted) return;
        setPage((current) => {
          if (offset === 0 || !current || current.stage !== value.stage || current.target !== value.target) return value;
          return { ...value, offset: 0, rows: [...current.rows, ...value.rows] };
        });
      })
      .catch((cause) => {
        if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "学習データを取得できませんでした。");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [limit, offset, open, projectId, stage, target]);

  const resetRows = () => {
    setOffset(0);
    setPage(null);
    setError("");
    tableWrapRef.current?.scrollTo({ top: 0 });
  };
  const loadMoreOnScroll = () => {
    const element = tableWrapRef.current;
    if (!element || !page || loading || page.rows.length >= page.total) return;
    if (element.scrollHeight - element.scrollTop - element.clientHeight <= 80) {
      setOffset(page.rows.length);
    }
  };
  return <details className="model-training-data" open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>
      <span><b>学習データ</b><small>原値からモデル入力まで確認</small></span>
      {page && <em>{page.total.toLocaleString("ja-JP")}行 · {page.parent_conditions.toLocaleString("ja-JP")}条件</em>}
    </summary>
    <div className="training-data-controls">
      <label>目的変数
        <select value={target} onChange={(event) => { setTarget(event.target.value); resetRows(); }}>
          {modelPackage.predictors.map((predictor) => <option key={predictor.target} value={predictor.target}>
            {taskDefinition?.outputs.find((output) => output.key === predictor.target)?.label ?? predictor.target}
          </option>)}
        </select>
      </label>
      <div className="training-data-tabs" role="tablist" aria-label="学習データの段階">
        <button type="button" role="tab" aria-selected={stage === "curation"} className={stage === "curation" ? "active" : ""} onClick={() => { setStage("curation"); resetRows(); }}>原値と前処理</button>
        <button type="button" role="tab" aria-selected={stage === "selected"} className={stage === "selected" ? "active" : ""} onClick={() => { setStage("selected"); resetRows(); }}>採用された個々値</button>
        <button type="button" role="tab" aria-selected={stage === "features"} className={stage === "features" ? "active" : ""} onClick={() => { setStage("features"); resetRows(); }}>モデル入力特徴量</button>
      </div>
    </div>
    <p className="training-data-note">
      {stage === "curation"
        ? "元データは変更せず、Profileが解釈した値と採否理由を並べています。原値と正規化値が同じ場合も省略しません。"
        : stage === "selected"
        ? "目的変数に実測があり、学習条件を通過した行です。正規化・結合後、特徴量変換前の入力を表示します。"
        : page?.training_unit === "parent_condition_mean"
          ? "Feature Pipelineで変換後、同じ親工程条件の個々値を平均した実際のモデル入力です。個々値数も併記します。"
          : "Feature Pipelineで変換し、個々の観測ごとにモデルへ渡した数値を特徴量順に表示します。"}
    </p>
    {page && stage === "curation" && <div className="curation-summary" aria-label="前処理結果の集計">
      <div><small>元データ</small><strong>{page.curation_summary.source_rows.toLocaleString("ja-JP")}</strong><span>行</span></div>
      <div><small>入力を解釈可能</small><strong>{page.curation_summary.input_usable_rows.toLocaleString("ja-JP")}</strong><span>行</span></div>
      <div><small>注意あり</small><strong>{page.curation_summary.warning_rows.toLocaleString("ja-JP")}</strong><span>行</span></div>
      <div><small>隔離</small><strong>{page.curation_summary.quarantined_rows.toLocaleString("ja-JP")}</strong><span>行</span></div>
      <dl>{page.curation_summary.targets.map((item) => <div key={item.target}>
        <dt>{taskDefinition?.outputs.find((output) => output.key === item.target)?.label ?? item.target}</dt>
        <dd><b>{item.usable_rows.toLocaleString("ja-JP")}</b>行 · {item.source_groups.toLocaleString("ja-JP")}グループ</dd>
        {Object.keys(item.exclusion_reasons ?? {}).length > 0 && <details><summary>不採用理由</summary><ul>
          {Object.entries(item.exclusion_reasons ?? {}).map(([reason, count]) => <li key={reason}>{reason}<b>{count.toLocaleString("ja-JP")}行</b></li>)}
        </ul></details>}
      </div>)}</dl>
      {Object.keys(page.curation_summary.exclusion_reasons ?? {}).length > 0 && <details>
        <summary>隔離理由</summary>
        <ul>{Object.entries(page.curation_summary.exclusion_reasons ?? {}).map(([reason, count]) => <li key={reason}>
          <span>{reason}</span><b>{count.toLocaleString("ja-JP")}行</b>
        </li>)}</ul>
      </details>}
    </div>}
    {error && <p className="panel-error">{error}</p>}
    {page && !error ? <>
      <div className="training-data-table-wrap" ref={tableWrapRef} onScroll={loadMoreOnScroll}>
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
        <span>{page.total ? `${page.rows.length} / ${page.total}行を表示` : "0行"}</span>
        <span>{loading ? "続きを読み込み中…" : page.rows.length < page.total ? "下へスクロールして続きを表示" : "すべて表示しました"}</span>
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
