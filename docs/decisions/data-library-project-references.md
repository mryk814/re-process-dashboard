# Data LibraryとProject参照境界

| 項目 | 内容 |
|---|---|
| 状態 | 採用。single-task境界を実装済み。Chain Projectへの拡張は多段Chain ADRで採用・実装済み |
| 決定日 | 2026-07-22 |
| 実装追記 | 2026-07-25 |
| 対象 | データ登録、予測Task、Project、Model Package、探索・類似度・支持範囲、Project scientific identity |

## 背景

決定当時のアプリは、起動時に `source_kind` ごとに一つのExcelを読み込み、そのデータとactive Model PackageからTask runtimeを構築していた。
Projectは `task_id` を持つが、使用したデータ、Profile、Model Packageを固定していなかった。

このため、同じ予測Taskで複数データを使い分けること、データ追加後も過去の判断を再現すること、学習には使えないが探索には必要な行を保持することが難しかった。

データ、Task、モデル、Projectを一つの所有階層へ押し込めず、独立した不変資産をProjectが参照する構造へ変更する。

## 決定

### Data AssetとDataset Revision

- `DataAsset` は元ファイルの不変なバイト列を表し、SHA-256、元ファイル名、媒体種別、登録日時を持つ。pathはidentityではなくlocatorとして扱う。
- 利用者が登録したファイルはmanaged libraryへコピーし、同じSHA-256は重複登録しない。bundled sourceは不変な組み込みlocatorとして登録できる。
- `DatasetProfileRevision` は、外部シート、列、単位、entity identity、relation、eligibilityをcanonical contractへ写す不変の解釈契約である。
- `DatasetRevision` は `DataAsset + DatasetProfileRevision + canonicalization contract` の組である。
- 元ファイルまたはProfileの意味が変わった場合は上書きせず、新しいRevisionを作る。
- 元Excel／CSVは読み取り専用とし、アプリから変更しない。

Profileはデータの意味を与えるが、分析目的は持たない。何を予測するかはPrediction Taskが定義する。

### Dataset View

`DatasetViewRevision` はProjectが参照するデータ集合を表す。

最初の実装では一つのDataset Revisionを参照する `single` と、複数のDataset Revisionを境界を保って並べる `cohort_comparison` を扱う。

`cohort_comparison` は行を暗黙にpoolしない。設備、試験場所、取得時期などの由来とDataset Revision IDを保持する。
学習用のpool、entity join、lookup enrichmentは科学的意味とcardinalityを持つ別の派生レシピであり、汎用UIから作成しない。

### Projectと検討グループ

Projectは、ある時点で再現可能な一つの検討と意思決定を表す。
現在の科学的identityは明示的なunionである。

#### Single-task Project

次を固定する。

- Dataset View Revision
- Prediction Task IDと契約digest
- Model Package IDとmanifest digest

使用データ、Task、モデルを変更して既存Projectの意味を上書きしない。
変更後の検討は新しいProjectとして作り、必要ならコピー元またはpredecessor Projectを記録する。

#### Chain Projectへの後続拡張

多段Chain ADRにより、Project identityへ次を追加した。

- Chain Revision ID
- Chain Revision digest

Chain RevisionはChain Definition、binding、単位変換、順序付きStageのcontract／Package／Dataset／Profile参照を固定する。
Chain Projectをsingle-task runtimeへ暗黙接続せず、専用のcandidate preparationとStage executionで扱う。

この拡張は「一つのProjectへ任意のTaskを後から追加する」ことではない。Project作成時にsingle-taskまたは固定Chain Revisionのどちらかを選び、identity kindを後から暗黙変更しない。

内部モデルの `ProjectSeries` は、利用者向けUIでは「検討グループ」として扱う。
検討グループ自身はデータ、Task、モデル、Chainを固定せず、Projectはscientific identityが異なっていても任意のグループへ所属・移動できる。

前のProjectと継続理由（Task変更、データ追加、追試、モデル更新、Chain化、再評価）はグループ所属とは独立して記録する。
Projectは任意の `predecessor_project_id` を持ち、別グループのProjectを続き元にしてもよい。

UIの階層は「検討グループ > Project > 候補」に統一し、前後関係の操作だけを「このProjectの続き」と表現する。

### Prediction TaskとDecision Activity

- `Prediction Task` は入力、出力、単位、制約、Feature Pipelineとの契約を持つ開発者管理の予測能力である。
- `Decision Activity` は判断に使う問い、必要なruntime operation／resource、入力parameter、結果契約を持つ明示実行単位である。
- Explore、Quality、Lineageなどのデータ作業面と、候補比較、範囲探索、実測照合などの既存操作をTaskDefinitionへ汎用workflowとして埋め込まない。

現在のproduction Decision Activityはロバストネス／公差解析であり、candidate revisionと実行条件を固定したimmutable Runを保存する。

### 探索データ、学習コホート、モデル適用先

次を別のidentityとして扱う。

1. `Exploration Corpus`：Dataset View内のcanonical化できた全レコード。X／Y欠損やrelation不備も理由付きで保持する。
2. `Training Cohort`：Model Packageのpredictor・目的変数ごとに、feature化可能なXと対象Yを持ち、eligibilityを満たす行だけから作る。
3. `Application Dataset`：Projectが参照し、候補の由来、実測比較、系譜、文脈上の類似条件に使うDataset View。

TSがありELがない行はTSのTraining Cohortには入り、ELのTraining Cohortには入らないが、Exploration Corpusからは消さない。
除外理由は `missing_target`、`incomplete_input`、`policy_rejected` など機械判定可能な値で残す。

複数観測familyを持つDatasetでは、観測行固有入力、target別cohort、split group、provenanceをfamilyごとのTraining Viewへ保持する。親単位の代表値へ暗黙集約しない。

### Model Recipe、Model Package、互換性

- `Model Recipe` はGPの構造、前処理、学習方法など再学習可能な方式を表す。
- `Model Package` は特定のTraining Cohortでfitした不変のdata-only成果物を表す。

Model Packageは学習Dataset Revision、Profile digest、source digest、目的変数別Training Cohort digestを来歴として保持する。
これらはPackageが何から作られたかを検証する情報であり、ProjectのApplication Datasetとの完全一致条件にはしない。

別Datasetへの推論適用は、Prediction Task、canonical input paths、quantity、単位、カテゴリ、Feature Pipelineが互換なら許可する。
未知カテゴリ、必須入力不足、固定context違反は拒否する。
別ファイルであることだけを理由に拒否しない。

### モデル支持と文脈上の類似条件

- `Model support` は、predictor・目的変数ごとのTraining Cohortにあるtraining Xから計算する。
- `Contextual similarity` は、ProjectのApplication Dataset内にあるfeature化可能なXから計算する。Y欠損行を含めてよい。
- モデル学習後に追加されたY付き行は、必要に応じて `post-training evidence` として区別する。

画面とAPIでこの二つを同じ「類似実験」または単一Support値として混在させない。
予測SnapshotはApplication Dataset、Model Package、Training Cohort、support計算規則のidentityを固定する。

ChainではStageごとのPackage／Dataset／Profile lockと、段単体評価／通し評価を分離する。上流予測を通した最終性能をStage C単体の品質として表示しない。

### runtime解決

Task registryはPrediction Taskの不変な契約とTaskModuleを所有する。
起動時の単一runtimeをProjectから暗黙参照しない。

`ProjectRuntimeResolver` はsingle-task Projectが固定したDataset View RevisionとModel Package manifest digestを解決し、Task契約との互換性を検証してruntimeを構築する。
runtime cache keyにはtask contract digest、package manifest digest、Dataset View digestを含める。

Chain ProjectはChain Revisionに固定されたStage contract、Package、Dataset／Profile、bindingを専用execution serviceで解決する。single-task resolverへfallbackしない。

`active-packages.json` は新規single-task Projectまたは新しいChain Revisionを構築する際の既定候補としてだけ使用する。
Projectが固定したPackageが見つからない場合、履歴は閲覧できるがlive predictionは利用不可とし、active Packageへfallbackしない。

### Profile authoring

Profileの作成・変更はProjectに紐づけず、Data LibraryへDatasetを登録する独立フロー `Profile Workbench` とする。
Project内の通常作業面には置かない。

任意JSON editorを主UIにせず、次を支援する。

- source inventoryと既存Profile類似度
- mapping、key、relation、unit、eligibility候補
- 人間が判断すべき曖昧箇所の抽出
- canonical preview、除外理由、未解決relation
- 継承解決後のeffective Profile差分と影響範囲
- 最小override JSONと契約テスト雛形の生成

新しい物理量、TaskDefinition、Feature Engineering、runtime／model adapter、モデル品質承認は引き続き開発者管理とする。

## APIとUIの境界

- トップレベルに `Projects` と `Data Library` を置く。
- Profile WorkbenchはData Libraryから開始するDataset登録フローとする。
- データが一つしかない場合、Project作成時のデータ選択を自動化する。常時表示の「起動中データ」切替は置かない。
- single-task Project作成は、Dataset View、互換Prediction Task、Model Packageの順に行う。
- Chain Project作成は、Dataset／Template、固定可能なChain Revisionの順に行う。
- Project内には利用可能な操作とActivityだけを表示し、利用不能理由をcapabilityから説明する。
- Data Libraryは登録、Profile検出、検証結果、対応Task、利用Project、archive状態を表示する。

## 既存データの移行

SQLite migrationは追加的に行い、既存Project、候補、Snapshot、判断を削除・再seedしない。

1. 起動時に読み込めた既存source／ProfileをDataAsset、Profile Revision、Dataset Revisionとして登録する。
2. source単位のsingle Dataset Viewを作る。
3. 既存Projectへ、そのTaskのupgrade時runtimeが使用しているDataset Viewとactive Packageを固定する。過去時点のbindingは現DBだけから復元できないため、`assumed_current_at_upgrade` と明記し、当時のものだったと偽装しない。
4. 既存Prediction Snapshotは変更せず、既存来歴を読み取れるままにする。
5. 参照中のDataset Revision、View、Series、Package、Chain Revisionは物理削除せずarchiveまたは履歴参照可能に保つ。
6. Project scientific identityは `single_task | chain` の明示unionとして移行し、部分的なbindingを捏造しない。

## 実装状況

このADRのsingle-task境界は実装済みである。

- Data Asset／Profile／Dataset／Viewの不変identity
- ProjectへのDataset View／Task／Package固定
- Project単位runtime解決
- Training CohortとModel support
- Data Library、Profile Workbench、Project作成
- generated Task inventoryとdrift check

後続の [多段Chainアーキテクチャ](multistage-chain-architecture.md) により、Project scientific identity、Stage lock、Chain execution／Snapshot、中間実測variant、段単体／通し評価、明示的な不確かさ伝播を追加した。

現在の機能一覧とv1固有境界は [現行システム基準](../current-system-baseline.md) を参照する。

## 対象外

- アプリ内モデル学習
- 任意コードを含むProfileやモデルplugin
- 一般利用者向けの汎用join builder
- 既存Projectのデータ、モデル、Chain Revisionを自動更新すること
- Datasetの境界を失う暗黙のpooling
- single-taskとchainのidentityを暗黙変換すること
