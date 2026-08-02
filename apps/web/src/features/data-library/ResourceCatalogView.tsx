import { useEffect, useMemo, useState } from "react";
import {
  type ApiDataLibraryDataset,
  type ApiModelPackageRef,
  type ApiProject,
} from "../../shared/api/workbench-api";
import {
  compatibleTaskIdsForDataset,
  datasetDisplayName,
  modelPackageDecisionSummary,
  modelPackageDisplayNames,
  trainingDataset,
} from "../../shared/dataLibraryPresentation";
import { useTaskLabels } from "../../shared/useTaskLabels";
import { SeriesLibrarySection } from "./SeriesLibrarySection";
import type { PreparedCsvProjectBinding } from "./CsvTaskOnboarding";
import {
  DataLibraryResourceLoading,
  DataLibraryResourceNotice,
} from "./DataLibraryResourceStatus";
import { DataOnboardingHub } from "./DataOnboardingHub";
import {
  dataLibraryResourceFamilies,
  type DataLibraryResources,
} from "./useDataLibraryResources";
import {
  packageTrainingSnapshotLink,
  useResourceCatalogActions,
} from "./useResourceCatalogActions";
import type { DataLibraryLocation } from "./location";

const shortDigest = (value: string) => value.replace(/^sha256:/, "").slice(0, 10);
const formatDate = (value: string) => new Date(value).toLocaleDateString("ja-JP");
const modelStorageLabel = (item: ApiModelPackageRef) => item.storage_scope === "personal"
  ? "自分のモデル"
  : "同梱モデル";
export function ResourceCatalogView({
  projects,
  onAddDataset,
  onStartProject,
  onOpenTrainingData,
  onOpenStorage,
  location,
  onNavigate,
  resources,
  compareOpen,
  onCompareOpenChange,
}: {
  projects: ApiProject[];
  onAddDataset: (
    mode?: "revision" | "mapping",
    baseDatasetRevisionId?: string,
  ) => void;
  onStartProject: (datasetViewRevisionId: string, binding?: Omit<PreparedCsvProjectBinding, "datasetViewId">) => void;
  onOpenTrainingData: (projectId: string) => void;
  onOpenStorage: () => void;
  location: DataLibraryLocation;
  onNavigate: (location: DataLibraryLocation, replace?: boolean) => void;
  resources: DataLibraryResources;
  compareOpen: boolean;
  onCompareOpenChange: (open: boolean) => void;
}) {
  const {
    options,
    datasets,
    modelPackages,
    resourceStates,
    loadResources,
    retryResource,
  } = resources;
  const actions = useResourceCatalogActions({ resources, onNavigate });
  const [compareName, setCompareName] = useState("");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [selectedDatasetId, setSelectedDatasetId] = useState(location.datasetRevisionId ?? "");
  const [modelGuideOpen, setModelGuideOpen] = useState(false);
  const [guideTaskId, setGuideTaskId] = useState("");
  const [copiedGuide, setCopiedGuide] = useState(false);
  const [samplesOpen, setSamplesOpen] = useState(false);
  const [datasetStateFilter, setDatasetStateFilter] = useState("available");
  const taskLabel = useTaskLabels();
  const allResourcesUnavailable = dataLibraryResourceFamilies.every((family) => {
    const state = resourceStates[family];
    return state.phase === "error" && !state.loadedAt;
  });
  const initialLoading = dataLibraryResourceFamilies.some((family) => {
    const state = resourceStates[family];
    return state.phase === "loading" && !state.loadedAt;
  });
  const projectCreationAvailabilityConfirmed = resourceStates.options.phase === "ready"
    && resourceStates.modelPackages.phase === "ready";
  const renderResourceNotice = (
    family: keyof typeof resourceStates,
    impact: string,
  ) => <DataLibraryResourceNotice
    family={family}
    impact={impact}
    resourceStates={resourceStates}
    onRetry={(target) => void retryResource(target)}
  />;
  const renderResourceLoading = (family: keyof typeof resourceStates) =>
    <DataLibraryResourceLoading family={family} resourceStates={resourceStates} />;

  const comparisonSets = options?.dataset_views.filter((view) => view.kind === "cohort_comparison") ?? [];
  const filteredDatasets = useMemo(
    () => datasets.filter((item) => {
      if (datasetStateFilter === "available") return !item.dataset_revision.archived_at;
      if (datasetStateFilter === "archived") return Boolean(item.dataset_revision.archived_at);
      return true;
    }),
    [datasetStateFilter, datasets],
  );
  const managedDatasets = filteredDatasets.filter((item) => item.data_asset.locator_kind === "managed");
  const bundledDatasets = filteredDatasets.filter((item) => item.data_asset.locator_kind === "bundled");
  const selectedDataset = filteredDatasets.find((item) => item.dataset_revision.id === selectedDatasetId)
    ?? managedDatasets[0]
    ?? bundledDatasets[0];
  const selectedEffectiveProfile = selectedDataset?.profile_revision.effective_profile_json;
  const selectedLineage = selectedDataset?.dataset_views
    ?.flatMap((view) => view.members)
    .map((member) => member.provenance_json)
    .find((provenance) => provenance?.lineage_kind === "dataset_revision_update");
  const requiresExactProfile = Boolean(
    selectedEffectiveProfile
    && "shared" in selectedEffectiveProfile
    && "tasks" in selectedEffectiveProfile,
  );
  const exactProfileMissing = Boolean(
    selectedDataset
    && requiresExactProfile
    && !selectedDataset.profile_available,
  );
  const selectedDatasetPackages = selectedDataset
    ? modelPackages.filter((item) => trainingDataset(item, datasets)?.dataset_revision.id === selectedDataset.dataset_revision.id)
    : [];
  const packageDisplayNames = useMemo(
    () => modelPackageDisplayNames(modelPackages),
    [modelPackages],
  );
  useEffect(() => {
    if (!selectedDataset) return;
    setSelectedDatasetId(selectedDataset.dataset_revision.id);
    setGuideTaskId((current) => selectedDataset.supported_task_ids.includes(current)
      ? current
      : selectedDataset.supported_task_ids[0] ?? "");
  }, [selectedDataset?.dataset_revision.id]);
  useEffect(() => {
    if (!location.datasetRevisionId) return;
    const requested = datasets.find((item) => item.dataset_revision.id === location.datasetRevisionId);
    if (requested) setSelectedDatasetId(requested.dataset_revision.id);
  }, [datasets, location.datasetRevisionId]);
  useEffect(() => {
    if (!location.packageReferenceId) return;
    window.requestAnimationFrame(() => {
      const target = document.querySelector<HTMLElement>(
        `[data-model-package-ref="${CSS.escape(location.packageReferenceId!)}"]`,
      );
      target?.scrollIntoView({ block: "nearest" });
      target?.focus({ preventScroll: true });
    });
  }, [location.packageReferenceId, selectedDataset?.dataset_revision.id]);
  const modelGuide = useMemo(() => {
    if (!selectedDataset || !guideTaskId || exactProfileMissing) return "";
    const quote = (value: string) => `'${value.replaceAll("'", "''")}'`;
    const profileArgument = selectedDataset.profile_available ? " --profile $profile" : "";
    const profileSetup = selectedDataset.profile_available
      ? ['$profile = "<登録時と同じProfile JSONのパス>"']
      : [];
    const profileOption = selectedDataset.profile_available ? ["  --profile $profile `"] : [];
    return [
      `$task = ${quote(guideTaskId)}`,
      '$source = "<元データのExcelまたはCSVのパス>"',
      ...profileSetup,
      '$packageId = "$task-local-$(Get-Date -Format yyyyMMdd-HHmmss)"',
      '$packageVersion = "1.0.0"',
      '$datasetOutput = "artifacts/model-data/$packageId.json"',
      "$modelStore = if ($env:WORKBENCH_MODEL_STORE_PATH) { $env:WORKBENCH_MODEL_STORE_PATH } else { Join-Path $env:LOCALAPPDATA 'Material Decision Workbench\\models' }",
      "",
      `npm run model:diagnose -- --task $task --source $source${profileArgument}`,
      "",
      "npm run model:build -- `",
      "  --task $task `",
      "  --source $source `",
      ...profileOption,
      "  --package-id $packageId `",
      "  --package-version $packageVersion `",
      "  --dataset-output $datasetOutput",
      "",
      "npm run model:promote -- `",
      "  --task $task `",
      "  --source $source `",
      ...profileOption,
      '  --package "artifacts/model-package-candidates/$packageId" `',
      "  --store $modelStore",
      "",
      "npm run task:inventory",
      "npm run model:status",
    ].join("\n");
  }, [exactProfileMissing, guideTaskId, selectedDataset]);

  const toggleDataset = (dataset: ApiDataLibraryDataset) => {
    const id = dataset.dataset_revision.id;
    setSelectedIds((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  };

  async function createComparison() {
    if (await actions.createComparison(compareName, selectedIds)) {
      onCompareOpenChange(false);
      setCompareName("");
      setSelectedIds([]);
    }
  }

  const openModelGuide = (taskId?: string) => {
    if (!selectedDataset) return;
    setGuideTaskId(taskId ?? selectedDataset.supported_task_ids[0] ?? "");
    setCopiedGuide(false);
    setModelGuideOpen(true);
  };

  const copyModelGuide = async () => {
    try {
      await navigator.clipboard.writeText(modelGuide);
      setCopiedGuide(true);
    } catch {
      actions.reportError("PowerShell手順をコピーできませんでした。テキスト欄を選択してコピーしてください。");
    }
  };

  const renderDatasetCard = (item: ApiDataLibraryDataset) => {
    const singleView = item.dataset_views?.find((view) => view.kind === "single");
    const relatedViewIds = new Set(item.dataset_views?.map((view) => view.id) ?? []);
    const usingProjects = projects.filter((project) => project.dataset_view_revision_id && relatedViewIds.has(project.dataset_view_revision_id));
    const compatibleTaskIds = options ? compatibleTaskIdsForDataset(item, options) : [];
    const relatedPackages = modelPackages.filter(
      (modelPackage) => trainingDataset(modelPackage, datasets)?.dataset_revision.id === item.dataset_revision.id,
    );
    const archived = Boolean(item.dataset_revision.archived_at);
    const startUnavailableReason = !projectCreationAvailabilityConfirmed
      ? "予測タスクとModel Packageの現在値を確認できません"
      : item.supported_task_ids.length === 0
      ? "対応する予測タスクがありません"
      : compatibleTaskIds.length === 0
        ? "利用可能なModel Packageがありません"
        : "";
    const selected = selectedDataset?.dataset_revision.id === item.dataset_revision.id;
    return <article className={`dataset-card ${selected ? "selected" : ""}`} key={item.dataset_revision.id}>
      <button
        type="button"
        className="dataset-card-select"
        aria-pressed={selected}
        aria-label={`${datasetDisplayName(item)}の詳細を表示`}
        onClick={() => {
          setSelectedDatasetId(item.dataset_revision.id);
          setModelGuideOpen(false);
        }}
      >
        <span className="dataset-card-main">
          <strong title={item.data_asset.original_filename}>{item.data_asset.original_filename}</strong>
          <span>{item.data_asset.locator_kind === "managed" ? "自分のデータ" : "同梱サンプル"} · {formatDate(item.dataset_revision.created_at)}</span>
          {archived && <small className="resource-state archived">利用停止中</small>}
        </span>
        <span className="dataset-card-summary">
          <span>{item.supported_task_ids.length ? item.supported_task_ids.map(taskLabel).join(" / ") : "予測タスク未定義"}</span>
          <small>{relatedPackages.filter((modelPackage) => !modelPackage.archived_at).length}モデル · {usingProjects.length}プロジェクト</small>
        </span>
      </button>
      <div className="dataset-project-links">
        {!archived && singleView && <button
          className="outline-button dataset-start-project"
          aria-label={`${datasetDisplayName(item)}でプロジェクトを作成${startUnavailableReason ? `：${startUnavailableReason}` : ""}`}
          title={startUnavailableReason || `${datasetDisplayName(item)}でプロジェクトを作成`}
          disabled={Boolean(startUnavailableReason)}
          onClick={() => onStartProject(singleView.id)}
        >プロジェクト作成</button>}
        <details className="resource-manage-menu">
          <summary aria-label={`${datasetDisplayName(item)}の管理`}>管理</summary>
          <div>
            <strong>{datasetDisplayName(item)}</strong>
            <small>{archived ? "新規利用を再開します。" : usingProjects.length > 0 ? `${usingProjects.length}件のプロジェクトが参照中のため利用停止できません。` : "元データは残し、一覧と新規利用から外します。"}</small>
            <button
              type="button"
              className={archived ? "outline-button resource-state-action" : "text-button resource-state-action"}
              disabled={actions.changingResourceId === item.dataset_revision.id || (!archived && usingProjects.length > 0)}
              onClick={() => void actions.changeDatasetState(item)}
            >{actions.changingResourceId === item.dataset_revision.id ? "更新中…" : archived ? "利用可能に戻す" : "利用停止にする"}</button>
          </div>
        </details>
      </div>
    </article>;
  };

  return (
    <>
      <DataOnboardingHub
        selectedDataset={selectedDataset}
        location={location}
        onAddDataset={onAddDataset}
        onStartProject={onStartProject}
        onOpenStorage={onOpenStorage}
        onNavigate={onNavigate}
      />
      {actions.error && <p className="panel-error" role="alert">{actions.error}</p>}
      {actions.undoAction && <div className="library-undo" role="status">
        <span>{actions.undoAction.label}を{actions.undoAction.archived ? "復元" : "利用停止"}しました。</span>
        <button type="button" className="text-button" disabled={actions.changingResourceId === actions.undoAction.id} onClick={() => void actions.undoLastChange()}>元に戻す</button>
        <button type="button" className="text-button library-undo-dismiss" aria-label="通知を閉じる" onClick={actions.dismissUndo}>×</button>
      </div>}
      {compareOpen && options && <section id="dataset-comparison-builder" className="dataset-compare-builder" aria-labelledby="dataset-comparison-heading">
        <div><h3 id="dataset-comparison-heading">境界を保った比較セット</h3><p>設備・場所などの違いを残したまま並べます。学習用に自動結合はしません。</p></div>
        <label>比較名<input value={compareName} onChange={(event) => setCompareName(event.target.value)} placeholder="設備A / 設備B 比較" /></label>
        <div className="dataset-compare-options">{options.datasets.map((item) => <label key={item.dataset_revision.id}><input type="checkbox" checked={selectedIds.includes(item.dataset_revision.id)} onChange={() => toggleDataset(item)} />{datasetDisplayName(item)}</label>)}</div>
        <button className="primary-button" disabled={selectedIds.length < 2} onClick={() => void createComparison()}>比較セットを作成</button>
      </section>}

      {initialLoading && <p className="project-empty-inline" role="status">データライブラリを読み込んでいます…</p>}
      {allResourcesUnavailable && <section className="library-error-state" aria-labelledby="data-library-offline-heading">
        <div>
          <h3 id="data-library-offline-heading">Data Libraryに接続できません</h3>
          <p>Dataset、予測タスク、Model Packageを取得できませんでした。接続を確認してから再読み込みしてください。</p>
        </div>
        <button className="outline-button" onClick={() => void loadResources()}>すべて再読み込み</button>
      </section>}
      {resourceStates.datasets.phase !== "loading" && !allResourcesUnavailable && <>
        <section className="data-library-section dataset-library">
          <div className="panel-title library-title-with-filter">
            <div><h3>使うデータを選ぶ</h3><span>{filteredDatasets.length} / {datasets.length}件</span></div>
            <label>状態<select value={datasetStateFilter} onChange={(event) => setDatasetStateFilter(event.target.value)}><option value="available">利用可能</option><option value="archived">利用停止中</option><option value="">すべて</option></select></label>
          </div>
          {renderResourceLoading("datasets")}
          {renderResourceNotice("datasets", "Datasetを取得できるまで、source／revisionの閲覧とProject作成はできません。")}
          {renderResourceLoading("options")}
          {renderResourceNotice("options", "Datasetは閲覧できますが、予測タスクとProject作成条件は確認できません。")}
          {!selectedDataset && renderResourceNotice(
            "modelPackages",
            "Datasetが空でもModel Packageの取得失敗を確認できます。失敗した項目だけを再試行してください。",
          )}
          {(resourceStates.datasets.phase === "ready" || resourceStates.datasets.loadedAt) && <div className="dataset-groups">
            <section aria-labelledby="managed-datasets-heading">
              <div className="dataset-group-title"><h4 id="managed-datasets-heading">自分のデータ</h4><span>{managedDatasets.length}件</span></div>
              {managedDatasets.length
                ? <div className="dataset-list">{managedDatasets.map(renderDatasetCard)}</div>
                : <div className="dataset-group-empty"><p>追加したExcelやCSVはここにまとまります。</p><button className="primary-button" onClick={() => onAddDataset("mapping")}>最初のデータセットを追加</button></div>}
            </section>
            <details className="bundled-dataset-group" open={samplesOpen} onToggle={(event) => setSamplesOpen(event.currentTarget.open)}>
              <summary><span>同梱サンプル</span><small>{bundledDatasets.length}件 · 必要なときだけ開く</small></summary>
              <div className="dataset-list">{bundledDatasets.map(renderDatasetCard)}</div>
            </details>
          </div>}
          {resourceStates.datasets.phase === "ready" && filteredDatasets.length === 0 && <p className="library-empty">この状態のDatasetはありません。</p>}
        </section>

        {selectedDataset && <section className="data-library-section dataset-context" aria-labelledby="dataset-context-heading">
          <header>
            <div>
              <span className="overline">SELECTED DATASET</span>
              <h3 id="dataset-context-heading">{selectedDataset.data_asset.original_filename}</h3>
              <p>{selectedDataset.supported_task_ids.map(taskLabel).join(" / ") || "予測タスク未定義"}</p>
            </div>
            <div className="dataset-context-actions">
              <button className="outline-button" type="button" disabled={selectedDataset.supported_task_ids.length === 0} onClick={() => openModelGuide()}>このデータでモデルを更新</button>
            </div>
          </header>
          <div className="dataset-context-facts">
            <div><span>データの種類</span><strong>{selectedDataset.data_asset.locator_kind === "managed" ? "自分で追加" : "同梱サンプル"}</strong></div>
            <div><span>プロファイル</span><strong>{selectedDataset.profile_revision.name} · r{selectedDataset.profile_revision.revision}</strong></div>
            <div><span>利用できるモデル</span><strong>{resourceStates.modelPackages.phase === "error" ? `前回: ${selectedDatasetPackages.filter((item) => !item.archived_at).length}件` : `${selectedDatasetPackages.filter((item) => !item.archived_at).length}件`}</strong></div>
            <details><summary>識別情報</summary><code title={selectedDataset.dataset_revision.dataset_digest}>{shortDigest(selectedDataset.dataset_revision.dataset_digest)}</code><small title={selectedDataset.data_asset.sha256}>source SHA-256: {shortDigest(selectedDataset.data_asset.sha256)}</small></details>
          </div>
          {selectedLineage && <div className="dataset-revision-lineage" aria-label="Dataset revision lineage">
            <span>Source更新</span>
            <code title={String(selectedLineage.previous_source_sha256 ?? "")}>{shortDigest(String(selectedLineage.previous_source_sha256 ?? ""))}</code>
            <b aria-hidden="true">→</b>
            <code title={selectedDataset.data_asset.sha256}>{shortDigest(selectedDataset.data_asset.sha256)}</code>
            <small title={String(selectedLineage.previous_dataset_revision_id ?? "")}>同じProfileから作成した新しいDataset Revision</small>
          </div>}
          <div className="model-package-library">
            <div className="panel-title"><h4>このデータで使うモデル</h4><span>{selectedDatasetPackages.length}件</span></div>
            {renderResourceLoading("modelPackages")}
            {renderResourceNotice("modelPackages", "Model Packageを取得できるまで、このDatasetでProjectを作成できるか判断できません。")}
            {selectedDatasetPackages.length > 0
              ? <div className="model-package-list">{selectedDatasetPackages.map((item) => {
                const source = trainingDataset(item, datasets);
                const decision = modelPackageDecisionSummary(item);
                const usingProjects = projects.filter((project) => project.model_package_ref_id === item.id);
                const trainingSnapshotLink = packageTrainingSnapshotLink(item);
                return <article
                  key={item.id}
                  data-model-package-ref={item.id}
                  tabIndex={location.packageReferenceId === item.id ? -1 : undefined}
                  aria-label={location.packageReferenceId === item.id
                    ? `${packageDisplayNames.get(item.id)}（Model Libraryから選択）`
                    : undefined}
                >
                  <div>
                    <strong>{packageDisplayNames.get(item.id)}</strong>
                    <span title={item.task_id}>{taskLabel(item.task_id)}</span>
                    <span className="package-badges">
                      <small className={`package-origin ${item.storage_scope}`}>{modelStorageLabel(item)}</small>
                      <small className={item.archived_at ? "package-state archived" : "package-state"}>{item.archived_at ? "アーカイブ" : decision?.experimental ? "試験モデル" : "利用可能"}</small>
                    </span>
                  </div>
                  <dl>
                    <div><dt>使いどころ</dt><dd>{decision?.useCase ?? "—"}</dd></div>
                    <div><dt>学習単位</dt><dd>{decision?.trainingUnit ?? "—"}</dd></div>
                    <div><dt>予測タスク</dt><dd>{taskLabel(item.task_id)}</dd></div>
                    <div><dt>学習時プロファイル</dt><dd>{source ? `${source.profile_revision.name} · r${source.profile_revision.revision}` : "—"}</dd></div>
                  </dl>
                  {usingProjects[0] && <div className="model-package-actions">
                    <button
                      type="button"
                      className="outline-button"
                      onClick={() => onOpenTrainingData(usingProjects[0].id)}
                    >学習データの採否を見る</button>
                    <small>{usingProjects[0].name}で固定されたPackageを確認</small>
                  </div>}
                  {trainingSnapshotLink && <button
                    type="button"
                    className="text-button model-package-snapshot-link"
                    disabled={actions.openingTrainingSnapshotId === trainingSnapshotLink.snapshotId}
                    onClick={() => void actions.openTrainingSnapshot(trainingSnapshotLink)}
                  >{actions.openingTrainingSnapshotId === trainingSnapshotLink.snapshotId ? "Snapshotを確認中…" : "固定した学習Snapshotを見る"}</button>}
                  <details className="model-package-technical"><summary>前提・技術情報</summary><p>{decision?.uncertainty}</p><p>{decision?.caution}</p><dl><div><dt>パッケージID</dt><dd>{item.package_id}</dd></div><div><dt>マニフェスト識別子</dt><dd title={item.manifest_digest}>{shortDigest(item.manifest_digest)}</dd></div></dl></details>
                  <details className="resource-manage-menu">
                    <summary aria-label={`${packageDisplayNames.get(item.id)}の管理`}>管理</summary>
                    <div>
                      <strong>{packageDisplayNames.get(item.id)} · {taskLabel(item.task_id)}</strong>
                      <small>{item.archived_at ? "新規利用を再開します。" : usingProjects.length > 0 ? `${usingProjects.length}件のプロジェクトが参照中のため利用停止できません。` : "Packageは残し、新しいプロジェクトでの利用から外します。"}</small>
                      <button
                        type="button"
                        className={item.archived_at ? "outline-button resource-state-action" : "text-button resource-state-action"}
                        disabled={actions.changingResourceId === item.id || (!item.archived_at && usingProjects.length > 0)}
                        onClick={() => void actions.changeModelPackageState(item)}
                      >{actions.changingResourceId === item.id ? "更新中…" : item.archived_at ? "利用可能に戻す" : "利用停止にする"}</button>
                    </div>
                  </details>
                </article>;
              })}</div>
              : resourceStates.modelPackages.phase === "ready"
                ? <div className="model-package-empty"><p>このデータに対応するモデルはまだありません。</p><button className="outline-button" type="button" onClick={() => openModelGuide()}>モデルを作成する</button></div>
                : null}
          </div>
        </section>}

        {modelGuideOpen && selectedDataset && <section className="data-library-section model-update-guide" aria-labelledby="model-update-guide-heading">
          <header><div><span className="overline">MODEL UPDATE</span><h3 id="model-update-guide-heading">モデルを追加する</h3><p>リポジトリ直下のPowerShellで診断 → 候補作成 → リポジトリ外の個人モデルstoreへ昇格します。アプリは起動したままで構いません。</p></div><button type="button" className="text-button" aria-label="モデル更新手順を閉じる" onClick={() => setModelGuideOpen(false)}>閉じる</button></header>
          {exactProfileMissing
            ? <div className="panel-error" role="alert">
              <strong>登録時のProfileが見つからないため、モデル更新を開始できません</strong>
              <p>自動検出へ切り替えると、Dataset登録時と学習時で列や単位の解釈が変わる可能性があります。</p>
              <p>Profile Workbenchから登録時と同じJSONを出力し、モデル更新時にそのJSONを<code>--profile</code>へ指定してください。</p>
              <code>{selectedDataset.profile_revision.profile_digest.replace(/^sha256:/, "")}.json</code>
              <small>この画面では個人ファイルの保存先を表示しません。</small>
            </div>
            : <>
              <label>予測タスク<select value={guideTaskId} onChange={(event) => { setGuideTaskId(event.target.value); setCopiedGuide(false); }}>{selectedDataset.supported_task_ids.map((taskId) => <option key={taskId} value={taskId}>{taskLabel(taskId)}</option>)}</select></label>
              <ol><li><strong>診断</strong><span>データと予測契約の整合を確認</span></li><li><strong>候補作成</strong><span>新しい不変Packageを構築・検証</span></li><li><strong>昇格・反映</strong><span>個人モデルstoreへ昇格し、下のボタンで再読込</span></li></ol>
              <textarea aria-label="PowerShellモデル更新手順" readOnly value={modelGuide} rows={18} />
              <div className="model-update-actions">
                <button className="primary-button" type="button" onClick={() => void copyModelGuide()}>{copiedGuide ? "コピーしました" : "PowerShell手順をコピー"}</button>
                <button className="outline-button" type="button" disabled={actions.refreshingPackages} onClick={() => void actions.refreshTaskResources()}>{actions.refreshingPackages ? "再読込中…" : "個人Taskとモデルを再読込"}</button>
                <small>保存済み予測は再計算されません。検証済みの個人Taskも再起動なしで反映します。</small>
              </div>
              {actions.refreshMessage && <p role="status">{actions.refreshMessage}</p>}
              {actions.refreshWarnings.length > 0 && <details className="model-refresh-warnings">
                <summary>除外されたモデルを確認</summary>
                <ul>{actions.refreshWarnings.map((warning, index) => <li key={`${warning.source}:${warning.reference ?? index}`}>
                  <strong>{warning.reference ?? "available-packages.json"}</strong>
                  <span>{warning.message}</span>
                  <small title={warning.source}>{warning.source}</small>
                </li>)}</ul>
              </details>}
            </>}
        </section>}

        <details className="data-library-secondary">
          <summary><span>比較セットと系列データ</span><small>{comparisonSets.length}比較セット · 必要なときに開く</small></summary>
          <div className={`data-library-grid ${comparisonSets.length === 0 ? "comparison-empty" : ""}`}>
            <div className="data-library-section comparison-set-section"><div className="panel-title"><h3>比較セット</h3><span>{comparisonSets.length}件</span></div>{comparisonSets.length ? <div className="comparison-set-list">{comparisonSets.map((view) => { const members = view.members.map((member) => member.cohort_label || datasetDisplayName(datasets.find((dataset) => dataset.dataset_revision.id === member.dataset_revision_id))).join(" / "); return <div key={view.id}><strong>{view.name}</strong><span title={members}>{members}</span><code title={view.view_digest}>{shortDigest(view.view_digest)}</code></div>; })}</div> : <p className="library-empty">比較セットはまだありません。必要なときに上の「＋ 比較セット」から作成できます。</p>}</div>
          </div>
          <SeriesLibrarySection />
        </details>
      </>}
    </>
  );
}
