import type { ReactNode } from "react";

export function ProjectCreationPanel({
  open,
  loading,
  error,
  onClose,
  children,
}: {
  open: boolean;
  loading: boolean;
  error: string;
  onClose: () => void;
  children: ReactNode;
}) {
  if (!open) return null;
  return <section className="project-create-panel" aria-label="新規プロジェクトの開始方法" aria-busy={loading}>
    <div className="panel-title project-create-heading">
      <div><h3>新しいプロジェクト</h3><span>開始方法を選んでから作成します</span></div>
      <button type="button" className="outline-button" disabled={loading} onClick={onClose}>作成をやめる</button>
    </div>
    {error && <p className="panel-error" role="alert">{error}</p>}
    {children}
  </section>;
}
