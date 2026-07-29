import { DeveloperControlCenter } from "./DeveloperControlCenter";
import type { TaskDefinitionContract } from "../candidates";

type DeveloperTab = "overview" | "training" | "guide" | "diagnostics";

export function WorkspaceAdminPage({
  developerTab,
  developerTabError,
  developerGuideId,
  projectId,
  taskDefinition,
  onDeveloperLocationChange,
  onOpenProfileWorkbench,
  onOpenStorage,
}: {
  developerTab?: DeveloperTab;
  developerTabError?: string;
  developerGuideId?: string;
  projectId?: string;
  taskDefinition: TaskDefinitionContract | null;
  onDeveloperLocationChange: (tab: DeveloperTab, guideId?: string) => void;
  onOpenProfileWorkbench: () => void;
  onOpenStorage: () => void;
}) {
  return <div className="workspace-admin-page">
    <div className="page-intro">
      <div>
        <span className="overline">WORKSPACE 全体</span>
        <h2>ワークスペース</h2>
        <p>全Projectの固定参照、学習View、変更ガイド、モデル実行環境を確認します。</p>
      </div>
      <button type="button" className="outline-button" onClick={onOpenStorage}>
        保存場所を管理
      </button>
    </div>
    <DeveloperControlCenter
      onOpenProfileWorkbench={onOpenProfileWorkbench}
      initialTab={developerTab}
      invalidTabId={developerTabError}
      initialGuideId={developerGuideId}
      projectId={projectId}
      taskDefinition={taskDefinition}
      onLocationChange={onDeveloperLocationChange}
    />
  </div>;
}
