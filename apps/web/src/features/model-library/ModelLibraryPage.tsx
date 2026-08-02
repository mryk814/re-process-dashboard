import { useEffect, useMemo, useState } from "react";
import {
  workbenchApi,
  type ApiModelLibraryCatalog,
  type ApiPredictionGraphDefinition,
} from "../../shared/api/workbench-api";
import {
  draftDefinitionFromCatalog,
  type ModelLibraryDataIntent,
  type ModelLibraryProjectIntent,
  type ModelLibraryTab,
} from "../../shared/modelLibrary";

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

function dataReferenceEntries(
  reference: ApiModelLibraryCatalog["packages"][number]["data_references"],
): Array<[string, string]> {
  return [
    ["Dataset View Revision", reference.dataset_view_revision_ids.join(" / ") || "記録なし"],
    ["Dataset Revision", reference.dataset_revision_ids.join(" / ") || "記録なし"],
    ["Profile Revision", reference.profile_revision_ids.join(" / ") || "記録なし"],
    ["Profile digest", reference.profile_digests.map(shortDigest).join(" / ") || "記録なし"],
    ["Training Snapshot", reference.training_snapshot_id ?? "記録なし"],
    ["Connector", reference.connector_id ?? "記録なし"],
  ];
}

export function ModelLibraryPage({
  tab,
  dataContext,
  onTabChange,
  onClearDataContext,
  onOpenDataLibrary,
  onOpenStudio,
  onStartProject,
}: {
  tab: ModelLibraryTab;
  dataContext?: ModelLibraryDataIntent;
  onTabChange: (tab: ModelLibraryTab) => void;
  onClearDataContext: () => void;
  onOpenDataLibrary: (intent?: ModelLibraryDataIntent) => void;
  onOpenStudio: (definition?: ApiPredictionGraphDefinition) => Promise<void>;
  onStartProject: (intent: ModelLibraryProjectIntent) => void;
}) {
  const [catalog, setCatalog] = useState<ApiModelLibraryCatalog | null>(null);
  const [error, setError] = useState("");
  const [requestVersion, setRequestVersion] = useState(0);
  const [openingStudio, setOpeningStudio] = useState(false);

  async function openStudio(definition?: ApiPredictionGraphDefinition) {
    setOpeningStudio(true);
    setError("");
    try {
      await onOpenStudio(definition);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Graph draftを作成できませんでした。");
    } finally {
      setOpeningStudio(false);
    }
  }

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

  const contextDatasetRevisionId = dataContext?.datasetRevisionId;
  const relatedPackages = contextDatasetRevisionId
    ? catalog.packages.filter((item) => item.data_references.dataset_revision_ids.includes(contextDatasetRevisionId))
    : catalog.packages;
  const relatedPackageDigests = new Set(relatedPackages.map((item) => item.manifest_digest));
  const relatedGraphs = contextDatasetRevisionId
    ? catalog.graphs.filter((graph) => graph.definitions.some((definition) =>
      definition.revisions.some((revision) => revision.stages.some((stage) =>
        stage.data_references.dataset_revision_ids.includes(contextDatasetRevisionId)
        || relatedPackageDigests.has(stage.package_manifest_digest),
      )),
    ))
    : catalog.graphs;
  const counts: Record<ModelLibraryTab, number> = {
    tasks: catalog.tasks.length,
    packages: relatedPackages.length,
    transforms: catalog.transforms.length,
    graphs: relatedGraphs.length,
  };

  return <section className="model-library-page">
    <header className="model-library-header">
      <div>
        <span className="overline">MODEL LIBRARY · READ ONLY</span>
        <h1>モデル資産を確認する</h1>
        <p>利用できる契約と固定Revisionを比較し、データ準備、Project利用、Graph authoringへ進みます。</p>
      </div>
      <div className="model-library-header-actions">
        <button type="button" className="outline-button" onClick={() => onOpenDataLibrary()}>Data Library</button>
        <button type="button" className="primary-button" disabled={openingStudio} onClick={() => void openStudio()}>
          {openingStudio ? "Graphを準備中…" : "Graphを作成"}
        </button>
      </div>
    </header>
    {error && <div className="model-library-refresh-error" role="alert">
      <span>{error}。取得済みの一覧を保持しています。</span>
      <button type="button" className="text-button" onClick={() => setRequestVersion((value) => value + 1)}>再試行</button>
    </div>}
    {contextDatasetRevisionId && <div className="model-library-refresh-error" role="status">
      <span>
        Dataset Revision <code>{contextDatasetRevisionId}</code> を固定参照する
        Package {relatedPackages.length}件 / Graph {relatedGraphs.length}件を表示しています。
      </span>
      <button type="button" className="text-button" onClick={onClearDataContext}>全資産を見る</button>
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
          && item.data_references.dataset_revision_ids.length > 0
          && item.state.availability === "available");
        const projectUnavailableId = `task-project-unavailable-${task.task_id.replaceAll(/[^a-zA-Z0-9_-]/g, "-")}`;
        return <article className="model-asset-card" key={task.task_id}>
          <header><div><span className="model-asset-kind">TASK</span><h2>{task.label}</h2><code>{task.task_id}</code></div><AssetStateSummary state={task.state} /></header>
          <p>{task.inputs.length}入力から{task.outputs.length}出力を予測 · Package {task.package_reference_ids.length}件 · Graph {task.graph_revision_ids.length}件</p>
          <div className="model-asset-actions">
            <button type="button" className="outline-button" onClick={() => onOpenDataLibrary({
              datasetRevisionId: packageWithData?.data_references.dataset_revision_ids[0],
              packageReferenceId: packageWithData?.reference_id,
            })}>対応データを確認</button>
            <button
              type="button"
              className="primary-button"
              disabled={!packageWithData}
              aria-describedby={!packageWithData ? projectUnavailableId : undefined}
              onClick={() => packageWithData && onStartProject({
                kind: "single_task",
                datasetViewRevisionId: packageWithData.data_references.dataset_view_revision_ids[0],
                datasetRevisionId: packageWithData.data_references.dataset_revision_ids[0],
                taskId: task.task_id,
                packageReferenceId: packageWithData.reference_id,
                packageManifestDigest: packageWithData.manifest_digest,
              })}
            >Projectを作成</button>
          </div>
          {!packageWithData && <p id={projectUnavailableId} className="model-action-reason">利用可能なPackageとDataset参照が揃うとProjectを作成できます。</p>}
          <IdentityDetails title="入出力と契約identity" entries={[
            ["Task contract", shortDigest(task.contract_digest)],
            ["Inputs", task.inputs.map((port) => `${port.label}${port.unit && port.unit !== "1" ? ` (${port.unit})` : ""}`).join(" / ")],
            ["Outputs", task.outputs.map((port) => `${port.label}${port.unit && port.unit !== "1" ? ` (${port.unit})` : ""}`).join(" / ")],
          ]} />
        </article>;
      })}
    </div>}

    {tab === "packages" && <div className="model-asset-list" role="tabpanel" id="model-library-panel-packages" aria-labelledby="model-library-tab-packages">
      {relatedPackages.length === 0 && (contextDatasetRevisionId
        ? <div className="model-library-empty" role="status"><strong>このDatasetを固定参照するModel Packageはありません</strong><span>全資産表示へ戻るか、Data Libraryで別のDatasetを選んでください。</span></div>
        : <EmptyAssetState label="Model Package" />)}
      {relatedPackages.map((item) => {
        const datasetRevisionId = contextDatasetRevisionId
          ?? item.data_references.dataset_revision_ids[0];
        const datasetViewId = contextDatasetRevisionId
          ? item.data_references.dataset_revision_ids.length === 1
            && item.data_references.dataset_view_revision_ids.length === 1
            ? item.data_references.dataset_view_revision_ids[0]
            : undefined
          : item.data_references.dataset_view_revision_ids[0];
        const projectAvailable = Boolean(datasetViewId && datasetRevisionId && item.state.availability === "available");
        const projectUnavailableId = `package-project-unavailable-${item.reference_id.replaceAll(/[^a-zA-Z0-9_-]/g, "-")}`;
        return <article className="model-asset-card" key={item.reference_id}>
          <header><div><span className="model-asset-kind">PACKAGE · {item.storage_scope === "personal" ? "PERSONAL" : "BUNDLED"}</span><h2>{item.package_id}</h2><span>{taskLabels.get(item.task_id) ?? item.task_id} · {item.version}</span></div><AssetStateSummary state={item.state} /></header>
          <p>{item.predictor_families.map((predictor) => `${predictor.target}: ${predictor.predictive_family}`).join(" · ") || "predictor identityなし"}</p>
          <div className="model-asset-actions">
            <button type="button" className="outline-button" onClick={() => onOpenDataLibrary({
              datasetRevisionId,
              packageReferenceId: item.reference_id,
            })}>Data Libraryで確認</button>
            <button
              type="button"
              className="primary-button"
              disabled={!projectAvailable}
              aria-describedby={!projectAvailable ? projectUnavailableId : undefined}
              onClick={() => {
                if (!projectAvailable || !datasetViewId || !datasetRevisionId) return;
                onStartProject({
                  kind: "single_task",
                  datasetViewRevisionId: datasetViewId,
                  datasetRevisionId,
                  taskId: item.task_id,
                  packageReferenceId: item.reference_id,
                  packageManifestDigest: item.manifest_digest,
                });
              }}
            >Projectを作成</button>
          </div>
          {!projectAvailable && <p id={projectUnavailableId} className="model-action-reason">
            {item.state.availability !== "available"
              ? `${item.state.reason}。${item.state.recovery_hint}`
              : contextDatasetRevisionId && !datasetViewId
                ? "このPackageには複数のDataset／View参照があります。Data Libraryで利用するViewを選んでからProjectを作成してください。"
              : "Dataset ViewとDataset Revisionの固定参照が揃うとProjectを作成できます。"}
          </p>}
          <IdentityDetails title="Pipeline・検証・固定参照" entries={[
            ["Manifest", shortDigest(item.manifest_digest)],
            ["Feature Pipeline", item.feature_pipeline ? `${item.feature_pipeline.identity_id} · ${item.feature_pipeline.version}` : "記録なし"],
            ["Feature Recipe", item.feature_recipe ? `${item.feature_recipe.identity_id} · ${item.feature_recipe.version} · ${shortDigest(item.feature_recipe.digest)}` : "未使用"],
            ["Validation Plan", item.validation_plans.map((plan) => `${plan.target}: ${plan.strategy} (${shortDigest(plan.digest)})`).join(" / ") || "記録なし"],
            ["Quality summary", item.quality_summary_available ? "品質要約あり" : "未登録"],
            ["Training source", item.data_references.source_names.join(" / ") || "記録なし"],
            ...dataReferenceEntries(item.data_references),
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
      {relatedGraphs.length === 0 && (contextDatasetRevisionId
        ? <div className="model-library-empty" role="status"><strong>このDatasetを固定参照するPrediction Graphはありません</strong><span>全資産表示へ戻るか、Data Libraryで別のDatasetを選んでください。</span></div>
        : <EmptyAssetState label="Prediction Graph" />)}
      {relatedGraphs.map((graph) => <article className="model-asset-card model-graph-card" key={graph.graph_id}>
        <header><div><span className="model-asset-kind">PREDICTION GRAPH</span><h2>{graph.label}</h2><code>{graph.graph_id}</code></div><AssetStateSummary state={graph.state} /></header>
        <p>Task {graph.compatible_task_ids.length}件 · Transform {graph.compatible_transform_ids.length}件 · Project {graph.project_references.length}件</p>
        {graph.definitions.filter((definition) => !contextDatasetRevisionId || definition.revisions.some((revision) =>
          revision.stages.some((stage) =>
            stage.data_references.dataset_revision_ids.includes(contextDatasetRevisionId)
            || relatedPackageDigests.has(stage.package_manifest_digest),
          ),
        )).map((definition) => {
          const visibleRevisions = contextDatasetRevisionId
            ? definition.revisions.filter((revision) => revision.stages.some((stage) =>
              stage.data_references.dataset_revision_ids.includes(contextDatasetRevisionId)
              || relatedPackageDigests.has(stage.package_manifest_digest),
            ))
            : definition.revisions;
          const studioDefinition = definition.definition.schema_version === "prediction-graph-definition/v1"
            ? definition.definition
            : undefined;
          const studioAvailable = Boolean(studioDefinition);
          const studioReasonId = `studio-unavailable-${definition.definition_id.replaceAll(/[^a-zA-Z0-9_-]/g, "-")}`;
          return <details className="model-graph-detail" key={definition.definition_id}>
          <summary>{visibleRevisions.length}件の固定Revision · input {definition.projection.inputs.length} · decision output {definition.projection.decision_outputs.length}</summary>
          <div className="model-graph-flow">
            <section><h3>Inputs</h3><ul>{definition.projection.inputs.map((input) => <li key={input.input_id}>{input.label}</li>)}</ul></section>
            <section><h3>Stages / fixed references</h3>
              <p className="model-graph-layers">Branch layers: {definition.projection.topology.topological_layers.map((layer) => layer.join(" + ")).join(" → ")}</p>
              {visibleRevisions.map((revision) => {
                const directContextStages = contextDatasetRevisionId
                  ? revision.stages.filter((stage) => stage.data_references.dataset_revision_ids.includes(contextDatasetRevisionId))
                  : revision.stages;
                const contextViewIds = [...new Set(directContextStages.flatMap((stage) => stage.data_references.dataset_view_revision_ids))];
                const datasetViewRevisionId = contextDatasetRevisionId
                  ? contextViewIds.length === 1 ? contextViewIds[0] : undefined
                  : contextViewIds[0];
                const projectAvailable = revision.state.availability === "available"
                  && revision.stages.every((stage) => stage.available);
                const projectReasonId = `graph-project-unavailable-${revision.revision_id.replaceAll(/[^a-zA-Z0-9_-]/g, "-")}`;
                return <div className="model-graph-revision" key={revision.revision_id}>
              <strong>Revision {revision.revision}</strong><span>{lifecycleLabel[revision.state.lifecycle]} · {availabilityLabel[revision.state.availability]}</span>
              <code>{revision.revision_id} · {shortDigest(revision.revision_digest)}</code>
              <ul>{revision.stages.map((stage) => <li key={stage.stage_id}>
                <b>{stage.stage_id}</b>
                <span>{stage.contract_id} · {shortDigest(stage.package_manifest_digest)}</span>
                <small>{dataReferenceEntries(stage.data_references).map(([label, value]) => `${label}: ${value}`).join(" · ")}</small>
                {!stage.available && <small>{stage.reason}</small>}
              </li>)}</ul>
              <small>Project refs: {revision.project_references.map((project) => project.project_id).join(" / ") || "未使用"}</small>
              <div className="model-graph-revision-actions">
                <button
                  type="button"
                  className="outline-button"
                  disabled={!projectAvailable}
                  aria-describedby={!projectAvailable ? projectReasonId : undefined}
                  onClick={() => projectAvailable && onStartProject({
                    kind: "graph",
                    graphId: graph.graph_id,
                    definitionId: definition.definition_id,
                    revisionId: revision.revision_id,
                    revisionDigest: revision.revision_digest,
                    datasetViewRevisionId,
                  })}
                >このRevisionでProjectを作成</button>
              </div>
              {!projectAvailable && <p id={projectReasonId} className="model-action-reason">
                {revision.state.reason}。{revision.state.recovery_hint}
              </p>}
            </div>;
              })}</section>
            <section><h3>Decision outputs</h3><ul>{definition.projection.decision_outputs.map((output) => <li key={output.output_id}>{output.label}<small>{output.required_for_complete_result ? "必須" : "任意"}</small></li>)}</ul></section>
          </div>
          <div className="model-asset-actions">
            <button
              type="button"
              className="primary-button"
              disabled={!studioAvailable || openingStudio}
              aria-describedby={!studioAvailable ? studioReasonId : undefined}
              onClick={() => studioDefinition && void openStudio(draftDefinitionFromCatalog(studioDefinition))}
            >{openingStudio ? "Graphを準備中…" : "Studioで新しいRevisionを作成"}</button>
          </div>
          {studioAvailable && <p className="model-action-reason">
            複製元のevidenceを保持します。公開前に根拠の種類・制約・利用範囲を確認してください。
          </p>}
          {!studioAvailable && <p id={studioReasonId} className="model-action-reason">
            この既存Chain定義は参照専用です。固定RevisionのProject利用は上の操作から開始できます。
          </p>}
        </details>;
        })}
      </article>)}
    </div>}
  </section>;
}
