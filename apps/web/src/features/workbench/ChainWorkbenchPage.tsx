import { useEffect, useMemo, useRef, useState } from "react";
import {
  workbenchApi,
  type ApiActualConditionedVariant,
  type ApiCandidate,
  type ApiCandidateInput,
  type ApiChainCandidateContract,
  type ApiChainExecution,
  type ApiChainSnapshot,
  type ApiSubsystemAvailability,
} from "../../shared/api/workbench-api";
import {
  fromApiCandidate,
  getCandidateInputValue,
  LatestSaveQueue,
  rebaseChangedFields,
  setCandidateInputValue,
} from "../candidates";
import { BlendEditorPanel } from "./BlendEditorPanel";
import { ChainUncertaintyPanel } from "./ChainUncertaintyPanel";
import {
  CandidateRequestGeneration,
  type CandidateRequestToken,
} from "./candidateRequestGeneration";
import { ApiClientError } from "../../shared/api/client";
import { formatNumberAtDecimals } from "../../shared/taskPresentation";
import "./chain-workbench.css";

type StageStatus = "latest" | "running" | "stale" | "failed";
type ChainCandidateInputDefinition = ApiChainCandidateContract["external_inputs"][number];

const statusLabel: Record<StageStatus, string> = {
  latest: "最新",
  running: "再計算中",
  stale: "古い",
  failed: "失敗",
};

function predictions(stage: ApiChainExecution["stages"][number] | undefined) {
  const raw = stage?.result?.predictions;
  return raw && typeof raw === "object"
    ? raw as Record<string, { value?: number; std?: number; unit?: string }>
    : {};
}

function inputNumber(value: unknown) {
  return typeof value === "number" && Number.isFinite(value)
    ? String(Number(value.toFixed(6)))
    : "";
}

function candidatePayload(candidate: ApiCandidate): ApiCandidateInput {
  return {
    name: candidate.name,
    inputs: candidate.inputs,
    blend: candidate.blend,
    editor_state: candidate.editor_state,
    blend_validation: candidate.blend_validation,
    provenance: candidate.provenance,
  };
}

export function ChainWorkbenchPage({
  projectId,
  initialCandidateId,
  unavailable,
  displayDecimalOverrides,
  onCandidateSelected,
}: {
  projectId: string;
  initialCandidateId?: string;
  unavailable?: ApiSubsystemAvailability;
  displayDecimalOverrides?: Record<string, number>;
  onCandidateSelected: (candidateId: string) => void;
}) {
  const readOnly = Boolean(unavailable);
  const [candidates, setCandidates] = useState<ApiCandidate[]>([]);
  const [contract, setContract] = useState<ApiChainCandidateContract | null>(null);
  const [selectedId, setSelectedId] = useState(initialCandidateId ?? "");
  const [execution, setExecution] = useState<ApiChainExecution | null>(null);
  const [snapshots, setSnapshots] = useState<ApiChainSnapshot[]>([]);
  const [viewedSnapshotId, setViewedSnapshotId] = useState("");
  const [variants, setVariants] = useState<ApiActualConditionedVariant[]>([]);
  const [draftActualId, setDraftActualId] = useState("");
  const [actualDraft, setActualDraft] = useState<Record<string, string>>({});
  const [numericDraft, setNumericDraft] = useState<Record<string, string>>({});
  const [statusMessage, setStatusMessage] = useState("Chain候補を読み込んでいます");
  const [busy, setBusy] = useState(false);
  const saveTimer = useRef<number | undefined>(undefined);
  const requestSequence = useRef(0);
  const saveQueue = useRef(new LatestSaveQueue<ApiCandidate>());
  const candidateRequests = useRef(new CandidateRequestGeneration());
  const authoritative = useRef(new Map<string, ApiCandidate>());
  const selected = candidates.find((item) => item.id === selectedId) ?? candidates[0];
  const stageB = execution?.stages.find((stage) => stage.stage_id === "B");
  const stageC = execution?.stages.find((stage) => stage.stage_id === "C");
  const stageBPredictions = predictions(stageB);
  const stageCPredictions = predictions(stageC);
  const comparisonSnapshot = snapshots.find(
    (snapshot) => snapshot.identity.candidate_revision === selected?.revision,
  );
  const viewedSnapshot = snapshots.find(
    (snapshot) => snapshot.snapshot_id === viewedSnapshotId,
  ) ?? comparisonSnapshot ?? snapshots[0];
  const latestVariant = variants.find(
    (variant) => variant.identity.base_candidate_revision === selected?.revision,
  );
  const variantCPredictions = latestVariant?.stage_c_result?.predictions && typeof latestVariant.stage_c_result.predictions === "object"
    ? latestVariant.stage_c_result.predictions as Record<string, { value?: number; std?: number; unit?: string }>
    : {};
  const stageBDefinitions = useMemo(
    () => stageB?.output_definitions.length
      ? stageB.output_definitions
      : comparisonSnapshot?.stages.find((stage) => stage.stage_id === "B")?.output_definitions ?? [],
    [comparisonSnapshot?.snapshot_id, stageB?.result_input_digest],
  );
  const stageCDefinitions = useMemo(
    () => stageC?.output_definitions.length
      ? stageC.output_definitions
      : comparisonSnapshot?.stages.find((stage) => stage.stage_id === "C")?.output_definitions ?? [],
    [comparisonSnapshot?.snapshot_id, stageC?.result_input_digest],
  );
  const stageBKeys = useMemo(
    () => stageBDefinitions.map((definition) => definition.key),
    [stageBDefinitions],
  );
  const inputDefinitions = useMemo(
    () => [...(contract?.external_inputs ?? [])].sort((left, right) => left.order - right.order),
    [contract],
  );
  const scalarInputDefinitions = useMemo(
    () => inputDefinitions.filter((definition) => definition.kind !== "sparse_blend"),
    [inputDefinitions],
  );
  const blendInputDefinition = inputDefinitions.find(
    (definition) => definition.kind === "sparse_blend",
  );
  const formatStageNumber = (
    value: unknown,
    definition: ApiChainExecution["stages"][number]["output_definitions"][number],
    useProjectOverride = false,
  ) => typeof value === "number" && Number.isFinite(value)
    ? formatNumberAtDecimals(
      value,
      useProjectOverride
        ? displayDecimalOverrides?.[`output.${definition.key}`] ?? definition.display_decimals
        : definition.display_decimals,
    )
    : "—";
  const predictionCell = (
    prediction: { value?: number; std?: number } | undefined,
    definition: ApiChainExecution["stages"][number]["output_definitions"][number],
    useProjectOverride = false,
  ) => {
    if (typeof prediction?.value !== "number" || !Number.isFinite(prediction.value)) return "—";
    const unit = definition.unit.trim();
    const formattedValue = formatStageNumber(prediction.value, definition, useProjectOverride);
    const uncertainty = typeof prediction.std === "number" && Number.isFinite(prediction.std)
      ? `標準偏差 ±${formatStageNumber(prediction.std, definition, useProjectOverride)}${unit ? ` ${unit}` : ""}`
      : "区間なし";
    return <span className="chain-prediction-value">
      <strong>{formattedValue}{unit ? ` ${unit}` : ""}</strong>
      <small>{uncertainty}</small>
    </span>;
  };

  async function loadCandidateEvidence(
    candidateId: string,
    token: CandidateRequestToken,
  ) {
    try {
      const [nextExecution, nextSnapshots, nextVariants] = await Promise.all([
        workbenchApi.chainExecution(projectId, candidateId).catch(() => null),
        workbenchApi.listChainSnapshots(projectId, candidateId),
        workbenchApi.listChainAnalysisVariants(projectId, candidateId),
      ]);
      if (!candidateRequests.current.isCurrent(token)) return;
      setExecution(nextExecution);
      setSnapshots(nextSnapshots);
      setViewedSnapshotId((current) => (
        nextSnapshots.some((snapshot) => snapshot.snapshot_id === current)
          ? current
          : nextSnapshots[0]?.snapshot_id ?? ""
      ));
      setVariants(nextVariants);
      setStatusMessage(nextExecution ? "固定されたA → B → Cを表示しています" : "まだChainを実行していません");
    } catch (cause) {
      if (candidateRequests.current.isCurrent(token)) {
        setStatusMessage(cause instanceof Error ? cause.message : "Chain候補の計算状態を読み込めませんでした");
      }
    }
  }

  useEffect(() => {
    let active = true;
    const projectToken = candidateRequests.current.activate(projectId, "");
    requestSequence.current += 1;
    setBusy(false);
    const loadCandidates = readOnly
      ? Promise.all([
        workbenchApi.chainCandidateContract(projectId).catch(() => null),
        workbenchApi.listChainCandidates(projectId),
      ])
      // この画面は疎配合Chain専用の入力面。Chainが別のcandidate adapterを宣言している
      // 場合は、契約APIを叩く前にcapabilityで判断して明示的に伝える。
      : workbenchApi.chainCandidateCapability(projectId).then((capability) => {
      if (!active || !candidateRequests.current.isCurrent(projectToken)) return null;
      if (!capability.sparse_blend) {
        setStatusMessage(
          `このChainは疎な配合明細を使いません（candidate adapter: ${capability.adapter_id}）。`
          + "この画面は疎配合Chain専用です。",
        );
        return null;
      }
      return Promise.all([
        workbenchApi.chainCandidateContract(projectId),
        workbenchApi.listChainCandidates(projectId),
      ]);
    });
    void loadCandidates.then(async (loaded) => {
      if (loaded === null) return;
      const [loadedContract, items] = loaded;
      if (!active || !candidateRequests.current.isCurrent(projectToken)) return;
      setContract(loadedContract);
      setCandidates(items);
      authoritative.current = new Map(items.map((item) => [item.id, item]));
      const candidateId = items.some((item) => item.id === initialCandidateId)
        ? initialCandidateId!
        : items[0]?.id ?? "";
      setSelectedId(candidateId);
      if (candidateId) {
        const candidateToken = candidateRequests.current.activate(projectId, candidateId);
        onCandidateSelected(candidateId);
        await loadCandidateEvidence(candidateId, candidateToken);
      } else {
        setStatusMessage("Chain候補はまだありません");
      }
    }).catch((cause) => {
      if (active && candidateRequests.current.isCurrent(projectToken)) {
        setStatusMessage(cause instanceof Error ? cause.message : "Chain候補を読み込めませんでした");
      }
    });
    return () => {
      active = false;
      candidateRequests.current.invalidate();
      requestSequence.current += 1;
      if (saveTimer.current) window.clearTimeout(saveTimer.current);
    };
  }, [projectId, readOnly]);

  useEffect(() => {
    if (!stageBKeys.length) return;
    setActualDraft((current) => stageBKeys.every((key) => key in current)
      ? current
      : Object.fromEntries(stageBKeys.map((key) => [key, ""])));
  }, [stageBKeys.join("\u001f")]);

  useEffect(() => {
    if (
      !initialCandidateId
      || initialCandidateId === selectedId
      || !candidates.some((candidate) => candidate.id === initialCandidateId)
    ) return;
    const candidateToken = candidateRequests.current.activate(projectId, initialCandidateId);
    requestSequence.current += 1;
    setSelectedId(initialCandidateId);
    setBusy(false);
    setDraftActualId("");
    setActualDraft({});
    setExecution(null);
    setStatusMessage("Chain候補を切り替えています");
    void loadCandidateEvidence(initialCandidateId, candidateToken);
  }, [initialCandidateId, projectId, selectedId, candidates]);

  useEffect(() => {
    if (!selected) return;
    setNumericDraft(Object.fromEntries(
      scalarInputDefinitions
        .filter((definition) => definition.kind === "number")
        .map((definition) => [
          definition.candidate_path,
          inputNumber(getCandidateInputValue(selected.inputs, definition.candidate_path)),
        ]),
    ));
  }, [selected?.id, scalarInputDefinitions]);

  async function execute(candidate: ApiCandidate) {
    const candidateToken = candidateRequests.current.current();
    if (
      candidateToken.projectId !== projectId
      || candidateToken.candidateId !== candidate.id
    ) return;
    const sequence = ++requestSequence.current;
    setExecution((current) => current ? {
      ...current,
      status: "running",
      stages: current.stages.map((stage) => ({
        ...stage,
        status: stage.status === "stale" ? "running" : stage.status,
      })),
    } : current);
    setStatusMessage("変更を保存し、下流を自動再計算しています");
    try {
      const result = await workbenchApi.executeChain(
        projectId,
        candidate.id,
        candidate.revision,
        `web-${candidate.id}-r${candidate.revision}-${sequence}`,
      );
      if (
        sequence !== requestSequence.current
        || result.status === "superseded"
        || !candidateRequests.current.isCurrent(candidateToken)
      ) return;
      setExecution(result);
      setStatusMessage(result.status === "latest" ? "自動再計算が完了しました" : "一部のStageを更新できませんでした");
    } catch (cause) {
      if (
        sequence === requestSequence.current
        && candidateRequests.current.isCurrent(candidateToken)
      ) {
        setStatusMessage(cause instanceof Error ? cause.message : "Chainを実行できませんでした");
        await loadCandidateEvidence(candidate.id, candidateToken);
      }
    }
  }

  function markStagesStale(firstAffectedStageId: string) {
    setExecution((current) => current ? {
      ...current,
      status: "stale",
      stages: current.stages.map((stage, index, stages) => {
        const start = stages.findIndex(
          (candidateStage) => candidateStage.stage_id === firstAffectedStageId,
        );
        return {
          ...stage,
          status: start < 0 || index >= start ? "stale" : stage.status,
        };
      }),
    } : current);
  }

  async function persistCandidate(optimistic: ApiCandidate) {
    const candidateToken = candidateRequests.current.current();
    const initial = authoritative.current.get(optimistic.id) ?? optimistic;
    const basePayload = candidatePayload(initial);
    const draftPayload = candidatePayload(optimistic);
    const queued = saveQueue.current.enqueue(
      optimistic.id,
      initial,
      async (serverCandidate) => {
        const rebased = rebaseChangedFields(
          basePayload,
          draftPayload,
          candidatePayload(serverCandidate),
        );
        return workbenchApi.updateChainCandidate(
          projectId,
          optimistic.id,
          { ...rebased, expected_revision: serverCandidate.revision },
        );
      },
      (error) => {
        const current = error instanceof ApiClientError ? error.currentCandidate : undefined;
        if (!current) throw error;
        authoritative.current.set(optimistic.id, current);
        return current;
      },
    );
    try {
      const saved = await queued.promise;
      authoritative.current.set(saved.id, saved);
      if (!queued.isLatest()) return;
      setCandidates((items) => items.map((item) => item.id === saved.id ? saved : item));
      if (!candidateRequests.current.isCurrent(candidateToken)) return;
      if (saved.blend_validation?.status === "invalid") {
        setStatusMessage("成立条件を確認してください。draftは保存しましたが、Chainは実行していません");
        return;
      }
      await execute(saved);
    } catch (cause) {
      if (
        queued.isLatest()
        && candidateRequests.current.isCurrent(candidateToken)
      ) {
        setStatusMessage(cause instanceof Error ? cause.message : "Chain候補を保存できませんでした");
      }
    } finally {
      queued.release();
    }
  }

  function editNumeric(
    definition: ChainCandidateInputDefinition,
    rawValue: string,
  ) {
    if (!selected || definition.kind !== "number" || !definition.editable) return;
    saveQueue.current.supersede(selected.id);
    setNumericDraft((current) => ({
      ...current,
      [definition.candidate_path]: rawValue,
    }));
    if (!rawValue.trim()) {
      if (saveTimer.current) window.clearTimeout(saveTimer.current);
      setStatusMessage("空欄は0として保存しません。数値を入力してください");
      return;
    }
    const value = Number(rawValue);
    if (!Number.isFinite(value)) {
      if (saveTimer.current) window.clearTimeout(saveTimer.current);
      setStatusMessage("有限の数値を入力してください");
      return;
    }
    if (
      value < definition.allowed_range!.min
      || value > definition.allowed_range!.max
    ) {
      if (saveTimer.current) window.clearTimeout(saveTimer.current);
      setStatusMessage(
        `${definition.label}は許容範囲 `
        + `${definition.allowed_range!.min}〜${definition.allowed_range!.max} `
        + `${definition.unit ?? ""}で入力してください`,
      );
      return;
    }
    candidateRequests.current.activate(projectId, selected.id);
    requestSequence.current += 1;
    const optimistic: ApiCandidate = {
      ...selected,
      inputs: setCandidateInputValue(
        selected.inputs,
        definition.candidate_path,
        value,
      ),
    };
    setCandidates((items) => items.map((item) => item.id === selected.id ? optimistic : item));
    markStagesStale(definition.first_affected_stage_id);
    setStatusMessage("編集停止後に自動保存・再計算します");
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => {
      void persistCandidate(optimistic);
    }, 450);
  }

  function editCategorical(
    definition: ChainCandidateInputDefinition,
    value: string,
  ) {
    if (
      !selected
      || definition.kind !== "categorical"
      || !definition.editable
      || !definition.choices.includes(value)
    ) return;
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    saveQueue.current.supersede(selected.id);
    candidateRequests.current.activate(projectId, selected.id);
    requestSequence.current += 1;
    const optimistic: ApiCandidate = {
      ...selected,
      inputs: setCandidateInputValue(
        selected.inputs,
        definition.candidate_path,
        value,
      ),
    };
    setCandidates((items) => items.map((item) => (
      item.id === selected.id ? optimistic : item
    )));
    markStagesStale(definition.first_affected_stage_id);
    setStatusMessage("選択を保存し、下流を自動再計算しています");
    void persistCandidate(optimistic);
  }

  function editBlend(
    blend: NonNullable<ApiCandidate["blend"]>,
    lockedMaterialIds = selected?.editor_state?.locked_material_ids ?? [],
  ) {
    if (!selected || !blendInputDefinition || !blendInputDefinition.editable) return;
    saveQueue.current.supersede(selected.id);
    candidateRequests.current.activate(projectId, selected.id);
    requestSequence.current += 1;
    const optimistic: ApiCandidate = {
      ...selected,
      blend,
      editor_state: { locked_material_ids: lockedMaterialIds },
    };
    setCandidates((items) => items.map((item) => item.id === selected.id ? optimistic : item));
    markStagesStale(blendInputDefinition.first_affected_stage_id);
    setStatusMessage("配合を保存し、A → B → Cを自動再計算します");
    void persistCandidate(optimistic);
  }

  async function createStarterCandidate() {
    if (!contract) return;
    const projectToken = candidateRequests.current.current();
    setBusy(true);
    setStatusMessage("固定されたChain契約から基準配合を作成しています");
    try {
      const created = await workbenchApi.createChainCandidate(projectId, contract.starter_candidate);
      if (!candidateRequests.current.isCurrent(projectToken)) return;
      const candidateToken = candidateRequests.current.activate(projectId, created.id);
      requestSequence.current += 1;
      authoritative.current.set(created.id, created);
      setCandidates([created]);
      setSelectedId(created.id);
      setNumericDraft(Object.fromEntries(
        scalarInputDefinitions
          .filter((definition) => definition.kind === "number")
          .map((definition) => [
            definition.candidate_path,
            inputNumber(getCandidateInputValue(created.inputs, definition.candidate_path)),
          ]),
      ));
      onCandidateSelected(created.id);
      setBusy(false);
      if (candidateRequests.current.isCurrent(candidateToken)) await execute(created);
    } catch (cause) {
      if (candidateRequests.current.isCurrent(projectToken)) {
        setStatusMessage(cause instanceof Error ? cause.message : "基準配合を作成できませんでした");
      }
    } finally {
      if (candidateRequests.current.isCurrent(projectToken)) setBusy(false);
    }
  }

  async function saveSnapshot() {
    if (!selected) return;
    const candidateToken = candidateRequests.current.current();
    setBusy(true);
    try {
      const snapshot = await workbenchApi.createChainSnapshot(projectId, selected.id, selected.revision);
      if (!candidateRequests.current.isCurrent(candidateToken)) return;
      setSnapshots((items) => [snapshot, ...items.filter((item) => item.snapshot_id !== snapshot.snapshot_id)]);
      setViewedSnapshotId(snapshot.snapshot_id);
      setStatusMessage("現在の全Stageをスナップショットに固定しました");
    } catch (cause) {
      if (candidateRequests.current.isCurrent(candidateToken)) {
        setStatusMessage(cause instanceof Error ? cause.message : "スナップショットを保存できませんでした");
      }
    } finally {
      if (candidateRequests.current.isCurrent(candidateToken)) setBusy(false);
    }
  }

  async function createVariant() {
    if (!selected || !comparisonSnapshot) return;
    const candidateToken = candidateRequests.current.current();
    setBusy(true);
    try {
      const values = Object.fromEntries(
        stageBKeys.map((key) => [key, Number(actualDraft[key])]),
      );
      const variant = await workbenchApi.createChainAnalysisVariant(projectId, selected.id, {
        candidate_revision: selected.revision,
        comparison_snapshot_id: comparisonSnapshot.snapshot_id,
        actual_records: [{ actual_id: draftActualId.trim(), values }],
      });
      if (!candidateRequests.current.isCurrent(candidateToken)) return;
      setVariants((items) => [variant, ...items]);
      setDraftActualId("");
      setActualDraft(Object.fromEntries(stageBKeys.map((key) => [key, ""])));
      setStatusMessage("実測Bを使うStage C分析を、通常Chainとは別に固定しました");
    } catch (cause) {
      if (candidateRequests.current.isCurrent(candidateToken)) {
        setStatusMessage(cause instanceof Error ? cause.message : "実測を使った分析を保存できませんでした");
      }
    } finally {
      if (candidateRequests.current.isCurrent(candidateToken)) setBusy(false);
    }
  }

  if (!selected) {
    return <section className="chain-workbench chain-empty">
      <h2>Chain候補</h2>
      {unavailable && <div className="task-unavailable-banner" role="status">
        <strong>{unavailable.message}</strong>
        <span>{unavailable.impact}</span>
        <small>原因: {unavailable.cause}</small>
        <small>復旧: {unavailable.recovery_hint}</small>
      </div>}
      <p>{statusMessage}</p>
      <small>候補が登録されると、A → B → Cの計算状態と中間実測をここで確認できます。</small>
      <button className="primary-button" disabled={readOnly || !contract || busy} onClick={() => void createStarterCandidate()}>
        固定契約から基準配合を作成
      </button>
    </section>;
  }

  return <section className="chain-workbench" aria-label="Chain候補作業面">
    {unavailable && <div className="task-unavailable-banner" role="status">
      <strong>{unavailable.message}</strong>
      <span>{unavailable.impact}</span>
      <small>保存済みの候補・実行結果・Snapshot・実測analysisは参照できます。</small>
      <small>原因: {unavailable.cause}</small>
      <small>復旧: {unavailable.recovery_hint}</small>
    </div>}
    <header className="chain-workbench-header">
      <div><span className="overline">CHAIN WORKBENCH</span><h2>{selected.name}</h2></div>
      <label>候補
        <select value={selected.id} onChange={(event) => {
          const candidateId = event.target.value;
          const candidateToken = candidateRequests.current.activate(projectId, candidateId);
          requestSequence.current += 1;
          setSelectedId(candidateId);
          setBusy(false);
          setDraftActualId("");
          setActualDraft({});
          onCandidateSelected(candidateId);
          setExecution(null);
          setStatusMessage("Chain候補を切り替えています");
          void loadCandidateEvidence(candidateId, candidateToken);
        }}>
          {candidates.map((candidate) => <option key={candidate.id} value={candidate.id}>{candidate.name} · r{candidate.revision}</option>)}
        </select>
      </label>
    </header>

    <div className="chain-stage-rail" aria-label="Chain Stageの鮮度">
      {(["A", "B", "C"] as const).map((stageId, index) => {
        const stage = execution?.stages.find((item) => item.stage_id === stageId);
        const status = (stage?.status ?? "stale") as StageStatus;
        return <div className="chain-stage-slot" key={stageId}>
          <div className={`chain-stage-node ${status}`}>
            <b>{stageId}</b><span>{stageId === "A" ? "材料成分" : stageId === "B" ? "溶着成分" : "特性"}</span>
            <em>{statusLabel[status]}</em>
            {stageId === "B" && latestVariant && <small>実測照合あり</small>}
            {stageId === "C" && latestVariant && <small>別analysisあり</small>}
          </div>
          {index < 2 && <i aria-hidden="true">→</i>}
        </div>;
      })}
    </div>
    <div className="chain-status-line" role="status">{statusMessage}</div>

    <div className="chain-edit-strip">
      <div className="chain-input-fields">
        {scalarInputDefinitions.map((definition) => {
          const disabled = readOnly || !definition.editable;
          const reason = !definition.editable
            ? definition.read_only_reason
            : readOnly
              ? unavailable?.impact
              : undefined;
          return <label
            className="chain-input-control"
            data-chain-external-path={definition.external_path}
            key={definition.external_path}
            title={reason ?? undefined}
          >
            <span>
              <b>{definition.label}</b>
              {definition.unit && <small>{definition.unit}</small>}
            </span>
            {definition.kind === "number"
              ? <input
                type="number"
                min={definition.allowed_range!.min}
                max={definition.allowed_range!.max}
                step={10 ** -(definition.display_decimals ?? 0)}
                disabled={disabled}
                aria-describedby={`${selected.id}-${definition.order}-range`}
                value={
                  numericDraft[definition.candidate_path]
                  ?? inputNumber(
                    getCandidateInputValue(
                      selected.inputs,
                      definition.candidate_path,
                    ),
                  )
                }
                onChange={(event) => editNumeric(definition, event.target.value)}
              />
              : <select
                disabled={disabled}
                value={String(
                  getCandidateInputValue(
                    selected.inputs,
                    definition.candidate_path,
                  ) ?? "",
                )}
                onChange={(event) => editCategorical(definition, event.target.value)}
              >
                {definition.choices.map((choice) => (
                  <option key={choice} value={choice}>{choice}</option>
                ))}
              </select>}
            {definition.kind === "number" && <small
              className="chain-input-range"
              id={`${selected.id}-${definition.order}-range`}
            >
              許容 {definition.allowed_range!.min}〜{definition.allowed_range!.max}
            </small>}
            {reason && <small className="chain-input-reason">{reason}</small>}
          </label>;
        })}
      </div>
      <div className="chain-edit-actions">
      <button className="primary-button" disabled={readOnly || busy || execution?.status !== "latest"} onClick={() => void saveSnapshot()}>
        {busy ? "保存中…" : "全Stageを固定"}
      </button>
      <small>{comparisonSnapshot ? `現revisionを固定済み · 全${snapshots.length}件` : "実測分析には現revisionのsnapshotが必要です"}</small>
      </div>
    </div>

    {viewedSnapshot && <details className="chain-snapshot-evidence" open={readOnly ? true : undefined}>
      <summary>固定Snapshotの証跡 <b>{snapshots.length}</b></summary>
      <div className="chain-snapshot-heading">
        <label>固定時点
          <select value={viewedSnapshot.snapshot_id} onChange={(event) => setViewedSnapshotId(event.target.value)}>
            {snapshots.map((snapshot) => (
              <option key={snapshot.snapshot_id} value={snapshot.snapshot_id}>
                編集版 {snapshot.identity.candidate_revision} · {new Date(snapshot.created_at).toLocaleString("ja-JP")}
              </option>
            ))}
          </select>
        </label>
        <span>request {viewedSnapshot.request_id}</span>
      </div>
      <details>
        <summary>固定入力</summary>
        <pre>{JSON.stringify(viewedSnapshot.external_input, null, 2)}</pre>
      </details>
      <div className="chain-snapshot-stages">
        {viewedSnapshot.stages.map((stage) => {
          const liveStage = execution?.chain_revision_digest === viewedSnapshot.identity.chain_revision_digest
            ? execution.stages.find((item) => item.stage_id === stage.stage_id)
            : undefined;
          const definitions = stage.output_definitions.length
            ? stage.output_definitions
            : liveStage?.output_definitions ?? [];
          const stagePredictions = predictions(stage);
          return <details key={stage.stage_id}>
            <summary>Stage {stage.stage_id} · {statusLabel[stage.status as StageStatus]}</summary>
            {stage.stage_id === "A"
              ? <pre>{JSON.stringify(stage.result, null, 2)}</pre>
              : definitions.length
                ? <table className="chain-snapshot-output-table">
                <thead><tr><th>出力</th><th>固定した予測</th></tr></thead>
                <tbody>{definitions.map((definition) => <tr key={definition.key}>
                  <th>{definition.label}</th>
                  <td>{predictionCell(stagePredictions[definition.key], definition, stage.stage_id === "C")}</td>
                </tr>)}</tbody>
                </table>
                : <p className="chain-output-unavailable">このSnapshotの出力定義を確認できません。</p>}
          </details>;
        })}
      </div>
    </details>}

    {selected.blend && contract && blendInputDefinition && <details
      className="chain-blend-panel"
      data-chain-external-path={blendInputDefinition.external_path}
    >
      <summary>{blendInputDefinition.label}を編集</summary>
      {(!blendInputDefinition.editable || readOnly) && <p className="chain-input-reason">
        {blendInputDefinition.read_only_reason ?? unavailable?.impact}
      </p>}
      {!readOnly && blendInputDefinition.editable && <BlendEditorPanel
        projectId={projectId}
        candidate={fromApiCandidate(selected)}
        transformId={contract.transform_id}
        chainMode
        onBlend={(_candidateId, blend, lockedMaterialIds) => editBlend(blend, lockedMaterialIds)}
        onLocks={(_candidateId, lockedMaterialIds) => editBlend(selected.blend!, lockedMaterialIds)}
      />}
    </details>}

    <div className="chain-result-grid">
      <section className="chain-result-card">
        <header><div><span>STAGE B</span><h3>溶着金属成分</h3></div>{latestVariant && <b className="source-badge actual-match">実測照合あり</b>}</header>
        <div className="chain-table-scroll">
          <table><thead><tr><th>成分</th><th>予測</th><th>実測</th></tr></thead>
            <tbody>{stageBDefinitions.map((definition) => <tr key={definition.key}><th>{definition.label}</th><td>{predictionCell(stageBPredictions[definition.key], definition)}</td><td>{latestVariant ? <>{formatStageNumber(latestVariant.measured_stage_b[definition.key], definition)}{definition.unit.trim() ? ` ${definition.unit.trim()}` : ""}</> : "—"}</td></tr>)}</tbody>
          </table>
          {!stageBDefinitions.length && <p className="chain-output-unavailable">Stage Bの出力定義を取得できません。</p>}
        </div>
      </section>
      <section className="chain-result-card">
        <header><div><span>STAGE C</span><h3>特性</h3></div><b className="source-badge predicted">通常Chain</b></header>
        <div className="chain-table-scroll">
          <table><thead><tr><th>特性</th><th>予測B経由</th><th>実測B経由</th></tr></thead>
            <tbody>{stageCDefinitions.map((definition) => <tr key={definition.key}><th>{definition.label}</th><td>{predictionCell(stageCPredictions[definition.key], definition, true)}</td><td className={latestVariant ? "actual-conditioned" : ""}>{latestVariant ? predictionCell(variantCPredictions[definition.key], definition, true) : "—"}</td></tr>)}</tbody>
          </table>
          {!stageCDefinitions.length && <p className="chain-output-unavailable">Stage Cの出力定義を取得できません。</p>}
        </div>
        {latestVariant && <small className="variant-note">実測B経由は別analysis variantです。通常Chainを上書きしません。</small>}
      </section>
    </div>

    {execution && <ChainUncertaintyPanel
      projectId={projectId}
      candidateId={selected.id}
      candidateRevision={selected.revision}
      pointExecutionReady={
        execution.status === "latest"
        && execution.candidate_revision === selected.revision
      }
      chainRevisionDigest={execution.chain_revision_digest}
      pointExecutionRequestId={execution.request_id}
      readOnly={readOnly}
    />}

    {!readOnly && <details className="chain-actual-panel">
      <summary>実測Bを使ってStage Cを別分析</summary>
      <p>16成分がすべて揃った実測だけを使用します。不足分を予測値で補いません。</p>
      <label className="actual-id-field">実測ID<input value={draftActualId} onChange={(event) => setDraftActualId(event.target.value)} placeholder="例: WM-001" /></label>
      <div className="actual-value-grid">{stageBDefinitions.map((definition) => <label key={definition.key}><span>{definition.label}<small>{definition.unit}</small></span><input type="number" step={10 ** -definition.display_decimals} value={actualDraft[definition.key] ?? ""} onChange={(event) => setActualDraft((current) => ({ ...current, [definition.key]: event.target.value }))} /></label>)}</div>
      <button className="primary-button" disabled={busy || !draftActualId.trim() || !comparisonSnapshot || stageBKeys.some((key) => !actualDraft[key]?.trim() || !Number.isFinite(Number(actualDraft[key])))} onClick={() => void createVariant()}>
        実測を使用した別分析を固定
      </button>
    </details>}

    {variants.length > 0 && <details className="chain-variant-history">
      <summary>実測analysis履歴 <b>{variants.length}</b></summary>
      {variants.map((variant) => <article key={variant.variant_id}>
        <div><b>実測を使用</b><span>{variant.identity.actual_ids.join(", ")}</span></div>
        <small>base r{variant.identity.base_candidate_revision} · snapshot {variant.identity.comparison_snapshot_id.slice(0, 8)} · coverage {variant.identity.coverage.length}/{variant.identity.coverage.length}</small>
      </article>)}
    </details>}
  </section>;
}
