import type { ReactNode } from "react";

export function ProjectEvidenceHistory({
  subtitle,
  loading,
  error,
  empty,
  emptyMessage,
  onRetry,
  children,
}: {
  subtitle: string;
  loading: boolean;
  error: boolean;
  empty: boolean;
  emptyMessage: string;
  onRetry: () => void;
  children: ReactNode;
}) {
  return <section className="project-history-section">
    <div className="panel-title"><h3>候補と判断履歴</h3><span>{subtitle}</span></div>
    {error ? <div className="project-history-error" role="alert">
      <p>候補と判断履歴を取得できませんでした。保存済みのデータは失われていません。</p>
      <button type="button" className="outline-button" onClick={onRetry}>履歴を再取得</button>
    </div> : loading ? <p className="empty-evidence">履歴を読み込んでいます。</p>
      : empty ? <div className="project-empty-state"><p>{emptyMessage}</p></div>
        : children}
  </section>;
}
