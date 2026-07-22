import { useEffect, useMemo, useState } from "react";
import {
  workbenchApi,
  type ApiDataLibraryDataset,
  type ApiProject,
  type ApiProjectCreationOptions,
} from "../../shared/api/workbench-api";

const shortDigest = (value: string) => value.replace(/^sha256:/, "").slice(0, 10);
const formatDate = (value: string) => new Date(value).toLocaleDateString("ja-JP");

export function DataLibraryPage({ projects }: { projects: ApiProject[] }) {
  const [options, setOptions] = useState<ApiProjectCreationOptions | null>(null);
  const [error, setError] = useState("");
  const [compareOpen, setCompareOpen] = useState(false);
  const [compareName, setCompareName] = useState("");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);

  const load = () => workbenchApi.projectCreationOptions()
    .then(setOptions)
    .catch((cause) => setError(cause instanceof Error ? cause.message : "データライブラリを取得できませんでした。"));
  useEffect(() => { void load(); }, []);

  const projectsByView = useMemo(() => {
    const grouped = new Map<string, ApiProject[]>();
    for (const project of projects) {
      if (!project.dataset_view_revision_id) continue;
      grouped.set(project.dataset_view_revision_id, [...(grouped.get(project.dataset_view_revision_id) ?? []), project]);
    }
    return grouped;
  }, [projects]);

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
        <div><span className="overline">DATA LIBRARY</span><h2>データライブラリ</h2><p>ExcelとProfile Revisionを一つのDatasetとして管理します。</p></div>
        <button className="outline-button" onClick={() => setCompareOpen((value) => !value)}>＋ 比較セット</button>
      </div>
      {error && <p className="panel-error" role="alert">{error}</p>}
      {compareOpen && options && <section className="dataset-compare-builder">
        <div><strong>境界を保った比較セット</strong><p>設備・場所などの違いを残したまま並べます。学習用に自動結合はしません。</p></div>
        <label>比較名<input value={compareName} onChange={(event) => setCompareName(event.target.value)} placeholder="設備A / 設備B 比較" /></label>
        <div className="dataset-compare-options">{options.datasets.map((item) => <label key={item.dataset_revision.id}><input type="checkbox" checked={selectedIds.includes(item.dataset_revision.id)} onChange={() => toggleDataset(item)} />{item.data_asset.original_filename}</label>)}</div>
        <button className="primary-button" disabled={selectedIds.length < 2} onClick={() => void createComparison()}>比較セットを作成</button>
      </section>}

      {!options ? <p className="project-empty-inline">読み込んでいます…</p> : <>
        <section className="data-library-section">
          <div className="panel-title"><h3>Datasets</h3><span>{options.datasets.length}件</span></div>
          <div className="dataset-list">{options.datasets.map((item) => {
            const singleView = item.dataset_views?.find((view) => view.kind === "single");
            const usingProjects = singleView ? projectsByView.get(singleView.id) ?? [] : [];
            return <article className="dataset-card" key={item.dataset_revision.id}>
              <div className="dataset-card-main"><strong>{item.data_asset.original_filename.replace(/\.xlsx$/i, "")}</strong><span>{item.data_asset.locator_kind === "managed" ? "取り込みデータ" : "同梱データ"} · {formatDate(item.dataset_revision.created_at)}</span></div>
              <dl><div><dt>Excel</dt><dd>{item.data_asset.original_filename}</dd></div><div><dt>Profile</dt><dd>{item.profile_revision.name} · r{item.profile_revision.revision}</dd></div><div><dt>Prediction Tasks</dt><dd>{item.supported_task_ids.join(" / ")}</dd></div><div><dt>Identity</dt><dd title={item.dataset_revision.dataset_digest}>{shortDigest(item.dataset_revision.dataset_digest)}</dd></div></dl>
              <div className="dataset-project-links">{usingProjects.length ? usingProjects.map((project) => <span key={project.id}>{project.name}</span>) : <small>参照中のプロジェクトなし</small>}</div>
            </article>;
          })}</div>
        </section>

        <section className="data-library-grid">
          <div className="data-library-section"><div className="panel-title"><h3>Dataset Views</h3><span>{options.dataset_views.length}件</span></div><div className="compact-record-list">{options.dataset_views.map((view) => <div key={view.id}><strong>{view.name}</strong><span>{view.kind === "single" ? "単一Dataset" : `${view.members.length} cohort 比較`}</span><code>{shortDigest(view.view_digest)}</code></div>)}</div></div>
          <div className="data-library-section"><div className="panel-title"><h3>Model Packages</h3><span>{options.model_packages.length}件</span></div><div className="compact-record-list">{options.model_packages.map((item) => <div key={item.id}><strong>{item.package_id}</strong><span>{item.task_id}</span><code>{shortDigest(item.manifest_digest)}</code></div>)}</div></div>
        </section>

        <section className="data-library-section"><div className="panel-title"><h3>一連の検討</h3><span>{options.project_series.length}件</span></div><div className="series-list">{options.project_series.map((series) => { const members = projects.filter((project) => project.project_series_id === series.id); return <div key={series.id}><strong>{series.name}</strong><span>{members.length ? members.map((item) => item.name).join(" → ") : "プロジェクトなし"}</span></div>; })}</div></section>
      </>}
    </div>
  );
}
