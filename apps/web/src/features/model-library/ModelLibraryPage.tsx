import { useEffect, useMemo, useState } from "react";
import {
  workbenchApi,
  type ApiModelLibraryCatalog,
} from "../../shared/api/workbench-api";
import type { ModelLibraryTab } from "../../shared/modelLibrary";

type AssetState = ApiModelLibraryCatalog["tasks"][number]["state"];

const tabs: Array<{ id: ModelLibraryTab; label: string }> = [
  { id: "tasks", label: "Prediction Task" },
  { id: "packages", label: "Model Package" },
  { id: "transforms", label: "Transform" },
  { id: "graphs", label: "Prediction Graph" },
];

const availabilityLabel = {
  available: "利用可能",
  degraded: "一部利用不可",
  unavailable: "利用不可",
} as const;

const lifecycleLabel = {
  current: "現行",
  superseded: "旧版",
  research_only: "研究用途",
  compatibility_only: "互換表示",
} as const;

function shortDigest(value: string | null | undefined) {
  if (!value) return "—";
  return value.replace(/^sha256:/, "").slice(0, 12);
}

function AssetStateSummary({ state }: { state: AssetState }) {
  const needsExplanation = state.availability !== "available"
    || state.lifecycle !== "current";
  return <div className={`model-asset-state ${state.availability}`}>
    <div className="model-asset-badges">
      <span>{availabilityLabel[state.availability]}</span>
      {state.lifecycle !== "current"
        && <span>{lifecycleLabel[state.lifecycle]}</span>}
    </div>
    {needsExplanation && <div className="model-asset-explanation">
      <strong>{state.reason}</strong>
      <span>{state.impact}</span>
      <small>次の手順: {state.recovery_hint}</small>
    </div>}
  </div>;
}

function IdentityDetails({
  title,
  entries,
}: {
  title: string;
  entries: Array<[string, string | number]>;
}) {
  return <details className="model-identity-details">
    <summary>{title}</summary>
    <dl>{entries.map(([label, value]) => <div key={label}>
      <dt>{label}</dt><dd>{value}</dd>
    </div>)}</dl>
  </details>;
}

function EmptyAssetState({ label }: { label: string }) {
  return <div className="model-library-empty" role="status">
    <strong>{label}は登録されていません</strong>
    <span>Data LibraryまたはStudioで準備した資産が、このWorkspaceへ登録されると表示されます。</span>
  </div>;
}

export function ModelLibraryPage({
  tab,
  onTabChange,
  onOpenDataLibrary,
  onOpenStudio,
  onStartProject,
}: {
  tab: ModelLibraryTab;
  onTabChange: (tab: ModelLibraryTab) => void;
  onOpenDataLibrary: () => void;
  onOpenStudio: () => void;
  onStartProject: (datasetViewRevisionId: string) => void;
}) {
  const [catalog, setCatalog] = useState<ApiModelLibraryCatalog | null>(null);
  const [error, setError] = useState("");
  const [requestVersion, setRequestVersion] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setError("");
    void workbenchApi.modelLibraryCatalog(controller.signal)
      .then(setCatalog)
      .catch((cause: unknown) => {
        if (!controller.signal.aborted) {
          setError(cause instanceof Error
            ? cause.message
            : "Model Libraryを取得できませんでした。");
        }
      });
    return () => controller.abort();
  }, [requestVersion]);

  const taskLabels = useMemo(
    () => new Map(catalog?.tasks.map((task) => [task.task_id, task.label])),
    [catalog],
  );

  if (!catalog && !error) return <section className="model-library-state" role="status">
    <span className="overline">MODEL LIBRARY</span>
    <h2>モデル資産を読み込んでいます</h2>
  </section>;

  if (!catalog) return <section className="model-library-state" role="alert">
    <span className="overline">MODEL LIBRARY</span>
    <h2>Model Libraryに接続できません</h2>
    <p>{error}</p>
    <button className="primary-button" type="button" onClick={() => setRequestVersion((value) => value + 1)}>再試行</button>
  </section>;

  const counts: Record<ModelLibraryTab, number> = {
    tasks: catalog.tasks.length,
    packages: catalog.packages.length,
    transforms: catalog.transforms.length,
    graphs: catalog.graphs.length,
  };

  return <section className="model-library-page">
    <header className="model-library-header">
      <div>
        <span className="overline">MODEL LIBRARY · READ ONLY</span>
        <h1>モデル資産を確認する</h1>
        <p>利用できる契約と固定Revisionを比較し、データ準備、Project利用、Graph authoringへ進みます。</p>
      </div>
      <div className="model-library-header-actions">
        <button type="button" className="outline-button" onClick={onOpenDataLibrary}>Data Library</button>
        <button type="button" className="primary-button" onClick={onOpenStudio}>Graphを作成</button>
      </div>
    </header>
    {error && <div className="model-library-refresh-error" role="alert">
      <span>{error}。取得済みの一覧を保持しています。</span>
      <button type="button" className="text-button" onClick={() => setRequestVersion((value) => value + 1)}>再試行</button>
    </div>}
    <div className="model-library-tabs" role="tablist" aria-label="モデル資産種別">
      {tabs.map((item, index) => <button
        key={item.id}
        id={`model-library-tab-${item.id}`}
        type="button"
        role="tab"
        aria-selected={tab === item.id}
        aria-controls={`model-library-panel-${item.id}`}
        tabIndex={tab === item.id ? 0 : -1}
        onClick={() => onTabChange(item.id)}
        onKeyDown={(event) => {
          const delta = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
          const target = event.key === "Home" ? 0
            : event.key === "End" ? tabs.length - 1
              : delta ? (index + delta + tabs.length) % tabs.length : -1;
          if (target >= 0) {
            event.preventDefault();
            onTabChange(tabs[target].id);
            document.getElementById(`model-library-tab-${tabs[target].id}`)?.focus();
          }
        }}
      >{item.label}<span>{counts[item.id]}</span></button>)}
    </div>

    {tab === "tasks" && <div className="model-asset-list" role="tabpanel" id="model-library-panel-tasks" aria-labelledby="model-library-tab-tasks">
      {catalog.tasks.length === 0 && <EmptyAssetState label="Prediction Task" />}
      {catalog.tasks.map((task) => {
        const packageWithData = catalog.packages.find((item) =>
          item.task_id === task.task_id
          && item.data_references.dataset_view_revision_ids.length > 0
          && item.state.availability === "available");
        return <article className="model-asset-card" key={task.task_id}>
          <header><div><span className="model-asset-kind">TASK</span><h2>{task.label}</h2><code>{task.task_id}</code></div><AssetStateSummary state={task.state} /></header>
          <p>{task.inputs.length}入力から{task.outputs.length}出力を予測 · Package {task.package_reference_ids.length}件 · Graph {task.graph_revision_ids.length}件</p>
          <div className="model-asset-actions">
            <button type="button" className="outline-button" onClick={onOpenDataLibrary}>対応データを確認</button>
            <button
              type="button"
              className="primary-button"
              disabled={!packageWithData}
              title={packageWithData ? undefined : "利用可能なPackageとDataset参照が必要です"}
              onClick={() => packageWithData && onStartProject(packageWithData.data_references.dataset_view_revision_ids[0])}
            >Projectを作成</button>
          </div>
          <IdentityDetails title="入出力と契約identity" entries={[
            ["Task contract", shortDigest(task.contract_digest)],
            ["Inputs", task.inputs.map((port) => `${port.label}${port.unit && port.unit !== "1" ? ` (${port.unit})` : ""}`).join(" / ")],
            ["Outputs", task.outputs.map((port) => `${port.label}${port.unit && port.unit !== "1" ? ` (${port.unit})` : ""}`).join(" / ")],
          ]} />
        </article>;
      })}
    </div>}

    {tab === "packages" && <div className="model-asset-list" role="tabpanel" id="model-library-panel-packages" aria-labelledby="model-library-tab-packages">
      {catalog.packages.length === 0 && <EmptyAssetState label="Model Package" />}
      {catalog.packages.map((item) => {
        const datasetViewId = item.data_references.dataset_view_revision_ids[0];
        return <article className="model-asset-card" key={item.reference_id}>
          <header><div><span className="model-asset-kind">PACKAGE · {item.storage_scope === "personal" ? "PERSONAL" : "BUNDLED"}</span><h2>{item.package_id}</h2><span>{taskLabels.get(item.task_id) ?? item.task_id} · {item.version}</span></div><AssetStateSummary state={item.state} /></header>
          <p>{item.predictor_families.map((predictor) => `${predictor.target}: ${predictor.predictive_family}`).join(" · ") || "predictor identityなし"}</p>
          <div className="model-asset-actions">
            <button type="button" className="outline-button" onClick={onOpenDataLibrary}>Data Libraryで確認</button>
            <button type="button" className="primary-button" disabled={!datasetViewId || item.state.availability !== "available"} onClick={() => datasetViewId && onStartProject(datasetViewId)}>Projectを作成</button>
          </div>
          <IdentityDetails title="Pipeline・検証・固定参照" entries={[
            ["Manifest", shortDigest(item.manifest_digest)],
            ["Feature Pipeline", item.feature_pipeline ? `${item.feature_pipeline.identity_id} · ${item.feature_pipeline.version}` : "記録なし"],
            ["Feature Recipe", item.feature_recipe ? `${item.feature_recipe.identity_id} · ${item.feature_recipe.version} · ${shortDigest(item.feature_recipe.digest)}` : "未使用"],
            ["Validation Plan", item.validation_plans.map((plan) => `${plan.target}: ${plan.strategy} (${shortDigest(plan.digest)})`).join(" / ") || "記録なし"],
            ["Training source", item.data_references.source_names.join(" / ") || "記録なし"],
          ]} />
        </article>;
      })}
    </div>}

    {tab === "transforms" && <div className="model-asset-list" role="tabpanel" id="model-library-panel-transforms" aria-labelledby="model-library-tab-transforms">
      {catalog.transforms.length === 0 && <EmptyAssetState label="Transform" />}
      {catalog.transforms.map((item) => <article className="model-asset-card" key={item.transform_id}>
        <header><div><span className="model-asset-kind">TRANSFORM</span><h2>{item.label}</h2><code>{item.transform_id}</code></div><AssetStateSummary state={item.state} /></header>
        <p>Graph Revision {item.graph_revision_ids.length}件で利用 · 入力 {item.surface?.input_ports.length ?? 0} · 出力 {item.surface?.output_ports.length ?? 0}</p>
        <IdentityDetails title="変換契約と利用箇所" entries={[
          ["Package", shortDigest(item.package_manifest_digest)],
          ["Graph revisions", item.graph_revision_ids.join(" / ") || "未使用"],
          ["Inputs", item.surface?.input_ports.map((port) => port.path).join(" / ") || "解決不能"],
          ["Outputs", item.surface?.output_ports.map((port) => port.path).join(" / ") || "解決不能"],
        ]} />
      </article>)}
    </div>}

    {tab === "graphs" && <div className="model-asset-list" role="tabpanel" id="model-library-panel-graphs" aria-labelledby="model-library-tab-graphs">
      {catalog.graphs.length === 0 && <EmptyAssetState label="Prediction Graph" />}
      {catalog.graphs.map((graph) => <article className="model-asset-card model-graph-card" key={graph.graph_id}>
        <header><div><span className="model-asset-kind">PREDICTION GRAPH</span><h2>{graph.label}</h2><code>{graph.graph_id}</code></div><AssetStateSummary state={graph.state} /></header>
        <p>Task {graph.compatible_task_ids.length}件 · Transform {graph.compatible_transform_ids.length}件 · Project {graph.project_references.length}件</p>
        {graph.definitions.map((definition) => <details className="model-graph-detail" key={definition.definition_id}>
          <summary>{definition.revisions.length}件の固定Revision · input {definition.projection.inputs.length} · decision output {definition.projection.decision_outputs.length}</summary>
          <div className="model-graph-flow">
            <section><h3>Inputs</h3><ul>{definition.projection.inputs.map((input) => <li key={input.input_id}>{input.label}</li>)}</ul></section>
            <section><h3>Stages / fixed references</h3>
              <p className="model-graph-layers">Branch layers: {definition.projection.topology.topological_layers.map((layer) => layer.join(" + ")).join(" → ")}</p>
              {definition.revisions.map((revision) => <div className="model-graph-revision" key={revision.revision_id}>
              <strong>Revision {revision.revision}</strong><span>{lifecycleLabel[revision.state.lifecycle]} · {availabilityLabel[revision.state.availability]}</span>
              <ul>{revision.stages.map((stage) => <li key={stage.stage_id}><b>{stage.stage_id}</b><span>{stage.contract_id} · {shortDigest(stage.package_manifest_digest)}</span>{!stage.available && <small>{stage.reason}</small>}</li>)}</ul>
            </div>)}</section>
            <section><h3>Decision outputs</h3><ul>{definition.projection.decision_outputs.map((output) => <li key={output.output_id}>{output.label}<small>{output.required_for_complete_result ? "必須" : "任意"}</small></li>)}</ul></section>
          </div>
        </details>)}
        <div className="model-asset-actions"><button type="button" className="primary-button" onClick={onOpenStudio}>Studioで新しいRevisionを作成</button></div>
      </article>)}
    </div>}
  </section>;
}
