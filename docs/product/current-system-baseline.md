<!--
document-status: current
verified-commit: a200415e5fdf8789011052f0a3a8139324304bce
owner: architecture
source-of-truth: implemented v1 boundary and capability status
-->

<!-- current-contract:csv-onboarding:standard-builder-build-verify-promote -->

# 現行システム基準

この文書は、リポジトリの**現在の実装前提、再利用可能な境界、v1固有の制約**を一枚で確認するための基準である。

- プロダクトの長期的な性格と対象外は [アプリ憲章](app-charter.md) を正本とする。
- 個別契約の詳細は各契約文書とコード上の型を正本とする。
- 過去の設計判断は `docs/decisions/` に残す。ADRの背景説明を現在の機能一覧として読まない。
- Task、source、Profile、active Model Packageの現在値は [生成済みTask inventory](../contracts/task-inventory.json) を正本とする。
- Taskごとのsource shape、authoring、Package、runtime、Graph、候補provenanceの横断表示は [生成済みCapability Atlas](../contracts/capability-atlas.json) を正本とする。これはpersonal WorkspaceのModel Libraryを列挙しない。

## 状態の読み方

この文書では、次の三つを混ぜない。

| 状態 | 意味 |
| --- | --- |
| **実装済み** | 現在のproduction contractまたは画面／APIから利用できる。正確なTask、Package、capabilityの一覧はgenerated inventoryを優先する |
| **実験中** | contractやspikeで実証済みでも、production UIの一般的な作成・編集・実行導線にはまだ採用していない |
| **将来候補** | 必要性や境界は分かっているが、current capabilityではない |

## 1. 現在のプロダクト境界

Evidence Decision Workbenchは、研究開発者がローカルWindows PCで利用する
判断根拠ワークベンチである。
製品核はdomain-neutralであり、最初のdomainと同梱Taskは材料・製造である。

現在の境界は次の三層である。

```text
Workbench Core
  Project、Dataset／Profile、Task、Package、Candidate、
  Prediction／Support、Design Space／Objective、Run、Snapshot、Actual

Domain capability
  composition、heat program、sparse blend、材料lineage、
  Welding Chain等の型付き・allow-list済み能力

Task／Example
  焼鈍、熱延、溶接、工具摩耗、電池、工程異常等の具体的な縦スライス
```

利用者向け名称と保存identityの方針は
[domain-neutralな製品境界](../decisions/domain-neutral-product-boundary.md)を正本とする。

Python namespace、npm package、sidecar、operator環境変数の現行identityは
[内部コード identityの移行](../decisions/internal-code-identity-migration.md)を正本とする。

## 内部コードとoperator identity

アプリ本体のPython namespaceは`decision_workbench`である。

root packageは`evidence-decision-workbench`であり、WebとDesktopのworkspace packageは`@evidence-decision-workbench/*`である。

sidecarのprogram名は`decision-workbench-sidecar`である。

Model Package overrideには`DECISION_WORKBENCH_*`を使う。

旧`MATERIAL_WORKBENCH_*`を検出した起動は、置換先を示して停止する。

Electron appId、user-data path、SQLite、localStorage、`.mdwb`、Task、Profile、Dataset、Package IDとdigestはこのrenameで変更しない。

現在、Projectの科学的identityは次の明示的なunionである。

```text
single_task
  Dataset View Revision
  Task contract digest
  Model Package manifest digest
  Project Design Space digest（新規Project。legacyは未固定を明示）

chain
  Chain Revision ID
  Chain Revision digest

prediction_graph
  Graph Definition / Revision ID
  Graph Revision digest
```

`single_task` Projectでは一つのPrediction Taskを候補比較、予測、応答曲線、類似実績、Snapshot、実測照合、検討アクティビティへ接続する。

`chain` Projectでは、再利用可能なTaskまたは決定論的transformを順序付きStageとしてbindingし、段別実行、部分再計算、Snapshot、段単体／通し評価、中間実測variant、明示的な不確かさ伝播を扱う。

既存Projectのidentityを新しいデータ、Package、Chain Revisionへ自動更新しない。

## 2. データと保存の現在地

ExcelまたはCSVのsource assetは読取専用の不変入力として扱う。pathはidentityではなくlocatorであり、内容SHA-256、Profile Revision、Dataset Revision、Dataset View Revisionを分離する。

アプリはローカルSQLiteへ、少なくとも次を保存する。

- Projectと検討グループ
- CandidateとCandidate Revision
- Prediction Snapshot
- Screening Run
- Decision Activity Run
- Decision Case / Decision Replay Run
- Actual Measurement
- Chain Definition / Chain Revision
- Chain Execution / Chain Snapshot
- 中間実測を使用したactual-conditioned variant
- Chain uncertainty distribution run
- Stage AのLP／MILP配合逆算から作成した通常Candidateとprovenance

元sourceの値をアプリが直接修正しない。canonical dataset、training view、feature representationはsourceとProfileから派生させる。

## 3. 固く維持する契約

次は柔軟性向上のために緩めない。

- source、Profile、Dataset、Task、Feature Pipeline、Model Packageのversionとdigest
- Candidate Revisionとcompare-and-swap更新
- 保存済みSnapshot／Runのimmutability
- 単位、quantity、basis、Stage bindingの明示
- stale responseとsuperseded workの破棄
- unsafe serialization、任意Python、任意pluginの不許可
- 予測値、実測値、入力ばらつき、モデル不確実性、段間伝播不確かさの区別
- 学習行、目的変数別cohort、split group、除外理由の来歴
- active Packageを既存Projectへ暗黙fallbackしないこと

## 4. 現在再利用できる境界

### Prediction Task

TaskDefinitionはcanonical input、output、単位、許容範囲、制約を定義する。Model PackageやDatasetの具体的locatorをTaskDefinitionへ埋め込まない。

通常の数値・カテゴリ表形式Taskは共通Tabular Profile、runtime、feature builder、Package builderを再利用できる。

### Model Package

Model Packageはdata-onlyであり、allow-list済みruntime adapterだけが読み込む。Task、Feature Pipeline、training provenance、artifact hash、quality report、smoke inputを固定する。

複数出力が同じartifactを参照するI/O契約は将来拡張できる形を維持するが、現行データで再評価したshared multi-output GPは、target別modelより精度、calibration、artifact size、推論時間が悪化したため採用していない。runtime、Package、active設定は追加していない。採否根拠は[複数出力で共有するモデル成果物](../decisions/shared-multi-output.md)を参照する。

### Dataset Profile family

データ形状に応じて複数のProfile familyを許可する。Profile schemaを無理に一つへ統合しない。

現在の主なfamilyは次である。

- Workbookのentity／relationを扱うDataset Input Profile
- 独立した表形式を扱うTabular Dataset Profile
- 複数観測familyと行固有入力を扱うObservation Dataset Profile
- Stage B学習用のWorkbook Profile

各familyは、学習行、target eligibility、split group、provenance、quality findingを失わない派生表へ変換する。

### Source data lifecycle

外部sourceの定期更新は、Connector、Raw Snapshot、Curation Run、承認済みCanonical Dataset Revision、Training Snapshotを不変資産として分離する。取得は再学習・Package active化・既存Project更新を起動しない。詳細は[Source更新と承認付きDataset lifecycle](../contracts/source-data-lifecycle.md)を参照する。

### CSV / 単一表XLSX onboarding

**実装済み**：Data Libraryは、利用者が確認した一行一観測のCSVまたは単一表XLSXから標準Tabular Taskを準備できる。
XLSXはvisible sheetを明示選択し、stored valueの型を保持する。formula、merged cell、
hidden sheetの選択、非矩形表はfail-closedとし、Profile Workbenchへ案内する。
下流ではcanonical CSV snapshotを使い、元XLSXのdigest、sheet、reader policyをDataset provenanceへ固定する。
画面経路はTask scaffold、allow-list済みstandard builderのbuild／verify、明示的promotion、
Dataset登録、runtime再読込を行う。任意の学習コード・任意estimator・自動active切替ではない。
外部training、標準builder、runtime inference、active Package切替の境界は
[アプリ憲章](app-charter.md#csv-onboardingの標準builder境界)を正本とする。

### Decision Activity

Activityは画面名ではなく、問い、必要能力、入力parameter、結果契約を表す。現在のproduction registryには
ロバストネス／公差解析、候補差分の要因分解、目標へ届く最小変更が登録されている。

### Decision Replay

Decision Caseは判断時点までのCandidate RevisionとPrediction Snapshotを固定する。後着ActualはCase本体を変更せず追加専用attachmentとして別レイヤーへ結ぶ。Replayは当時のCandidate集合だけへ固定policyを再適用し、同じTask contract、Objective、target集合を持つ後発Project/Packageを明示選択してhindsight再評価する。Case、attachment、Runはいずれも追加専用であり、既存のCandidate、Snapshot、Actual、Decision Activity identityを変更しない。詳細は[Decision Replay](../contracts/decision-replay.md)を参照する。

### Chain Definition

ChainDefinitionはStage順序、external input、Stage間binding、明示的な単位変換を表す。Task自身へChain固有のbindingを埋め込まない。

### Package-first application boundary

Chainの計画・段実行・全体実行・snapshotは`application/chain/`に、Workspace backup／restoreの
phaseは`application/workspace_bundle/`に置く。前者の`__init__.py`はre-exportしない境界印であり、
後者の`__init__.py`だけが公開use-caseをまとめる薄いfacadeである。旧flat moduleを互換shimとして
残さないため、内部実装者はphaseまたはuse-caseの責任名から入口を辿る。

## 5. 現在v1固有の境界

以下は現時点で完全な汎用基盤ではない。別ユースケースへ適用する際は、既存名だけを見て再利用可能と判断しない。

### Task入力shape

通常Taskの`canonical-candidate/v1` input groupは主に `composition`、`process`、
`categorical`、`heat_pattern` である。
これは現在のscalar／材料互換Candidate familyであり、Workbench Coreの万能shapeではない。
非材料Taskは不要なgroupを空またはnullで保持する。
画像、スペクトル、グラフ、複数明細集合はproduction契約に含まれない。

一般的な可変長系列は、Candidate入力とは独立したRaw Series／Canonical Series／Feature Representation契約、永続化、API、inspectorを持つ。通常Taskへ自動bindingはせず、Taskごとの縦スライスで明示する。詳細は[可変長系列の契約](../contracts/variable-length-series.md)を参照する。

疎な原料配合は通常のscalar inputへ押し込まず、別のSparse Blend契約として実装している。

### Task integration

Task追加は内部allow-listである `TaskModule` への明示登録を必要とする。標準Tabular Taskでは共通関数を再利用できるが、特殊データや特殊runtimeは縦スライス実装を必要とする。

これは任意pluginを避けるための意図的な境界である。一方、中央registryへTask固有処理が集中しすぎないかは継続して確認する。

### Project Design Space

新しい単一Task Projectは、TaskDefinitionを狭める不変なDesign Space Revisionを固定する。
範囲探索とロバストネス解析は同じdigestを来歴へ残す。既存Projectは履歴を推測せず、
`unbound_legacy`として読み出す。詳細は[Project Design Space](../contracts/project-design-space.md)を参照する。

### Objective Definition

Objective Definitionは、どのoutputをどの方向・目標・許容範囲で評価するか、制約を満たさない候補をどう扱うか、改善基準となるincumbentを何に固定するかをversionとdigest付きで定義する。Project Design Space、Proposal Strategy、Prediction Taskとは別の不変な判断基準である。詳細は[Objective Definition](../contracts/objective-definition.md)を参照する。

### Proposal StrategyとBatch Selector

範囲探索は、利用目的を`design_space_map`／`goal_search`／`experiment_batch`として固定し、allow-listされたCandidate Generator、Acquisition Evaluator、SelectorをProposal Strategyとして解決する。領域表示はProject Objectiveを適用せず、実験batchは保存済みgoal-search Runのpoolを再生成せずに参照する。保存済みRunは、Design Space／Objective／predictive Package／Feature Pipeline／Datasetのdigest、実際に使ったstrategy、seed、評価pool、棄却理由、獲得値の内訳を固定する。`design-prior-package/v1`を明示選択したRunは、予測Packageとは別にそのmanifest digest、generator、novelty lane、各標本の経験分布evidenceを固定する。これは`p(x)`だけを表し、hard feasibilityやpredictive supportを代替しない。

**実験中**：Generative Design Labは固定synthetic fixtureでLHS、Sobol、empirical、
kNN、Gaussian rank copulaと保守的batch選抜を比較し、tiny VAEを`no_adopt`として
記録する。これはoffline評価証拠であり、production Proposal registry、Package、
Project、保存済みRunを変更しない。

任意の実験batchを作る場合は、点ごとのProposal Strategyとは別のBatch Selectorが、取得価値、多様性、pending候補、対照・反復、カテゴリquota、実験費用、setup制約を扱う。現行実装はmarginal acquisition後の決定論的batch選択であり、joint q-acquisitionやbatch Thompson Samplingを実装済みとはみなさない。詳細は[Curation and Proposal architecture](../contracts/curation-and-proposal-architecture.md)を参照する。

### Decision Activity

request／resultは`schema_version`判別unionであり、現在はロバストネス解析、候補差分説明、
Project Design SpaceとObjectiveに固定した目標到達案を登録している。
共通serviceとUI shellはActivity IDやTask IDで分岐せず、allow-listされたhandler／view registryから解決する。

### Chain execution

Chain Coreは候補shapeを解釈せず、Stage順序、binding、単位変換、部分再計算、鮮度、provenance、snapshotを扱う。候補shape、初期値、妥当性検証、決定論的Stage、追加revision参照はallow-listされたcandidate adapterへ分離している。

最初の縦切りである溶接材料A→B→Cは`sparse_blend/v1` adapterと専用Workbenchを持つ。これとは別に、疎配合も決定論的Stageも持たないscalar候補の二段Chainを通し、Chain Coreを変更せずDefinition、binding、execution、snapshotを再利用できることを確認した。この二段Chainは**実験中**の反証であり、一般利用者向けのscalar editorが実装済みという意味ではない。

一方、現在の画面は疎配合Chain専用であり、scalar候補の編集画面はない。また、決定論的Stageを二段以上持つ候補shape、画像・系列などの非scalar外部入力、domain固有の実験資源はadapter追加なしには扱わない。

詳細は[Chain実行と証跡](../contracts/chain-execution.md)と[拡張性反証結果](../architecture/extensibility-spikes.md)を参照する。

### Chain uncertainty

明示実行の固定seed Monte Carloとして実装している。Stage B／Cの残差区間から独立な正規近似を構成しており、posteriorでもoutput相関モデルでもない。点推定の自動実行とは別Runとして保存する。

### Blend optimization

Stage Aの固定科学変換境界に限り、目標材料成分から配合へのLP／MILP逆算を扱う。これは一般的な特性逆問題、Bayesian optimization、自動最良候補選択ではない。

## 6. 実装と文書のauthority map

| 関心 | 正本 |
|---|---|
| プロダクトの性格、対象外 | `docs/product/app-charter.md` |
| 現在の実装前提とv1境界 | この文書 |
| Python／npm／sidecar／operator identity | `docs/decisions/internal-code-identity-migration.md` |
| Task、source、Profile、active Packageの一覧 | `docs/contracts/task-inventory.json` |
| Task／Canonical Candidate／Runtime Capability | `backend/src/decision_workbench/contracts/task_contracts.py` |
| Chain Definition／Revision／binding | `backend/src/decision_workbench/contracts/chain_contracts.py` |
| Chain execution／snapshot／actual variant | `backend/src/decision_workbench/contracts/chain_execution_contracts.py` |
| Chainのplan／stage／execution／snapshot use case | `backend/src/decision_workbench/application/chain/{plan,stage_execution,execution,snapshot}.py` |
| Workspace backup／restoreの公開入口とphase | `backend/src/decision_workbench/application/workspace_bundle/` と `docs/architecture/persistence-transaction-boundaries.md` |
| 複数aggregate commandのSQLite transaction | `backend/src/decision_workbench/persistence/store_unit_of_work.py` |
| Source lifecycle revisionの永続化 | `backend/src/decision_workbench/persistence/data_lifecycle_repository.py` |
| Workbookのrelation解釈とcanonical／lineage生成 | `backend/src/decision_workbench/data/importer.py` |
| Decision Activity | `backend/src/decision_workbench/contracts/decision_activity_contracts.py` |
| Decision Case／Replay Run | `backend/src/decision_workbench/contracts/decision_replay_contracts.py` と `docs/contracts/decision-replay.md` |
| Project Design Space | `backend/src/decision_workbench/contracts/design_space_contracts.py` と `docs/contracts/project-design-space.md` |
| Objective Definition | `backend/src/decision_workbench/contracts/objective_contracts.py` と `docs/contracts/objective-definition.md` |
| Proposal Strategy／Acquisition | `backend/src/decision_workbench/contracts/proposal_contracts.py` と `docs/contracts/curation-and-proposal-architecture.md` |
| Batch Selector | `backend/src/decision_workbench/contracts/batch_proposal_contracts.py` と `docs/contracts/curation-and-proposal-architecture.md` |
| Model Package | `docs/contracts/model-package-contract.md` と対応するcontract code |
| Dataset解釈 | Profile familyごとのschemaと契約文書 |
| OpenAPI／frontend API型 | FastAPI OpenAPIと`apps/web/src/generated/` |
| 過去の採否判断 | `docs/decisions/`、Issue、Pull Request |

文書とコードが食い違う場合は、次のように扱う。

1. generated inventoryまたはcontract codeから現在の事実を確認する。
2. 文書が将来形を現在形として書いていないか確認する。
3. 実装がADRの安全原則を破っている場合は、文書を実装へ合わせず問題として扱う。
4. 実装済み機能が将来候補のままなら、入口文書を更新する。

## 7. 変更時に確認する差分

前提に関わる変更では、コード差分だけでなく次を確認する。

- Project scientific identityが変わるか
- Candidate shapeが増えるか
- Profile familyが増えるか
- TaskModuleの登録点が増えるか
- Runtime CapabilityまたはApplication Capabilityが増えるか
- 新しいimmutable Run／Snapshotが増えるか
- OpenAPIとgenerated frontend型が追従したか
- Task inventoryが再生成され、dirtyにならないか
- `app-charter.md`、この文書、`developer-start-here.md`の前提が変わるか
- 歴史的ADRの状態欄が現在の追跡先を誤って示していないか

## 8. 汎用性検証の現在地

大規模な共通化を先に行わず、異質な実例で変更範囲を測る。完了した反証は次の通りである。

| 反証ケース | 結果 |
|---|---|
| 新しい通常CSV回帰Task | 共通Tabular Profile／runtime／Package builderを再利用できた |
| 溶接以外の複数観測family Dataset | family固有の学習行構成を保ちつつ、共通Task／Package境界へ接続できた |
| 疎配合と決定論的Stageを持たない二段Chain | candidate adapter分離後、Chain Coreの変更なしで実行できた |
| ロバストネス以外のDecision Activity | 候補差分説明と目標到達案を型付きregistryへ追加できた |
| 可変長系列の保存・変換・閲覧 | Raw／Canonical／Featureを独立assetとして固定できた |

まだ将来候補であり、現在の実装と混同しない反証ケースは次である。

1. 可変長系列assetをCandidate、Task、Model Packageへ実際にbindingする縦スライス
2. 画像、スペクトル、グラフ、複数明細集合を入力にするTask
3. 決定論的Stageを二段以上持つChainと、その候補編集画面
4. joint posterior sampleを必要とするq-acquisitionまたはbatch Thompson Sampling
5. 承認済みTraining Snapshotからの明示的な再学習・Package昇格workflow

新しいケースでは、既存ファイル変更数、新しいcontract、API／UI分岐、registry追加点を記録する。二つ目の実例を通す前に「汎用基盤完成」と判断しない。
