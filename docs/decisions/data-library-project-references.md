# Data LibraryとProject参照境界

| 項目 | 内容 |
|---|---|
| 状態 | 採用。段階的に実装する |
| 決定日 | 2026-07-22 |
| 対象 | データ登録、予測タスク、Project、Model Package、探索・類似度・支持範囲 |

## 背景

現行アプリは、起動時に `source_kind` ごとに一つのExcelを読み込み、そのデータとactive Model PackageからTask runtimeを構築する。
Projectは `task_id` を持つが、使用したデータ、Profile、Model Packageを固定しない。
このため、同じ予測タスクで複数データを使い分けること、データ追加後も過去の判断を再現すること、学習には使えないが探索には必要な行を保持することが難しい。

データ、タスク、モデル、Projectを一つの所有階層へ押し込めず、独立した不変資産をProjectが参照する構造へ変更する。

## 決定

### Data AssetとDataset Revision

- `DataAsset` は元ファイルの不変なバイト列を表し、SHA-256、元ファイル名、媒体種別、登録日時を持つ。pathはidentityではなくlocatorとして扱う。
- 利用者が登録したファイルはmanaged libraryへコピーし、同じSHA-256は重複登録しない。bundled sourceは不変な組み込みlocatorとして登録できる。
- `DatasetProfileRevision` は、外部シート、列、単位、entity identity、relation、eligibilityをcanonical contractへ写す不変の解釈契約である。
- `DatasetRevision` は `DataAsset + DatasetProfileRevision + canonicalization contract` の組である。
- 元ファイルまたはProfileの意味が変わった場合は上書きせず、新しいRevisionを作る。
- 元Excelは読み取り専用とし、アプリから変更しない。

Profileはデータの意味を与えるが、分析目的は持たない。何を予測するかはPrediction Taskが定義する。

### Dataset View

`DatasetViewRevision` はProjectが参照するデータ集合を表す。
最初の実装では一つのDataset Revisionを参照する `single` と、複数のDataset Revisionを境界を保って並べる `cohort_comparison` を扱う。

`cohort_comparison` は行を暗黙にpoolしない。設備、試験場所、取得時期などの由来とDataset Revision IDを保持する。
学習用のpool、entity join、lookup enrichmentは科学的意味とcardinalityを持つ別の派生レシピであり、汎用UIから作成しない。

### ProjectとProject Series

Projectは、ある時点で再現可能な一つの検討と意思決定を表し、次を固定する。

- Dataset View Revision
- Prediction Task IDと契約digest
- Model Package IDとmanifest digest

使用データ、タスク、モデルを変更して既存Projectの意味を上書きしない。
変更後の検討は新しいProjectとして作り、必要ならコピー元Projectを記録する。

`ProjectSeries` は、同じ科学的・意思決定上の問いを継承するProject群である。
Series自身はデータ、タスク、モデルを固定しない。
Projectは任意のSeriesに所属し、前のProject、継続理由（データ追加、追試、モデル更新、再評価）を記録できる。
Series内の分岐を表せるよう、Projectは任意の `predecessor_project_id` を持つ。

UIでは常に「シリーズ」という箱を要求せず、「一連の検討」「この検討の続き」として扱う。

### Prediction TaskとActivity

- `Prediction Task` は入力、出力、単位、制約、Feature Pipelineとの契約を持つ開発者管理の予測能力である。
- `Activity` はExplore、Quality、Lineage、候補比較、範囲探索、実測照合など、Project内で利用者が行う作業である。

データ探索や品質確認をPrediction Taskへ追加してTaskDefinitionを汎用ワークフロー定義にしない。

### 探索データ、学習コホート、モデル適用先

次を別のidentityとして扱う。

1. `Exploration Corpus`：Dataset View内のcanonical化できた全レコード。X/Y欠損やrelation不備も理由付きで保持する。
2. `Training Cohort`：Model Packageのpredictor・目的変数ごとに、feature化可能なXと対象Yを持ち、eligibilityを満たす行だけから作る。
3. `Application Dataset`：Projectが参照し、候補の由来、実測比較、系譜、文脈上の類似条件に使うDataset View。

TSがありELがない行はTSのTraining Cohortには入り、ELのTraining Cohortには入らないが、Exploration Corpusからは消さない。
除外理由は `missing_target`、`incomplete_input`、`policy_rejected` など機械判定可能な値で残す。

### Model Recipe、Model Package、互換性

- `Model Recipe` はGPの構造、前処理、学習方法など再学習可能な方式を表す。
- `Model Package` は特定のTraining Cohortでfitした不変の成果物を表す。

Model Packageは学習Dataset Revision、Profile digest、source digest、目的変数別Training Cohort digestを来歴として保持する。
これらはPackageが何から作られたかを検証するための情報であり、ProjectのApplication Datasetとの完全一致条件にはしない。

別Datasetへの推論適用は、Prediction Task、canonical input paths、単位・カテゴリ、Feature Pipelineが互換なら許可する。
未知カテゴリ、必須入力不足、固定context違反は拒否する。
別ファイルであることだけを理由に拒否しない。

### モデル支持と文脈上の類似条件

- `Model support` は、predictor・目的変数ごとのTraining Cohortにあるtraining Xから計算する。
- `Contextual similarity` は、ProjectのApplication Dataset内にあるfeature化可能なXから計算する。Y欠損行を含めてよい。
- モデル学習後に追加されたY付き行は、必要に応じて `post-training evidence` として区別する。

画面とAPIでこの二つを同じ「類似実験」または単一Support値として混在させない。
予測スナップショットはApplication Dataset、Model Package、Training Cohort、support計算規則のidentityを固定する。

### runtime解決

Task registryはPrediction Taskの不変な契約とTaskModuleだけを所有する。
起動時の単一runtimeをProjectから暗黙参照しない。

`ProjectRuntimeResolver` はProjectが固定したDataset View RevisionとModel Package manifest digestを解決し、Task契約との互換性を検証してruntimeを構築する。
runtime cache keyにはtask contract digest、package manifest digest、Dataset View digestを含める。

`active-packages.json` は新規Projectの既定候補としてのみ使用する。
Projectが固定したPackageが見つからない場合、履歴は閲覧できるがlive predictionは利用不可とし、active Packageへfallbackしない。

### Profile authoring

Profileの作成・変更は一般利用者向け機能ではなく、開発者向け `Profile Workbench` とする。
任意JSON editorを主UIにせず、次を支援する。

- workbook inventoryと既存Profile類似度
- mapping、key、relation、unit、eligibility候補
- 人間が判断すべき曖昧箇所の抽出
- canonical preview、除外理由、未解決relation
- 継承解決後のeffective Profile差分と影響範囲
- 最小override JSONと契約テスト雛形の生成

新しい物理量、TaskDefinition、Feature Engineering、runtime/model adapter、モデル品質承認は引き続き開発者管理とする。

## APIとUIの境界

- トップレベルに `Projects` と `Data Library` を置く。Model管理とProfile WorkbenchはDeveloper領域に置く。
- データが一つしかない場合、Project作成時のデータ選択を自動化する。常時表示の「起動中データ」切替は置かない。
- Project作成は、Dataset View選択、互換Prediction Task選択、Model Package選択の順に行う。
- Project内には利用可能なActivityだけを表示する。
- Data Libraryは登録、Profile検出、検証結果、対応Task、利用Project、archive状態を表示する。

## 既存データの移行

SQLite migrationは追加的に行い、既存Project、候補、スナップショット、判断を削除・再seedしない。

1. 起動時に読み込めた既存source/profileをDataAsset、Profile Revision、Dataset Revisionとして登録する。
2. source単位のsingle Dataset Viewを作る。
3. 既存Projectへ、そのtaskのupgrade時runtimeが使用しているDataset Viewとactive Packageを固定する。過去時点のbindingは現DBだけから復元できないため、`assumed_current_at_upgrade` と明記し、当時のものだったと偽装しない。
4. 既存prediction snapshotは変更せず、既存来歴を読み取れるままにする。
5. 参照中のDataset Revision、View、Seriesは物理削除せずarchiveする。

## 実装順

1. 永続identityとmigration、Data Library read API
2. ProjectへのDataset View／Model Package固定とSeries
3. Project単位のdata/runtime解決
4. Package来歴検証と推論互換性検証の分離
5. 目的変数別Training CohortとModel support
6. Data Library／Project作成／Series UI
7. Developer向けProfile Workbench

## 対象外

- アプリ内モデル学習
- 任意コードを含むProfileやモデルプラグイン
- 一般利用者向けの汎用join builder
- 既存Projectのデータやモデルを自動更新すること
- Datasetの境界を失う暗黙のpooling
