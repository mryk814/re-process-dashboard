import type { ApiDecisionActivityRun } from "../../../shared/api/workbench-api";

function recorded(value: string | number | null | undefined): string {
  return value === null || value === undefined || value === "" ? "記録なし" : String(value);
}

export function ActivityRunHistory({
  label,
  runs,
  activeRunId,
  onSelectRun,
}: {
  label: string;
  runs: ApiDecisionActivityRun[];
  activeRunId: string | null;
  onSelectRun: (runId: string | null) => void;
}) {
  if (runs.length === 0) return null;
  const selectedId = activeRunId ?? runs[0]?.id;

  return <nav className="activity-run-history" aria-label={label}>
    <span className="activity-run-history-title"><strong>保存結果</strong><small>{runs.length}件</small></span>
    <div className="activity-run-history-items">{runs.map((run, index) => {
        const selected = run.id === selectedId;
        const visibleLabel = index === 0
          ? `最新 · ${new Date(run.created_at).toLocaleString("ja-JP")}`
          : new Date(run.created_at).toLocaleString("ja-JP");
        return <button
          type="button"
          className={selected ? "active" : ""}
          aria-current={selected ? "true" : undefined}
          aria-label={`${index === 0 ? "最新結果" : `保存履歴 ${index}`}、${new Date(run.created_at).toLocaleString("ja-JP")}`}
          onClick={() => onSelectRun(run.id)}
          key={run.id}
        >{visibleLabel}</button>;
      })}</div>
  </nav>;
}

export function ActivityRunProvenance({ run }: { run: ApiDecisionActivityRun }) {
  const provenance = run.provenance;
  const model = provenance.model;
  const entries = [
    ["Activity", `${run.definition.label} ${provenance.activity_version || "記録なし"}`],
    ["作成日時", run.created_at ? new Date(run.created_at).toLocaleString("ja-JP") : "記録なし"],
    ["Candidate", `${provenance.candidate_id || "記録なし"} · 編集版 ${provenance.candidate_revision ?? "記録なし"}`],
    ["Task ID", recorded(provenance.task_id)],
    ["Task contract digest", recorded(provenance.task_contract_digest)],
    ["Model", model.model ? `${model.model.id} · v${model.model.version} · ${model.model.method}` : "記録なし"],
    ["Package", model.package ? `${model.package.id} · v${model.package.version}` : "記録なし"],
    ["Package manifest", recorded(model.package?.manifest_sha256)],
    ["Model Package digest", recorded(provenance.model_package_digest)],
    ["Feature pipeline", model.feature_pipeline
      ? `${model.feature_pipeline.id} · v${model.feature_pipeline.version}`
      : "記録なし"],
    ["Feature pipeline digest", recorded(model.feature_pipeline?.digest || provenance.feature_pipeline_digest)],
    ["Training data", model.training_data
      ? `${model.training_data.training_data_id} · ${model.training_data.feature_dataset_id}`
      : "記録なし"],
    ["Training source SHA-256", recorded(model.training_data?.source_sha256)],
    ["Training code revision", recorded(model.training_data?.training_code_revision)],
    ["Canonical Dataset Revision", recorded(model.source_lifecycle?.canonical_dataset_revision_id)],
    ["Canonical Dataset digest", recorded(model.source_lifecycle?.canonical_dataset_digest)],
    ["Training Snapshot", recorded(model.source_lifecycle?.training_snapshot_id)],
    ["Training Snapshot digest", recorded(model.source_lifecycle?.training_snapshot_digest)],
    ["Materialized training SHA-256", recorded(model.source_lifecycle?.materialized_training_sha256)],
    ["Canonical input digest", recorded(provenance.canonical_input_digest)],
    ["Parameters digest", recorded(provenance.parameters_digest)],
    ["Design Space digest", recorded(provenance.project_design_space_digest)],
    ["Objective digest", recorded(provenance.objective_definition_digest)],
  ] as const;

  return <details className="activity-run-provenance">
    <summary>この結果の再現情報</summary>
    <dl>
      {entries.map(([label, value]) => <div key={label}>
        <dt>{label}</dt>
        <dd><code>{value}</code></dd>
      </div>)}
    </dl>
    <small>Run ID <code>{recorded(run.id)}</code> · identity <code>{recorded(run.semantic_identity)}</code></small>
  </details>;
}
