import { useEffect, useRef, useState, type CSSProperties } from "react";
import { candidateInputIdentity } from "../../shared/api/inferenceRequestCache";
import {
  workbenchApi,
  type ApiLineageCandidateOption,
  type ApiSimilarObservation,
} from "../../shared/api/workbench-api";
import { assessOutputValues, measurementSpreadText } from "../../shared/outputPresentation";
import { CandidateAddButton } from "../../shared/ui/CandidateAddButton";
import { formatTaskNumber } from "../../shared/taskPresentation";
import { type CandidateViewModel as Candidate, type TaskDefinitionContract, type TaskOutputDefinition } from "../candidates";
import {
  emptyInferenceSurface,
  inferenceSurfaceStatus,
  rejectInferenceSurface,
  requestInferenceSurface,
  resolveInferenceSurface,
} from "./inferenceSurfaceState";
import { similarObservationRowKey } from "./similarObservationIdentity";

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
  taskDefinition,
  displayDecimalOverrides,
  available,
  targetSpecific,
  ready,
  onAddCandidate,
}: {
  projectId: string;
  candidate: Candidate;
  outputs: TaskOutputDefinition[];
  taskDefinition: TaskDefinitionContract | null;
  displayDecimalOverrides?: Record<string, number>;
  available: boolean;
  targetSpecific: boolean;
  ready: boolean;
  onAddCandidate: (
    entityKey: string,
    processKey?: string,
    meltKey?: string,
  ) => Promise<boolean>;
}) {
  const [surface, setSurface] = useState(() => emptyInferenceSurface<ApiSimilarObservation[]>());
  const [addingKey, setAddingKey] = useState("");
  const [addedChoiceKeys, setAddedChoiceKeys] = useState<string[]>([]);
  const [candidateOptions, setCandidateOptions] = useState<Record<string, ApiLineageCandidateOption[]>>({});
  const [selectedChoiceKeys, setSelectedChoiceKeys] = useState<Record<string, string>>({});
  const [choiceErrors, setChoiceErrors] = useState<Record<string, string>>({});
  const [selectedTarget, setSelectedTarget] = useState(outputs[0]?.key ?? "");
  const surfaceRef = useRef(surface);
  const inputIdentity = candidateInputIdentity(candidate.raw.inputs);
  const similarityScope = `${projectId}\u001f${candidate.id}\u001fsimilarity:${selectedTarget}:6`;
  const identity = `${similarityScope}\u001f${candidate.raw.revision}\u001f${inputIdentity}`;
  useEffect(() => {
    const empty = emptyInferenceSurface<ApiSimilarObservation[]>();
    surfaceRef.current = empty;
    setSurface(empty);
    setAddedChoiceKeys([]);
    setCandidateOptions({});
    setSelectedChoiceKeys({});
    setChoiceErrors({});
    setAddingKey("");
  }, [candidate.id]);
  useEffect(() => {
    if (!outputs.some((output) => output.key === selectedTarget)) {
      setSelectedTarget(outputs[0]?.key ?? "");
    }
  }, [outputs, selectedTarget]);
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
      selectedTarget || undefined,
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
  }, [available, candidate.id, candidate.raw.archived_at, candidate.raw.revision, identity, inputIdentity, projectId, ready, selectedTarget]);
  const status = inferenceSurfaceStatus(surface);
  const similar = surface.currentIdentity?.startsWith(`${similarityScope}\u001f`) ? surface.data ?? [] : [];
  const hasMeltKey = similar.some((item) => item.melt_key);
  const canAddCandidates = similar.some((item) => item.process_key);
  const processLabel = canAddCandidates
    ? similar.find((item) => item.process_label)?.process_label ?? "参照条件"
    : "観測キー";
  const visibleOutputs = targetSpecific ? outputs.filter((output) => output.key === selectedTarget) : outputs;
  const measurementForOutput = (item: ApiSimilarObservation, output: TaskOutputDefinition) => {
    const summaryKey = [...(output.measurement_keys ?? []), output.key, output.label]
      .find((key) => item.repeat_summary?.[key]);
    const summary = summaryKey ? item.repeat_summary?.[summaryKey] : undefined;
    const raw = item.outputs?.[output.key];
    return summary
      ? summary
      : typeof raw === "number"
        ? { mean: raw, std: 0, n: 1 }
        : null;
  };
  const isBinaryMeasurement = (output: TaskOutputDefinition) => (
    output.unit === "1"
    && output.plausibility_range?.min === 0
    && output.plausibility_range?.max === 1
  );
  const outputNumber = (value: number, output: TaskOutputDefinition) => taskDefinition
    ? formatTaskNumber(value, taskDefinition, `output.${output.key}`, displayDecimalOverrides)
    : formatNumber(value, 1);
  const choiceKey = (option: ApiLineageCandidateOption) => `${option.process_key}\u001f${option.melt_key}`;
  const add = async (
    entityKey: string,
    option: ApiLineageCandidateOption,
    rowKey: string,
  ) => {
    const key = choiceKey(option);
    setAddingKey(`${rowKey}\u001f${key}`);
    try {
      if (await onAddCandidate(entityKey, option.process_key, option.melt_key)) {
        setAddedChoiceKeys((current) => current.includes(key) ? current : [...current, key]);
      }
    } finally {
      setAddingKey("");
    }
  };
  const prepareCandidate = async (item: ApiSimilarObservation) => {
    const entityKey = item.process_key;
    if (!entityKey) return;
    const rowKey = similarObservationRowKey(item);
    setAddingKey(rowKey);
    setChoiceErrors((current) => ({ ...current, [rowKey]: "" }));
    try {
      const lineage = await workbenchApi.lineage(projectId, entityKey);
      const matchingProcess = (lineage.candidate_options ?? []).filter(
        (option) => option.process_key === entityKey,
      );
      const exact = item.melt_key
        ? matchingProcess.find((option) => option.melt_key === item.melt_key)
        : undefined;
      const options = exact ? [exact] : matchingProcess;
      if (options.length === 1) {
        await add(entityKey, options[0], rowKey);
        return;
      }
      if (options.length === 0) {
        setChoiceErrors((current) => ({
          ...current,
          [rowKey]: "この実測から引き継げる上流条件が見つかりません。",
        }));
        return;
      }
      setCandidateOptions((current) => ({ ...current, [rowKey]: options }));
      setSelectedChoiceKeys((current) => ({ ...current, [rowKey]: "" }));
    } catch (cause) {
      setChoiceErrors((current) => ({
        ...current,
        [rowKey]: cause instanceof Error
          ? cause.message
          : "候補にする上流条件を取得できませんでした。",
      }));
    } finally {
      setAddingKey("");
    }
  };
  return (
    <section className="similar-evidence-panel">
      <div className="evidence-title">
        <div>
          <h2><span className="reference-data-kicker">参照データ</span>近い実測条件 <span>（モデル学習範囲とは別）</span></h2>
          <span className="similar-caption">このプロジェクトが参照するデータセット内で、モデル入力が近い実測条件です</span>
        </div>
        <div className="similar-evidence-actions">
          {targetSpecific && outputs.length > 1 && <label>近さの基準
            <select value={selectedTarget} onChange={(event) => setSelectedTarget(event.target.value)}>
              {outputs.map((output) => <option key={output.key} value={output.key}>{output.label}</option>)}
            </select>
          </label>}
          {similar.length > 0 && <span className={`inference-surface-status ${status}`}>{status === "latest" ? "最新" : status === "refreshing" ? "更新中" : status === "stale" ? "旧revision・更新中" : "更新失敗・旧結果"}</span>}
        </div>
      </div>
      {!available ? (
        <p className="empty-evidence">このタスクでは類似実験を利用できません。</p>
      ) : candidate.raw.archived_at ? (
        <p className="empty-evidence">archive済み候補では新しい根拠計算を行いません。</p>
      ) : !ready ? (
        <p className="empty-evidence">入力を保存後に近さを更新します。</p>
      ) : similar.length ? (
        <div className="similar-table-scroll"><table className={`similar-table similar-summary-table${hasMeltKey ? "" : " no-melt-key"}`} style={{ "--similar-output-count": Math.max(visibleOutputs.length, 1) } as CSSProperties}>
          <thead><tr><th className="similar-distance-header">距離</th>{hasMeltKey && <th className="similar-key-header">溶製成績書</th>}<th className="similar-key-header">{processLabel}</th>{visibleOutputs.map((output) => <th className="similar-output-header" key={output.key}>{output.label}<small>{output.unit === "1" ? "" : output.unit}</small></th>)}{canAddCandidates && <th className="similar-action-header" />}</tr></thead>
          <tbody>{similar.map((item) => (
            <tr key={similarObservationRowKey(item)}>
              <td className="similar-distance"><b>{item.distance.toFixed(2)}</b><span className="layer-chip historical">参照データ</span></td>
              {hasMeltKey && <td><span className="similar-key" title={item.melt_key ?? undefined}>{item.melt_key ?? "—"}</span></td>}
              <td><span className="similar-key" title={item.process_key ?? item.parent_key}>{item.process_key ?? item.parent_key}</span></td>
              {visibleOutputs.map((output) => {
                const summary = measurementForOutput(item, output);
                if (!summary) return <td className="similar-output-cell empty-cell" key={output.key}>—</td>;
                const assessment = assessOutputValues(output, [summary.mean], "実測値");
                const binary = isBinaryMeasurement(output);
                const value = binary ? (summary.mean >= 0.5 ? "fail" : "pass") : outputNumber(summary.mean, output);
                const spread = measurementSpreadText(summary.std, summary.n, (amount) => outputNumber(amount, output));
                return <td className={`similar-output-cell${assessment.implausible ? " implausible-output" : ""}`} key={output.key} title={assessment.warning ?? (binary ? `${output.label}: ${value} / n=${summary.n}` : `${output.label}: ${value} ${output.unit} / ${spread.title}`)}><strong>{value}</strong>{!binary && <small>{spread.text}</small>}{assessment.implausible && <small className="output-warning-badge">⚠</small>}</td>;
              })}
              {canAddCandidates && <td className="similar-action-cell">
                {item.process_key && candidateOptions[similarObservationRowKey(item)]?.length ? (() => {
                  const entityKey = item.process_key;
                  const rowKey = similarObservationRowKey(item);
                  const options = candidateOptions[rowKey];
                  const selectedKey = selectedChoiceKeys[rowKey] ?? "";
                  const selectedOption = options.find((option) => choiceKey(option) === selectedKey);
                  const operationKey = selectedOption ? `${rowKey}\u001f${choiceKey(selectedOption)}` : "";
                  const added = selectedOption ? addedChoiceKeys.includes(choiceKey(selectedOption)) : false;
                  return <div className="similar-candidate-choice">
                    <label>上流条件
                      <select
                        aria-label={`${entityKey}の上流条件`}
                        value={selectedKey}
                        onChange={(event) => setSelectedChoiceKeys((current) => ({
                          ...current,
                          [rowKey]: event.target.value,
                        }))}
                      >
                        <option value="">選択してください</option>
                        {options.map((option) => <option key={choiceKey(option)} value={choiceKey(option)}>
                          {option.process_label} {option.process_key} / 成分 {option.melt_key}
                        </option>)}
                      </select>
                    </label>
                    <CandidateAddButton
                      compact
                      disabled={!selectedOption || addingKey === operationKey || added}
                      onClick={() => { if (selectedOption) void add(entityKey, selectedOption, rowKey); }}
                    >
                      {added ? "追加済み" : operationKey && addingKey === operationKey ? "追加中…" : "選んで候補化"}
                    </CandidateAddButton>
                  </div>;
                })() : <CandidateAddButton
                  compact
                  disabled={!item.process_key || addingKey.startsWith(similarObservationRowKey(item))}
                  onClick={() => void prepareCandidate(item)}
                >
                  {addingKey.startsWith(similarObservationRowKey(item)) ? "追加中…" : "実測から候補化"}
                </CandidateAddButton>}
                {choiceErrors[similarObservationRowKey(item)] && <small className="similar-choice-error" role="alert">{choiceErrors[similarObservationRowKey(item)]}</small>}
              </td>}
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
