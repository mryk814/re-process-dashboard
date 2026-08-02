/**
 * The execution contract for every Playwright spec.
 *
 * `shared-read-only` may share the seeded default workspace and therefore run
 * with Playwright workers. `isolated` gets a whole fresh server, DB, and stores
 * per spec process through `test:e2e:isolated`. `serial-journey` deliberately
 * keeps one worker because its assertions mutate the seeded Project or carry a
 * UI journey across phases. `dedicated-runtime` has a separate process contract.
 */
export const suiteInventory = {
  "accessibility-smoke.spec.ts": {
    kind: "serial-journey",
    cleanupOwner: "spec API cleanup",
    reason: "default Projectと追加Projectの両方を更新するアクセシビリティjourney。",
  },
  "analysis-navigation-resume.spec.ts": {
    kind: "isolated",
    cleanupOwner: "fresh spec process",
    reason: "専用の単段／Chain Project、Candidate、Run、Snapshotを作成して分析位置の復元を確認する。",
  },
  "annealing-time-basis.spec.ts": {
    kind: "isolated",
    cleanupOwner: "fresh spec process",
    reason: "専用Projectの候補を更新するため、他specとはDBごと分離する。",
  },
  "api-offline.spec.ts": {
    kind: "serial-journey",
    cleanupOwner: "browser route teardown",
    reason: "default Projectを表示しつつAPI遮断を注入するfailure journey。",
  },
  "candidate-comparison-accessibility.spec.ts": {
    kind: "serial-journey",
    cleanupOwner: "spec API cleanup",
    reason: "default Projectの候補と比較基準を更新する。",
  },
  "candidate-comparison-reference.spec.ts": {
    kind: "serial-journey",
    cleanupOwner: "seeded DB disposal",
    reason: "比較基準の選択をdefault Projectへ保存する。",
  },
  "candidate-save-safety.spec.ts": {
    kind: "serial-journey",
    cleanupOwner: "fresh DB disposal",
    reason: "単段／Chain ProjectとCandidateを作成し、保存競合と画面離脱境界を確認する。",
  },
  "chain-degraded.spec.ts": {
    kind: "dedicated-runtime",
    cleanupOwner: "chain degraded config",
    reason: "壊れたChain evaluation fixtureと専用API processが必要。",
  },
  "chain-graph-viewer.spec.ts": {
    kind: "isolated",
    cleanupOwner: "fresh spec process",
    reason: "固定Chain Project、Candidate、Executionを作成してread-only graphを確認する。",
  },
  "chain-input-contract.spec.ts": {
    kind: "isolated",
    cleanupOwner: "fresh spec process",
    reason: "Chain Project、Candidate、Runを生成する。",
  },
  "chain-output-presentation.spec.ts": {
    kind: "serial-journey",
    cleanupOwner: "fresh DB disposal",
    reason: "専用Chain ProjectとCandidateを作成し、目標値と判断履歴を更新する。",
  },
  "chain-studio.spec.ts": {
    kind: "serial-journey",
    cleanupOwner: "seeded DB disposal",
    reason: "Chain、Project、Candidateを作成し、同一画面で公開から実行まで確認する。",
  },
  "data-library-structure.spec.ts": {
    kind: "serial-journey",
    cleanupOwner: "seeded DB and personal store disposal",
    reason: "同一file内で個人TaskとModel Packageを準備するため、default suiteで直列実行する。",
  },
  "decision-activity.spec.ts": {
    kind: "serial-journey",
    cleanupOwner: "spec API cleanup",
    reason: "default ProjectとActivity Runを更新する。",
  },
  "decision-navigation.spec.ts": {
    kind: "serial-journey",
    cleanupOwner: "fresh DB disposal",
    reason: "default Projectからの画面遷移と専用Project作成を混在させる。",
  },
  "degraded-task.spec.ts": {
    kind: "dedicated-runtime",
    cleanupOwner: "run-degraded-task-e2e.mjs",
    reason: "欠損Packageを注入する専用runtimeが必要。",
  },
  "domain-neutral-product.spec.ts": {
    kind: "serial-journey",
    cleanupOwner: "fresh DB disposal",
    reason: "専用ProjectとCandidateを作成し、Data Libraryへの画面遷移まで確認する。",
  },
  "evidence-resource-states.spec.ts": {
    kind: "serial-journey",
    cleanupOwner: "fresh DB disposal",
    reason: "default Project上でQuality／Lineageの失敗応答と再試行を注入する。",
  },
  "first-run-guidance.spec.ts": {
    kind: "serial-journey",
    cleanupOwner: "spec API cleanup",
    reason: "default Projectの導線と一時Project作成を確認する。",
  },
  "inference-p0.spec.ts": {
    kind: "serial-journey",
    cleanupOwner: "seeded DB disposal",
    reason: "default Candidateの予測状態を更新する。",
  },
  "interval-semantics.spec.ts": {
    kind: "serial-journey",
    cleanupOwner: "fresh DB disposal",
    reason: "専用ProjectとScreening Runを作成し、区間意味の画面契約を確認する。",
  },
  "microstructure-evidence.spec.ts": {
    kind: "shared-read-only",
    cleanupOwner: "none",
    reason: "seeded lineage assetを読むだけで、API mutationを行わない。",
  },
  "model-library.spec.ts": {
    kind: "serial-journey",
    cleanupOwner: "fresh DB disposal",
    reason: "Model Libraryから固定identityを確認し、Prediction Graph draftとProject作成導線を検証する。",
  },
  "navigation-intent.spec.ts": {
    kind: "serial-journey",
    cleanupOwner: "spec API cleanup",
    reason: "default Projectの候補導線とhistoryを更新する。",
  },
  "numeric-domain-semantics.spec.ts": {
    kind: "serial-journey",
    cleanupOwner: "seeded DB disposal",
    reason: "専用ProjectとCandidateを作成して数値domainのAPI／UI契約を確認する。",
  },
  "prediction-graph-material-fixture.spec.ts": {
    kind: "serial-journey",
    cleanupOwner: "fresh DB disposal",
    reason: "Prediction Graph Project、Candidate、Executionを作成して同梱fixtureを確認する。",
  },
  "profile-workbench-authoring.spec.ts": {
    kind: "isolated",
    cleanupOwner: "fresh spec process",
    reason: "個人ProfileとDatasetを作成する。",
  },
  "project-hub.spec.ts": {
    kind: "serial-journey",
    cleanupOwner: "spec API cleanup",
    reason: "Project groupとdefault Projectを広く更新する。",
  },
  "project-resource-failure.spec.ts": {
    kind: "serial-journey",
    cleanupOwner: "fresh DB disposal",
    reason: "default Projectの参照取得失敗と局所再試行を注入する。",
  },
  "responsive-navigation.spec.ts": {
    kind: "shared-read-only",
    cleanupOwner: "none",
    reason: "画面幅とlocal dialogだけを扱い、永続resourceを更新しない。",
  },
  "sample-gallery.spec.ts": {
    kind: "dedicated-runtime",
    cleanupOwner: "sample gallery config",
    reason: "gallery専用DB起動契約を持つ。",
  },
  "screening-workbench.spec.ts": {
    kind: "isolated",
    cleanupOwner: "fresh spec process",
    reason: "専用Projectでscreening Runを作成する。",
  },
  "series-library.spec.ts": {
    kind: "shared-read-only",
    cleanupOwner: "none",
    reason: "同梱Seriesの閲覧とfeature表示だけを確認する。",
  },
  "shared-workbench.spec.ts": {
    kind: "serial-journey",
    cleanupOwner: "seeded DB disposal",
    reason: "default Candidateを連続操作する長い回帰journey。",
  },
  "similar-upstream-choice.spec.ts": {
    kind: "serial-journey",
    cleanupOwner: "spec API cleanup",
    reason: "default Candidateとsimilar responseを更新・注入する。",
  },
  "source-lifecycle.spec.ts": {
    kind: "serial-journey",
    cleanupOwner: "fresh DB disposal",
    reason: "Connector、Raw Snapshot、Curation Runを相互参照しながら作成する。",
  },
  "source-lifecycle-resource-failure.spec.ts": {
    kind: "serial-journey",
    cleanupOwner: "fresh DB disposal",
    reason: "ConnectorとCuration Runを作成し、reason row取得失敗と局所再試行を注入する。",
  },
  "startup-diagnostic.spec.ts": {
    kind: "dedicated-runtime",
    cleanupOwner: "startup diagnostic config",
    reason: "APIを起動しないdiagnostic-only web runtime。",
  },
  "workspace-scope.spec.ts": {
    kind: "shared-read-only",
    cleanupOwner: "none",
    reason: "legacy URLの正規化とhistoryだけを確認する。",
  },
};

export const sharedReadOnlySpecs = Object.entries(suiteInventory)
  .filter(([, entry]) => entry.kind === "shared-read-only")
  .map(([filename]) => filename);

export const isolatedSpecs = Object.entries(suiteInventory)
  .filter(([, entry]) => entry.kind === "isolated")
  .map(([filename]) => filename);

export const suiteKinds = new Set([
  "shared-read-only",
  "isolated",
  "serial-journey",
  "dedicated-runtime",
  "blocked",
]);
