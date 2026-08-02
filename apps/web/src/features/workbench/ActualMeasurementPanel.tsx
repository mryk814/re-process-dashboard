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
import { formatPredictionPoint } from "../../shared/predictionPresentation";
import {
  actualDraftHasUserInput,
  emptyActualDraft,
  reconcileActualDraftRevision,
  type ActualDraft,
} from "./actualMeasurementDraftState";

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
  const ownerIdentity = `${projectId}:${candidate.id}`;
  const ownerIdentityRef = useRef(ownerIdentity);
  ownerIdentityRef.current = ownerIdentity;
  const formToggleRef = useRef<HTMLButtonElement>(null);
  const propertySelectRef = useRef<HTMLSelectElement>(null);
  const saveButtonRef = useRef<HTMLButtonElement>(null);
  const [data, setData] = useState<ApiPredictionVsActual | null>(null);
  const [draft, setDraft] = useState<ActualDraft>(() => emptyActualDraft(firstOutput));
  const [draftOwner, setDraftOwner] = useState(ownerIdentity);
  const [targetRevision, setTargetRevision] = useState(candidate.raw.revision);
  const [pendingRevision, setPendingRevision] = useState<number | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function load(signal?: AbortSignal) {
    const requestedIdentity = ownerIdentity;
    const result = await workbenchApi.predictionVsActual(projectId, candidate.id, signal);
    if (ownerIdentityRef.current === requestedIdentity) setData(result);
  }

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setSaving(false);
    setError("");
    setNotice("");
    setData(null);
    setDraft(emptyActualDraft(firstOutput));
    setDraftOwner(ownerIdentity);
    setTargetRevision(candidate.raw.revision);
    setPendingRevision(null);
    setFormOpen(false);
    void load(controller.signal)
      .catch((cause: unknown) => {
        if (!controller.signal.aborted && ownerIdentityRef.current === ownerIdentity) {
          setError(actualMeasurementErrorMessage(
            cause,
            "予測と実測の照合履歴を取得できませんでした。",
          ));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted && ownerIdentityRef.current === ownerIdentity) setLoading(false);
      });
    return () => controller.abort();
  }, [ownerIdentity, firstOutput]);

  const draftDirty = actualDraftHasUserInput(draft, firstOutput);
  useEffect(() => {
    if (draftOwner !== ownerIdentity || candidate.raw.revision === targetRevision) return;
    const next = reconcileActualDraftRevision(
      { targetRevision, pendingRevision },
      candidate.raw.revision,
      draftDirty,
    );
    setTargetRevision(next.targetRevision);
    setPendingRevision(next.pendingRevision);
  }, [
    candidate.raw.revision,
    draftDirty,
    draftOwner,
    ownerIdentity,
    pendingRevision,
    targetRevision,
  ]);

  const selectedOutput = outputByKey.get(draft.property);
  const targetKind = selectedOutput?.target_kind ?? "continuous";
  const mean = Number(draft.mean);
  const std = Number(draft.std);
  const replicates = Number(draft.replicates);
  // A single observation carries no measurable spread; ±0 would read as "no scatter".
  const singleMeasurement = replicates === 1;
  const semanticSelection = targetKind === "binary" || targetKind === "ordinal";
  const valid = Boolean(selectedOutput)
    && draft.mean.trim() !== ""
    && (semanticSelection || Number.isFinite(mean))
    && (targetKind !== "count" || (Number.isInteger(mean) && mean >= 0))
    && Number.isFinite(std)
    && std >= 0
    && Number.isInteger(replicates)
    && replicates >= 1
    && replicates <= 999;

  async function saveActual() {
    if (!selectedOutput || !valid || !ready || saving || pendingRevision !== null) return;
    const requestedIdentity = ownerIdentity;
    const requestedRevision = targetRevision;
    setSaving(true);
    setError("");
    setNotice("");
    const body: ApiActualMeasurementInput = {
      property: selectedOutput.key,
      mean: targetKind === "binary" || targetKind === "ordinal" ? null : mean,
      value: targetKind === "binary" || targetKind === "ordinal" ? draft.mean : null,
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
        requestedRevision,
        body,
      );
      if (ownerIdentityRef.current !== requestedIdentity) return;
      setDraft(emptyActualDraft(selectedOutput.key));
      setFormOpen(false);
      setPendingRevision(null);
      setTargetRevision(candidate.raw.revision);
      try {
        await load();
      } catch {
        if (ownerIdentityRef.current === requestedIdentity) {
          setNotice("実測と固定Snapshotは保存済みです。照合履歴だけを再読み込みしてください。");
        }
      }
    } catch (cause) {
      if (ownerIdentityRef.current === requestedIdentity) {
        setError(actualMeasurementErrorMessage(cause, "実測を登録できませんでした。"));
      }
    } finally {
      if (ownerIdentityRef.current === requestedIdentity) {
        setSaving(false);
        requestAnimationFrame(() => formToggleRef.current?.focus());
      }
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
          ref={formToggleRef}
          type="button"
          className="outline-button"
          aria-expanded={formOpen}
          disabled={saving}
          onClick={() => setFormOpen((open) => !open)}
        >
          {formOpen ? "入力を閉じる" : "実測を登録"}
        </button>
      </header>
      {formOpen && (
        <div className="actual-measurement-form">
          {pendingRevision !== null && <div className="actual-revision-warning" role="alert" tabIndex={0}>
            <strong>候補が編集版 {targetRevision} から {pendingRevision} へ更新されました</strong>
            <span>入力中の実測は保持しています。どの編集版の予測へ固定するかを選んでください。</span>
            <div>
              <button type="button" className="outline-button" disabled={saving} onClick={() => {
                setTargetRevision(candidate.raw.revision);
                setPendingRevision(null);
                requestAnimationFrame(() => saveButtonRef.current?.focus());
              }}>現在の編集版 {candidate.raw.revision} へ引き継ぐ</button>
              <button type="button" className="text-button danger" disabled={saving} onClick={() => {
                setDraft(emptyActualDraft(firstOutput));
                setTargetRevision(candidate.raw.revision);
                setPendingRevision(null);
                requestAnimationFrame(() => propertySelectRef.current?.focus());
              }}>入力を破棄</button>
            </div>
          </div>}
          <label>特性<select ref={propertySelectRef} disabled={saving} value={draft.property} onChange={(event) => setDraft((current) => ({ ...current, property: event.target.value }))}>{taskDefinition.outputs.map((output) => <option value={output.key} key={output.key}>{output.label}</option>)}</select></label>
          <label>実測値<span className="actual-value-field">{targetKind === "binary" ? <select aria-label="実測値" disabled={saving} value={draft.mean} onChange={(event) => setDraft((current) => ({ ...current, mean: event.target.value }))}><option value="">選択</option><option value={selectedOutput?.binary?.event_label ?? ""}>{selectedOutput?.binary?.event_label}</option><option value={selectedOutput?.binary?.non_event_label ?? ""}>{selectedOutput?.binary?.non_event_label}</option></select> : targetKind === "ordinal" ? <select aria-label="実測値" disabled={saving} value={draft.mean} onChange={(event) => setDraft((current) => ({ ...current, mean: event.target.value }))}><option value="">選択</option>{selectedOutput?.ordinal?.categories.map((category) => <option value={category} key={category}>{category}</option>)}</select> : <input aria-label="実測値" type="number" min={targetKind === "count" ? "0" : undefined} step={targetKind === "count" ? "1" : "any"} disabled={saving} value={draft.mean} onChange={(event) => setDraft((current) => ({ ...current, mean: event.target.value }))} />}<small>{selectedOutput?.unit}</small></span></label>
          <label>標準偏差<input aria-label="実測の標準偏差" type="number" min="0" step="any" disabled={saving || singleMeasurement} value={singleMeasurement ? "" : draft.std} placeholder={singleMeasurement ? "1点測定" : undefined} onChange={(event) => setDraft((current) => ({ ...current, std: event.target.value }))} />{singleMeasurement && <small>1点測定ではばらつきを記録しません</small>}</label>
          <label>反復数<input aria-label="実測の反復数" type="number" min="1" max="999" step="1" disabled={saving} value={draft.replicates} onChange={(event) => setDraft((current) => ({ ...current, replicates: event.target.value }))} /></label>
          <label>実験番号<input disabled={saving} value={draft.experimentNo} onChange={(event) => setDraft((current) => ({ ...current, experimentNo: event.target.value }))} /></label>
          <label>測定日<input type="date" disabled={saving} value={draft.measuredAt} onChange={(event) => setDraft((current) => ({ ...current, measuredAt: event.target.value }))} /></label>
          <label className="actual-note-field">メモ<input disabled={saving} value={draft.note} onChange={(event) => setDraft((current) => ({ ...current, note: event.target.value }))} /></label>
          <button ref={saveButtonRef} type="button" className="primary-button" disabled={!valid || !ready || saving || pendingRevision !== null} onClick={() => void saveActual()}>{saving ? "予測を固定して保存中…" : `編集版 ${targetRevision} の予測と実測を保存`}</button>
          {!ready && <small className="actual-form-hint">候補の入力を保存すると登録できます。</small>}
        </div>
      )}
      {notice && <p className="panel-notice" role="status">{notice}</p>}
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
              const actualValue = actual.value_label
                ?? (output?.target_kind === "binary"
                  ? actual.mean === 1 ? output.binary?.event_label : actual.mean === 0 ? output.binary?.non_event_label : undefined
                  : undefined)
                ?? formatValue(outputKey, actual.mean ?? 0);
              const difference = prediction && prediction.unit === actual.unit && prediction.target_kind !== "binary"
                ? actualDifference(actual.mean ?? 0, prediction.value)
                : null;
              const metadata = measurementMetadata(actual);
              return <tr key={actual.id}>
                <th>{output?.label ?? actual.property}<small>{actual.unit}</small></th>
                <td>{prediction ? <><b>{formatPredictionPoint(prediction, (value) => formatValue(outputKey, value))}</b><small>{prediction.unit}</small></> : <span className="empty-cell">保存済みsnapshotに予測なし</span>}</td>
                <td><b>{actualValue}</b><small>{actual.unit}{actual.std > 0 ? ` / ±${formatValue(outputKey, actual.std)}` : ""}</small></td>
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
