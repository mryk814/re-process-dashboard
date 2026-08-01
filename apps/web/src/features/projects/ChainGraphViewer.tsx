import { useEffect, useMemo, useState } from "react";

import type { ApiChainExecution, ApiChainTemplate } from "../../shared/api/workbench-api";
import { workbenchApi } from "../../shared/api/workbench-api";
import {
  buildChainGraph,
  revisionStage,
  shortDigest,
  stageBindingCounts,
  stageStatus,
  type ChainGraphEdge,
} from "./chainGraphPresentation";

type Props = {
  projectId: string;
  candidateId?: string;
  template: ApiChainTemplate;
  revision: ApiChainTemplate["revisions"][number];
};

function freshnessLabel(status: ReturnType<typeof stageStatus>) {
  return status === "latest" ? "最新"
    : status === "running" ? "再計算中"
      : status === "stale" ? "古い結果あり"
        : status === "failed" ? "失敗（保持結果あり）"
          : "実行結果なし";
}

function portDetails(edge: ChainGraphEdge) {
  const port = edge.sourcePort;
  const conversion = edge.binding.conversion;
  return [
    ["source", edge.source.label],
    ["target", edge.target.label],
    ["value kind", port?.value_kind ?? "固定Definitionにport surfaceなし"],
    ["quantity", port?.quantity ?? "固定Definitionにport surfaceなし"],
    ["basis", port?.basis ?? "—"],
    ["source unit", conversion?.source_unit ?? port?.unit ?? "—"],
    ["target unit", conversion?.target_unit ?? port?.unit ?? "固定Definitionにport surfaceなし"],
    ["conversion", conversion
      ? `${conversion.conversion_id} · factor ${conversion.factor} · offset ${conversion.offset}`
      : "変換なし"],
  ] as const;
}

export function ChainGraphViewer({ projectId, candidateId, template, revision }: Props) {
  const [execution, setExecution] = useState<ApiChainExecution | null>(null);
  const [executionState, setExecutionState] = useState<"loading" | "ready" | "unavailable">(
    candidateId ? "loading" : "unavailable",
  );
  const [selectedEdgeId, setSelectedEdgeId] = useState<string>();
  const [selectedStageId, setSelectedStageId] = useState<string>();
  const edges = useMemo(() => buildChainGraph(template.definition), [template.definition]);
  const selectedEdge = edges.find((edge) => edge.id === selectedEdgeId);

  useEffect(() => {
    if (!candidateId) {
      setExecution(null);
      setExecutionState("unavailable");
      return;
    }
    const controller = new AbortController();
    setExecutionState("loading");
    workbenchApi.chainExecution(projectId, candidateId, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) {
          setExecution(value);
          setExecutionState("ready");
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setExecution(null);
          setExecutionState("unavailable");
        }
      });
    return () => controller.abort();
  }, [candidateId, projectId]);

  return <section className="chain-graph-viewer" aria-labelledby="chain-graph-heading">
    <header className="chain-graph-header">
      <div>
        <span className="overline">CHAIN MAP · READ ONLY</span>
        <h2 id="chain-graph-heading">{template.definition.label}</h2>
        <p>固定したStage、binding、単位変換を確認します。ここから候補・Chain Revisionは変更できません。</p>
      </div>
      <dl className="chain-graph-identity">
        <div><dt>Revision</dt><dd>r{revision.revision}</dd></div>
        <div><dt>Chain digest</dt><dd title={revision.revision_digest}>{shortDigest(revision.revision_digest)}</dd></div>
        <div><dt>Binding digest</dt><dd title={revision.binding_digest}>{shortDigest(revision.binding_digest)}</dd></div>
        <div><dt>Unit conversion</dt><dd title={revision.unit_conversion_digest}>{shortDigest(revision.unit_conversion_digest)}</dd></div>
      </dl>
    </header>

    <p className="chain-graph-live-state" role="status">
      {executionState === "loading" ? "選択中候補のStage状態を読み込み中です。"
        : executionState === "ready" ? "選択中候補の最新実行状態を表示しています。"
          : "候補を選ぶと、この位置でStageごとの実行状態を確認できます。"}
    </p>

    <div className="chain-graph-canvas" aria-label="Chainの左から右への概略図">
      <div className="chain-graph-external" aria-label="外部入力">
        <strong>外部入力</strong>
        {template.definition.external_inputs.map((port) => <button
          type="button"
          key={port.path}
          className="chain-graph-external-port"
          onClick={() => setSelectedEdgeId(edges.find((edge) => edge.source.kind === "external" && edge.source.id === port.path)?.id)}
        >{port.path}</button>)}
      </div>
      <div className="chain-graph-stages">
        {template.definition.stages.map((stage) => {
          const lock = revisionStage(revision, stage.stage_id);
          const counts = stageBindingCounts(template.definition, stage.stage_id);
          const status = stageStatus(execution, stage.stage_id);
          return <article key={stage.stage_id} className={`chain-graph-node ${status}`}>
            <button
              type="button"
              className="chain-graph-node-button"
              aria-pressed={selectedStageId === stage.stage_id}
              onClick={() => { setSelectedStageId(stage.stage_id); setSelectedEdgeId(undefined); }}
            >
              <span className="chain-graph-node-order">{stage.stage_id}</span>
              <span><b>{stage.stage_kind === "task" ? "予測Task" : "決定論的Transform"}</b><small>{stage.contract_id}</small></span>
              <em>{freshnessLabel(status)}</em>
            </button>
            <div className="chain-graph-node-counts"><span>入力 {counts.inputs}</span><span>出力 {counts.outputs}</span></div>
            {lock && <details className="chain-graph-locks"><summary>固定参照</summary><dl>
              <div><dt>contract</dt><dd title={lock.contract_digest}>{shortDigest(lock.contract_digest)}</dd></div>
              <div><dt>package</dt><dd title={lock.package_manifest_digest}>{shortDigest(lock.package_manifest_digest)}</dd></div>
              {lock.dataset_view_revision_id && <div><dt>dataset view</dt><dd>{lock.dataset_view_revision_id}</dd></div>}
              {lock.dataset_profile_digest && <div><dt>profile</dt><dd title={lock.dataset_profile_digest}>{shortDigest(lock.dataset_profile_digest)}</dd></div>}
            </dl></details>}
          </article>;
        })}
      </div>
    </div>

    <section className="chain-graph-bindings" aria-labelledby="chain-bindings-heading">
      <div className="chain-graph-section-title"><div><h3 id="chain-bindings-heading">binding一覧</h3><p>図に依存せず、同じ接続を順番に確認できます。</p></div></div>
      <div className="chain-graph-table-wrap"><table>
        <thead><tr><th scope="col">source</th><th scope="col">target</th><th scope="col">変換</th><th scope="col">詳細</th></tr></thead>
        <tbody>{edges.map((edge) => <tr key={edge.id} className={selectedEdgeId === edge.id ? "selected" : undefined}>
          <th scope="row">{edge.source.label}</th><td>{edge.target.label}</td>
          <td>{edge.binding.conversion ? edge.binding.conversion.conversion_id : "なし"}</td>
          <td><button type="button" className="outline-button" onClick={() => { setSelectedEdgeId(edge.id); setSelectedStageId(undefined); }}>確認</button></td>
        </tr>)}</tbody>
      </table></div>
    </section>

    {(selectedEdge || selectedStageId) && <aside className="chain-graph-inspector" aria-live="polite" aria-label="Chain inspector">
      {selectedEdge && <><h3>Binding inspector</h3><dl>{portDetails(selectedEdge).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}</dl>
        <p className="chain-graph-inspector-note">Stage outputのport surfaceは、固定Definitionが公開するpathと変換だけで表示しています。型・quantity・basisは固定Definitionに含まれない場合、推測で補いません。</p></>}
      {selectedStageId && (() => {
        const stage = template.definition.stages.find((item) => item.stage_id === selectedStageId)!;
        const lock = revisionStage(revision, selectedStageId);
        const live = execution?.stages.find((item) => item.stage_id === selectedStageId);
        return <><h3>Stage inspector · {stage.stage_id}</h3><dl>
          <div><dt>kind</dt><dd>{stage.stage_kind}</dd></div><div><dt>contract</dt><dd>{stage.contract_id}</dd></div>
          <div><dt>contract digest</dt><dd>{lock?.contract_digest ?? "固定参照を解決できません"}</dd></div>
          <div><dt>package digest</dt><dd>{lock?.package_manifest_digest ?? "固定参照を解決できません"}</dd></div>
          <div><dt>live state</dt><dd>{freshnessLabel(stageStatus(execution, stage.stage_id))}</dd></div>
          {live?.error && <div><dt>error</dt><dd>{live.error}</dd></div>}
        </dl></>;
      })()}
    </aside>}
  </section>;
}
