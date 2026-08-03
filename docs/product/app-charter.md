<!--
document-status: current
verified-commit: a200415e5fdf8789011052f0a3a8139324304bce
owner: product architecture
source-of-truth: product scope and safety boundary
-->

# アプリ憲章

## 性格分類

Evidence Decision Workbench（判断根拠ワークベンチ）は、研究開発の意思決定を
支えるデスクトップ向けローカルアプリである。

単に予測値を表示するのではなく、候補、予測幅、支持範囲、類似実績、実測、検討Runを同じProject文脈で比較し、判断時点の証拠を再現可能に保存する。

現在の実装前提とv1固有の境界は [現行システム基準](current-system-baseline.md) を参照する。
domain-neutralな製品核と材料・製造domainの分離は
[domain-neutralな製品境界](../decisions/domain-neutral-product-boundary.md)を正本とする。

## Core、domain capability、Task

製品は次の三層を区別する。

- **Workbench Core**：Project、Dataset／Profile、Task、Package、Candidate、
  Prediction／Support、Design Space／Objective、Run、Snapshot、Actualを扱う。
- **Domain capability**：composition、heat program、sparse blend、材料lineage、
  Welding Chain等を型付き・allow-list済み能力として追加する。
- **Task／Example**：焼鈍、熱延、溶接、工具摩耗等の具体的なデータ、入力、出力、
  科学的制約を縦スライスとして提供する。

Coreを任意plugin基盤や万能domain schemaにしない。
材料固有の意味を一般語へ薄めず、Taskまたはdomain capabilityとして宣言する。

## データの重さ

ExcelまたはCSVのsource assetを読取専用の正本として扱う。pathはidentityではなくlocatorであり、内容SHA-256、Profile Revision、Dataset Revision、Dataset View Revisionを分離する。

アプリは候補、予測Snapshot、範囲探索Run、検討アクティビティRun、実測値、Chain実行・Snapshot、不確かさ伝播Run、逆算由来候補をローカルSQLiteへ保存する。canonical dataset、training view、feature representationはsourceとProfileから派生させ、元sourceを変更しない。

外部source更新では、credentialを保存しないConnector、不変Raw Snapshot、versioned Curation Recipe、品質判定、明示承認、Training Snapshotを分離する。source refreshから再学習、active Package切替、既存Project更新を自動実行しない。

### CSV onboardingの標準builder境界

Data Libraryの「新しい予測問題」は、利用者が確認した一行一観測のCSVから、
allow-list済みの標準Tabular Taskを準備する画面経路である。
同じ契約で、visible sheetを明示確認した一枚の矩形XLSXも受け入れる。
XLSXは元source digest、sheet、stored-value reader policyを固定したうえでcanonical snapshotへ
materializeし、formula、merged cell、hidden sheet、複雑な複数表はProfile Workbenchへ送る。
この経路はTask scaffold、標準builderによるPackage buildとverify、明示的なPackage promotion、
Dataset登録とruntime再読込を一つの補償可能な操作として扱う。

これは任意の学習環境をアプリへ持ち込むことではない。
任意Python、任意estimator、ハイパーパラメータ探索、外部training artifactの無検証取込は対象外である。
外部で学習・評価したPackageは、別途data-only Package契約とverifyを満たしたものだけを登録できる。
standard onboarding builderで作ったPackageも、active Packageの切替、既存Projectの更新、候補やSnapshotの再計算を自動では行わない。

## 利用者・配布

研究開発者が自分のWindows PCで使うローカルアプリ。
最初の利用domainと同梱Taskは材料・製造である。
Electron、React、FastAPIの境界を維持し、将来のWeb化より現在の検討速度、
オフライン利用、配布版の再現性を優先する。

## ローカルAPIの信頼境界

FastAPIはloopbackだけで待ち受けるが、loopbackであることだけを認証の代わりにはしない。

- Electronは起動ごとのlaunch tokenをsidecarとrenderer通信へ固定する。
- `npm run dev`も起動ごとのtokenを発行し、Vite proxyがAPI requestへ付与する。tokenをbrowser bundleへ埋め込まない。
- `file://`由来の`Origin: null`はDesktop launch tokenがある場合だけ許可する。
- tokenなしでAPIを単独起動した場合、browser originはloopbackまたは明示設定したoriginだけを許可する。Originを持たないlocal CLIとtest clientは利用できる。
- インターネット向け公開、別PCからの接続、共有サーバー運用は対象外であり、この境界をそのまま流用しない。

## 判断の安全原則

- source、Profile、Dataset、Task、Feature Pipeline、Model Package、Candidate、Chainのversionとdigestを固定する。
- 保存済みSnapshotやRunを最新データ・最新Packageで自動再計算しない。
- 予測値、実測値、入力ばらつき、モデル不確実性、段間伝播不確かさを混同しない。
- 単位変換、Stage binding、候補制約、除外理由を暗黙処理しない。
- stale responseとsuperseded workを現在の候補へ反映しない。
- Model PackageからPythonコード、pickle、joblib、任意pluginを読み込まない。
- Activity、逆算、探索の結果から候補・Snapshot・active Packageを自動採用しない。

## Projectの科学的identity

Projectは、ある時点の再現可能な検討単位である。現在は次の三種類を明示的に扱う。

### Single-task Project

- Dataset View Revision
- Prediction Task contract digest
- Model Package manifest digest

を固定し、一つのTaskを候補比較、予測、応答曲線、類似実績、Snapshot、実測照合、検討アクティビティへ接続する。

Project-level Design Spaceは、この固定されたTaskの入力について、今回の検討で変更してよい範囲と選択肢を定める。

Objective Definitionは、このProjectで達成したい目的、hard constraint、soft preferenceを定める。

どちらもTask Definitionそのものを書き換えず、Projectの意思決定文脈へversion付きで固定する。

### Chain Project

- Chain Revision ID
- Chain Revision digest

を固定する。Chain Revisionは順序付きStage、binding、Task／transform contract、Package、Dataset／Profile、単位変換を固定する。

段別実行、変更段以降だけの再計算、段単体／通し評価、中間実測を使う別analysis variant、明示的な不確かさ伝播を扱う。Task自身へChain固有bindingを埋め込まない。

### Prediction Graph Project

- Prediction Graph Definition / Revision ID
- Graph Revision digest

を固定する。明示的なstage依存とdecision outputを持ち、公開済みの不変RevisionをProjectへbindingする。同梱Graphは比較fixtureであり、任意pluginや一般的なworkflow実行基盤ではない。

## 予測Taskとモデルの構成

production Taskは独立した縦スライスとして持ち、入力schema、特徴量Pipeline、Model Package、支持度参照、候補比較を混在させない。同じTaskはsingle-task ProjectでもChain Stageでも再利用できる。

現行のTask登録内容、source、active Packageは [生成済みTask inventory](../contracts/task-inventory.json) を唯一の件数・構成一覧とする。
Taskごとのauthoring、runtime、Graph、候補provenanceを横断して判断するときは、[生成済みCapability Atlas](../contracts/capability-atlas.json)を読む。Atlasは同梱contractから生成するauthorityであり、個人Workspaceを読むModel Libraryは動的なread modelとして混ぜない。

Model Packageはdata-onlyであり、allow-list済みadapterだけが読み込む。新しいモデル手法はTaskやUIをモデル実装へ固定せず、共通Predictive Summaryへ変換する。

## データ解釈と学習行

Profileは外部sourceをcanonicalな意味へ対応付ける。データ形状に応じて複数のProfile familyを許可し、万能schemaへ押し込まない。

- relationの一行をそのまま学習行へ変換しない。
- 工程条件、反復観測、観測行固有入力を分離する。
- 目的変数ごとにTraining Cohortを持ち、欠損targetのためにsource行を消さない。
- raw、curated、canonical、feature representationを同じ値として扱わない。

## 検討アクティビティ

判断に必要な問いを、画面名ではなくActivity Definitionとして定義する。Activityは必要なruntime capability、resource、parameter、result契約を持ち、candidate revisionと実行条件を固定したRunを保存する。

現在のproduction Activityは、ロバストネス／公差解析、候補差分の要因分解、目標へ届く最小変更の三つである。

Activity結果は判断材料であり、自動意思決定ではない。

## 制約付き逆算の境界

固定されたStage A科学変換の範囲では、目標材料成分から原料配合へのLP／MILP逆算を扱う。solver、Design Space、科学master、商用catalog、基準candidate revisionをprovenanceへ固定し、結果は通常Candidateとして明示的に保存する。

これは一般的な特性逆問題、Bayesian optimization、任意の非線形最適化、自動最良候補選択を意味しない。

## 標準からの逸脱

- 画面はデスクトップ中心。モバイルは内容確認できる縮退表示までとする。
- 応答曲線や区間は、実モデルまたは実データから計算できる情報だけを表示する。
- 汎用plugin基盤より、data-only契約と少数のallow-list済み実装を優先する。
- Profile、Candidate Shape、Chain candidate preparationは、異質なデータを一つのschemaへ統合するより型付きの複数familyを許可する。

## 対象外とするもの

- 任意のアプリ内モデル学習、ハイパーパラメータ探索、active Packageの自動切替。
- 一般目的のBayesian optimization、自動実験実行、特性から全Stageを反転する汎用逆問題。
- 認証・監査・高可用性などのエンタープライズ品質、汎用プラグイン基盤、汎用EDA・BI・ETL builder。
- 元データの直接修正、学習データの自動更新、source更新を契機にした自動再学習。
- 複数特性の同時確率を、output相関を持たない近似から生成すること。
- 候補の「検討中」「実験予定」などの厳密なワークフローステータス管理。
- 画像、一般グラフ、任意の可変長系列を万能入力として扱うこと。

## 採用済みの拡張方向

- Data AssetとDataset Profileを不変なDataset RevisionとしてData Libraryへ登録する。
- Source Lifecycleでは、Connector、Raw Snapshot、Curation Recipe、品質判定、承認、Training Snapshotを分離する。
- Projectはsingle-taskまたはChainの科学的identityを固定し、過去の判断を自動更新しない。
- Project-level Design SpaceとObjective DefinitionをProjectの意思決定文脈へ固定する。
- 探索データ、目的変数別Training Cohort、モデル支持範囲、Project内の類似条件を分離する。
- 複数Projectは任意の検討グループへ束ね、所属と前後関係を分離する。
- 判断に必要な問いを、型付きparameter／resultを持つ検討アクティビティとして追加する。
- 可変長系列ではraw series、canonical series、model representationを分離し、契約、保存、API、inspectorを接続する。
- ChainはTask／transformをbindingで再利用し、段別の版、実測、精度、不確かさを分離する。
- 第二のscalar Chainで、疎配合や溶接固有の決定論的Stageに依存しないChain Coreを検証する。

これらは契約または縦スライスとして実装済みである。

ただし、契約が実装済みであることと、すべてのproduction画面から作成、編集、実行できることは同じではない。

可変長系列はinspectorまで、Source Lifecycleは資源登録と承認境界まで、第二のscalar Chainはproofまでが現行範囲であり、production UIの採用範囲は [現行システム基準](current-system-baseline.md) を正本とする。

## 未実装の将来候補

- 新しいCandidate Shape：任意JSONではなく、persistence、diff、copy、snapshotの意味を持つ型付きfamilyとして追加できること。
- Source Connectorの自動refresh運用：更新、承認、再学習、active化を自動連結せず、利用者が各境界を確認できること。
- scalar Chainをproduction UIから作成、編集、実行する導線：既存のWelding Chain専用画面を一般editorと呼ばず、Task familyごとの入力体験を検証してから導入すること。

詳細は [現行システム基準](current-system-baseline.md)、[Data LibraryとProject参照境界](../decisions/data-library-project-references.md)、[検討アクティビティ](../contracts/decision-activities.md)、[多段Chainアーキテクチャ](../decisions/multistage-chain-architecture.md) を参照する。
