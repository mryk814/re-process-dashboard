import { useEffect, useMemo, useState } from "react";

import { fromApiCandidate } from "../candidates";
import {
  workbenchApi,
  type ApiCandidate,
  type ApiCandidateInput,
  type ApiChainGraph,
  type ApiPredictionGraphCandidateInput,
  type ApiPredictionGraphDecisionOutputActual,
  type ApiPredictionGraphExecution,
  type ApiPredictionGraphSnapshot,
} from "../../shared/api/workbench-api";
import { BlendEditorPanel } from "./BlendEditorPanel";
import {
  isPredictionGraphActualWritable,
  resolvePredictionGraphActualOutputId,
} from "./predictionGraphActualState";

type ExecutionResource =
  | { status: "loading" }
  | { status: "not_run" }
  | { status: "error"; message: string }
  | { status: "ready"; data: ApiPredictionGraphExecution };

type Props = {
  projectId: string;
  initialCandidateId?: string;
  requestedSnapshotId?: string;
  registerNavigationGuard: (guard: () => Promise<boolean>) => () => void;
  onCandidateSelected: (candidateId: string) => void;
  onSnapshotSelected: (snapshotId: string | undefined) => void;
  onOpenInspector: (candidateId: string) => void;
};

const candidateInput = (candidate: ApiCandidate): ApiCandidateInput => ({
  name: candidate.name,
  inputs: candidate.inputs,
  blend: candidate.blend,
  editor_state: candidate.editor_state,
  blend_validation: candidate.blend_validation,
  provenance: candidate.provenance,
  input_missing_kinds: candidate.input_missing_kinds,
});

const prettyValue = (value: unknown) => {
  if (typeof value === "number") return value.toLocaleString("ja-JP", { maximumFractionDigits: 4 });
  if (typeof value === "string") return value;
  return JSON.stringify(value);
};

function valueAt(candidate: ApiCandidateInput, path: string): number | string | undefined {
  if (path === "blend") return undefined;
  const [group, key] = path.split(".");
  if (group === "process" || group === "composition") return candidate.inputs[group][key];
  if (group === "categorical") return candidate.inputs.categorical?.[key];
  return undefined;
}

function withValue(candidate: ApiCandidateInput, path: string, value: number | string): ApiCandidateInput {
  const next = structuredClone(candidate);
  const [group, key] = path.split(".");
  if (group === "process" || group === "composition") next.inputs[group][key] = Number(value);
  if (group === "categorical") next.inputs.categorical = { ...next.inputs.categorical, [key]: String(value) };
  return next;
}

export function PredictionGraphDecisionWorkspace({
  projectId,
  initialCandidateId,
  requestedSnapshotId,
  registerNavigationGuard,
  onCandidateSelected,
  onSnapshotSelected,
  onOpenInspector,
}: Props) {
  const [candidates, setCandidates] = useState<ApiCandidate[]>([]);
  const [inputDefinitions, setInputDefinitions] = useState<ApiPredictionGraphCandidateInput[]>([]);
  const [graph, setGraph] = useState<ApiChainGraph | null>(null);
  const [selectedId, setSelectedId] = useState(initialCandidateId ?? "");
  const [draft, setDraft] = useState<ApiCandidateInput | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saveState, setSaveState] = useState<"idle" | "saving" | "conflict">("idle");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [executions, setExecutions] = useState<Record<string, ExecutionResource>>({});
  const [snapshots, setSnapshots] = useState<ApiPredictionGraphSnapshot[]>([]);
  const [actuals, setActuals] = useState<ApiPredictionGraphDecisionOutputActual[]>([]);
  const [snapshotId, setSnapshotId] = useState(requestedSnapshotId ?? "");
  const [actualOutputId, setActualOutputId] = useState("");
  const [actualMean, setActualMean] = useState("");
  const [actualUnit, setActualUnit] = useState("");
  const [measurementId, setMeasurementId] = useState("");
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");

  const selected = candidates.find((candidate) => candidate.id === selectedId) ?? null;
  const selectedResource = selected ? executions[selected.id] : undefined;
  const selectedExecution = selectedResource?.status === "ready" ? selectedResource.data : null;
  const selectedSnapshot = snapshots.find((item) => item.snapshot_id === snapshotId) ?? null;
  const actualWritable = isPredictionGraphActualWritable(selected, selectedSnapshot);
  const graphDefinition = graph?.definition.schema_version === "prediction-graph-definition/v1"
    ? graph.definition
    : null;
  const outputDefinitions = graphDefinition?.decision_outputs ?? [];
  const outputIds = selectedSnapshot?.terminal_outputs
    .filter((item) => item.status === "latest")
    .map((item) => item.output_id) ?? [];
  const missingRequiredOutputs = selectedExecution?.terminal_outputs
    .filter((item) => item.required_for_complete_result && item.status !== "latest")
    .map((item) => item.output_id) ?? [];

  useEffect(() => registerNavigationGuard(async () => (
    !dirty || window.confirm("保存していないGraph候補の変更を破棄して移動しますか？")
  )), [dirty, registerNavigationGuard]);

  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (dirty) event.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const loadExecution = async (candidateId: string, signal?: AbortSignal): Promise<ExecutionResource> => {
    try {
      const data = await workbenchApi.predictionGraphExecutionResource(projectId, candidateId, signal);
      return data ? { status: "ready", data } : { status: "not_run" };
    } catch (cause) {
      const text = cause instanceof Error ? cause.message : "実行結果を取得できませんでした";
      return { status: "error", message: text };
    }
  };

  const refresh = async (preferredCandidateId?: string, signal?: AbortSignal) => {
    const loaded = await workbenchApi.listCandidates(projectId, false, signal);
    if (signal?.aborted) return;
    setCandidates(loaded);
    const resolvedId = preferredCandidateId && loaded.some((item) => item.id === preferredCandidateId)
      ? preferredCandidateId
      : loaded.some((item) => item.id === selectedId)
        ? selectedId
        : loaded[0]?.id ?? "";
    setSelectedId(resolvedId);
    if (resolvedId) onCandidateSelected(resolvedId);
    setExecutions(Object.fromEntries(loaded.map((item) => [item.id, { status: "loading" }])));
    const resources = await Promise.all(loaded.map(async (candidate) => (
      [candidate.id, await loadExecution(candidate.id, signal)] as const
    )));
    if (signal?.aborted) return;
    setExecutions(Object.fromEntries(resources));
  };

  useEffect(() => {
    const controller = new AbortController();
    void Promise.all([
      workbenchApi.predictionGraphCandidateInputs(projectId, controller.signal),
      workbenchApi.chainGraph(projectId, controller.signal),
    ]).then(([inputs, nextGraph]) => {
      if (controller.signal.aborted) return;
      setInputDefinitions(inputs);
      setGraph(nextGraph);
    }).catch((cause) => {
      if (!controller.signal.aborted) setMessage(cause instanceof Error ? cause.message : "Graph契約を読み込めませんでした");
    });
    void refresh(initialCandidateId, controller.signal).catch((cause) => {
      if (controller.signal.aborted) return;
      setMessage(cause instanceof Error ? cause.message : "候補を読み込めませんでした");
    });
    return () => controller.abort();
    // Project identity is the reload boundary.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  useEffect(() => {
    if (initialCandidateId && initialCandidateId !== selectedId && candidates.some((item) => item.id === initialCandidateId)) {
      setSelectedId(initialCandidateId);
    }
  }, [candidates, initialCandidateId, selectedId]);

  useEffect(() => {
    if (!selected) {
      setDraft(null);
      setSnapshots([]);
      setActuals([]);
      return;
    }
    setDraft(candidateInput(selected));
    setDirty(false);
    setSaveState("idle");
    setFieldErrors({});
    setSnapshots([]);
    setActuals([]);
    setSnapshotId("");
    setActualOutputId("");
    setActualMean("");
    setActualUnit("");
    setMeasurementId("");
    let cancelled = false;
    void Promise.all([
      workbenchApi.listPredictionGraphSnapshots(projectId, selected.id),
      workbenchApi.listPredictionGraphActuals(projectId, selected.id),
    ]).then(([nextSnapshots, nextActuals]) => {
      if (cancelled) return;
      setSnapshots(nextSnapshots);
      setActuals(nextActuals);
      const preferred = requestedSnapshotId && nextSnapshots.some((item) => item.snapshot_id === requestedSnapshotId)
        ? requestedSnapshotId
        : nextSnapshots[0]?.snapshot_id ?? "";
      setSnapshotId(preferred);
    }).catch((cause) => {
      if (!cancelled) setMessage(cause instanceof Error ? cause.message : "証拠履歴を読み込めませんでした");
    });
    return () => {
      cancelled = true;
    };
  }, [projectId, requestedSnapshotId, selected]);

  const run = async (label: string, operation: () => Promise<void>) => {
    setBusy(label);
    setMessage("");
    try {
      await operation();
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : `${label}に失敗しました`);
    } finally {
      setBusy("");
    }
  };

  const selectCandidate = (candidateId: string) => {
    if (dirty && !window.confirm("保存していない変更を破棄して候補を切り替えますか？")) return;
    setSelectedId(candidateId);
    onCandidateSelected(candidateId);
  };

  const validate = () => {
    const errors: Record<string, string> = {};
    if (!draft) return errors;
    for (const field of inputDefinitions) {
      if (!field.editable || field.kind !== "number") continue;
      const value = valueAt(draft, field.candidate_path);
      if (typeof value !== "number" || !Number.isFinite(value)) errors[field.input_id] = "有限の数値を入力してください";
      else if (field.allowed_range && (value < field.allowed_range.min || value > field.allowed_range.max)) {
        errors[field.input_id] = `${field.allowed_range.min}〜${field.allowed_range.max}の範囲で入力してください`;
      }
    }
    setFieldErrors(errors);
    return errors;
  };

  const stageOutputMeta = (sourceStageId: string, sourceOutputKey: string) => (
    selectedExecution?.stages.find((stage) => stage.stage_id === sourceStageId)
      ?.output_definitions.find((output) => output.key === sourceOutputKey)
  );
  const snapshotOutputUnit = (outputId: string) => {
    const definition = outputDefinitions.find((item) => item.output_id === outputId);
    if (!definition || !selectedSnapshot) return "";
    return selectedSnapshot.stages
      .find((stage) => stage.stage_id === definition.source_stage_id)
      ?.output_definitions.find((output) => output.key === definition.source_output_key)
      ?.unit ?? "";
  };
  useEffect(() => {
    setActualOutputId((current) => (
      resolvePredictionGraphActualOutputId(current, selectedSnapshot)
    ));
  }, [selectedSnapshot]);

  useEffect(() => {
    setActualUnit(actualOutputId ? snapshotOutputUnit(actualOutputId) : "");
    // Unit is derived from the selected immutable Snapshot's Stage contract.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [actualOutputId, selectedSnapshot, graphDefinition]);

  return <main className="page-panel graph-decision-workspace">
    <header className="page-intro">
      <div>
        <span className="overline">GRAPH DECISION WORKSPACE</span>
        <h2>候補とDecision Outputを一つの面で比較</h2>
        <p>候補条件を保存して明示実行し、固定Snapshotに対する実測だけを記録します。</p>
      </div>
      {selected && <button type="button" onClick={() => onOpenInspector(selected.id)}>Graph inspector</button>}
    </header>
    {message && <div className="connection-banner" role="alert"><strong>{message}</strong></div>}

    <section className="graph-decision-layout">
      <aside className="graph-decision-candidates" aria-label="候補一覧">
        <h3>候補</h3>
        {candidates.map((candidate) => {
          const resource = executions[candidate.id];
          const status = resource?.status === "ready" ? resource.data.status
            : resource?.status === "error" ? "読込失敗"
              : resource?.status === "loading" ? "読込中" : "未実行";
          return <button type="button" className={candidate.id === selectedId ? "active" : ""} key={candidate.id} onClick={() => selectCandidate(candidate.id)}>
            <strong>{candidate.name}</strong><small>r{candidate.revision} · {status}</small>
          </button>;
        })}
        <button type="button" disabled={Boolean(busy)} onClick={() => void run("候補追加", async () => {
          const starter = await workbenchApi.predictionGraphStarterCandidate(projectId);
          const created = await workbenchApi.createPredictionGraphCandidate(projectId, starter);
          await refresh(created.id);
          setMessage("公開Graph契約から基準候補を追加しました。");
        })}>＋ 基準候補を追加</button>
      </aside>

      <div className="graph-decision-main">
        {selected && draft && <>
          <section className="graph-decision-card">
            <div className="graph-decision-card-heading">
              <div><span className="overline">CANDIDATE INPUT</span><h3>{selected.name}</h3></div>
              <div className="graph-decision-actions">
                {dirty && <span role="status">未保存</span>}
                {saveState === "conflict" && <button type="button" onClick={() => void refresh(selected.id)}>最新revisionを再読込</button>}
                <button type="button" disabled={!dirty || Boolean(busy)} onClick={() => void run("保存", async () => {
                  if (Object.keys(validate()).length) return;
                  setSaveState("saving");
                  try {
                    const result = await workbenchApi.updatePredictionGraphCandidate(projectId, selected.id, { ...draft, expected_revision: selected.revision });
                    if (result.status === "conflict") {
                      setSaveState("conflict");
                      setMessage("別の更新が先に保存されました。draftを保持したまま最新revisionを確認できます。");
                      return;
                    }
                    await refresh(result.candidate.id);
                    setDirty(false);
                    setSaveState("idle");
                    setMessage("候補条件を保存しました。実行はまだ行っていません。");
                  } catch (cause) {
                    setSaveState("idle");
                    throw cause;
                  }
                })}>{saveState === "saving" ? "保存中…" : "保存"}</button>
                <button type="button" className="primary-button" disabled={dirty || Boolean(busy)} onClick={() => void run("実行", async () => {
                  setExecutions((current) => ({ ...current, [selected.id]: { status: "loading" } }));
                  try {
                    const result = await workbenchApi.executePredictionGraph(projectId, selected.id, selected.revision, `graph-workspace-${crypto.randomUUID()}`);
                    setExecutions((current) => ({ ...current, [selected.id]: { status: "ready", data: result } }));
                    setMessage("Prediction Graphを実行しました。");
                  } catch (cause) {
                    setExecutions((current) => ({ ...current, [selected.id]: { status: "error", message: cause instanceof Error ? cause.message : "実行に失敗しました" } }));
                    throw cause;
                  }
                })}>実行</button>
              </div>
            </div>
            <div className="graph-input-groups">
              {(["design_variable", "scenario_context"] as const).map((role) => <fieldset key={role}>
                <legend>{role === "design_variable" ? "設計変数" : "評価context（探索変数ではありません）"}</legend>
                {inputDefinitions.filter((field) => field.role === role && field.kind !== "sparse_blend").map((field) => <label key={field.input_id}>
                  <span>{field.label} {field.unit && <small>{field.unit}</small>}</span>
                  {field.kind === "number"
                    ? <input type="number" value={Number(valueAt(draft, field.candidate_path) ?? 0)} disabled={!field.editable} min={field.allowed_range?.min} max={field.allowed_range?.max} step="any" onChange={(event) => {
                      setDraft(withValue(draft, field.candidate_path, Number(event.target.value)));
                      setDirty(true);
                    }} />
                    : <select value={String(valueAt(draft, field.candidate_path) ?? "")} disabled={!field.editable} onChange={(event) => {
                      setDraft(withValue(draft, field.candidate_path, event.target.value));
                      setDirty(true);
                    }}>{field.choices.map((choice) => <option key={choice}>{choice}</option>)}</select>}
                  <small>影響: {field.affected_output_ids.join(" / ")} · 保存後に該当branchがstaleになります</small>
                  {fieldErrors[field.input_id] && <strong className="field-error" role="alert">{fieldErrors[field.input_id]}</strong>}
                </label>)}
              </fieldset>)}
            </div>
            {inputDefinitions.some((field) => field.kind === "sparse_blend") && <BlendEditorPanel
              projectId={projectId}
              candidate={fromApiCandidate({ ...selected, ...draft })}
              chainMode
              onBlend={(_candidateId, blend) => {
                setDraft({ ...draft, blend });
                setDirty(true);
              }}
              onLocks={(_candidateId, lockedMaterialIds) => {
                setDraft({ ...draft, editor_state: { ...draft.editor_state, locked_material_ids: lockedMaterialIds } });
                setDirty(true);
              }}
            />}
          </section>

          <section className="graph-decision-card">
            <div className="graph-decision-card-heading">
              <div><span className="overline">DECISION OUTPUT</span><h3>候補比較</h3></div>
              <button type="button" disabled={selectedExecution?.status !== "complete" || Boolean(busy)} onClick={() => void run("Snapshot保存", async () => {
                const created = await workbenchApi.createPredictionGraphSnapshot(projectId, selected.id, selected.revision);
                setSnapshots((current) => [created, ...current]);
                setSnapshotId(created.snapshot_id);
                onSnapshotSelected(created.snapshot_id);
                setMessage("現在のPrediction Graph結果をSnapshotとして固定しました。");
              })}>Snapshotを固定</button>
            </div>
            {selectedExecution && selectedExecution.status !== "complete" && <p className="field-error" role="status">
              Snapshotはrequired Decision Outputがすべてlatestのcomplete実行だけ固定できます。
              {missingRequiredOutputs.length > 0 && <> 不足: {missingRequiredOutputs.join(" / ")}</>}
            </p>}
            {selectedResource?.status === "error" && <div className="connection-banner" role="alert">
              <strong>実行結果を取得できませんでした</strong><span>{selectedResource.message}</span>
              <button type="button" onClick={() => void loadExecution(selected.id).then((resource) => setExecutions((current) => ({ ...current, [selected.id]: resource })))}>再試行</button>
            </div>}
            <div className="graph-output-table-wrap"><table>
              <thead><tr><th>Decision Output</th>{candidates.map((candidate) => <th key={candidate.id}>{candidate.name}</th>)}</tr></thead>
              <tbody>{outputDefinitions.map((definition) => {
                const meta = stageOutputMeta(definition.source_stage_id, definition.source_output_key);
                return <tr key={definition.output_id}>
                  <th>
                    <strong>{definition.label}</strong>
                    <small>{definition.group} · {definition.role} · {definition.required_for_complete_result ? "required" : "optional"}</small>
                    <small>{definition.source_stage_id}.{definition.source_output_key} · {meta?.unit ?? definition.evidence?.unit_or_scale ?? "単位未記録"}</small>
                    {definition.evidence && <small>{definition.evidence.evidence_kind} · production利用: {definition.evidence.production_use === "prohibited" ? "不可" : definition.evidence.production_use}</small>}
                  </th>
                  {candidates.map((candidate) => {
                    const resource = executions[candidate.id];
                    const output = resource?.status === "ready" ? resource.data.terminal_outputs.find((item) => item.output_id === definition.output_id) : null;
                    return <td key={candidate.id} className={output?.status === "latest" ? "latest" : ""}>
                      {output?.status === "latest" ? <><strong>{prettyValue(output.value)}</strong><small>{meta?.unit ?? definition.evidence?.unit_or_scale ?? ""}</small></>
                        : output ? <><strong>{output.status}</strong>{output.error && <small>{output.error}</small>}{output.blocked_by_stage_ids.length > 0 && <small>blocked: {output.blocked_by_stage_ids.join(", ")}</small>}</>
                          : resource?.status === "error" ? "読込失敗" : resource?.status === "loading" ? "読込中" : "未実行"}
                    </td>;
                  })}
                </tr>;
              })}</tbody>
            </table></div>
          </section>

          <section className="graph-decision-card">
            <div className="graph-decision-card-heading">
              <div><span className="overline">ACTUAL & HISTORY</span><h3>固定予測との照合</h3></div>
              <select aria-label="Snapshot履歴" value={snapshotId} onChange={(event) => {
                setSnapshotId(event.target.value);
                onSnapshotSelected(event.target.value || undefined);
              }}><option value="">Snapshotなし</option>{snapshots.map((item) => <option key={item.snapshot_id} value={item.snapshot_id}>
                {new Date(item.created_at).toLocaleString("ja-JP")} · candidate r{item.identity.candidate_revision} · {item.snapshot_id.slice(0, 8)}
              </option>)}</select>
            </div>
            {selectedSnapshot && !actualWritable && <p className="field-error" role="status">このSnapshotは過去のcandidate revisionです。履歴は参照できますが、新しいActualは現在revisionのSnapshotへ記録してください。</p>}
            <div className="graph-actual-form">
              <label>Output<select value={actualOutputId} disabled={!actualWritable} onChange={(event) => {
                const outputId = event.target.value;
                setActualOutputId(outputId);
                setActualUnit(snapshotOutputUnit(outputId));
              }}><option value="">選択</option>{outputIds.map((outputId) => <option key={outputId}>{outputId}</option>)}</select></label>
              <label>実測値<input type="number" disabled={!actualWritable} value={actualMean} onChange={(event) => setActualMean(event.target.value)} /></label>
              <label>単位<input value={actualUnit} readOnly aria-readonly="true" /></label>
              <label>測定ID<input value={measurementId} disabled={!actualWritable} onChange={(event) => setMeasurementId(event.target.value)} /></label>
              <button type="button" className="primary-button" disabled={!actualWritable || !actualOutputId || !actualMean || !actualUnit || !measurementId || Boolean(busy)} onClick={() => void run("Actual保存", async () => {
                const created = await workbenchApi.createPredictionGraphActual(projectId, selected.id, {
                  snapshot_id: snapshotId, output_id: actualOutputId, mean: Number(actualMean), std: 0, replicates: 1,
                  unit: actualUnit, measurement_id: measurementId, context: {}, note: "", measured_at: null,
                });
                setActuals((current) => [created, ...current]);
                setActualMean(""); setMeasurementId("");
                setMessage("実測を固定Prediction Snapshotへ記録しました。");
              })}>Actualを記録</button>
            </div>
            <div className="graph-actual-history">{actuals.map((actual) => <article key={actual.actual_id}>
              <strong>{actual.output_id}: {prettyValue(actual.mean)} {actual.unit}</strong>
              <span>予測 {prettyValue(actual.prediction_value)} · {actual.measurement_id}</span>
              <small>candidate r{actual.candidate_revision} · snapshot {actual.snapshot_id.slice(0, 8)}</small>
            </article>)}{actuals.length === 0 && <p>記録済みActualはありません。</p>}</div>
          </section>
        </>}
      </div>
    </section>
  </main>;
}
