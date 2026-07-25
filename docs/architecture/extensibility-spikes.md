# 拡張性の反証テスト設計

[extensibility-inventory.md](extensibility-inventory.md) で測定した登録点をもとに、
「現在の共通基盤が別のユースケースでも再利用できるか」を反証するケースを設計します。

この文書の役割は2つです。

1. 4つの反証ケースの**設計と受入条件**を固定する（実装前に決める）
2. 各ケースの**変更点マトリクス（予測）**を残し、スパイク実行後に実測値と差分を突き合わせる

予測を先に書く理由は、スパイク後に「やっぱり必要だった」と後付けで正当化しないためです。
実測欄が空のケースは、まだ反証されていません。

## 0. 共通ルール

- スパイクはfixtureまたは設計スパイクで行い、**本番の契約・保存形式を変更しない**
- 元データ（`data/source/`）を変更しない
- 「動いた」ではなく「既存機能をTask固有実装なしで再利用できたか」を記録する
- 途中で新しい型が必要になった場合、**なぜ既存型で表現できないか**を1文で書く
- 計測は下記8指標で統一する

| 指標 | 定義 |
| --- | --- |
| 既存ファイル変更数 | 既にあるファイルへの編集件数 |
| 新規ファイル数 | 追加したファイル件数（data-only含む） |
| 新規contract数 | 新しいpydanticモデル／JSON schema版の件数 |
| 新規API分岐数 | 既存endpointに増えた条件分岐の件数 |
| 新規UI分岐数 | 既存componentに増えた条件分岐の件数 |
| registry追加点 | `TASK_MODULES` 等の登録entry件数 |
| generated schema更新 | `api-types.ts` / `task-inventory.json` の更新有無 |
| 専用test fixture数 | そのケース専用に作ったfixture件数 |

## 1. ケースA：通常の表形式Task

### 目的

「同じ意味の別データ」ではなく「新しい標準表形式の予測問題」を、既存アプリ機能へTask固有実装なしで載せられるかを確認する。

### 題材（fixture）

| 項目 | 内容 |
| --- | --- |
| 形式 | CSV 1枚 |
| 入力 | 数値5列（`process.*`）＋ カテゴリ2列（`categorical.*`） |
| 出力 | 数値2列（うち1列は一部行でNaN＝欠損target） |
| 行数 | 300前後 |
| group | なし（1行=1条件） |

欠損targetとカテゴリ入力を必ず含めます。両方とも現行Tabular Profileの分岐に触るためです。

### 確認事項

- [ ] 既存 `TabularDatasetProfile` で表現できるか（`inputs` / `outputs` / `curation` / `quality_rules`）
- [ ] `TaskModule` の `data_loader` / `feature_row_builder` / `model_builder` / `starter_project` を**新規関数を書かずに** `_tabular_*(task_id)` factoryで賄えるか
- [ ] `task_modules.py` 以外の既存コード変更がゼロで済むか
- [ ] Workbench（プレビュー・詳細予測）、ロバストネス解析、範囲探索（screening）がそのまま使えるか
- [ ] 欠損targetが「学習除外」として扱われ、UIで予測値と実測値が混ざらないか
- [ ] `data_explorer` に `_TABULAR_EXPLORER` を付けたとき、`quality` 表示が成立するか

### 反証されたら何を意味するか

`task_modules.py` 以外に変更が出た場合、その差分が**Phase 2.3（Task integration pointの分割）の実需**です。
差分がゼロなら、標準表形式Taskのdata-only登録経路は既に成立しており、P2の優先度は下がります。

### 変更点マトリクス

| 指標 | 予測 | 実測 | 差分の理由 |
| --- | --- | --- | --- |
| 既存ファイル変更数 | 2（`task_modules.py`, `models/active-packages.json`） | | |
| 新規ファイル数 | 4（task definition JSON, tabular profile JSON, source CSV, package） | | |
| 新規contract数 | 0 | | |
| 新規API分岐数 | 0 | | |
| 新規UI分岐数 | 0 | | |
| registry追加点 | 2（`TASK_MODULES`, `_TABULAR_PROFILES`） | | |
| generated schema更新 | `task-inventory.json` のみ | | |
| 専用test fixture数 | 1 | | |

**予測の根拠**: [inventory §1.1](extensibility-inventory.md#11-task) の登録点表と、`_tabular_loader` / `_tabular_features` / `_tabular_builder` / `_tabular_starter` が `task_id` でパラメタ化済みであること。

**予測が外れる可能性が高い箇所**: 欠損targetの扱い（`observations[*]["eligible"]` の判定がProfile側で表現できるか）と、`_tabular_starter` が `model_family == "lightgbm_binary"` を直接見ている点（[task_modules.py:457](../../backend/src/material_workbench/task_modules.py#L457)）。

## 2. ケースB：複数sheet・複数観測family

### 目的

Observation Profileが溶接以外の分野でも再利用できるか、family名や溶接用語が契約へ漏れていないかを確認する。

### 題材（fixture）

工程条件シート1枚 ＋ 試験結果シート2枚の構成。溶接とは無関係な語彙を使います。

| sheet | 役割 |
| --- | --- |
| `conditions` | 工程条件（entity。1行=1条件） |
| `test_alpha` | 試験A結果（条件に対し複数行。試験行固有入力を1列持つ） |
| `test_beta` | 試験B結果（試験Aと異なるtarget。cohortが別） |

target別cohort（`test_alpha` にしか値がないtargetがある）を必ず含めます。

### 確認事項

- [ ] `ObservationDatasetProfile` の `families` / `canonical_inputs` / `canonical_outputs` / `fixed_context` を、溶接語彙なしで書けるか
- [ ] family名（`ObservationFamily.name`）が表示・集計に使われるだけで、**分岐条件になっていない**か
- [ ] 「試験行固有入力」を canonical input として宣言できるか（工程条件由来の入力と区別できるか）
- [ ] `ObservationTrainingDataset` 以降（学習・品質集計・Training Data Inspector）をProfile形式判定なしで通せるか
- [ ] `StageCData.canonical_training_dataset()` に相当する経路が溶接固有symbolを含まないか
- [ ] target別cohortが `TargetTrainingSummary` / `FamilyTrainingSummary` で正しく分離表示されるか

### 反証されたら何を意味するか

Observation Profileが再利用できるなら、**Phase 2.1のCanonical Training View境界はObservation familyの契約を昇格させれば足ります**（新設不要）。
再利用できない場合、漏れている溶接前提の一覧がPhase 2.1の作業範囲そのものになります。

### 変更点マトリクス

| 指標 | 予測 | 実測 | 差分の理由 |
| --- | --- | --- | --- |
| 既存ファイル変更数 | 1（`task_modules.py`） | | |
| 新規ファイル数 | 4（observation profile JSON, task definition JSON, source xlsx, package） | | |
| 新規contract数 | 0 | | |
| 新規API分岐数 | 0 | | |
| 新規UI分岐数 | 0 | | |
| registry追加点 | 1 | | |
| generated schema更新 | `task-inventory.json` のみ | | |
| 専用test fixture数 | 1 | | |

**予測が外れる可能性が高い箇所**: `_load_welding_stage_c` が `ObservationDatasetProfile` を受けるloaderを溶接向けにしか持たない点（[task_modules.py:219](../../backend/src/material_workbench/task_modules.py#L219)）。新しいObservation Taskには**新しいloader関数が必要**になる見込みで、これはケースAとの明確な差です。

## 3. ケースC：可変長温度系列Task

### 目的

Raw series / Canonical series / Feature representation の三層を分離できるかを確認する。
現行の `heat_pattern`（最大30点・時刻昇順・単一形状）で足りるかを反証する。

### 題材（fixture）

| 項目 | 内容 |
| --- | --- |
| 入力 | candidateごとに長さの違う時間―温度系列（10〜400点） |
| 単位 | 一部の行が `°F` / `min`（変換が必要） |
| 品質 | timestamp重複、逆行、欠測区間を含む |
| 出力 | 系列から抽出した工程特徴量に依存する数値2列 |

### 確認事項

- [ ] `TaskDefinition` を変更せずに宣言できるか（[task_contracts.py:40](../../backend/src/material_workbench/contracts/task_contracts.py#L40) の path正規表現、`:83` のgroup key Literal）
- [ ] `CanonicalCandidate` を変更せずに保存できるか（[task_contracts.py:357](../../backend/src/material_workbench/contracts/task_contracts.py#L357)）
- [ ] `CandidateInputs.heat_pattern` の30点上限（[schemas.py:247](../../backend/src/material_workbench/contracts/schemas.py#L247)）で足りるか
- [ ] 単位変換をProfileで**明示**でき、暗黙変換にならないか
- [ ] timestamp重複を「補完せず不適格にする」判定がProfileで表現できるか
- [ ] 系列そのもののdiff / copy / snapshot意味をUIで定義できるか

### 予測される結論

**現行契約では表現できません。** 理由:

1. `heat_pattern` は `(time_s, temperature_c)` 固定で、単位・欠測・品質状態を持てない
2. path正規表現とgroup key Literalが、`heat_pattern` 以外の系列groupを許さない
3. `CanonicalCandidate` に系列の正規化provenance（元単位、変換ID、除外点）を置く場所がない

したがってこのケースの成果物は「動くスパイク」ではなく、**本当に必要な新しい型の最小集合**の特定です。

### 必要になる新型の候補（スパイクで確定させる）

| 候補型 | 役割 | 既存型で表現できない理由 |
| --- | --- | --- |
| `SeriesCandidateInputs` | 候補入力としての系列参照 | `CanonicalCandidate` に可変長系列の入る場所がない |
| `CanonicalSeries` | 単位・時間基準・除外点を持つ正規化系列 | `HeatPoint` は2値のみで正規化履歴を持てない |
| `SeriesQualityFinding` | 重複・逆行・欠測の不適格判定 | 現行quality findingは行単位で系列内位置を持てない |
| `SeriesFeatureSpec` | 系列→特徴量の宣言 | `FeatureDefinition` はスカラー前提 |

**受入条件**: 上表が「4件で足りる」か「もっと必要」かを、fixtureに対して具体的に確認できていること。
本PRの段階では実装しません（計画P3）。

### 変更点マトリクス

| 指標 | 予測 | 実測 | 差分の理由 |
| --- | --- | --- | --- |
| 既存ファイル変更数 | 6以上（task_contracts, schemas, tabular or 新loader, runtime, UI 2件） | | |
| 新規ファイル数 | 5以上 | | |
| 新規contract数 | 4（上表） | | |
| 新規API分岐数 | 1以上（候補入力surface） | | |
| 新規UI分岐数 | 2以上（入力editor, diff表示） | | |
| registry追加点 | 2以上（Task, Candidate Shape） | | |
| generated schema更新 | 両方 | | |
| 専用test fixture数 | 2以上 | | |

## 4. ケースD：疎配合を使わない二段Chain

### 目的

Chain Coreと溶接adapterが分離できているかを反証する。
最も重要なケースです。[inventory §3](extensibility-inventory.md#3-chain-coreに残る溶接固有symbol全列挙) の21件が実際に障害になるかを確認します。

### 題材（fixture）

```text
外部入力（スカラーのみ）
      ↓
Task X（表形式・中間状態を出力）
      ↓
Task Y（中間状態＋外部入力から最終特性を出力）
```

- 決定論的Stageなし
- 疎配合なし
- 外部入力は `candidate.process.*` 相当のスカラーのみ
- Task X / Y はケースAで作る標準表形式Taskを流用

### 確認事項（inventory §3 の番号に対応）

- [ ] `prepare_candidate` を通せるか → #4 で失敗する見込み
- [ ] `_resolve` を通せるか → #5 で失敗する見込み
- [ ] 候補契約API / 初期候補APIが成立するか → #3, #6 で失敗する見込み
- [ ] 外部入力を `welding_context` 以外の名前空間で渡せるか → #1 で失敗する見込み
- [ ] `ChainDefinition` と `binding` は再利用できるか → 成立する見込み
- [ ] `execute()` の本体（binding解決・memo・generation・stale判定）は変更不要か → 成立する見込み
- [ ] `snapshot()` を作れるか → #11, #14 で失敗する見込み
- [ ] 不確かさ伝播を実行できるか → #18 で失敗する見込み
- [ ] `chain_execution.py` の変更行数はどれだけか

### 受入条件（Phase 2.5の完了条件）

**疎配合を使わない二段Chainが、Chain Coreの変更なしで実行できる。**
本PR時点では反証（＝現状では実行できないことの証拠）が成果物です。

### 予測される分離設計

```text
Chain Core
  ChainDefinition / ChainRevision / binding解決 / 単位変換
  stage実行 / memo / generation競合制御 / stale応答拒否
  partial recomputation / freshness / provenance
  snapshot（identityはadapterが提供する追加参照を受け取る）

CandidateShapeAdapter（Chainごとに1つ）
  外部入力の名前空間定義（現在の welding_context / test_context）
  候補の妥当性検証
  初期候補生成
  snapshot identityの追加参照（design_space / commercial_catalog）
  決定論的変換の出力→outputs写像（現在の material_composition / auxiliary_features）

SparseBlendChainAdapter  ← 現在の溶接Chainはこれになる
ScalarChainAdapter       ← ケースDが必要とする最小adapter
```

### 変更点マトリクス

| 指標 | 予測 | 実測 | 差分の理由 |
| --- | --- | --- | --- |
| 既存ファイル変更数 | 5以上（chain_execution, chain_uncertainty, chain_contracts, api/chains, bootstrap） | | |
| 新規ファイル数 | 2以上（adapter, chain定義） | | |
| 新規contract数 | 2以上（adapter境界, snapshot identity分離） | | |
| 新規API分岐数 | 0（目標。分岐ではなくadapter解決にしたい） | | |
| 新規UI分岐数 | 1以上（BlendEditorPanelの出し分け） | | |
| registry追加点 | 1（Chain adapter） | | |
| generated schema更新 | 両方 | | |
| 専用test fixture数 | 2以上 | | |

## 5. スパイク実行順

ケースAを最初に行います。Aの成果物（標準表形式Task 2件）がケースDのStage X / Yになるため、無駄がありません。

```text
A（標準表形式Task）
  ├→ B（Observation family再利用）   独立
  ├→ C（可変長系列。型の特定のみ）    独立
  └→ D（二段Chain。Aの成果を使う）
```

## 6. 証拠にもとづくIssue分割（この文書の時点での案）

インベントリだけで既に確定している事実を根拠に、次のリファクタリングを分割します。
**ケースA〜Dの実測が入る前に着手してよいのはP1-aだけです**（理由: 2件目のActivityを作る前でないと保存済みschemaが混ざる）。

### P1-a｜Decision Activityのparameter/result union化

- 根拠: [inventory §4](extensibility-inventory.md#4-decision-activity追加時の変更点)。2件目のActivity追加で**既存契約の破壊的変更が2件**発生する
- 範囲: `DecisionActivityRunRequest` / `DecisionActivityRun` のdiscriminated union化、registryのcapability宣言化（必要runtime operation / candidate shape / project resource / parameter schema / result schema）、`DecisionActivityPanel.tsx` のactivity_idハードコード解消
- 併せて片付ける: `_with_values` の焼鈍固有処理（`ls_mpm` / `heat_time_basis`）の所在を決める
- 完了条件: 2件目のActivityを、既存Activityのservice / API / UIへTask固有分岐を追加せず登録できる
- 依存: なし（ケースA〜Dの結果に依存しない）

### P1-b｜Chain Coreと溶接adapterの分離

- 根拠: [inventory §3](extensibility-inventory.md#3-chain-coreに残る溶接固有symbol全列挙) の分離対象14件
- 範囲: 上記§4の分離設計。`ChainSnapshotIdentity` から `design_space` / `commercial_catalog` を必須で持たない形へ、`_external_values` の名前空間をadapter提供へ、決定論的Stage 1段必須の解除
- 完了条件: ケースDがChain Coreの変更なしで実行できる
- 依存: **ケースDの実測が前提**

### P1-c｜Canonical Training View境界の明示

- 根拠: [inventory §2](extensibility-inventory.md#2-profile-familyごとの共通出力差分)。loader戻り値の共通契約が `DataDescriptor` の6フィールドしかなく、品質表示が未宣言の3フィールドに依存している
- 範囲: `quality` / `detected_quality` / `technical_columns` を含む境界の明示、`DataExplorerEntry.data` の型注釈の実態一致、Observation familyの `ObservationTrainingDataset` を全familyの共通到達点として昇格できるかの判断
- 完了条件: モデルbuilder・学習データInspector・品質集計がProfile形式を直接判定しない
- 依存: **ケースBの実測が前提**（Observation契約を昇格させるか新設するかが変わる）

### P1-d｜Candidate Shapeの整理方針の確定

- 根拠: [inventory §1.6](extensibility-inventory.md#16-candidate-shape)。共有schemaに `heat_time_basis`（焼鈍固有）と `blend` / `editor_state`（溶接固有）が混在
- 範囲: `ScalarCandidateInputs` / `SparseBlendCandidateInputs` へのunion化方針を決める（実装はしない）。任意JSONにしないこと、shapeごとにpersistence / diff / copy / snapshotの意味を定義すること、UIがshape capabilityからsurfaceを選ぶことを明記
- 完了条件: 方針文書があり、ケースCで必要と判明した型を後から**既存shapeを壊さず**追加できると説明できる
- 依存: ケースC・Dの結果を反映（着手は可能）

### P2｜Task integration registryの分割

- 根拠: ケースAの実測待ち。予測では `task_modules.py` 以外の変更がゼロなので、**実測が予測通りなら優先度は下がる**
- 依存: ケースAの実測が前提

### P3｜系列・Source Connector・Project-level Design Space

- 根拠: ケースCで特定した新型の最小集合、[inventory §1.11](extensibility-inventory.md#111-source-connector)、[§1.8](extensibility-inventory.md#18-design-space--commercial-catalog)
- 実needが出るまで着手しない

## 7. この文書の更新規則

- 実測欄を埋めるときは、予測を書き換えず**差分の理由**を書く
- 反証できなかったケース（＝既存基盤で足りたケース）も必ず記録する。共通化しない判断の根拠になる
- ケースが1つも通らないうちに「汎用基盤完成」と書かない
