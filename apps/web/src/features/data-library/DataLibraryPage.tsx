import { useEffect, useMemo, useState } from "react";
import {
  workbenchApi,
  type ApiDataLibraryDataset,
  type ApiModelPackageRef,
  type ApiProject,
  type ApiProjectCreationOptions,
} from "../../shared/api/workbench-api";
import {
  compatibleTaskIdsForDataset,
  datasetDisplayName,
  modelPackageDecisionSummary,
  modelPackageDisplayName,
  trainingDataSha,
  trainingDataset,
} from "../../shared/dataLibraryPresentation";
import { useTaskLabels } from "../../shared/useTaskLabels";
import { SeriesLibrarySection } from "./SeriesLibrarySection";
import { SourceLifecycleSection } from "./SourceLifecycleSection";

const shortDigest = (value: string) => value.replace(/^sha256:/, "").slice(0, 10);
const formatDate = (value: string) => new Date(value).toLocaleDateString("ja-JP");
type UndoAction = { kind: "dataset" | "package"; id: string; archived: boolean; label: string };

export function DataLibraryPage({
  projects,
  onAddDataset,
  onStartProject,
}: {
  projects: ApiProject[];
  onAddDataset: () => void;
  onStartProject: (datasetViewRevisionId: string) => void;
}) {
  const [options, setOptions] = useState<ApiProjectCreationOptions | null>(null);
  const [datasets, setDatasets] = useState<ApiDataLibraryDataset[]>([]);
  const [modelPackages, setModelPackages] = useState<ApiModelPackageRef[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [compareOpen, setCompareOpen] = useState(false);
  const [compareName, setCompareName] = useState("");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [packageTaskFilter, setPackageTaskFilter] = useState("");
  const [packageDatasetFilter, setPackageDatasetFilter] = useState("");
  const [packageStateFilter, setPackageStateFilter] = useState("");
  const [datasetStateFilter, setDatasetStateFilter] = useState("available");
  const [changingResourceId, setChangingResourceId] = useState("");
  const [undoAction, setUndoAction] = useState<UndoAction | null>(null);
  const [activeTab, setActiveTab] = useState<"browse" | "update">("browse");
  const taskLabel = useTaskLabels();

  const load = () => {
    setLoading(true);
    setError("");
    setOptions(null);
    setDatasets([]);
    setModelPackages([]);
    return Promise.all([
      workbenchApi.projectCreationOptions(),
      workbenchApi.listDataLibraryDatasets(true),
      workbenchApi.listModelPackageRefs(true),
    ])
      .then(([nextOptions, nextDatasets, nextModelPackages]) => {
        setOptions(nextOptions);
        setDatasets(nextDatasets);
        setModelPackages(nextModelPackages);
      })
      .catch((cause) => setError(cause instanceof Error ? cause.message : "データライブラリを取得できませんでした。"))
      .finally(() => setLoading(false));
  };
  useEffect(() => { void load(); }, []);

  const comparisonSets = options?.dataset_views.filter((view) => view.kind === "cohort_comparison") ?? [];
  const filteredDatasets = useMemo(
    () => datasets.filter((item) => {
      if (datasetStateFilter === "available") return !item.dataset_revision.archived_at;
      if (datasetStateFilter === "archived") return Boolean(item.dataset_revision.archived_at);
      return true;
    }),
    [datasetStateFilter, datasets],
  );
  const packageTaskIds = useMemo(
    () => [...new Set(modelPackages.map((item) => item.task_id))].sort(),
    [modelPackages],
  );
  const packageDatasets = useMemo(
    () => datasets.filter((dataset) => modelPackages.some(
      (item) => trainingDataset(item, datasets)?.dataset_revision.id === dataset.dataset_revision.id,
    )),
    [datasets, modelPackages],
  );
  const filteredModelPackages = useMemo(
    () => modelPackages.filter((item) => {
      if (packageTaskFilter && item.task_id !== packageTaskFilter) return false;
      if (packageStateFilter === "available" && item.archived_at) return false;
      if (packageStateFilter === "archived" && !item.archived_at) return false;
      if (!packageDatasetFilter) return true;
      return trainingDataset(item, datasets)?.dataset_revision.id === packageDatasetFilter;
    }),
    [datasets, modelPackages, packageDatasetFilter, packageStateFilter, packageTaskFilter],
  );

  async function changeDatasetState(item: ApiDataLibraryDataset) {
    const id = item.dataset_revision.id;
    const archived = !item.dataset_revision.archived_at;
    setChangingResourceId(id);
    setError("");
    try {
      await workbenchApi.setDatasetArchived(id, archived);
      setUndoAction({ kind: "dataset", id, archived: !archived, label: datasetDisplayName(item) });
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : archived ? "Datasetを利用停止できませんでした。" : "Datasetを復元できませんでした。");
    } finally {
      setChangingResourceId("");
    }
  }

  async function changeModelPackageState(item: ApiModelPackageRef) {
    const archived = !item.archived_at;
    setChangingResourceId(item.id);
    setError("");
    try {
      await workbenchApi.setModelPackageArchived(item.id, archived);
      setUndoAction({ kind: "package", id: item.id, archived: !archived, label: modelPackageDisplayName(item) });
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : archived ? "Model Packageを利用停止できませんでした。" : "Model Packageを復元できませんでした。");
    } finally {
      setChangingResourceId("");
    }
  }

  async function undoLastChange() {
    if (!undoAction) return;
    setChangingResourceId(undoAction.id);
    setError("");
    try {
      if (undoAction.kind === "dataset") {
        await workbenchApi.setDatasetArchived(undoAction.id, undoAction.archived);
      } else {
        await workbenchApi.setModelPackageArchived(undoAction.id, undoAction.archived);
      }
      setUndoAction(null);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "直前の操作を元に戻せませんでした。");
    } finally {
      setChangingResourceId("");
    }
  }

  const toggleDataset = (dataset: ApiDataLibraryDataset) => {
    const id = dataset.dataset_revision.id;
    setSelectedIds((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  };

  async function createComparison() {
    if (!options || selectedIds.length < 2 || !compareName.trim()) {
      setError("比較名と2件以上のDatasetを選んでください。");
      return;
    }
    try {
      await workbenchApi.createDatasetView({
        view_id: `cohort-${crypto.randomUUID()}`,
        revision: 1,
        name: compareName.trim(),
        kind: "cohort_comparison",
        members: selectedIds.map((dataset_revision_id, ordinal) => {
          const dataset = options.datasets.find((item) => item.dataset_revision.id === dataset_revision_id);
          return {
            dataset_revision_id,
            ordinal,
            cohort_key: `cohort-${ordinal + 1}`,
            cohort_label: dataset ? datasetDisplayName(dataset) : `Cohort ${ordinal + 1}`,
            provenance_json: {},
          };
        }),
      });
      setCompareOpen(false);
      setCompareName("");
      setSelectedIds([]);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "比較セットを作成できませんでした。");
    }
  }

  return (
    <div className="page-panel data-library-page">
      <div className="page-intro data-library-header">
        <div><span className="overline">DATA LIBRARY</span><h2>データライブラリ</h2><p>Excelとデータセットプロファイルを組み合わせたデータセットと、モデルの学習元を確認します。</p></div>
        {activeTab === "browse" && <div className="data-library-header-actions">
          <button className="primary-button" onClick={onAddDataset}>Excelからデータセットを追加</button>
          <button
            className="outline-button"
            aria-expanded={compareOpen}
            aria-controls="dataset-comparison-builder"
            onClick={() => setCompareOpen((value) => !value)}
          >＋ 比較セット</button>
        </div>}
      </div>
      <nav className="data-library-tabs" aria-label="データライブラリの表示" role="tablist">
        <button type="button" role="tab" aria-selected={activeTab === "browse"} className={activeTab === "browse" ? "active" : ""} onClick={() => setActiveTab("browse")}>閲覧</button>
        <button type="button" role="tab" aria-selected={activeTab === "update"} className={activeTab === "update" ? "active" : ""} onClick={() => setActiveTab("update")}>データ更新</button>
      </nav>
      {error && options && <p className="panel-error" role="alert">{error}</p>}
      {undoAction && <div className="library-undo" role="status">
        <span>{undoAction.label}を{undoAction.archived ? "復元" : "利用停止"}しました。</span>
        <button type="button" className="text-button" disabled={changingResourceId === undoAction.id} onClick={() => void undoLastChange()}>元に戻す</button>
        <button type="button" className="text-button library-undo-dismiss" aria-label="通知を閉じる" onClick={() => setUndoAction(null)}>×</button>
      </div>}
      {activeTab === "browse" && compareOpen && options && <section id="dataset-comparison-builder" className="dataset-compare-builder" aria-labelledby="dataset-comparison-heading">
        <div><h3 id="dataset-comparison-heading">境界を保った比較セット</h3><p>設備・場所などの違いを残したまま並べます。学習用に自動結合はしません。</p></div>
        <label>比較名<input value={compareName} onChange={(event) => setCompareName(event.target.value)} placeholder="設備A / 設備B 比較" /></label>
        <div className="dataset-compare-options">{options.datasets.map((item) => <label key={item.dataset_revision.id}><input type="checkbox" checked={selectedIds.includes(item.dataset_revision.id)} onChange={() => toggleDataset(item)} />{datasetDisplayName(item)}</label>)}</div>
        <button className="primary-button" disabled={selectedIds.length < 2} onClick={() => void createComparison()}>比較セットを作成</button>
      </section>}

      {loading && <p className="project-empty-inline" role="status">データライブラリを読み込んでいます…</p>}
      {!loading && error && !options && <section className="library-error-state">
        <p className="panel-error" role="alert">{error}</p>
        <button className="outline-button" onClick={() => void load()}>再読み込み</button>
      </section>}
      {options && activeTab === "browse" && <>
        <section className="data-library-section">
          <div className="panel-title library-title-with-filter">
            <div><h3>データセット（Dataset）</h3><span>{filteredDatasets.length} / {datasets.length}件</span></div>
            <label>状態<select value={datasetStateFilter} onChange={(event) => setDatasetStateFilter(event.target.value)}><option value="available">利用可能</option><option value="archived">利用停止中</option><option value="">すべて</option></select></label>
          </div>
          <div className="dataset-list">{filteredDatasets.map((item) => {
            const singleView = item.dataset_views?.find((view) => view.kind === "single");
            const relatedViewIds = new Set(item.dataset_views?.map((view) => view.id) ?? []);
            const usingProjects = projects.filter((project) => project.dataset_view_revision_id && relatedViewIds.has(project.dataset_view_revision_id));
            const compatibleTaskIds = compatibleTaskIdsForDataset(item, options);
            const archived = Boolean(item.dataset_revision.archived_at);
            const startUnavailableReason = item.supported_task_ids.length === 0
              ? "対応する予測タスクがありません"
              : compatibleTaskIds.length === 0
                ? "利用可能なModel Packageがありません"
                : "";
            return <article className="dataset-card" key={item.dataset_revision.id}>
              <div className="dataset-card-main"><strong title={item.data_asset.original_filename}>{item.data_asset.original_filename}</strong><span>{item.data_asset.locator_kind === "managed" ? "取り込みデータ" : "同梱データ"} · {formatDate(item.dataset_revision.created_at)}</span>{archived && <small className="resource-state archived">利用停止中</small>}</div>
              <dl><div><dt>データセットプロファイル</dt><dd>{item.profile_revision.name} · r{item.profile_revision.revision}</dd></div><div><dt>予測タスク</dt><dd title={item.supported_task_ids.join(" / ")}>{item.supported_task_ids.length ? item.supported_task_ids.map(taskLabel).join(" / ") : "未定義"}</dd></div><div><dt>データセット識別子</dt><dd title={item.dataset_revision.dataset_digest}>{shortDigest(item.dataset_revision.dataset_digest)}</dd></div></dl>
              <div className="dataset-project-links">
                <div>{usingProjects.length ? usingProjects.map((project) => <span key={project.id}>{project.name}</span>) : <small>参照中のプロジェクトなし</small>}</div>
                {!archived && singleView && <button
                  className="outline-button dataset-start-project"
                  aria-label={`${datasetDisplayName(item)}でプロジェクトを作成${startUnavailableReason ? `：${startUnavailableReason}` : ""}`}
                  title={startUnavailableReason || `${datasetDisplayName(item)}でプロジェクトを作成`}
                  disabled={Boolean(startUnavailableReason)}
                  onClick={() => onStartProject(singleView.id)}
                >プロジェクト作成</button>}
                {startUnavailableReason && <small className="dataset-start-unavailable">{startUnavailableReason}</small>}
                <details className="resource-manage-menu">
                  <summary aria-label={`${datasetDisplayName(item)}の管理`}>管理</summary>
                  <div>
                    <strong>{datasetDisplayName(item)}</strong>
                    <small>{archived ? "新規利用を再開します。" : usingProjects.length > 0 ? `${usingProjects.length}件のプロジェクトが参照中のため利用停止できません。` : "元データは残し、一覧と新規利用から外します。"}</small>
                    <button
                      type="button"
                      className={archived ? "outline-button resource-state-action" : "text-button resource-state-action"}
                      disabled={changingResourceId === item.dataset_revision.id || (!archived && usingProjects.length > 0)}
                      onClick={() => void changeDatasetState(item)}
                    >{changingResourceId === item.dataset_revision.id ? "更新中…" : archived ? "利用可能に戻す" : "利用停止にする"}</button>
                  </div>
                </details>
              </div>
            </article>;
          })}</div>
          {filteredDatasets.length === 0 && <p className="library-empty">この状態のDatasetはありません。</p>}
        </section>

        <SeriesLibrarySection />
        <section className={`data-library-grid ${comparisonSets.length === 0 ? "comparison-empty" : ""}`}>
          <div className="data-library-section comparison-set-section"><div className="panel-title"><h3>比較セット</h3><span>{comparisonSets.length}件</span></div>{comparisonSets.length ? <div className="comparison-set-list">{comparisonSets.map((view) => { const members = view.members.map((member) => member.cohort_label || datasetDisplayName(options.datasets.find((dataset) => dataset.dataset_revision.id === member.dataset_revision_id))).join(" / "); return <div key={view.id}><strong>{view.name}</strong><span title={members}>{members}</span><code title={view.view_digest}>{shortDigest(view.view_digest)}</code></div>; })}</div> : <p className="library-empty">比較セットはまだありません。必要なときに上の「＋ 比較セット」から作成できます。</p>}</div>
          <div className="data-library-section model-package-library">
            <div className="panel-title"><h3>モデルパッケージ</h3><span>{filteredModelPackages.length} / {modelPackages.length}件</span></div>
            <div className="model-package-toolbar" aria-label="モデルパッケージの絞り込み">
              <label>予測タスク<select value={packageTaskFilter} onChange={(event) => setPackageTaskFilter(event.target.value)}><option value="">すべて</option>{packageTaskIds.map((taskId) => <option key={taskId} value={taskId}>{taskLabel(taskId)}</option>)}</select></label>
              <label>学習元データセット<select value={packageDatasetFilter} onChange={(event) => setPackageDatasetFilter(event.target.value)}><option value="">すべて</option>{packageDatasets.map((dataset) => <option key={dataset.dataset_revision.id} value={dataset.dataset_revision.id}>{datasetDisplayName(dataset)}</option>)}</select></label>
              <label>状態<select value={packageStateFilter} onChange={(event) => setPackageStateFilter(event.target.value)}><option value="">すべて</option><option value="available">利用可能</option><option value="archived">アーカイブ</option></select></label>
              {(packageTaskFilter || packageDatasetFilter || packageStateFilter) && <button type="button" className="text-button" onClick={() => { setPackageTaskFilter(""); setPackageDatasetFilter(""); setPackageStateFilter(""); }}>絞り込みを解除</button>}
            </div>
            {filteredModelPackages.length > 0
              ? <div className="model-package-list">{filteredModelPackages.map((item) => {
                const source = trainingDataset(item, datasets);
                const sourceSha = trainingDataSha(item);
                const decision = modelPackageDecisionSummary(item);
                const usingProjects = projects.filter((project) => project.model_package_ref_id === item.id);
                return <article key={item.id}>
                  <div><strong>{modelPackageDisplayName(item)}</strong><span title={item.task_id}>{taskLabel(item.task_id)}</span><small className={item.archived_at ? "package-state archived" : "package-state"}>{item.archived_at ? "アーカイブ" : decision?.experimental ? "試験モデル" : "利用可能"}</small></div>
                  <dl>
                    <div><dt>使いどころ</dt><dd>{decision?.useCase ?? "—"}</dd></div>
                    <div><dt>学習単位</dt><dd>{decision?.trainingUnit ?? "—"}</dd></div>
                    <div><dt>学習元データセット</dt><dd title={source?.data_asset.original_filename ?? sourceSha ?? undefined}>{source ? datasetDisplayName(source) : sourceSha ? `未登録 ${sourceSha.slice(0, 10)}` : "マニフェストに記録なし"}</dd></div>
                    <div><dt>学習時プロファイル</dt><dd>{source ? `${source.profile_revision.name} · r${source.profile_revision.revision}` : "—"}</dd></div>
                  </dl>
                  <details className="model-package-technical"><summary>前提・技術情報</summary><p>{decision?.uncertainty}</p><p>{decision?.caution}</p><dl><div><dt>パッケージID</dt><dd>{item.package_id}</dd></div><div><dt>マニフェスト識別子</dt><dd title={item.manifest_digest}>{shortDigest(item.manifest_digest)}</dd></div></dl></details>
                  <details className="resource-manage-menu">
                    <summary aria-label={`${modelPackageDisplayName(item)}の管理`}>管理</summary>
                    <div>
                      <strong>{modelPackageDisplayName(item)} · {taskLabel(item.task_id)}</strong>
                      <small>{item.archived_at ? "新規利用を再開します。" : usingProjects.length > 0 ? `${usingProjects.length}件のプロジェクトが参照中のため利用停止できません。` : "Packageは残し、新しいプロジェクトでの利用から外します。"}</small>
                      <button
                        type="button"
                        className={item.archived_at ? "outline-button resource-state-action" : "text-button resource-state-action"}
                        disabled={changingResourceId === item.id || (!item.archived_at && usingProjects.length > 0)}
                        onClick={() => void changeModelPackageState(item)}
                      >{changingResourceId === item.id ? "更新中…" : item.archived_at ? "利用可能に戻す" : "利用停止にする"}</button>
                    </div>
                  </details>
                </article>;
              })}</div>
              : <p className="library-empty">条件に合うModel Packageはありません。</p>}
          </div>
        </section>
      </>}
      {activeTab === "update" && <SourceLifecycleSection datasets={datasets} />}
    </div>
  );
}
