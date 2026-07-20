import { useEffect, useState } from "react";
import { workbenchApi, type ApiQuality } from "../../shared/api/workbench-api";

export type QualityFilters = Readonly<{
  issueId?: string;
  type?: string;
  sheet?: string;
  key?: string;
}>;

export function DataExploreNavigation({
  active,
  onNavigate,
}: {
  active: "quality" | "lineage";
  onNavigate: (view: "quality" | "lineage") => void;
}) {
  return <div className="section-navigation" aria-label="データ探索">
    <div><span className="overline">データ探索</span><strong>実績のつながりと問題を同じ文脈で確認</strong></div>
    <nav aria-label="データ探索">
      <button className={active === "lineage" ? "active" : ""} onClick={() => onNavigate("lineage")}>実績・工程を探す</button>
      <button className={active === "quality" ? "active" : ""} onClick={() => onNavigate("quality")}>問題から探す</button>
    </nav>
  </div>;
}

export function LiveDataQualityPage({
  filters,
  onFiltersChange,
  onOpenLineage,
  showReferenceScenarios = false,
  mode = "issues",
}: {
  filters: QualityFilters;
  onFiltersChange: (filters: QualityFilters) => void;
  onOpenLineage: (issue: ApiQuality["detected_issues"][number], filters: QualityFilters) => void;
  showReferenceScenarios?: boolean;
  mode?: "issues" | "summary";
}) {
  type DetectedIssue = ApiQuality["detected_issues"][number];
  const [data, setData] = useState<ApiQuality | null>(null);
  const [error, setError] = useState(false);
  const [exportError, setExportError] = useState("");
  const [copiedKey, setCopiedKey] = useState("");
  const [copyError, setCopyError] = useState("");
  useEffect(() => {
    workbenchApi.quality()
      .then(setData)
      .catch(() => setError(true));
  }, []);
  const labels: Record<DetectedIssue["issue_type"], string> = {
    missing_key: "キー欠損",
    orphan_entity: "孤立",
    duplicate_key: "重複",
    invalid_reference: "不正参照",
  };
  const updateFilters = (patch: Partial<QualityFilters>) => onFiltersChange({ ...filters, ...patch, issueId: undefined });
  const sheets = Array.from(new Set(data?.detected_issues.map((issue) => issue.source_sheet) ?? [])).sort();
  const normalizedKey = filters.key?.trim().toLocaleLowerCase("ja-JP") ?? "";
  const visibleIssues = data?.detected_issues.filter((issue) =>
    (!filters.type || issue.issue_type === filters.type)
    && (!filters.sheet || issue.source_sheet === filters.sheet)
    && (!normalizedKey || `${issue.entity_key} ${issue.missing_reference_key ?? ""}`.toLocaleLowerCase("ja-JP").includes(normalizedKey))
  ) ?? [];
  const exportCsv = async () => {
    setExportError("");
    try {
      const csv = await workbenchApi.qualityCsv();
      const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "detected-data-quality.csv";
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch {
      setExportError("CSVを出力できませんでした。");
    }
  };
  const copyKey = async (key: string) => {
    setCopyError("");
    try {
      await navigator.clipboard.writeText(key);
      setCopiedKey(key);
    } catch {
      setCopyError("キーをコピーできませんでした。ブラウザのクリップボード権限を確認してください。");
    }
  };
  return (
    <div className="page-panel quality-page">
      <div className="page-intro">
        <div>
          <h2>{mode === "summary" ? "データ品質集計" : "問題から探す"}</h2>
          <p>元Excelを変更せず、関係と各工程シートを照合して実際の問題を検出します。</p>
        </div>
        {mode === "summary" && <button className="outline-button" onClick={() => void exportCsv()}>検出結果をCSV出力</button>}
      </div>
      {exportError && <p className="empty-evidence" role="alert">{exportError}</p>}
      {copyError && <p className="empty-evidence" role="alert">{copyError}</p>}
      {error ? (
        <p className="empty-evidence">データ品質を取得できません。API接続を確認してください。</p>
      ) : data ? (
        <>
          {mode === "summary" && <div className="quality-summary">
            <button type="button" className={!filters.type ? "active" : ""} onClick={() => updateFilters({ type: undefined })}>
              <b>{data.detected_total}</b>件を実検出
            </button>
            {Object.entries(data.detected_by_type).map(([type, count]) => (
              <button type="button" className={filters.type === type ? "active" : ""} key={type} onClick={() => updateFilters({ type })}>
                <b>{count}</b>{labels[type as DetectedIssue["issue_type"]] ?? type}
              </button>
            ))}
          </div>}
          {mode === "issues" && <>
            <div className="quality-filters" aria-label="検出結果フィルタ">
              <label>種別<select value={filters.type ?? ""} onChange={(event) => updateFilters({ type: event.target.value || undefined })}>
                <option value="">すべて</option>
                {Object.entries(labels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}
              </select></label>
              <label>元シート<select value={filters.sheet ?? ""} onChange={(event) => updateFilters({ sheet: event.target.value || undefined })}>
                <option value="">すべて</option>
                {sheets.map((sheet) => <option value={sheet} key={sheet}>{sheet}</option>)}
              </select></label>
              <label>キー<input value={filters.key ?? ""} onChange={(event) => updateFilters({ key: event.target.value || undefined })} placeholder="キーを絞り込み" /></label>
              <span>{visibleIssues.length}件</span>
            </div>
            <div className="table-scroll">
              <table className="quality-table">
                <thead><tr><th>検出種別</th><th>対象キー</th><th>元シート</th><th>検出内容</th><th>調査</th></tr></thead>
                <tbody>
                  {visibleIssues.map((issue) => (
                    <tr key={issue.issue_id} className={filters.issueId === issue.issue_id ? "quality-focus-row" : ""}>
                      <td><span className={`status-tag ${issue.issue_type === "invalid_reference" || issue.issue_type === "duplicate_key" ? "warn" : ""}`}>{labels[issue.issue_type]}</span></td>
                      <td>{issue.entity_key || "（空）"}</td><td>{issue.source_sheet}</td><td>{issue.detail}</td>
                      <td className="quality-actions">
                        {issue.focus_entity_key ? <button type="button" className="text-button" onClick={() => onOpenLineage(issue, filters)}>系譜で確認</button> : <span className="quality-unavailable">系譜を開けません。{issue.source_sheet}の該当行を確認</span>}
                        {issue.entity_key && <button type="button" className="text-button" onClick={() => void copyKey(issue.entity_key)}>{copiedKey === issue.entity_key ? "コピー済み" : "キーをコピー"}</button>}
                      </td>
                    </tr>
                  ))}
                  {!visibleIssues.length && <tr><td colSpan={5}>条件に一致する検出結果はありません。</td></tr>}
                </tbody>
              </table>
            </div>
          </>}
          {showReferenceScenarios && <details className="reference-scenarios">
            <summary>Excelに用意された確認用シナリオ（{data.reference_scenarios.length}件）</summary>
            <p>ここは検出結果ではなく、アプリの気づきを検証するために元データへ用意された参照ケースです。</p>
            <table className="quality-table"><tbody>{data.reference_scenarios.map((scenario) => <tr key={scenario.scenario_id}><td>{scenario.分類}</td><td>{scenario.対象キー}</td><td>{scenario.期待する気づき}</td></tr>)}</tbody></table>
          </details>}
        </>
      ) : <p className="empty-evidence">データ品質を読み込んでいます。</p>}
    </div>
  );
}
