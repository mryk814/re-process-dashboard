import { useEffect, useMemo, useState } from "react";
import {
  workbenchApi,
  type ApiDataLibraryDataset,
  type ApiProject,
  type ApiProjectCreationOptions,
} from "../../shared/api/workbench-api";
import {
  compatibleTaskIdsForDataset,
  datasetDisplayName,
  modelPackageDisplayName,
  trainingDataSha,
  trainingDataset,
} from "../../shared/dataLibraryPresentation";

const shortDigest = (value: string) => value.replace(/^sha256:/, "").slice(0, 10);
const formatDate = (value: string) => new Date(value).toLocaleDateString("ja-JP");

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
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [compareOpen, setCompareOpen] = useState(false);
  const [compareName, setCompareName] = useState("");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);

  const load = () => {
    setLoading(true);
    setError("");
    setOptions(null);
    return workbenchApi.projectCreationOptions()
      .then(setOptions)
      .catch((cause) => setError(cause instanceof Error ? cause.message : "データライブラリを取得できませんでした。"))
      .finally(() => setLoading(false));
  };
  useEffect(() => { void load(); }, []);

  const projectsByView = useMemo(() => {
    const grouped = new Map<string, ApiProject[]>();
    for (const project of projects) {
      if (!project.dataset_view_revision_id) continue;
      grouped.set(project.dataset_view_revision_id, [...(grouped.get(project.dataset_view_revision_id) ?? []), project]);
    }
    return grouped;
  }, [projects]);
  const comparisonSets = options?.dataset_views.filter((view) => view.kind === "cohort_comparison") ?? [];

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
        members: selectedIds.map((dataset_revision_id, ordinal) => ({
          dataset_revision_id,
          ordinal,
          cohort_key: `cohort-${ordinal + 1}`,
          cohort_label: options.datasets.find((item) => item.dataset_revision.id === dataset_revision_id)?.data_asset.original_filename ?? `Cohort ${ordinal + 1}`,
          provenance_json: {},
        })),
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
        <div><span className="overline">DATA LIBRARY</span><h2>データライブラリ</h2><p>ExcelとProfileを組み合わせたDatasetと、モデルの学習元を確認します。</p></div>
        <div className="data-library-header-actions">
          <button className="primary-button" onClick={onAddDataset}>ExcelからDatasetを追加</button>
          <button
            className="outline-button"
            aria-expanded={compareOpen}
            aria-controls="dataset-comparison-builder"
            onClick={() => setCompareOpen((value) => !value)}
          >＋ 比較セット</button>
        </div>
      </div>
      {error && options && <p className="panel-error" role="alert">{error}</p>}
      {compareOpen && options && <section id="dataset-comparison-builder" className="dataset-compare-builder" aria-labelledby="dataset-comparison-heading">
        <div><h3 id="dataset-comparison-heading">境界を保った比較セット</h3><p>設備・場所などの違いを残したまま並べます。学習用に自動結合はしません。</p></div>
        <label>比較名<input value={compareName} onChange={(event) => setCompareName(event.target.value)} placeholder="設備A / 設備B 比較" /></label>
        <div className="dataset-compare-options">{options.datasets.map((item) => <label key={item.dataset_revision.id}><input type="checkbox" checked={selectedIds.includes(item.dataset_revision.id)} onChange={() => toggleDataset(item)} />{item.data_asset.original_filename}</label>)}</div>
        <button className="primary-button" disabled={selectedIds.length < 2} onClick={() => void createComparison()}>比較セットを作成</button>
      </section>}

      {loading && <p className="project-empty-inline" role="status">データライブラリを読み込んでいます…</p>}
      {!loading && error && !options && <section className="library-error-state">
        <p className="panel-error" role="alert">{error}</p>
        <button className="outline-button" onClick={() => void load()}>再読み込み</button>
      </section>}
      {options && <>
        <section className="data-library-section">
          <div className="panel-title"><h3>Datasets</h3><span>{options.datasets.length}件</span></div>
          <div className="dataset-list">{options.datasets.map((item) => {
            const singleView = item.dataset_views?.find((view) => view.kind === "single");
            const usingProjects = singleView ? projectsByView.get(singleView.id) ?? [] : [];
            const compatibleTaskIds = compatibleTaskIdsForDataset(item, options);
            const startUnavailableReason = item.supported_task_ids.length === 0
              ? "対応する予測タスクがありません"
              : compatibleTaskIds.length === 0
                ? "利用可能なModel Packageがありません"
                : "";
            return <article className="dataset-card" key={item.dataset_revision.id}>
              <div className="dataset-card-main"><strong title={item.data_asset.original_filename}>{item.data_asset.original_filename}</strong><span>{item.data_asset.locator_kind === "managed" ? "取り込みデータ" : "同梱データ"} · {formatDate(item.dataset_revision.created_at)}</span></div>
              <dl><div><dt>Profile</dt><dd>{item.profile_revision.name} · r{item.profile_revision.revision}</dd></div><div><dt>Prediction Tasks</dt><dd>{item.supported_task_ids.length ? item.supported_task_ids.join(" / ") : "未定義"}</dd></div><div><dt>Dataset Identity</dt><dd title={item.dataset_revision.dataset_digest}>{shortDigest(item.dataset_revision.dataset_digest)}</dd></div></dl>
              <div className="dataset-project-links">
                <div>{usingProjects.length ? usingProjects.map((project) => <span key={project.id}>{project.name}</span>) : <small>参照中のプロジェクトなし</small>}</div>
                {singleView && <button
                  className="outline-button dataset-start-project"
                  aria-label={`${datasetDisplayName(item)}でプロジェクトを作成${startUnavailableReason ? `：${startUnavailableReason}` : ""}`}
                  title={startUnavailableReason || `${datasetDisplayName(item)}でプロジェクトを作成`}
                  disabled={Boolean(startUnavailableReason)}
                  onClick={() => onStartProject(singleView.id)}
                >プロジェクト作成</button>}
                {startUnavailableReason && <small className="dataset-start-unavailable">{startUnavailableReason}</small>}
              </div>
            </article>;
          })}</div>
        </section>

        <section className="data-library-grid">
          <div className="data-library-section"><div className="panel-title"><h3>比較セット</h3><span>{comparisonSets.length}件</span></div>{comparisonSets.length ? <div className="comparison-set-list">{comparisonSets.map((view) => { const members = view.members.map((member) => member.cohort_label || datasetDisplayName(options.datasets.find((dataset) => dataset.dataset_revision.id === member.dataset_revision_id))).join(" / "); return <div key={view.id}><strong>{view.name}</strong><span title={members}>{members}</span><code title={view.view_digest}>{shortDigest(view.view_digest)}</code></div>; })}</div> : <p className="library-empty">設備・場所などの境界を保って比べたいときに作成します。</p>}</div>
          <div className="data-library-section"><div className="panel-title"><h3>Model Packages</h3><span>{options.model_packages.length}件</span></div><div className="model-package-list">{options.model_packages.map((item) => { const source = trainingDataset(item, options.datasets); const sourceSha = trainingDataSha(item); return <article key={item.id}><div><strong>{modelPackageDisplayName(item)}</strong><span>{item.task_id}</span></div><dl><div><dt>学習元Dataset</dt><dd title={source?.data_asset.original_filename ?? sourceSha ?? undefined}>{source ? datasetDisplayName(source) : sourceSha ? `未登録 ${sourceSha.slice(0, 10)}` : "manifestに記録なし"}</dd></div><div><dt>学習時Profile</dt><dd>{source ? `${source.profile_revision.name} · r${source.profile_revision.revision}` : "—"}</dd></div><div><dt>Manifest</dt><dd title={item.manifest_digest}>{shortDigest(item.manifest_digest)}</dd></div></dl></article>; })}</div></div>
        </section>
      </>}
    </div>
  );
}
