import { useEffect, useMemo, useRef, useState } from "react";
import {
  workbenchApi,
  type ApiActualConditionedVariant,
  type ApiCandidate,
  type ApiCandidateInput,
  type ApiChainCandidateContract,
  type ApiChainExecution,
  type ApiChainSnapshot,
} from "../../shared/api/workbench-api";
import {
  fromApiCandidate,
  LatestSaveQueue,
  rebaseChangedFields,
} from "../candidates";
import { BlendEditorPanel } from "./BlendEditorPanel";
import { ApiClientError } from "../../shared/api/client";
import "./chain-workbench.css";

type StageStatus = "latest" | "running" | "stale" | "failed";

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

function number(value: unknown, digits = 3) {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString("ja-JP", { maximumFractionDigits: digits })
    : "—";
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
  onCandidateSelected,
}: {
  projectId: string;
  initialCandidateId?: string;
  onCandidateSelected: (candidateId: string) => void;
}) {
  const [candidates, setCandidates] = useState<ApiCandidate[]>([]);
  const [contract, setContract] = useState<ApiChainCandidateContract | null>(null);
  const [selectedId, setSelectedId] = useState(initialCandidateId ?? "");
  const [execution, setExecution] = useState<ApiChainExecution | null>(null);
  const [snapshots, setSnapshots] = useState<ApiChainSnapshot[]>([]);
  const [variants, setVariants] = useState<ApiActualConditionedVariant[]>([]);
  const [draftActualId, setDraftActualId] = useState("");
  const [actualDraft, setActualDraft] = useState<Record<string, string>>({});
  const [processDraft, setProcessDraft] = useState<Record<string, string>>({});
  const [statusMessage, setStatusMessage] = useState("Chain候補を読み込んでいます");
  const [busy, setBusy] = useState(false);
  const saveTimer = useRef<number | undefined>(undefined);
  const requestSequence = useRef(0);
  const saveQueue = useRef(new LatestSaveQueue<ApiCandidate>());
  const authoritative = useRef(new Map<string, ApiCandidate>());
  const selected = candidates.find((item) => item.id === selectedId) ?? candidates[0];
  const stageB = execution?.stages.find((stage) => stage.stage_id === "B");
  const stageC = execution?.stages.find((stage) => stage.stage_id === "C");
  const stageBPredictions = predictions(stageB);
  const stageCPredictions = predictions(stageC);
  const comparisonSnapshot = snapshots.find(
    (snapshot) => snapshot.identity.candidate_revision === selected?.revision,
  );
  const latestVariant = variants.find(
    (variant) => variant.identity.base_candidate_revision === selected?.revision,
  );
  const variantCPredictions = latestVariant?.stage_c_result?.predictions && typeof latestVariant.stage_c_result.predictions === "object"
    ? latestVariant.stage_c_result.predictions as Record<string, { value?: number; std?: number; unit?: string }>
    : {};
  const stageBKeys = useMemo(
    () => Object.keys(stageBPredictions).sort((a, b) => a.localeCompare(b, "en")),
    [stageB?.result_input_digest],
  );

  async function loadCandidateEvidence(candidateId: string) {
    const [nextExecution, nextSnapshots, nextVariants] = await Promise.all([
      workbenchApi.chainExecution(projectId, candidateId).catch(() => null),
      workbenchApi.listChainSnapshots(projectId, candidateId),
      workbenchApi.listChainAnalysisVariants(projectId, candidateId),
    ]);
    setExecution(nextExecution);
    setSnapshots(nextSnapshots);
    setVariants(nextVariants);
    setStatusMessage(nextExecution ? "固定されたA → B → Cを表示しています" : "まだChainを実行していません");
  }

  useEffect(() => {
    let active = true;
    void Promise.all([
      workbenchApi.chainCandidateContract(projectId),
      workbenchApi.listChainCandidates(projectId),
    ]).then(async ([loadedContract, items]) => {
      if (!active) return;
      setContract(loadedContract);
      setCandidates(items);
      authoritative.current = new Map(items.map((item) => [item.id, item]));
      const candidateId = items.some((item) => item.id === initialCandidateId)
        ? initialCandidateId!
        : items[0]?.id ?? "";
      setSelectedId(candidateId);
      if (candidateId) {
        onCandidateSelected(candidateId);
        await loadCandidateEvidence(candidateId);
      } else {
        setStatusMessage("Chain候補はまだありません");
      }
    }).catch((cause) => {
      if (active) setStatusMessage(cause instanceof Error ? cause.message : "Chain候補を読み込めませんでした");
    });
    return () => {
      active = false;
      if (saveTimer.current) window.clearTimeout(saveTimer.current);
    };
  }, [projectId]);

  useEffect(() => {
    if (!stageBKeys.length) return;
    setActualDraft((current) => stageBKeys.every((key) => key in current)
      ? current
      : Object.fromEntries(stageBKeys.map((key) => [key, ""])));
  }, [stageBKeys.join("\u001f")]);

  useEffect(() => {
    if (!selected) return;
    setProcessDraft(Object.fromEntries(
      Object.entries(selected.inputs.process).map(([key, value]) => [key, inputNumber(value)]),
    ));
  }, [selected?.id]);

  async function execute(candidate: ApiCandidate) {
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
      if (sequence !== requestSequence.current || result.status === "superseded") return;
      setExecution(result);
      setStatusMessage(result.status === "latest" ? "自動再計算が完了しました" : "一部のStageを更新できませんでした");
    } catch (cause) {
      if (sequence === requestSequence.current) {
        setStatusMessage(cause instanceof Error ? cause.message : "Chainを実行できませんでした");
        await loadCandidateEvidence(candidate.id);
      }
    }
  }

  function markStagesStale(from: "A" | "B" | "C") {
    const start = { A: 0, B: 1, C: 2 }[from];
    setExecution((current) => current ? {
      ...current,
      status: "stale",
      stages: current.stages.map((stage, index) => ({
        ...stage,
        status: index >= start ? "stale" : stage.status,
      })),
    } : current);
  }

  async function persistCandidate(optimistic: ApiCandidate) {
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
      if (saved.blend_validation?.status === "invalid") {
        setStatusMessage("成立条件を確認してください。draftは保存しましたが、Chainは実行していません");
        return;
      }
      await execute(saved);
    } catch (cause) {
      if (queued.isLatest()) {
        setStatusMessage(cause instanceof Error ? cause.message : "Chain候補を保存できませんでした");
      }
    } finally {
      queued.release();
    }
  }

  function editProcess(path: string, rawValue: string) {
    if (!selected) return;
    saveQueue.current.supersede(selected.id);
    setProcessDraft((current) => ({ ...current, [path]: rawValue }));
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
    const optimistic: ApiCandidate = {
      ...selected,
      inputs: {
        ...selected.inputs,
        process: { ...selected.inputs.process, [path]: value },
      },
    };
    setCandidates((items) => items.map((item) => item.id === selected.id ? optimistic : item));
    markStagesStale(path === "heat_input_kj_per_mm" ? "B" : "C");
    setStatusMessage("編集停止後に自動保存・再計算します");
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => {
      void persistCandidate(optimistic);
    }, 450);
  }

  function editBlend(
    blend: NonNullable<ApiCandidate["blend"]>,
    lockedMaterialIds = selected?.editor_state?.locked_material_ids ?? [],
  ) {
    if (!selected) return;
    saveQueue.current.supersede(selected.id);
    const optimistic: ApiCandidate = {
      ...selected,
      blend,
      editor_state: { locked_material_ids: lockedMaterialIds },
    };
    setCandidates((items) => items.map((item) => item.id === selected.id ? optimistic : item));
    markStagesStale("A");
    setStatusMessage("配合を保存し、A → B → Cを自動再計算します");
    void persistCandidate(optimistic);
  }

  async function createStarterCandidate() {
    if (!contract) return;
    setBusy(true);
    setStatusMessage("固定されたChain契約から基準配合を作成しています");
    try {
      const created = await workbenchApi.createChainCandidate(projectId, contract.starter_candidate);
      authoritative.current.set(created.id, created);
      setCandidates([created]);
      setSelectedId(created.id);
      setProcessDraft(Object.fromEntries(
        Object.entries(created.inputs.process).map(([key, value]) => [key, inputNumber(value)]),
      ));
      onCandidateSelected(created.id);
      await execute(created);
    } catch (cause) {
      setStatusMessage(cause instanceof Error ? cause.message : "基準配合を作成できませんでした");
    } finally {
      setBusy(false);
    }
  }

  async function saveSnapshot() {
    if (!selected) return;
    setBusy(true);
    try {
      const snapshot = await workbenchApi.createChainSnapshot(projectId, selected.id, selected.revision);
      setSnapshots((items) => [snapshot, ...items.filter((item) => item.snapshot_id !== snapshot.snapshot_id)]);
      setStatusMessage("現在の全Stageをスナップショットに固定しました");
    } catch (cause) {
      setStatusMessage(cause instanceof Error ? cause.message : "スナップショットを保存できませんでした");
    } finally {
      setBusy(false);
    }
  }

  async function createVariant() {
    if (!selected || !comparisonSnapshot) return;
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
      setVariants((items) => [variant, ...items]);
      setDraftActualId("");
      setActualDraft(Object.fromEntries(stageBKeys.map((key) => [key, ""])));
      setStatusMessage("実測Bを使うStage C分析を、通常Chainとは別に固定しました");
    } catch (cause) {
      setStatusMessage(cause instanceof Error ? cause.message : "実測を使った分析を保存できませんでした");
    } finally {
      setBusy(false);
    }
  }

  if (!selected) {
    return <section className="chain-workbench chain-empty">
      <h2>Chain候補</h2>
      <p>{statusMessage}</p>
      <small>候補が登録されると、A → B → Cの計算状態と中間実測をここで確認できます。</small>
      <button className="primary-button" disabled={!contract || busy} onClick={() => void createStarterCandidate()}>
        固定契約から基準配合を作成
      </button>
    </section>;
  }

  return <section className="chain-workbench" aria-label="Chain候補作業面">
    <header className="chain-workbench-header">
      <div><span className="overline">CHAIN WORKBENCH</span><h2>{selected.name}</h2></div>
      <label>候補
        <select value={selected.id} onChange={(event) => {
          const candidateId = event.target.value;
          setSelectedId(candidateId);
          setDraftActualId("");
          setActualDraft({});
          onCandidateSelected(candidateId);
          setExecution(null);
          setStatusMessage("Chain候補を切り替えています");
          void loadCandidateEvidence(candidateId);
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
      {(["heat_input_kj_per_mm", "preheat_temp_c", "test_temperature_c"] as const).map((path) => (
        <label key={path}>
          <span>{path === "heat_input_kj_per_mm" ? "入熱 (kJ/mm)" : path === "preheat_temp_c" ? "予熱 (℃)" : "試験温度 (℃)"}</span>
          <input type="number" step="any" value={processDraft[path] ?? inputNumber(selected.inputs.process[path])} onChange={(event) => editProcess(path, event.target.value)} />
        </label>
      ))}
      <button className="primary-button" disabled={busy || execution?.status !== "latest"} onClick={() => void saveSnapshot()}>
        {busy ? "保存中…" : "全Stageを固定"}
      </button>
      <small>{comparisonSnapshot ? `現revisionを固定済み · 全${snapshots.length}件` : "実測分析には現revisionのsnapshotが必要です"}</small>
    </div>

    {selected.blend && contract && <details className="chain-blend-panel">
      <summary>Stage A 配合を編集</summary>
      <BlendEditorPanel
        projectId={projectId}
        candidate={fromApiCandidate(selected)}
        transformId={contract.transform_id}
        chainMode
        onBlend={(_candidateId, blend, lockedMaterialIds) => editBlend(blend, lockedMaterialIds)}
        onLocks={(_candidateId, lockedMaterialIds) => editBlend(selected.blend!, lockedMaterialIds)}
      />
    </details>}

    <div className="chain-result-grid">
      <section className="chain-result-card">
        <header><div><span>STAGE B</span><h3>溶着金属成分</h3></div>{latestVariant && <b className="source-badge actual-match">実測照合あり</b>}</header>
        <div className="chain-table-scroll">
          <table><thead><tr><th>成分</th><th>予測</th><th>実測</th></tr></thead>
            <tbody>{stageBKeys.map((key) => <tr key={key}><th>{key}</th><td>{number(stageBPredictions[key]?.value)}</td><td>{latestVariant ? number(latestVariant.measured_stage_b[key]) : "—"}</td></tr>)}</tbody>
          </table>
        </div>
      </section>
      <section className="chain-result-card">
        <header><div><span>STAGE C</span><h3>特性</h3></div><b className="source-badge predicted">通常Chain</b></header>
        <div className="chain-table-scroll">
          <table><thead><tr><th>特性</th><th>予測B経由</th><th>実測B経由</th></tr></thead>
            <tbody>{Object.keys(stageCPredictions).map((key) => <tr key={key}><th>{key}</th><td>{number(stageCPredictions[key]?.value)}</td><td className={latestVariant ? "actual-conditioned" : ""}>{latestVariant ? number(variantCPredictions[key]?.value) : "—"}</td></tr>)}</tbody>
          </table>
        </div>
        {latestVariant && <small className="variant-note">実測B経由は別analysis variantです。通常Chainを上書きしません。</small>}
      </section>
    </div>

    <details className="chain-actual-panel">
      <summary>実測Bを使ってStage Cを別分析</summary>
      <p>16成分がすべて揃った実測だけを使用します。不足分を予測値で補いません。</p>
      <label className="actual-id-field">実測ID<input value={draftActualId} onChange={(event) => setDraftActualId(event.target.value)} placeholder="例: WM-001" /></label>
      <div className="actual-value-grid">{stageBKeys.map((key) => <label key={key}><span>{key}</span><input type="number" step="any" value={actualDraft[key] ?? ""} onChange={(event) => setActualDraft((current) => ({ ...current, [key]: event.target.value }))} /></label>)}</div>
      <button className="primary-button" disabled={busy || !draftActualId.trim() || !comparisonSnapshot || stageBKeys.some((key) => !actualDraft[key]?.trim() || !Number.isFinite(Number(actualDraft[key])))} onClick={() => void createVariant()}>
        実測を使用した別分析を固定
      </button>
    </details>

    {variants.length > 0 && <details className="chain-variant-history">
      <summary>実測analysis履歴 <b>{variants.length}</b></summary>
      {variants.map((variant) => <article key={variant.variant_id}>
        <div><b>実測を使用</b><span>{variant.identity.actual_ids.join(", ")}</span></div>
        <small>base r{variant.identity.base_candidate_revision} · snapshot {variant.identity.comparison_snapshot_id.slice(0, 8)} · coverage {variant.identity.coverage.length}/{variant.identity.coverage.length}</small>
      </article>)}
    </details>}
  </section>;
}
