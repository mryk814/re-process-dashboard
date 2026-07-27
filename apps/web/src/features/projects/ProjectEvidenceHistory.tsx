import type { ReactNode } from "react";
import { provenanceLabel } from "../../shared/candidateProvenance";
import { formatPredictionPoint } from "../../shared/predictionPresentation";
import { assessOutputValues, assessPrediction, resolveOutputDefinition } from "../../shared/outputPresentation";
import { formatNumberAtDecimals, formatTaskNumber, orderedTaskEntries } from "../../shared/taskPresentation";
import { CandidateAddButton } from "../../shared/ui/CandidateAddButton";
import type { TaskDefinitionContract } from "../candidates";
import type {
  ApiChainSnapshot,
  ApiPreview,
  ApiProjectHistory,
} from "../../shared/api/workbench-api";
import {
  chainHistoryPredictions,
  projectEvidenceHistoryViewState,
  terminalHistoryStage,
  type ChainHistoryPrediction,
} from "./projectEvidenceHistoryState";

export function ProjectEvidenceHistory({
  subtitle,
  loading,
  error,
  empty,
  emptyMessage,
  onRetry,
  children,
}: {
  subtitle: string;
  loading: boolean;
  error: boolean;
  empty: boolean;
  emptyMessage: string;
  onRetry: () => void;
  children: ReactNode;
}) {
  return <section id="project-candidate-history" className="project-history-section">
    <div className="panel-title"><h3>候補と判断履歴</h3><span>{subtitle}</span></div>
    {error ? <div className="project-history-error" role="alert">
      <p>候補と判断履歴を取得できませんでした。保存済みのデータは失われていません。</p>
      <button type="button" className="outline-button" onClick={onRetry}>履歴を再取得</button>
    </div> : loading ? <p className="empty-evidence">履歴を読み込んでいます。</p>
      : empty ? <div className="project-empty-state"><p>{emptyMessage}</p></div>
        : children}
  </section>;
}

type HistoryCandidate = ApiProjectHistory["candidates"][number];
type ChainStage = ApiChainSnapshot["stages"][number];
type ChainOutputDefinition = ChainStage["output_definitions"][number];

export type ProjectEvidenceHistoryListProps = {
  subtitle: string;
  loading: boolean;
  error: boolean;
  emptyMessage: string;
  history: ApiProjectHistory | null;
  chainMode: boolean;
  currentPreviews: Record<string, ApiPreview>;
  taskDefinition: TaskDefinitionContract | null;
  displayDecimalOverrides?: Record<string, number>;
  disabled: boolean;
  restoringCandidateId: string;
  onRetry: () => void;
  onOpenCandidate: (candidateId: string) => void;
  onRestoreArchivedCandidate: (candidateId: string) => void | Promise<void>;
  onOpenSnapshot: (snapshotId: string) => void | Promise<void>;
  onRestoreSnapshot: (snapshotId: string) => void | Promise<void>;
  onOpenChainSnapshot: (snapshot: ApiChainSnapshot) => void;
};

const formatDate = (value: string) => new Date(value).toLocaleString("ja-JP");
const fallbackNumber = (value: number) => value.toLocaleString("ja-JP", {
  maximumFractionDigits: 1,
});

function formatChainOutput(
  prediction: ChainHistoryPrediction | undefined,
  definition: ChainOutputDefinition,
): string {
  if (typeof prediction?.value !== "number" || !Number.isFinite(prediction.value)) {
    return "利用不可";
  }
  const value = formatNumberAtDecimals(prediction.value, definition.display_decimals);
  const unit = definition.unit.trim();
  return `${value}${unit ? ` ${unit}` : ""}`;
}

function ChainEvidenceRows({
  item,
  onOpenChainSnapshot,
}: {
  item: HistoryCandidate;
  onOpenChainSnapshot: (snapshot: ApiChainSnapshot) => void;
}) {
  const snapshots = item.chain_snapshots ?? [];
  const variants = item.chain_analysis_variants ?? [];
  const distributionRuns = item.chain_distribution_runs ?? [];
  const hasEvidence = snapshots.length > 0
    || variants.length > 0
    || distributionRuns.length > 0;

  return <>
    <p className="history-muted">
      現在のChain条件です。固定済みの判断時点は下に時系列で残ります。
    </p>
    {snapshots.map((snapshot) => {
      const terminalStage = terminalHistoryStage(snapshot.stages);
      const predictions = chainHistoryPredictions(terminalStage?.result);
      return <div className="history-snapshot-row chain-history-row" key={snapshot.snapshot_id}>
        <span className="history-kind fixed">全Stageを固定</span>
        {item.decision?.snapshot_id === snapshot.snapshot_id
          && <span className="decision-snapshot-badge">採用判断</span>}
        <span>編集版 {snapshot.identity.candidate_revision}</span>
        <span>{formatDate(snapshot.created_at)}</span>
        <span className="history-predictions">
          {terminalStage?.output_definitions.length
            ? terminalStage.output_definitions.map((definition) => (
              <span key={definition.key}>
                {definition.label} {formatChainOutput(predictions[definition.key], definition)}
              </span>
            ))
            : "終端Stageの出力定義を確認できません"}
        </span>
        {item.decision?.snapshot_id === snapshot.snapshot_id
          && <span className="decision-note-inline">判断理由: {item.decision.note}</span>}
        <button
          type="button"
          className="outline-button"
          onClick={() => onOpenChainSnapshot(snapshot)}
        >
          詳細
        </button>
      </div>;
    })}
    {variants.map((variant) => {
      const comparison = snapshots.find(
        (snapshot) => snapshot.snapshot_id === variant.identity.comparison_snapshot_id,
      );
      const terminalStage = terminalHistoryStage(comparison?.stages ?? []);
      const predictions = chainHistoryPredictions(variant.stage_c_result);
      return <div
        className="history-snapshot-row chain-history-row actual-conditioned"
        key={variant.variant_id}
      >
        <span className="history-kind fixed">実測Bを条件にした予測</span>
        <span>編集版 {variant.identity.base_candidate_revision}</span>
        <span>{formatDate(variant.created_at)}</span>
        <span className="history-predictions">
          {terminalStage?.output_definitions.length
            ? terminalStage.output_definitions.map((definition) => (
              <span key={definition.key}>
                {definition.label} {formatChainOutput(predictions[definition.key], definition)}
              </span>
            ))
            : "比較Snapshotの出力定義を確認できません"}
        </span>
        <span className="history-actual">
          実測ID {variant.identity.actual_ids.join(", ")} · {variant.identity.coverage.length}項目
        </span>
        <small>通常のChain結果は置き換えません。</small>
      </div>;
    })}
    {distributionRuns.map((run) => {
      const comparison = snapshots.find((snapshot) => (
        snapshot.identity.candidate_revision === run.provenance.candidate_revision
        && snapshot.identity.chain_revision_digest === run.provenance.chain_revision_digest
      ));
      const terminalStage = terminalHistoryStage(comparison?.stages ?? []);
      const terminalDistribution = run.stages[run.stages.length - 1];
      return <div className="history-snapshot-row chain-history-row" key={run.run_id}>
        <span className="history-kind fixed">不確かさを伝播</span>
        <span>編集版 {run.provenance.candidate_revision}</span>
        <span>{formatDate(run.created_at)}</span>
        <span>
          {run.status === "completed" ? "完了" : "一部Stageは利用不可"}
          {" · "}seed {run.provenance.seed} · {run.provenance.sample_count}標本
        </span>
        <span className="history-predictions">
          {terminalStage?.output_definitions.length
            ? terminalStage.output_definitions.map((definition) => {
              const summary = terminalDistribution?.propagated_uncertainty?.[definition.key];
              return <span key={definition.key}>
                {definition.label} {summary
                  ? `${formatNumberAtDecimals(
                    summary.quantiles["0.05"],
                    definition.display_decimals,
                  )}–${formatNumberAtDecimals(
                    summary.quantiles["0.95"],
                    definition.display_decimals,
                  )}${definition.unit ? ` ${definition.unit}` : ""}`
                  : "伝播区間なし"}
              </span>;
            })
            : "固定Snapshotの出力定義を確認できません"}
        </span>
      </div>;
    })}
    {!hasEvidence && <div className="project-empty-inline">
      <span>
        全Stageを固定した記録はまだありません。Chain候補で「全Stageを固定」すると判断時点が残ります。
      </span>
    </div>}
  </>;
}

export function ProjectEvidenceHistoryList({
  subtitle,
  loading,
  error,
  emptyMessage,
  history,
  chainMode,
  currentPreviews,
  taskDefinition,
  displayDecimalOverrides,
  disabled,
  restoringCandidateId,
  onRetry,
  onOpenCandidate,
  onRestoreArchivedCandidate,
  onOpenSnapshot,
  onRestoreSnapshot,
  onOpenChainSnapshot,
}: ProjectEvidenceHistoryListProps) {
  const viewState = projectEvidenceHistoryViewState({
    loading,
    error,
    candidateCount: history?.candidates.length ?? 0,
  });
  const outputLabels = new Map(
    (taskDefinition?.outputs ?? []).map((output) => [output.key, output.label]),
  );
  const outputDefinition = (key: string) => resolveOutputDefinition(
    taskDefinition?.outputs ?? [],
    key,
  );
  const orderedPredictions = <T,>(values: Record<string, T>) => taskDefinition
    ? orderedTaskEntries(taskDefinition, values)
    : Object.entries(values);
  const formatOutputNumber = (key: string, value: number) => taskDefinition
    ? formatTaskNumber(
      value,
      taskDefinition,
      `output.${key}`,
      displayDecimalOverrides,
    )
    : fallbackNumber(value);

  return <ProjectEvidenceHistory
    subtitle={subtitle}
    loading={viewState === "loading"}
    error={viewState === "error"}
    empty={viewState === "empty"}
    emptyMessage={emptyMessage}
    onRetry={onRetry}
  >
    {viewState === "ready" && history && <div className="project-history-list">
      {history.candidates.map((item) => {
        const preview = currentPreviews[item.candidate.id];
        return <article className="project-history-card" key={item.candidate.id}>
          <header>
            <div>
              <strong>{item.candidate.name}</strong>
              {item.candidate.archived_at && <span className="muted-badge">archive</span>}
            </div>
            {item.candidate.archived_at
              ? <button
                type="button"
                className="outline-button"
                disabled={disabled || Boolean(restoringCandidateId)}
                onClick={() => void onRestoreArchivedCandidate(item.candidate.id)}
              >
                {restoringCandidateId === item.candidate.id ? "復元中…" : "候補へ戻す"}
              </button>
              : <button
                type="button"
                className="outline-button"
                disabled={disabled}
                onClick={() => onOpenCandidate(item.candidate.id)}
              >
                現在の候補を見る
              </button>}
          </header>
          <div className="history-current-row">
            <span className="history-kind current">現在</span>
            <span>編集版 {item.current.revision}</span>
            <span>{formatDate(item.current.updated_at)}</span>
            <span className={item.candidate.provenance?.source_kind === "lineage"
              ? "history-origin reference-data"
              : "history-origin"}
            >
              {item.candidate.provenance?.source_kind === "lineage"
                && <b>参照データ由来</b>}
              {item.candidate.provenance
                ? provenanceLabel(item.candidate.provenance)
                : "由来不明"}
            </span>
          </div>
          {chainMode
            ? <ChainEvidenceRows
              item={item}
              onOpenChainSnapshot={onOpenChainSnapshot}
            />
            : <>
              {preview
                ? <div className="history-preview">
                  <span>現在のpreview</span>
                  {orderedPredictions(preview.predictions).map(([key, value]) => {
                    const assessment = assessPrediction(outputDefinition(key), value);
                    return <strong
                      className={assessment.implausible ? "implausible-output" : undefined}
                      title={assessment.warning ?? undefined}
                      key={key}
                    >
                      {outputLabels.get(key) ?? key}{" "}
                      {formatPredictionPoint(
                        value,
                        (numberValue) => formatOutputNumber(key, numberValue),
                      )}
                      {assessment.implausible
                        && <small className="output-warning-badge">⚠ 物理範囲外</small>}
                    </strong>;
                  })}
                </div>
                : <p className="history-muted">
                  現在のpreviewは未計算です。候補比較を開くと必要な候補だけ計算します。
                </p>}
              {item.snapshots.length
                ? <div className="history-snapshots">
                  {item.snapshots.map((snapshot) => <div
                    className="history-snapshot-row"
                    key={snapshot.id}
                  >
                    <span className="history-kind fixed">固定した予測</span>
                    {item.decision?.snapshot_id === snapshot.id
                      && <span className="decision-snapshot-badge">採用判断</span>}
                    <span>編集版 {snapshot.candidate_revision ?? "不明（旧形式）"}</span>
                    <span>{formatDate(snapshot.created_at)}</span>
                    <span className="history-predictions">
                      {orderedPredictions(snapshot.prediction_summary).map(
                        ([key, value], index) => {
                          const assessment = assessPrediction(outputDefinition(key), value);
                          return <span
                            className={assessment.implausible
                              ? "implausible-output"
                              : undefined}
                            title={assessment.warning ?? undefined}
                            key={key}
                          >
                            {index > 0 && " / "}
                            {outputLabels.get(key) ?? key}{" "}
                            {formatPredictionPoint(
                              value,
                              (numberValue) => formatOutputNumber(key, numberValue),
                            )}
                            {assessment.implausible
                              && <small className="output-warning-badge">
                                ⚠ 物理範囲外
                              </small>}
                          </span>;
                        },
                      )}
                    </span>
                    {item.actuals
                      .filter((actual) => actual.snapshot_id === snapshot.id)
                      .map((actual) => {
                        const definition = outputDefinition(actual.property);
                        const assessment = assessOutputValues(
                          definition,
                          [actual.mean],
                          "実測値",
                        );
                        const key = definition?.key ?? actual.property;
                        return <span
                          className={`history-actual${
                            assessment.implausible ? " implausible-output" : ""
                          }`}
                          title={assessment.warning ?? undefined}
                          key={actual.id}
                        >
                          実測 {definition?.label
                            ?? outputLabels.get(actual.property)
                            ?? actual.property}{" "}
                          {formatOutputNumber(key, actual.mean)} ±{" "}
                          {formatOutputNumber(key, actual.std)}{" "}
                          {definition?.unit ?? actual.unit}
                          {actual.experiment_no ? ` / ${actual.experiment_no}` : ""}
                          {assessment.implausible
                            && <small className="output-warning-badge">
                              ⚠ 物理範囲外
                            </small>}
                        </span>;
                      })}
                    {item.decision?.snapshot_id === snapshot.id
                      && <span className="decision-note-inline">
                        判断理由: {item.decision.note}
                      </span>}
                    <button
                      type="button"
                      className="outline-button"
                      onClick={() => void onOpenSnapshot(snapshot.id)}
                    >
                      詳細
                    </button>
                    <CandidateAddButton
                      compact
                      disabled={disabled}
                      onClick={() => void onRestoreSnapshot(snapshot.id)}
                    >
                      新しい候補として複製
                    </CandidateAddButton>
                  </div>)}
                </div>
                : <div className="project-empty-inline">
                  <span>
                    固定した予測はありません。上の「現在の候補を見る」から詳細予測を保存すると判断時点が残ります。
                  </span>
                </div>}
            </>}
        </article>;
      })}
    </div>}
  </ProjectEvidenceHistory>;
}
