import type { ReactNode } from "react";
import {
  AccessibleTabList,
  AccessibleTabPanel,
} from "../../shared/ui/AccessibleTabs";
import type { DataLibraryLocation } from "./location";

export function DataLibraryShell({
  location,
  onNavigate,
  actions,
  browse,
  update,
}: {
  location: DataLibraryLocation;
  onNavigate: (location: DataLibraryLocation, replace?: boolean) => void;
  actions?: ReactNode;
  browse: ReactNode;
  update: ReactNode;
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
    <AccessibleTabList
      idPrefix="data-library"
      label="データライブラリの表示"
      className="data-library-tabs"
      items={[
        { id: "browse", label: "閲覧" },
        { id: "update", label: "データ更新" },
      ]}
      selected={activeTab}
      onSelect={selectTab}
    />
    <AccessibleTabPanel idPrefix="data-library" tabId="browse" active={activeTab === "browse"}>
      {browse}
    </AccessibleTabPanel>
    <AccessibleTabPanel idPrefix="data-library" tabId="update" active={activeTab === "update"}>
      {update}
    </AccessibleTabPanel>
  </div>;
}
