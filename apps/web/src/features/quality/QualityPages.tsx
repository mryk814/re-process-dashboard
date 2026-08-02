import { useEffect, useRef, useState } from "react";
import { ApiClientError } from "../../shared/api/client";
import { workbenchApi, type ApiQuality } from "../../shared/api/workbench-api";
import {
  beginQualityResourceLoad,
  initialQualityResourceState,
  rejectQualityResourceLoad,
  resolveQualityResourceLoad,
} from "./qualityResourceState";

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

function resourceUnavailable(cause: unknown) {
  if (!(cause instanceof ApiClientError)) return false;
  return cause.availability?.status === "unavailable"
    || cause.code === "subsystem_unavailable"
    || cause.code === "runtime_unavailable"
    || cause.code === "task-store-unconfigured"
    || cause.code === "task-store-unavailable"
    || cause.code === "model-store-unconfigured"
    || cause.code === "model-store-unavailable";
}

function clientLoadedAt(value: string | null) {
  return value ? new Date(value).toLocaleString("ja-JP") : "";
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
      {qualityAvailable && <button className={active === "quality" ? "active" : ""} onClick={() => onNavigate("quality")}>データ品質</button>}
    </nav>
  </div>;
}

export function LiveDataQualityPage({
  projectId,
  filters,
  onFiltersChange,
  onOpenLineage,
  showReferenceScenarios = false,
}: {
  projectId: string;
  filters: QualityFilters;
  onFiltersChange: (filters: QualityFilters) => void;
  onOpenLineage: (issue: ApiQuality["detected_issues"][number], filters: QualityFilters) => void;
  showReferenceScenarios?: boolean;
}) {
  const [data, setData] = useState<ApiQuality | null>(null);
  const [resourceState, setResourceState] = useState(
    () => initialQualityResourceState(projectId),
  );
  const [refreshRevision, setRefreshRevision] = useState(0);
  const [exportError, setExportError] = useState("");
  const [copiedKey, setCopiedKey] = useState("");
  const [copyError, setCopyError] = useState("");
  const [groupOpenState, setGroupOpenState] = useState<Record<string, boolean>>({});
  const loadedProjectRef = useRef<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    const scope = projectId;
    const retainsCurrentEvidence = loadedProjectRef.current === scope;
    if (!retainsCurrentEvidence) setData(null);
    setResourceState((current) => beginQualityResourceLoad(current, scope));
    workbenchApi.quality(projectId)
      .then((quality) => {
        if (cancelled) return;
        loadedProjectRef.current = scope;
        setData(quality);
        setResourceState(resolveQualityResourceLoad(scope, quality.detected_total === 0));
      })
      .catch((cause) => {
        if (cancelled) return;
        setResourceState((current) => rejectQualityResourceLoad(
          current,
          scope,
          "データ品質の検出結果を取得できませんでした。",
          resourceUnavailable(cause),
        ));
      });
    return () => { cancelled = true; };
  }, [projectId, refreshRevision]);
  const currentResourceState = resourceState.scope === projectId
    ? resourceState
    : initialQualityResourceState(projectId);
  const currentData = loadedProjectRef.current === projectId ? data : null;
  const labels: Record<DetectedIssue["issue_type"], string> = {
    missing_key: "キー欠損",
    orphan_entity: "孤立",
    duplicate_key: "重複",
    invalid_reference: "不正参照",
    out_of_range: "範囲外",
    suspicious_distribution: "分布の偏り",
    curation_quarantine: "学習利用から隔離",
    missing_target: "目的変数の欠損",
    predictor_missing: "入力条件の欠損",
  };
  const updateFilters = (patch: Partial<QualityFilters>) => onFiltersChange({ ...filters, ...patch, issueId: undefined });
  const sheets = Array.from(new Set(currentData?.detected_issues.map((issue) => issue.source_sheet) ?? [])).sort();
  const normalizedKey = filters.key?.trim().toLocaleLowerCase("ja-JP") ?? "";
  const visibleIssues = currentData?.detected_issues.filter((issue) =>
    (!filters.type || issue.issue_type === filters.type)
    && (!filters.sheet || issue.source_sheet === filters.sheet)
    && (!normalizedKey || `${issue.entity_key} ${issue.missing_reference_key ?? ""}`.toLocaleLowerCase("ja-JP").includes(normalizedKey))
  ) ?? [];
  const visibleGroups = groupQualityIssues(visibleIssues);
  const clearFilters = () => onFiltersChange({});
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
          <h2>データ品質</h2>
          <p>元データを変更せず、関係・値域・分布から実際の問題を検出します。</p>
        </div>
        <div className="quality-summary-actions">
          <button
            className="outline-button"
            type="button"
            disabled={currentResourceState.phase === "loading"}
            onClick={() => setRefreshRevision((value) => value + 1)}
          >
            {currentResourceState.phase === "loading"
              ? "データ品質を読込中…"
              : currentResourceState.phase === "stale"
                || currentResourceState.phase === "error"
                || currentResourceState.phase === "unavailable"
                ? "データ品質を再試行"
                : "データ品質を更新"}
          </button>
          <button className="outline-button" onClick={() => void exportCsv()}>検出結果をCSV出力</button>
        </div>
      </div>
      {exportError && <p className="empty-evidence" role="alert">{exportError}</p>}
      {copyError && <p className="empty-evidence" role="alert">{copyError}</p>}
      {currentResourceState.phase === "loading" && (
        <p className="empty-evidence" role="status">
          {currentData && currentResourceState.loadedAt
            ? `データ品質を更新しています。表示中の結果は、この画面で ${clientLoadedAt(currentResourceState.loadedAt)} に取得した内容です。`
            : "データ品質を読み込んでいます。"}
        </p>
      )}
      {currentResourceState.phase === "stale" && (
        <div className="data-library-resource-error" role="alert">
          <div>
            <strong>データ品質を更新できませんでした</strong>
            <p>表示中の検出結果は保持しています。最新の結果として扱わないでください。</p>
            <small>この画面での取得時刻: {clientLoadedAt(currentResourceState.loadedAt)}</small>
          </div>
        </div>
      )}
      {(currentResourceState.phase === "error" || currentResourceState.phase === "unavailable") && (
        <div className="data-library-resource-error" role="alert">
          <div>
            <strong>{currentResourceState.phase === "unavailable"
              ? "このProjectのデータ品質は現在利用できません"
              : "データ品質を取得できませんでした"}</strong>
            <p>検出結果は未確認です。問題が0件という意味ではありません。</p>
            <small>この画面の「データ品質を再試行」は、この検出結果だけを読み直します。</small>
          </div>
        </div>
      )}
      {currentData ? (
        <>
          <details className="technical-contract dataset-identity">
            <summary>参照データを確認</summary>
            <span>{currentData.dataset.task_id}</span>
            <span>{currentData.dataset.profile_id}</span>
            <code title={currentData.dataset.source_sha256}>source sha256:{currentData.dataset.source_sha256.slice(0, 12)}…</code>
            <small>{currentData.dataset.source_path}</small>
            <small>{currentData.dataset.profile_path}</small>
          </details>
          <div className="quality-summary">
            <button type="button" className={!filters.type ? "active" : ""} onClick={() => updateFilters({ type: undefined })}>
              <b>{currentData.detected_total}</b>件を実検出
            </button>
            {Object.entries(currentData.detected_by_type).map(([type, count]) => (
              <button type="button" className={filters.type === type ? "active" : ""} key={type} onClick={() => updateFilters({ type })}>
                <b>{count}</b>{labels[type as DetectedIssue["issue_type"]] ?? type}
              </button>
            ))}
          </div>
          <>
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
                // toggle fires after dispatch finishes, so currentTarget is already null;
                // target stays the details element.
                return <details className="quality-issue-group" key={group.key} open={isOpen} onToggle={(event) => setGroupOpenState((current) => ({ ...current, [group.key]: (event.target as HTMLDetailsElement).open }))}>
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
            </div> : currentData.detected_total === 0
              ? <div className="quality-empty-state"><strong>問題は検出されませんでした</strong><p>検出は完了しています。元データやProfileが変わったときに再確認してください。</p></div>
              : <div className="quality-empty-state filtered"><strong>絞り込みに一致する問題はありません</strong><p>{currentData.detected_total.toLocaleString("ja-JP")}件の検出結果は残っています。</p><button type="button" className="outline-button" onClick={clearFilters}>すべての問題を表示</button></div>}
          </>
          {showReferenceScenarios && <details className="reference-scenarios">
            <summary>Excelに用意された確認用シナリオ（{currentData.reference_scenarios.length}件）</summary>
            <p>ここは検出結果ではなく、アプリの気づきを検証するために元データへ用意された参照ケースです。</p>
            <table className="quality-table"><tbody>{currentData.reference_scenarios.map((scenario) => <tr key={scenario.scenario_id}><td>{scenario.分類}</td><td>{scenario.対象キー}</td><td>{scenario.期待する気づき}</td></tr>)}</tbody></table>
          </details>}
        </>
      ) : null}
    </div>
  );
}
