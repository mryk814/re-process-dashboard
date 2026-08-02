import { useEffect, useMemo, useState } from "react";

import type { ApiChainGraph } from "../../shared/api/workbench-api";
import { workbenchApi } from "../../shared/api/workbench-api";
import {
  buildChainGraph,
  revisionStage,
  shortDigest,
  stageBindingCounts,
  stageStatus,
  type ApiGraphExecution,
  type ChainGraphEdge,
} from "./chainGraphPresentation";

type ChainInspection = { kind: "stage" | "edge"; id: string };
type ChainInspectionError = {
  kind: "ambiguous";
  stageId: string;
  edgeId: string;
};
type Props = {
  projectId: string;
  candidateId?: string;
  requestedInspection?: ChainInspection;
  inspectionError?: ChainInspectionError;
  onInspectionChange: (inspection?: ChainInspection) => void;
};

function freshnessLabel(status: ReturnType<typeof stageStatus>) {
  return status === "latest" ? "最新" : status === "running" ? "再計算中"
    : status === "stale" ? "古い結果あり" : status === "failed" ? "失敗（保持結果あり）"
      : status === "blocked_by_upstream" ? "上流失敗で未実行"
        : status === "unavailable" ? "利用不可" : "実行結果なし";
}

function portFields(prefix: string, port: ChainGraphEdge["sourcePort"] | ChainGraphEdge["targetPort"]) {
  return [
    [`${prefix} value kind`, port?.value_kind ?? "—"],
    [`${prefix} quantity`, port?.quantity ?? "—"],
    [`${prefix} unit`, port?.unit ?? "—"],
    [`${prefix} basis`, port?.basis ?? "—"],
  ] as const;
}

function portDetails(edge: ChainGraphEdge) {
  const conversion = edge.binding.conversion;
  return [
    ["source", edge.source.label], ["target", edge.target.label],
    ...portFields("source", edge.sourcePort), ...portFields("target", edge.targetPort),
    ["conversion", conversion
      ? `${conversion.conversion_id} · ${conversion.source_unit} → ${conversion.target_unit} · factor ${conversion.factor} · offset ${conversion.offset}`
      : "変換なし"],
    ["connection", edge.branchCount > 1 ? `この出力は ${edge.branchCount} 接続へ分岐` : edge.mergeCount > 1 ? `${edge.mergeCount} 接続をこのinputへ合流` : "1対1"],
    ...(edge.reason ? [["surface status", edge.reason] as const] : []),
  ] as const;
}

function EdgeButton({ edge, selected, onSelect }: { edge: ChainGraphEdge; selected: boolean; onSelect: () => void }) {
  const conversion = edge.binding.conversion;
  const connection = edge.branchCount > 1 ? `分岐 ${edge.branchCount} 本`
    : edge.mergeCount > 1 ? `合流 ${edge.mergeCount} 本` : null;
  return <button
    type="button"
    className={`chain-graph-rail ${edge.status}${selected ? " selected" : ""}`}
    data-chain-edge={edge.id}
    data-source={edge.source.label}
    data-target={edge.target.label}
    onClick={onSelect}
  >
    <span className="chain-graph-rail-route"><b>{edge.source.label}</b><span aria-hidden="true">→</span><b>{edge.target.label}</b></span>
    <span className="chain-graph-rail-detail">
      {conversion ? `単位変換: ${conversion.source_unit} → ${conversion.target_unit} (factor ${conversion.factor}, offset ${conversion.offset})` : "単位変換なし"}
      {connection && ` · ${connection}`}
      {edge.reason && ` · surface未解決: ${edge.reason}`}
    </span>
  </button>;
}

export function ChainGraphReadOnlyState({ loading, message }: { loading?: boolean; message: string }) {
  return <section className="chain-graph-read-only-state" aria-live="polite">
    <span className="overline">CHAIN MAP · READ ONLY</span>
    <h2>固定したChain構成を表示できません</h2>
    <p>{loading ? "固定したChain Revisionを読み込み中です。" : message}</p>
  </section>;
}

export function ChainGraphViewer({
  projectId,
  candidateId,
  requestedInspection,
  inspectionError,
  onInspectionChange,
}: Props) {
  const [graph, setGraph] = useState<ApiChainGraph | null>(null);
  const [graphState, setGraphState] = useState<"loading" | "ready" | "unavailable">("loading");
  const [execution, setExecution] = useState<ApiGraphExecution | null>(null);
  const [executionState, setExecutionState] = useState<"loading" | "ready" | "unavailable">(candidateId ? "loading" : "unavailable");
  const edges = useMemo(() => graph ? buildChainGraph(graph) : [], [graph]);
  const selectedEdgeId = requestedInspection?.kind === "edge" ? requestedInspection.id : undefined;
  const selectedStageId = requestedInspection?.kind === "stage" ? requestedInspection.id : undefined;
  const selectedEdge = edges.find((edge) => edge.id === selectedEdgeId);
  const selectedStage = graph?.definition.stages.find((stage) => stage.stage_id === selectedStageId);
  const unavailableInspection = requestedInspection && !selectedEdge && !selectedStage
    ? requestedInspection.id
    : undefined;

  useEffect(() => {
    const controller = new AbortController();
    setGraphState("loading");
    workbenchApi.chainGraph(projectId, controller.signal).then((value) => {
      if (!controller.signal.aborted) { setGraph(value); setGraphState("ready"); }
    }).catch(() => {
      if (!controller.signal.aborted) { setGraph(null); setGraphState("unavailable"); }
    });
    return () => controller.abort();
  }, [projectId]);

  useEffect(() => {
    if (!candidateId || !graph) { setExecution(null); setExecutionState("unavailable"); return; }
    const controller = new AbortController();
    setExecutionState("loading");
    const request = graph.definition.schema_version === "prediction-graph-definition/v1"
      ? workbenchApi.predictionGraphExecution(projectId, candidateId, controller.signal)
      : workbenchApi.chainExecution(projectId, candidateId, controller.signal);
    request.then((value) => {
      if (!controller.signal.aborted) { setExecution(value); setExecutionState("ready"); }
    }).catch(() => {
      if (!controller.signal.aborted) { setExecution(null); setExecutionState("unavailable"); }
    });
    return () => controller.abort();
  }, [candidateId, graph, projectId]);

  if (graphState === "loading") return <ChainGraphReadOnlyState loading message="" />;
  if (!graph) return <ChainGraphReadOnlyState message="APIへ接続できないか、固定したChain Revisionを解決できません。候補やRevisionをここから作成・変更することはできません。" />;

  return <section className="chain-graph-viewer" aria-labelledby="chain-graph-heading">
    <header className="chain-graph-header">
      <div><span className="overline">CHAIN MAP · READ ONLY</span><h2 id="chain-graph-heading">{graph.definition.label}</h2><p>Projectに固定されたStage surface、実際のbinding、単位変換を確認します。ここから候補・Chain Revisionは変更できません。</p></div>
      <dl className="chain-graph-identity">
        <div><dt>Revision</dt><dd>r{graph.revision.revision}</dd></div>
        <div><dt>Chain digest</dt><dd title={graph.revision.revision_digest}>{shortDigest(graph.revision.revision_digest)}</dd></div>
        <div><dt>Binding digest</dt><dd title={graph.revision.binding_digest}>{shortDigest(graph.revision.binding_digest)}</dd></div>
        <div><dt>Unit conversion</dt><dd title={graph.revision.unit_conversion_digest}>{shortDigest(graph.revision.unit_conversion_digest)}</dd></div>
      </dl>
    </header>
    <p className="chain-graph-live-state" role="status">{executionState === "loading" ? "選択中候補のStage状態を読み込み中です。" : executionState === "ready" ? "選択中候補の最新実行状態を表示しています。" : "候補を選ぶと、この位置でStageごとの実行状態を確認できます。"}</p>

    <section className="chain-graph-canvas" aria-label="固定したChainのStageと実際のbinding">
      <div className="chain-graph-external" aria-label="外部入力"><strong>外部入力</strong>{graph.prediction_graph.inputs.map((input) => <span key={input.input_id} className="chain-graph-external-port">{input.label}<small>{input.role} · {input.port.value_kind} · {input.port.quantity} · {input.port.unit ?? "unitなし"}</small></span>)}</div>
      <div className="chain-graph-stages">{graph.definition.stages.map((stage) => {
        const lock = revisionStage(graph.revision, stage.stage_id);
        const counts = stageBindingCounts(graph, stage.stage_id);
        const status = stageStatus(execution, stage.stage_id);
        const contract = graph.stage_contracts.find((item) => item.stage_id === stage.stage_id);
        return <article key={stage.stage_id} className={`chain-graph-node ${status}`}>
          <button type="button" className="chain-graph-node-button" aria-pressed={selectedStageId === stage.stage_id} onClick={() => onInspectionChange({ kind: "stage", id: stage.stage_id })}><span className="chain-graph-node-order">{stage.stage_id}</span><span><b>{stage.stage_kind === "task" ? "予測Task" : "決定論的Transform"}</b><small>{stage.contract_id}</small></span><em>{freshnessLabel(status)}</em></button>
          <div className="chain-graph-node-counts"><span>input {counts.inputs}</span><span>output {counts.outputs}</span></div>
          <p className="chain-graph-node-surface">{contract?.surface ? `${contract.surface.contract_id} · ${shortDigest(contract.surface.contract_digest)}` : `surface未解決: ${contract?.reason ?? "理由不明"}`}</p>
          {lock && <details className="chain-graph-locks"><summary>固定参照</summary><dl><div><dt>contract</dt><dd title={lock.contract_digest}>{shortDigest(lock.contract_digest)}</dd></div><div><dt>package</dt><dd title={lock.package_manifest_digest}>{shortDigest(lock.package_manifest_digest)}</dd></div>{lock.dataset_view_revision_id && <div><dt>dataset view</dt><dd>{lock.dataset_view_revision_id}</dd></div>}{lock.dataset_profile_digest && <div><dt>profile</dt><dd title={lock.dataset_profile_digest}>{shortDigest(lock.dataset_profile_digest)}</dd></div>}</dl></details>}
        </article>;
      })}</div>
    </section>

    {graph.prediction_graph.decision_outputs.length > 0 && <section className="chain-graph-outputs" aria-labelledby="chain-outputs-heading">
      <div className="chain-graph-section-title"><div><h3 id="chain-outputs-heading">Decision Output summary</h3><p>判断軸を先に確認し、Stage／Packageの詳細はGraphへ下げています。</p></div></div>
      <div className="chain-graph-output-grid">{graph.prediction_graph.decision_outputs.map((output) => {
        const terminal = execution?.schema_version === "prediction-graph-execution/v1"
          ? execution.terminal_outputs.find((item) => item.output_id === output.output_id)
          : undefined;
        const status = terminal?.status ?? stageStatus(execution, output.source_stage_id);
        return <article className={`chain-graph-output ${status}`} key={output.output_id}>
          <div><span>{output.group}</span><em>{freshnessLabel(status)}</em></div>
          <strong>{output.label}</strong>
          <small>{output.source_stage_id}.{output.source_output_key} · {output.role}</small>
          {output.evidence && <div className="chain-graph-output-evidence">
            <b>{output.evidence.evidence_kind.replaceAll("_", " ")}</b>
            <b>production利用: {output.evidence.production_use === "allowed" ? "可" : "不可"}</b>
            <span>{output.evidence.unit_or_scale} · goal {output.evidence.goal_direction} · causal claim {output.evidence.causal_claim}</span>
            <details><summary>証拠境界</summary><p>{output.evidence.limitation}</p><p>source: {output.evidence.source_variables.join(", ")}</p></details>
          </div>}
        </article>;
      })}</div>
    </section>}

    <section className="chain-graph-routes" aria-labelledby="chain-routes-heading"><div className="chain-graph-section-title"><div><h3 id="chain-routes-heading">実際の接続</h3><p>各railは固定Definitionのbinding一件です。分岐・合流・変換を文字でも確認できます。</p></div></div><div className="chain-graph-rails">{edges.map((edge) => <EdgeButton key={edge.id} edge={edge} selected={selectedEdgeId === edge.id} onSelect={() => onInspectionChange({ kind: "edge", id: edge.id })} />)}</div></section>

    <section className="chain-graph-bindings" aria-labelledby="chain-bindings-heading"><div className="chain-graph-section-title"><div><h3 id="chain-bindings-heading">binding一覧</h3><p>railと同じ接続を、表形式でも確認できます。</p></div></div><div className="chain-graph-table-wrap"><table><thead><tr><th scope="col">source</th><th scope="col">target</th><th scope="col">変換 / 接続</th><th scope="col">詳細</th></tr></thead><tbody>{edges.map((edge) => <tr key={edge.id} data-chain-edge={edge.id} data-source={edge.source.label} data-target={edge.target.label} className={selectedEdgeId === edge.id ? "selected" : undefined}><th scope="row">{edge.source.label}</th><td>{edge.target.label}</td><td>{edge.binding.conversion ? `${edge.binding.conversion.conversion_id} (${edge.binding.conversion.source_unit} → ${edge.binding.conversion.target_unit})` : "変換なし"}{edge.branchCount > 1 ? ` · 分岐 ${edge.branchCount}` : edge.mergeCount > 1 ? ` · 合流 ${edge.mergeCount}` : ""}</td><td><button type="button" className="outline-button" onClick={() => onInspectionChange({ kind: "edge", id: edge.id })}>確認</button></td></tr>)}</tbody></table></div></section>

    {(selectedEdge || selectedStage || unavailableInspection || inspectionError) && <aside className="chain-graph-inspector" aria-live="polite" aria-label="Chain inspector">
      {inspectionError ? <>
        <h3>検査対象を1つに絞ってください</h3>
        <p>Stage <code>{inspectionError.stageId}</code> と Binding <code>{inspectionError.edgeId}</code> が同時に指定されています。</p>
        <p><code>chain_stage</code> と <code>chain_edge</code> は、どちらか一方だけを指定してください。</p>
        <button type="button" className="outline-button" onClick={() => onInspectionChange({ kind: "stage", id: inspectionError.stageId })}>Stageを表示</button>
        <button type="button" className="outline-button" onClick={() => onInspectionChange({ kind: "edge", id: inspectionError.edgeId })}>Bindingを表示</button>
      </> : unavailableInspection ? <>
        <h3>指定された検査対象を表示できません</h3>
        <p><code>{unavailableInspection}</code> は、この固定Chain Revisionにありません。</p>
        <button type="button" className="outline-button" onClick={() => onInspectionChange()}>選択を解除</button>
      </> : selectedEdge ? <>
        <h3>Binding inspector</h3>
        <dl>{portDetails(selectedEdge).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}</dl>
        <p className="chain-graph-inspector-note">portの型・quantity・unit・basisは、Projectに固定されたStage surfaceだけを表示します。surfaceが残っていない古いRevisionは理由を示し、推測では補いません。</p>
      </> : selectedStage && (() => {
        const lock = revisionStage(graph.revision, selectedStage.stage_id);
        const live = execution?.stages.find((item) => item.stage_id === selectedStage.stage_id);
        const contract = graph.stage_contracts.find((item) => item.stage_id === selectedStage.stage_id);
        return <><h3>Stage inspector · {selectedStage.stage_id}</h3><dl><div><dt>kind</dt><dd>{selectedStage.stage_kind}</dd></div><div><dt>contract</dt><dd>{selectedStage.contract_id}</dd></div><div><dt>surface</dt><dd>{contract?.surface ? contract.surface.contract_digest : contract?.reason ?? "固定surfaceなし"}</dd></div><div><dt>contract digest</dt><dd>{lock?.contract_digest ?? "固定参照を解決できません"}</dd></div><div><dt>package digest</dt><dd>{lock?.package_manifest_digest ?? "固定参照を解決できません"}</dd></div><div><dt>live state</dt><dd>{freshnessLabel(stageStatus(execution, selectedStage.stage_id))}</dd></div>{live?.error && <div><dt>error</dt><dd>{live.error}</dd></div>}</dl></>;
      })()}
    </aside>}
  </section>;
}
