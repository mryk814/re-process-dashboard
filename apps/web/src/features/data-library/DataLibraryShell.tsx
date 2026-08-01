import type { ReactNode } from "react";
import type { DataLibraryLocation } from "./location";

export function DataLibraryShell({
  location,
  onNavigate,
  actions,
  children,
}: {
  location: DataLibraryLocation;
  onNavigate: (location: DataLibraryLocation, replace?: boolean) => void;
  actions?: ReactNode;
  children: ReactNode;
}) {
  const activeTab = location.tab;
  const selectTab = (tab: "browse" | "update") => {
    onNavigate(tab === "update"
      ? { tab: "update" }
      : {
        tab: "browse",
        onboardingMode: location.onboardingMode === "new-task" ? "new-task" : undefined,
      });
  };
  return <div className="page-panel data-library-page">
    <div className="page-intro data-library-header">
      <div><span className="overline">DATA LIBRARY</span><h2>データライブラリ</h2><p>Excelとデータセットプロファイルを組み合わせたデータセットと、モデルの学習元を確認します。</p></div>
      {actions}
    </div>
    <nav className="data-library-tabs" aria-label="データライブラリの表示" role="tablist">
      <button type="button" role="tab" aria-selected={activeTab === "browse"} className={activeTab === "browse" ? "active" : ""} onClick={() => selectTab("browse")}>閲覧</button>
      <button type="button" role="tab" aria-selected={activeTab === "update"} className={activeTab === "update" ? "active" : ""} onClick={() => selectTab("update")}>データ更新</button>
    </nav>
    {children}
  </div>;
}
