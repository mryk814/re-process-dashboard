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

### Dataset Profile family

データ形状に応じて複数のProfile familyを許可する。Profile schemaを無理に一つへ統合しない。

現在の主なfamilyは次である。

- Workbookのentity／relationを扱うDataset Input Profile
- 独立した表形式を扱うTabular Dataset Profile
- 複数観測familyと行固有入力を扱うObservation Dataset Profile
- Stage B学習用のWorkbook Profile

各familyは、学習行、target eligibility、split group、provenance、quality findingを失わない派生表へ変換する。

### Decision Activity

Activityは画面名ではなく、問い、必要能力、入力parameter、結果契約を表す。現在のproduction registryにはロバストネス／公差解析が登録されている。

### Chain Definition

ChainDefinitionはStage順序、external input、Stage間binding、明示的な単位変換を表す。Task自身へChain固有のbindingを埋め込まない。

## 5. 現在v1固有の境界

以下は現時点で完全な汎用基盤ではない。別ユースケースへ適用する際は、既存名だけを見て再利用可能と判断しない。

### Task入力shape

通常Taskのcanonical input groupは主に `composition`、`process`、`categorical`、`heat_pattern` である。画像、スペクトル、グラフ、一般的な可変長系列、複数明細集合はproduction契約に含まれない。

疎な原料配合は通常のscalar inputへ押し込まず、別のSparse Blend契約として実装している。

### Task integration

Task追加は内部allow-listである `TaskModule` への明示登録を必要とする。標準Tabular Taskでは共通関数を再利用できるが、特殊データや特殊runtimeは縦スライス実装を必要とする。

これは任意pluginを避けるための意図的な境界である。一方、中央registryへTask固有処理が集中しすぎないかは継続して確認する。

### Project Design Space

新しい単一Task Projectは、TaskDefinitionを狭める不変なDesign Space Revisionを固定する。
範囲探索とロバストネス解析は同じdigestを来歴へ残す。既存Projectは履歴を推測せず、
`unbound_legacy`として読み出す。詳細は[Project Design Space](project-design-space.md)を参照する。

### Decision Activity

request／resultは`schema_version`判別unionであり、現在はロバストネス解析と候補差分説明を登録している。
共通serviceとUI shellはActivity IDやTask IDで分岐せず、allow-listされたhandler／view registryから解決する。

### Chain execution

Chain contract自体はStageとbindingを一般化しているが、現在のcandidate preparationとexecution UXは溶接材料A→B→Cを最初の縦切りとしている。

現在のv1前提には次が含まれる。

- 疎なblendを持つcandidate
- 一つの決定論的Stage A
- welding context／test contextのexternal input展開
- Stage A科学master、商用catalog、Design Spaceの固定

疎配合を使わない二段Chainなどを追加するときは、Chain Coreを変更する前に、溶接固有candidate adapterとの分離可否を検証する。

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

## 8. 次の汎用性検証

大規模な共通化を先に行わず、次の反証ケースで変更範囲を測る。

1. 新しい通常CSV回帰Task
2. 溶接以外の複数観測family Dataset
3. 可変長温度系列を持つTask
4. 疎配合と決定論的Stageを持たない二段Chain
5. ロバストネス以外のDecision Activity

各ケースで、既存ファイル変更数、新しいcontract、API／UI分岐、registry追加点を記録する。二つ目の実例を通す前に「汎用基盤完成」と判断しない。
