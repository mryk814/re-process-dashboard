import { useEffect, useMemo, useRef, useState } from "react";
import {
  workbenchApi,
  type ApiActualConditionedVariant,
  type ApiCandidate,
  type ApiChainExecution,
  type ApiChainSnapshot,
} from "../../shared/api/workbench-api";
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

function candidateUpdate(candidate: ApiCandidate) {
  return {
    name: candidate.name,
    inputs: candidate.inputs,
    blend: candidate.blend,
    editor_state: candidate.editor_state,
    blend_validation: candidate.blend_validation,
    provenance: candidate.provenance,
    expected_revision: candidate.revision,
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
  const [selectedId, setSelectedId] = useState(initialCandidateId ?? "");
  const [execution, setExecution] = useState<ApiChainExecution | null>(null);
  const [snapshots, setSnapshots] = useState<ApiChainSnapshot[]>([]);
  const [variants, setVariants] = useState<ApiActualConditionedVariant[]>([]);
  const [draftActualId, setDraftActualId] = useState("");
  const [actualDraft, setActualDraft] = useState<Record<string, string>>({});
  const [statusMessage, setStatusMessage] = useState("Chain候補を読み込んでいます");
  const [busy, setBusy] = useState(false);
  const saveTimer = useRef<number | undefined>(undefined);
  const requestSequence = useRef(0);
  const selected = candidates.find((item) => item.id === selectedId) ?? candidates[0];
  const stageB = execution?.stages.find((stage) => stage.stage_id === "B");
  const stageC = execution?.stages.find((stage) => stage.stage_id === "C");
  const stageBPredictions = predictions(stageB);
  const stageCPredictions = predictions(stageC);
  const latestVariant = variants[0];
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
    void workbenchApi.listChainCandidates(projectId).then(async (items) => {
      if (!active) return;
      setCandidates(items);
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
    setActualDraft((current) => Object.keys(current).length
      ? current
      : Object.fromEntries(stageBKeys.map((key) => [key, String(stageBPredictions[key]?.value ?? "")])));
  }, [stageB?.result_input_digest]);

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

  function editProcess(path: string, rawValue: string) {
    if (!selected) return;
    const value = Number(rawValue);
    if (!Number.isFinite(value)) return;
    const optimistic: ApiCandidate = {
      ...selected,
      inputs: {
        ...selected.inputs,
        process: { ...selected.inputs.process, [path]: value },
      },
    };
    setCandidates((items) => items.map((item) => item.id === selected.id ? optimistic : item));
    setExecution((current) => current ? {
      ...current,
      status: "stale",
      stages: current.stages.map((stage) => ({
        ...stage,
        status: (
          path === "heat_input_kj_per_mm"
            ? stage.stage_id === "B" || stage.stage_id === "C"
            : stage.stage_id === "C"
        ) ? "stale" : stage.status,
      })),
    } : current);
    setStatusMessage("編集停止後に自動保存・再計算します");
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => {
      void workbenchApi.updateChainCandidate(
        projectId,
        optimistic.id,
        candidateUpdate(optimistic),
      ).then((saved) => {
        setCandidates((items) => items.map((item) => item.id === saved.id ? saved : item));
        return execute(saved);
      }).catch((cause) => {
        setStatusMessage(cause instanceof Error ? cause.message : "Chain候補を保存できませんでした");
      });
    }, 450);
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
    if (!selected || !snapshots[0]) return;
    setBusy(true);
    try {
      const values = Object.fromEntries(
        stageBKeys.map((key) => [key, Number(actualDraft[key])]),
      );
      const variant = await workbenchApi.createChainAnalysisVariant(projectId, selected.id, {
        candidate_revision: selected.revision,
        comparison_snapshot_id: snapshots[0].snapshot_id,
        actual_records: [{ actual_id: draftActualId.trim(), values }],
      });
      setVariants((items) => [variant, ...items]);
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
    </section>;
  }

  return <section className="chain-workbench" aria-label="Chain候補作業面">
    <header className="chain-workbench-header">
      <div><span className="overline">CHAIN WORKBENCH</span><h2>{selected.name}</h2></div>
      <label>候補
        <select value={selected.id} onChange={(event) => {
          const candidateId = event.target.value;
          setSelectedId(candidateId);
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
            {stageId === "B" && variants.length > 0 && <small>実測照合あり</small>}
            {stageId === "C" && variants.length > 0 && <small>実測を使用</small>}
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
          <input type="number" step="any" value={selected.inputs.process[path] ?? ""} onChange={(event) => editProcess(path, event.target.value)} />
        </label>
      ))}
      <button className="primary-button" disabled={busy || execution?.status !== "latest"} onClick={() => void saveSnapshot()}>
        {busy ? "保存中…" : "全Stageを固定"}
      </button>
      <small>{snapshots.length ? `固定済み ${snapshots.length}件` : "実測分析には先にsnapshotが必要です"}</small>
    </div>

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
      <button className="primary-button" disabled={busy || !draftActualId.trim() || !snapshots[0] || stageBKeys.some((key) => !Number.isFinite(Number(actualDraft[key])))} onClick={() => void createVariant()}>
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
