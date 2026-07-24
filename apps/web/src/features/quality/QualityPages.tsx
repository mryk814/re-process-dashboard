import { useEffect, useState } from "react";
import { workbenchApi, type ApiQuality } from "../../shared/api/workbench-api";

export type QualityFilters = Readonly<{
  issueId?: string;
  type?: string;
  sheet?: string;
  key?: string;
}>;

type DetectedIssue = ApiQuality["detected_issues"][number];

type QualityIssueGroup = Readonly<{
  key: string;
  issueType: DetectedIssue["issue_type"];
  sourceSheet: string;
  detail: string;
  issues: readonly DetectedIssue[];
}>;

function groupQualityIssues(issues: readonly DetectedIssue[]): readonly QualityIssueGroup[] {
  const groups = new Map<string, QualityIssueGroup>();
  for (const issue of issues) {
    const key = [issue.issue_type, issue.source_sheet, issue.detail].join("\u0000");
    const group = groups.get(key);
    if (group) {
      groups.set(key, { ...group, issues: [...group.issues, issue] });
    } else {
      groups.set(key, {
        key,
        issueType: issue.issue_type,
        sourceSheet: issue.source_sheet,
        detail: issue.detail,
        issues: [issue],
      });
    }
  }
  return [...groups.values()];
}

export function DataExploreNavigation({
  active,
  qualityAvailable,
  lineageAvailable,
  onNavigate,
}: {
  active: "quality" | "lineage";
  qualityAvailable: boolean;
  lineageAvailable: boolean;
  onNavigate: (view: "quality" | "lineage") => void;
}) {
  return <div className="section-navigation" aria-label="データ探索">
    <div><span className="overline">データ探索</span><strong>実績のつながりと問題を同じ文脈で確認</strong></div>
    <nav aria-label="データ探索">
      {lineageAvailable && <button className={active === "lineage" ? "active" : ""} onClick={() => onNavigate("lineage")}>実績・工程を探す</button>}
      {qualityAvailable && <button className={active === "quality" ? "active" : ""} onClick={() => onNavigate("quality")}>問題から探す</button>}
    </nav>
  </div>;
}

export function LiveDataQualityPage({
  projectId,
  filters,
  onFiltersChange,
  onOpenLineage,
  showReferenceScenarios = false,
  mode = "issues",
}: {
  projectId: string;
  filters: QualityFilters;
  onFiltersChange: (filters: QualityFilters) => void;
  onOpenLineage: (issue: ApiQuality["detected_issues"][number], filters: QualityFilters) => void;
  showReferenceScenarios?: boolean;
  mode?: "issues" | "summary";
}) {
  const [data, setData] = useState<ApiQuality | null>(null);
  const [error, setError] = useState(false);
  const [exportError, setExportError] = useState("");
  const [copiedKey, setCopiedKey] = useState("");
  const [copyError, setCopyError] = useState("");
  const [groupOpenState, setGroupOpenState] = useState<Record<string, boolean>>({});
  useEffect(() => {
    let cancelled = false;
    setData(null);
    setError(false);
    workbenchApi.quality(projectId)
      .then((quality) => { if (!cancelled) setData(quality); })
      .catch(() => { if (!cancelled) setError(true); });
    return () => { cancelled = true; };
  }, [projectId]);
  const labels: Record<DetectedIssue["issue_type"], string> = {
    missing_key: "キー欠損",
    orphan_entity: "孤立",
    duplicate_key: "重複",
    invalid_reference: "不正参照",
    out_of_range: "範囲外",
    suspicious_distribution: "分布の偏り",
    curation_quarantine: "学習利用から隔離",
    missing_target: "目的変数の欠損",
  };
  const updateFilters = (patch: Partial<QualityFilters>) => onFiltersChange({ ...filters, ...patch, issueId: undefined });
  const sheets = Array.from(new Set(data?.detected_issues.map((issue) => issue.source_sheet) ?? [])).sort();
  const normalizedKey = filters.key?.trim().toLocaleLowerCase("ja-JP") ?? "";
  const visibleIssues = data?.detected_issues.filter((issue) =>
    (!filters.type || issue.issue_type === filters.type)
    && (!filters.sheet || issue.source_sheet === filters.sheet)
    && (!normalizedKey || `${issue.entity_key} ${issue.missing_reference_key ?? ""}`.toLocaleLowerCase("ja-JP").includes(normalizedKey))
  ) ?? [];
  const visibleGroups = groupQualityIssues(visibleIssues);
  const exportCsv = async () => {
    setExportError("");
    try {
      const csv = await workbenchApi.qualityCsv(projectId);
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
          <p>元データを変更せず、関係・値域・分布から実際の問題を検出します。</p>
        </div>
        {mode === "summary" && <button className="outline-button" onClick={() => void exportCsv()}>検出結果をCSV出力</button>}
      </div>
      {exportError && <p className="empty-evidence" role="alert">{exportError}</p>}
      {copyError && <p className="empty-evidence" role="alert">{copyError}</p>}
      {error ? (
        <p className="empty-evidence">データ品質を取得できません。API接続を確認してください。</p>
      ) : data ? (
        <>
          <details className="technical-contract dataset-identity">
            <summary>参照データを確認</summary>
            <span>{data.dataset.task_id}</span>
            <span>{data.dataset.profile_id}</span>
            <code title={data.dataset.source_sha256}>source sha256:{data.dataset.source_sha256.slice(0, 12)}…</code>
            <small>{data.dataset.source_path}</small>
            <small>{data.dataset.profile_path}</small>
          </details>
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
              <label>元データ<select value={filters.sheet ?? ""} onChange={(event) => updateFilters({ sheet: event.target.value || undefined })}>
                <option value="">すべて</option>
                {sheets.map((sheet) => <option value={sheet} key={sheet}>{sheet}</option>)}
              </select></label>
              <label>キー<input value={filters.key ?? ""} onChange={(event) => updateFilters({ key: event.target.value || undefined })} placeholder="キーを絞り込み" /></label>
              <span>{visibleGroups.length}種類・{visibleIssues.length}件</span>
            </div>
            {visibleGroups.length ? <div className="quality-issue-groups">
              {visibleGroups.map((group) => {
                const hasFocusedIssue = group.issues.some((issue) => issue.issue_id === filters.issueId);
                const isRepeated = group.issues.length > 1;
                const isOpen = hasFocusedIssue || Boolean(normalizedKey) || (groupOpenState[group.key] ?? !isRepeated);
                return <details className="quality-issue-group" key={group.key} open={isOpen} onToggle={(event) => setGroupOpenState((current) => ({ ...current, [group.key]: event.currentTarget.open }))}>
                  <summary>
                    <span className={`status-tag ${group.issueType !== "missing_key" && group.issueType !== "orphan_entity" ? "warn" : ""}`}>{labels[group.issueType]}</span>
                    <strong>{group.sourceSheet}</strong>
                    <span title={group.detail}>{group.detail}</span>
                    <b>{group.issues.length}件</b>
                  </summary>
                  <div className="table-scroll">
                    <table className="quality-table">
                      <thead><tr><th>対象キー</th><th>調査</th></tr></thead>
                      <tbody>{group.issues.map((issue) => (
                        <tr key={issue.issue_id} className={filters.issueId === issue.issue_id ? "quality-focus-row" : ""}>
                          <td>{issue.entity_key || "（空）"}</td>
                          <td className="quality-actions">
                            {issue.focus_entity_key ? <button type="button" className="text-button" onClick={() => onOpenLineage(issue, filters)}>系譜で確認</button> : <span className="quality-unavailable">{issue.source_sheet}の該当列を確認</span>}
                            {issue.entity_key && <button type="button" className="text-button" onClick={() => void copyKey(issue.entity_key)}>{copiedKey === issue.entity_key ? "コピー済み" : "キーをコピー"}</button>}
                          </td>
                        </tr>
                      ))}</tbody>
                    </table>
                  </div>
                </details>;
              })}
            </div> : <p className="empty-evidence">条件に一致する検出結果はありません。</p>}
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
