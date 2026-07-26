import { useEffect, useMemo, useRef, useState } from "react";
import {
  formatDisplayNumber,
  type CandidateViewModel,
  type DisplayDecimalOverrides,
  type TaskDefinitionContract,
} from "../candidates";
import {
  workbenchApi,
  type ApiActualMeasurementInput,
  type ApiPredictionVsActual,
} from "../../shared/api/workbench-api";
import {
  actualDifference,
  actualMeasurementErrorMessage,
  measurementMetadata,
  signedDifference,
} from "./actualMeasurementPresentation";

type ActualDraft = {
  property: string;
  mean: string;
  std: string;
  replicates: string;
  experimentNo: string;
  measuredAt: string;
  note: string;
};

const emptyDraft = (property: string): ActualDraft => ({
  property,
  mean: "",
  std: "0",
  replicates: "1",
  experimentNo: "",
  measuredAt: "",
  note: "",
});

export function ActualMeasurementPanel({
  projectId,
  candidate,
  taskDefinition,
  displayDecimalOverrides,
  ready,
}: {
  projectId: string;
  candidate: CandidateViewModel;
  taskDefinition: TaskDefinitionContract;
  displayDecimalOverrides?: DisplayDecimalOverrides;
  ready: boolean;
}) {
  const firstOutput = taskDefinition.outputs[0]?.key ?? "";
  const outputByKey = useMemo(
    () => new Map(taskDefinition.outputs.map((output) => [output.key, output])),
    [taskDefinition.outputs],
  );
  const identity = `${projectId}:${candidate.id}:${candidate.raw.revision}`;
  const identityRef = useRef(identity);
  identityRef.current = identity;
  const [data, setData] = useState<ApiPredictionVsActual | null>(null);
  const [draft, setDraft] = useState<ActualDraft>(() => emptyDraft(firstOutput));
  const [formOpen, setFormOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function load(signal?: AbortSignal) {
    const requestedIdentity = identity;
    const result = await workbenchApi.predictionVsActual(projectId, candidate.id, signal);
    if (identityRef.current === requestedIdentity) setData(result);
  }

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    setData(null);
    setDraft(emptyDraft(firstOutput));
    setFormOpen(false);
    void load(controller.signal)
      .catch((cause: unknown) => {
        if (!controller.signal.aborted && identityRef.current === identity) {
          setError(actualMeasurementErrorMessage(
            cause,
            "予測と実測の照合履歴を取得できませんでした。",
          ));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted && identityRef.current === identity) setLoading(false);
      });
    return () => controller.abort();
  }, [identity, firstOutput]);

  const selectedOutput = outputByKey.get(draft.property);
  const mean = Number(draft.mean);
  const std = Number(draft.std);
  const replicates = Number(draft.replicates);
  // A single observation carries no measurable spread; ±0 would read as "no scatter".
  const singleMeasurement = replicates === 1;
  const valid = Boolean(selectedOutput)
    && draft.mean.trim() !== ""
    && Number.isFinite(mean)
    && Number.isFinite(std)
    && std >= 0
    && Number.isInteger(replicates)
    && replicates >= 1
    && replicates <= 999;

  async function saveActual() {
    if (!selectedOutput || !valid || !ready || saving) return;
    const requestedIdentity = identity;
    setSaving(true);
    setError("");
    const body: ApiActualMeasurementInput = {
      property: selectedOutput.key,
      mean,
      std: singleMeasurement ? 0 : std,
      replicates,
      unit: selectedOutput.unit,
      experiment_no: draft.experimentNo.trim(),
      measured_at: draft.measuredAt || null,
      note: draft.note.trim(),
    };
    try {
      await workbenchApi.createActual(
        projectId,
        candidate.id,
        candidate.raw.revision,
        body,
      );
      if (identityRef.current !== requestedIdentity) return;
      await load();
      if (identityRef.current !== requestedIdentity) return;
      setDraft(emptyDraft(selectedOutput.key));
      setFormOpen(false);
    } catch (cause) {
      if (identityRef.current === requestedIdentity) {
        setError(actualMeasurementErrorMessage(cause, "実測を登録できませんでした。"));
      }
    } finally {
      if (identityRef.current === requestedIdentity) setSaving(false);
    }
  }

  const formatValue = (outputKey: string, value: number) =>
    formatDisplayNumber(value, taskDefinition, `output.${outputKey}`, displayDecimalOverrides);
  const comparisons = [...(data?.comparisons ?? [])].reverse();

  return (
    <section className="actual-measurement-panel" aria-label="予測と実測の照合">
      <header>
        <div>
          <span className="overline">PREDICTION / ACTUAL</span>
          <h2>予測と実測の照合 <small>{candidate.label}</small></h2>
        </div>
        <button
          type="button"
          className="outline-button"
          aria-expanded={formOpen}
          onClick={() => setFormOpen((open) => !open)}
        >
          {formOpen ? "入力を閉じる" : "実測を登録"}
        </button>
      </header>
      {formOpen && (
        <div className="actual-measurement-form">
          <label>特性<select value={draft.property} onChange={(event) => setDraft((current) => ({ ...current, property: event.target.value }))}>{taskDefinition.outputs.map((output) => <option value={output.key} key={output.key}>{output.label}</option>)}</select></label>
          <label>実測値<span className="actual-value-field"><input aria-label="実測値" type="number" step="any" value={draft.mean} onChange={(event) => setDraft((current) => ({ ...current, mean: event.target.value }))} /><small>{selectedOutput?.unit}</small></span></label>
          <label>標準偏差<input aria-label="実測の標準偏差" type="number" min="0" step="any" disabled={singleMeasurement} value={singleMeasurement ? "" : draft.std} placeholder={singleMeasurement ? "1点測定" : undefined} onChange={(event) => setDraft((current) => ({ ...current, std: event.target.value }))} />{singleMeasurement && <small>1点測定ではばらつきを記録しません</small>}</label>
          <label>反復数<input aria-label="実測の反復数" type="number" min="1" max="999" step="1" value={draft.replicates} onChange={(event) => setDraft((current) => ({ ...current, replicates: event.target.value }))} /></label>
          <label>実験番号<input value={draft.experimentNo} onChange={(event) => setDraft((current) => ({ ...current, experimentNo: event.target.value }))} /></label>
          <label>測定日<input type="date" value={draft.measuredAt} onChange={(event) => setDraft((current) => ({ ...current, measuredAt: event.target.value }))} /></label>
          <label className="actual-note-field">メモ<input value={draft.note} onChange={(event) => setDraft((current) => ({ ...current, note: event.target.value }))} /></label>
          <button type="button" className="primary-button" disabled={!valid || !ready || saving} onClick={() => void saveActual()}>{saving ? "予測を固定して保存中…" : "この編集版の予測と実測を保存"}</button>
          {!ready && <small className="actual-form-hint">候補の入力を保存すると登録できます。</small>}
        </div>
      )}
      {error && <p className="panel-error" role="alert">{error}</p>}
      {loading ? <p className="empty-evidence">照合履歴を読み込んでいます。</p> : comparisons.length === 0 ? <p className="empty-evidence">実測はまだありません。登録時に、この編集版の詳細予測も固定されます。</p> : (
        <div className="actual-comparison-scroll">
          <table className="actual-comparison-table">
            <thead><tr><th>特性</th><th>固定予測</th><th>実測</th><th>差（実測−予測）</th><th>測定metadata</th><th>固定した条件</th></tr></thead>
            <tbody>{comparisons.map((comparison) => {
              const actual = comparison.actual;
              const output = outputByKey.get(actual.property);
              const prediction = comparison.prediction.predictions[actual.property];
              const outputKey = output?.key ?? actual.property;
              const difference = prediction && prediction.unit === actual.unit
                ? actualDifference(actual.mean, prediction.value)
                : null;
              const metadata = measurementMetadata(actual);
              return <tr key={actual.id}>
                <th>{output?.label ?? actual.property}<small>{actual.unit}</small></th>
                <td>{prediction ? <><b>{formatValue(outputKey, prediction.value)}</b><small>{prediction.unit}</small></> : <span className="empty-cell">保存済みsnapshotに予測なし</span>}</td>
                <td><b>{formatValue(outputKey, actual.mean)}</b><small>{actual.unit}{actual.std > 0 ? ` / ±${formatValue(outputKey, actual.std)}` : ""}</small></td>
                <td className={difference == null ? "" : difference > 0 ? "actual-difference positive" : difference < 0 ? "actual-difference negative" : "actual-difference"}>{difference == null ? prediction ? "単位不一致" : "—" : <>{signedDifference(difference, (value) => formatValue(outputKey, value))}<small>{actual.unit}</small></>}</td>
                <td><span className="actual-metadata">{metadata.map((item, index) => <small key={`${index}:${item}`}>{item}</small>)}</span></td>
                <td><span className="actual-metadata"><small>編集版 {comparison.candidate_revision ?? "不明（旧形式）"}</small><small>{new Date(comparison.snapshot_created_at).toLocaleString("ja-JP")}</small><small title={comparison.provenance.package?.manifest_sha256}>{comparison.provenance.package?.id || "Package情報なし"}</small></span></td>
              </tr>;
            })}</tbody>
          </table>
        </div>
      )}
      <footer>表示する予測は登録時の固定snapshotです。現在の候補やPackageが変わっても自動更新しません。</footer>
    </section>
  );
}
