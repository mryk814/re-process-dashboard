import { useEffect, useRef, useState } from "react";
import { candidateInputIdentity } from "../../shared/api/inferenceRequestCache";
import { workbenchApi, type ApiSimilarObservation } from "../../shared/api/workbench-api";
import { assessOutputValues } from "../../shared/outputPresentation";
import { CandidateAddButton } from "../../shared/ui/CandidateAddButton";
import { type CandidateViewModel as Candidate, type TaskOutputDefinition } from "../candidates";
import {
  emptyInferenceSurface,
  inferenceSurfaceStatus,
  rejectInferenceSurface,
  requestInferenceSurface,
  resolveInferenceSurface,
} from "./inferenceSurfaceState";

function formatNumber(value: number, digits = 0) {
  return value.toLocaleString("ja-JP", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function SimilarityEvidencePanel({
  projectId,
  candidate,
  outputs,
  available,
  ready,
  onAddCandidate,
}: {
  projectId: string;
  candidate: Candidate;
  outputs: TaskOutputDefinition[];
  available: boolean;
  ready: boolean;
  onAddCandidate: (entityKey: string) => Promise<boolean>;
}) {
  const [surface, setSurface] = useState(() => emptyInferenceSurface<ApiSimilarObservation[]>());
  const [addingKey, setAddingKey] = useState("");
  const [addedKeys, setAddedKeys] = useState<string[]>([]);
  const surfaceRef = useRef(surface);
  const inputIdentity = candidateInputIdentity(candidate.raw.inputs);
  const similarityScope = `${projectId}\u001f${candidate.id}\u001fsimilarity:6`;
  const identity = `${similarityScope}\u001f${candidate.raw.revision}\u001f${inputIdentity}`;
  useEffect(() => {
    const empty = emptyInferenceSurface<ApiSimilarObservation[]>();
    surfaceRef.current = empty;
    setSurface(empty);
    setAddedKeys([]);
    setAddingKey("");
  }, [candidate.id]);
  useEffect(() => {
    if (!available || !ready || candidate.raw.archived_at) return;
    const controller = new AbortController();
    const requested = requestInferenceSurface(surfaceRef.current, identity);
    surfaceRef.current = requested;
    setSurface(requested);
    void workbenchApi.similarCandidates(
      projectId,
      candidate.id,
      candidate.raw.revision,
      inputIdentity,
      6,
      controller.signal,
    ).then((loaded) => {
      if (controller.signal.aborted) return;
      const resolved = resolveInferenceSurface(surfaceRef.current, requested.requestSequence, identity, loaded);
      surfaceRef.current = resolved;
      setSurface(resolved);
    }).catch((cause) => {
      if (controller.signal.aborted) return;
      const rejected = rejectInferenceSurface(surfaceRef.current, requested.requestSequence, identity, cause);
      surfaceRef.current = rejected;
      setSurface(rejected);
    });
    return () => controller.abort();
  }, [available, candidate.id, candidate.raw.archived_at, candidate.raw.revision, identity, inputIdentity, projectId, ready]);
  const status = inferenceSurfaceStatus(surface);
  const similar = surface.currentIdentity?.startsWith(`${similarityScope}\u001f`) ? surface.data ?? [] : [];
  const processLabel = similar.find((item) => item.process_label)?.process_label ?? "工程履歴";
  const measuredOutputs = (item: ApiSimilarObservation) => outputs.flatMap((output) => {
    const summaryKey = [...(output.measurement_keys ?? []), output.key, output.label]
      .find((key) => item.repeat_summary?.[key]);
    const summary = summaryKey ? item.repeat_summary?.[summaryKey] : undefined;
    return summary ? [{ output, summary }] : [];
  });
  const add = async (entityKey: string) => {
    setAddingKey(entityKey);
    try {
      if (await onAddCandidate(entityKey)) setAddedKeys((current) => current.includes(entityKey) ? current : [...current, entityKey]);
    } finally {
      setAddingKey("");
    }
  };
  return (
    <section className="similar-evidence-panel">
      <div className="evidence-title">
        <div>
          <h2><span className="reference-data-kicker">参照データ</span>近い実測条件 <span>（モデル学習範囲とは別）</span></h2>
          <span className="similar-caption">このプロジェクトが参照するDataset内で、成分・工程・熱履歴が近い条件です</span>
        </div>
        {similar.length > 0 && <span className={`inference-surface-status ${status}`}>{status === "latest" ? "最新" : status === "refreshing" ? "更新中" : status === "stale" ? "旧revision・更新中" : "更新失敗・旧結果"}</span>}
      </div>
      {!available ? (
        <p className="empty-evidence">このタスクでは類似実験を利用できません。</p>
      ) : candidate.raw.archived_at ? (
        <p className="empty-evidence">archive済み候補では新しい根拠計算を行いません。</p>
      ) : !ready ? (
        <p className="empty-evidence">入力を保存後に近さを更新します。</p>
      ) : similar.length ? (
        <div className="similar-table-scroll"><table className="similar-table similar-summary-table">
          <thead><tr><th>距離</th><th>溶製成績書 key</th><th>{processLabel} key</th><th>実績値</th><th /></tr></thead>
          <tbody>{similar.map((item) => (
            <tr key={`${item.layer ?? "training"}-${item.parent_key}`}>
              <td className="similar-distance"><b>{item.distance.toFixed(2)}</b><span className="layer-chip historical">参照データ</span></td>
              <td className="similar-key">{item.melt_key ?? "—"}</td>
              <td className="similar-key">{item.process_key ?? item.parent_key}</td>
              <td><div className="similar-value-list"><small>{item.source || item.observation_id || "実績"}</small>{measuredOutputs(item).map(({ output, summary }) => { const assessment = assessOutputValues(output, [summary.mean], "実測値"); return <span className={assessment.implausible ? "implausible-output" : undefined} key={output.key} title={assessment.warning ?? `${output.label}: ${formatNumber(summary.mean, 1)} ± ${formatNumber(summary.std, 1)} ${output.unit} / n=${summary.n}`}><b>{output.key === "lambda" ? "λ" : output.key}</b><strong>{formatNumber(summary.mean, 1)}</strong>{assessment.implausible && <small className="output-warning-badge">⚠</small>}</span>; })}</div></td>
              <td className="similar-action-cell">
                <CandidateAddButton compact disabled={!item.process_key || addingKey === item.process_key || addedKeys.includes(item.process_key ?? "")} onClick={() => { if (item.process_key) void add(item.process_key); }}>
                  {addedKeys.includes(item.process_key ?? "") ? "追加済み" : addingKey === item.process_key ? "追加中…" : "実測から候補化"}
                </CandidateAddButton>
              </td>
            </tr>
          ))}</tbody>
        </table></div>
      ) : status === "error" ? (
        <p className="empty-evidence">類似実験を取得できませんでした。閉じて再度開くと再試行します。</p>
      ) : (
        <p className="empty-evidence">類似実験を取得しています。</p>
      )}
    </section>
  );
}
