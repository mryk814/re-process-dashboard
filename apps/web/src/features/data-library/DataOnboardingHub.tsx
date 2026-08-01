import type { ApiDataLibraryDataset } from "../../shared/api/workbench-api";
import { CsvTaskOnboarding, type PreparedCsvProjectBinding } from "./CsvTaskOnboarding";
import type { DataLibraryLocation } from "./location";

export function DataOnboardingHub({
  selectedDataset,
  location,
  onAddDataset,
  onStartProject,
  onOpenStorage,
  onNavigate,
}: {
  selectedDataset?: ApiDataLibraryDataset;
  location: DataLibraryLocation;
  onAddDataset: (
    mode?: "revision" | "mapping",
    baseDatasetRevisionId?: string,
  ) => void;
  onStartProject: (
    datasetViewRevisionId: string,
    binding?: Omit<PreparedCsvProjectBinding, "datasetViewId">,
  ) => void;
  onOpenStorage: () => void;
  onNavigate: (location: DataLibraryLocation, replace?: boolean) => void;
}) {
  const newTaskGuideOpen = location.onboardingMode === "new-task";
  return <>
    <section className="data-onboarding-paths" aria-labelledby="data-onboarding-heading">
      <header><div><span className="overline">ADD DATA</span><h3 id="data-onboarding-heading">追加するデータはどれですか</h3></div><small>先に作業量と安全境界を分けます</small></header>
      <div>
        <button
          type="button"
          disabled={!selectedDataset}
          onClick={() => selectedDataset && onAddDataset("revision", selectedDataset.dataset_revision.id)}
        ><b>更新版</b><span>同じTask・Profile</span><small>Source差分 → 新Revision → 再学習</small></button>
        <button type="button" onClick={() => onAddDataset("mapping")}><b>列名・構造が違う</b><span>既存Taskへ対応付け</span><small>Profile draft → 検証 → 登録</small></button>
        <button type="button" aria-expanded={newTaskGuideOpen} onClick={() => onNavigate({ tab: "browse", onboardingMode: newTaskGuideOpen ? undefined : "new-task" })}><b>新しい予測問題</b><span>入力・出力も新しい</span><small>意味と単位を確認 → scaffold</small></button>
      </div>
    </section>
    {newTaskGuideOpen && <section className="data-library-section new-task-guide" aria-labelledby="new-task-guide-heading">
      <header><div><span className="overline">NEW TASK SCAFFOLD</span><h3 id="new-task-guide-heading">完全に新しいTaskを準備</h3><p>任意コードは生成せず、確認済みのTaskDefinition・Dataset Profile・標準学習recipeを個人Task storeへ作ります。</p></div><button type="button" className="text-button" aria-label="新しいTaskの手順を閉じる" onClick={() => onNavigate({ tab: "browse" })}>閉じる</button></header>
      <ol><li><b>列を棚卸し</b><span>型・範囲・候補値だけをread-onlyで確認</span></li><li><b>意味を確定</b><span>入力／出力、canonical key、単位を明示</span></li><li><b>学習・昇格</b><span>allow-list済みEstimatorでbuild / verify / promote</span></li><li><b>アプリへ接続</b><span>個人Taskを再読込し、そのままProjectを作成</span></li></ol>
      <p className="new-task-ui-path">CSVまたは単一表Excelを選び、意味・単位・範囲を確認すると、標準recipeで <b>build → verify → promote → 再読込</b> までをこの画面で実行します。途中で止まった場合は、登録済みとして扱わず理由を表示します。</p>
      <div className="new-task-safety"><strong>自動確定しない項目</strong><span>物理的意味 · 単位 · 物理／通常／学習範囲 · 学習一行 · relation · 目的変数</span><small>inspectの最小値・最大値は要約です。物理範囲には流用せず、未解決が1件でもあればdraftで止まります。元データ、個人Profile、Packageはリポジトリへ追加しません。</small></div>
      <CsvTaskOnboarding
        onOpenStorage={onOpenStorage}
        onOpenProfileWorkbench={() => onAddDataset("mapping")}
        onPrepared={(binding) => onStartProject(binding.datasetViewId, binding)}
      />
    </section>}
  </>;
}
