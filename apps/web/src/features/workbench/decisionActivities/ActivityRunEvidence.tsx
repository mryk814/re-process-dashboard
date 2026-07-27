import type { ApiDecisionActivityRun } from "../../../shared/api/workbench-api";

function shortDigest(value: string | null | undefined): string {
  return value ? value.slice(0, 12) : "記録なし";
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
    <span>結果</span>
    {runs.map((run, index) => {
      const selected = run.id === selectedId;
      const visibleLabel = index === 0
        ? `最新結果 · ${new Date(run.created_at).toLocaleString("ja-JP")}`
        : new Date(run.created_at).toLocaleString("ja-JP");
      return <button
        type="button"
        className={selected ? "active" : ""}
        aria-current={selected ? "true" : undefined}
        aria-label={`${index === 0 ? "最新結果" : `保存履歴 ${index}`}、${new Date(run.created_at).toLocaleString("ja-JP")}`}
        onClick={() => onSelectRun(run.id)}
        key={run.id}
      >{visibleLabel}</button>;
    })}
  </nav>;
}

export function ActivityRunProvenance({ run }: { run: ApiDecisionActivityRun }) {
  const provenance = run.provenance;
  const entries = [
    ["Activity", `${run.definition.label} ${provenance.activity_version || "記録なし"}`],
    ["作成日時", run.created_at ? new Date(run.created_at).toLocaleString("ja-JP") : "記録なし"],
    ["Candidate", `${provenance.candidate_id || "記録なし"} · 編集版 ${provenance.candidate_revision ?? "記録なし"}`],
    ["Task", `${provenance.task_id || "記録なし"} · ${shortDigest(provenance.task_contract_digest)}`],
    ["Model Package", shortDigest(provenance.model_package_digest)],
    ["Feature pipeline", shortDigest(provenance.feature_pipeline_digest)],
    ["入力", shortDigest(provenance.canonical_input_digest)],
    ["実行条件", shortDigest(provenance.parameters_digest)],
    ["Design Space", shortDigest(provenance.project_design_space_digest)],
    ["Objective", shortDigest(provenance.objective_definition_digest)],
  ] as const;

  return <details className="activity-run-provenance">
    <summary>この結果の再現情報</summary>
    <dl>
      {entries.map(([label, value]) => <div key={label}>
        <dt>{label}</dt>
        <dd>{value}</dd>
      </div>)}
    </dl>
    <small>Run ID {run.id || "記録なし"} · identity {shortDigest(run.semantic_identity)}</small>
  </details>;
}
