# 現行システム基準

この文書は、リポジトリの**現在の実装前提、再利用可能な境界、v1固有の制約**を一枚で確認するための基準である。

- プロダクトの長期的な性格と対象外は [アプリ憲章](app-charter.md) を正本とする。
- 個別契約の詳細は各契約文書とコード上の型を正本とする。
- 過去の設計判断は `docs/decisions/` に残す。ADRの背景説明を現在の機能一覧として読まない。
- Task、source、Profile、active Model Packageの現在値は [生成済みTask inventory](task-inventory.json) を正本とする。

## 1. 現在のプロダクト境界

Material Decision Workbenchは、材料研究者がローカルWindows PCで利用する意思決定支援アプリである。

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

複数出力が同じartifactを参照するI/O契約は将来拡張できる形を維持するが、現行データで再評価したshared multi-output GPは、target別modelより精度、calibration、artifact size、推論時間が悪化したため採用していない。runtime、Package、active設定は追加していない。採否根拠は[複数出力で共有するモデル成果物](decisions/shared-multi-output.md)を参照する。

### Dataset Profile family

データ形状に応じて複数のProfile familyを許可する。Profile schemaを無理に一つへ統合しない。

現在の主なfamilyは次である。

- Workbookのentity／relationを扱うDataset Input Profile
- 独立した表形式を扱うTabular Dataset Profile
- 複数観測familyと行固有入力を扱うObservation Dataset Profile
- Stage B学習用のWorkbook Profile

各familyは、学習行、target eligibility、split group、provenance、quality findingを失わない派生表へ変換する。

### Source data lifecycle

外部sourceの定期更新は、Connector、Raw Snapshot、Curation Run、承認済みCanonical Dataset Revision、Training Snapshotを不変資産として分離する。取得は再学習・Package active化・既存Project更新を起動しない。詳細は[Source更新と承認付きDataset lifecycle](source-data-lifecycle.md)を参照する。

### Decision Activity

Activityは画面名ではなく、問い、必要能力、入力parameter、結果契約を表す。現在のproduction registryには
ロバストネス／公差解析、候補差分の要因分解、目標へ届く最小変更が登録されている。

### Chain Definition

ChainDefinitionはStage順序、external input、Stage間binding、明示的な単位変換を表す。Task自身へChain固有のbindingを埋め込まない。

## 5. 現在v1固有の境界

以下は現時点で完全な汎用基盤ではない。別ユースケースへ適用する際は、既存名だけを見て再利用可能と判断しない。

### Task入力shape

通常Taskのcanonical input groupは主に `composition`、`process`、`categorical`、`heat_pattern` である。画像、スペクトル、グラフ、複数明細集合はproduction契約に含まれない。

一般的な可変長系列は、Candidate入力とは独立したRaw Series／Canonical Series／Feature Representation契約、永続化、API、inspectorを持つ。通常Taskへ自動bindingはせず、Taskごとの縦スライスで明示する。詳細は[可変長系列の契約](variable-length-series.md)を参照する。

疎な原料配合は通常のscalar inputへ押し込まず、別のSparse Blend契約として実装している。

### Task integration

Task追加は内部allow-listである `TaskModule` への明示登録を必要とする。標準Tabular Taskでは共通関数を再利用できるが、特殊データや特殊runtimeは縦スライス実装を必要とする。

これは任意pluginを避けるための意図的な境界である。一方、中央registryへTask固有処理が集中しすぎないかは継続して確認する。

### Project Design Space

新しい単一Task Projectは、TaskDefinitionを狭める不変なDesign Space Revisionを固定する。
範囲探索とロバストネス解析は同じdigestを来歴へ残す。既存Projectは履歴を推測せず、
`unbound_legacy`として読み出す。詳細は[Project Design Space](project-design-space.md)を参照する。

### Objective Definition

Objective Definitionは、どのoutputをどの方向・目標・許容範囲で評価するか、制約を満たさない候補をどう扱うか、改善基準となるincumbentを何に固定するかをversionとdigest付きで定義する。Project Design Space、Proposal Strategy、Prediction Taskとは別の不変な判断基準である。詳細は[Objective Definition](objective-definition.md)を参照する。

### Proposal StrategyとBatch Selector

範囲探索は、利用目的を`design_space_map`／`goal_search`／`experiment_batch`として固定し、allow-listされたCandidate Generator、Acquisition Evaluator、SelectorをProposal Strategyとして解決する。領域表示はProject Objectiveを適用せず、実験batchは保存済みgoal-search Runのpoolを再生成せずに参照する。保存済みRunは、Design Space／Objective／Package／Feature Pipeline／Datasetのdigest、実際に使ったstrategy、seed、評価pool、棄却理由、獲得値の内訳を固定する。

任意の実験batchを作る場合は、点ごとのProposal Strategyとは別のBatch Selectorが、取得価値、多様性、pending候補、対照・反復、カテゴリquota、実験費用、setup制約を扱う。現行実装はmarginal acquisition後の決定論的batch選択であり、joint q-acquisitionやbatch Thompson Samplingを実装済みとはみなさない。詳細は[Curation and Proposal architecture](curation-and-proposal-architecture.md)を参照する。

### Decision Activity

request／resultは`schema_version`判別unionであり、現在はロバストネス解析、候補差分説明、
Project Design SpaceとObjectiveに固定した目標到達案を登録している。
共通serviceとUI shellはActivity IDやTask IDで分岐せず、allow-listされたhandler／view registryから解決する。

### Chain execution

Chain Coreは候補shapeを解釈せず、Stage順序、binding、単位変換、部分再計算、鮮度、provenance、snapshotを扱う。候補shape、初期値、妥当性検証、決定論的Stage、追加revision参照はallow-listされたcandidate adapterへ分離している。

最初の縦切りである溶接材料A→B→Cは`sparse_blend/v1` adapterと専用Workbenchを持つ。これとは別に、疎配合も決定論的Stageも持たないscalar候補の二段Chainを通し、Chain Coreを変更せずDefinition、binding、execution、snapshotを再利用できることを確認した。

一方、現在の画面は疎配合Chain専用であり、scalar候補の編集画面はない。また、決定論的Stageを二段以上持つ候補shape、画像・系列などの非scalar外部入力、domain固有の実験資源はadapter追加なしには扱わない。

詳細は[Chain実行と証跡](chain-execution.md)と[拡張性反証結果](architecture/extensibility-spikes.md)を参照する。

### Chain uncertainty

明示実行の固定seed Monte Carloとして実装している。Stage B／Cの残差区間から独立な正規近似を構成しており、posteriorでもoutput相関モデルでもない。点推定の自動実行とは別Runとして保存する。

### Blend optimization

Stage Aの固定科学変換境界に限り、目標材料成分から配合へのLP／MILP逆算を扱う。これは一般的な特性逆問題、Bayesian optimization、自動最良候補選択ではない。

## 6. 実装と文書のauthority map

| 関心 | 正本 |
|---|---|
| プロダクトの性格、対象外 | `docs/app-charter.md` |
| 現在の実装前提とv1境界 | この文書 |
| Task、source、Profile、active Packageの一覧 | `docs/task-inventory.json` |
| Task／Canonical Candidate／Runtime Capability | `backend/src/material_workbench/contracts/task_contracts.py` |
| Chain Definition／Revision／binding | `backend/src/material_workbench/contracts/chain_contracts.py` |
| Chain execution／snapshot／actual variant | `backend/src/material_workbench/contracts/chain_execution_contracts.py` |
| Decision Activity | `backend/src/material_workbench/contracts/decision_activity_contracts.py` |
| Project Design Space | `backend/src/material_workbench/contracts/design_space_contracts.py` と `docs/project-design-space.md` |
| Objective Definition | `backend/src/material_workbench/contracts/objective_contracts.py` と `docs/objective-definition.md` |
| Proposal Strategy／Acquisition | `backend/src/material_workbench/contracts/proposal_contracts.py` と `docs/curation-and-proposal-architecture.md` |
| Batch Selector | `backend/src/material_workbench/contracts/batch_proposal_contracts.py` と `docs/curation-and-proposal-architecture.md` |
| Model Package | `docs/model-package-contract.md` と対応するcontract code |
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
