import {
  resourceLabels,
  type DataLibraryResourceFamily,
  type DataLibraryResourceStates,
} from "./useDataLibraryResources";

export function DataLibraryResourceNotice({
  family,
  impact,
  resourceStates,
  onRetry,
}: {
  family: DataLibraryResourceFamily;
  impact: string;
  resourceStates: DataLibraryResourceStates;
  onRetry: (family: DataLibraryResourceFamily) => void;
}) {
  const state = resourceStates[family];
  if (state.phase !== "error") return null;
  const stale = state.loadedAt ? new Date(state.loadedAt).toLocaleString("ja-JP") : null;
  return <div className="data-library-resource-error" role="alert">
    <div>
      <strong>{resourceLabels[family]}を更新できませんでした</strong>
      <p>{state.error}</p>
      {stale && <small>表示中の情報は前回取得時点（{stale}）です。現在値として扱わないでください。</small>}
      <small>{impact}</small>
    </div>
    <button
      type="button"
      className="outline-button"
      onClick={() => onRetry(family)}
    >{resourceLabels[family]}を再試行</button>
  </div>;
}

export function DataLibraryResourceLoading({
  family,
  resourceStates,
}: {
  family: DataLibraryResourceFamily;
  resourceStates: DataLibraryResourceStates;
}) {
  const state = resourceStates[family];
  if (state.phase !== "loading" && state.phase !== "refreshing") return null;
  return <p className="data-library-resource-loading" role="status">
    {resourceLabels[family]}を{state.phase === "refreshing" ? "更新中です" : "読み込んでいます"}…
  </p>;
}
