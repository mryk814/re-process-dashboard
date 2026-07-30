# 拡張性の反証テスト設計

[extensibility-inventory.md](extensibility-inventory.md) で測定した登録点をもとに、
「現在の共通基盤が別のユースケースでも再利用できるか」を反証するケースを設計します。

この文書の役割は2つです。

1. 4つの反証ケースの**設計と受入条件**を固定する（実装前に決める）
2. 各ケースの**変更点マトリクス（予測）**を残し、スパイク実行後に実測値と差分を突き合わせる

予測を先に書く理由は、スパイク後に「やっぱり必要だった」と後付けで正当化しないためです。

> **履歴と現行構成:** ケースA〜Dの変更点マトリクスと初回実測欄に残る
> `task_modules.py`、`TaskModule.model_builder`、module-level `TASK_MODULES` は、
> #501以前の構成を記録した履歴名です。現在は
> `task_composition/ports.py`、`descriptors.py`、`builtin_tasks.py`、`catalog.py`
> に分割され、標準モデルは `TaskModule.standard_model_authoring` で宣言します。
> 再現スクリプトは現行catalogを一時差し替える方式へ更新済みです。

## 実行状況

5ケースすべて実行済みです。再現手順は [spikes/README.md](../../backend/scripts/experiments/spikes/README.md) を参照してください。

| ケース | 初回実測 | P1実装後 | 一行要約 |
| --- | --- | --- | --- |
| A 標準表形式Task | 成立 | 成立 | 新規Python関数0件で既存アプリ機能を全て利用できた。Package構築の順序制約が2件見つかった |
| B 複数観測family | 半分成立 | **全項目OK** | Profile契約とTraining Viewは最初から再利用できた。runtimeとbuilderはP1-dでパラメタ化 |
| C 可変長系列 | 不成立（7/7） | 不成立（方針確定） | 現行契約では表現できない。必要な型と着手条件を[方針文書](candidate-shape-policy.md)へ固定 |
| D 二段Chain | 不成立（6点） | **全項目OK** | 契約層は最初から再利用できた。候補層はP1-bでcandidate adapterへ分離 |
| E 同構造データ差し替え | 成立（範囲検証の穴1件） | **全項目OK** | 契約もコードも変更せず差し替えられた。宣言範囲を超えたデータが黙って通る穴を塞いだ |

再現:

```bash
uv run python backend/scripts/experiments/spikes/spike_case_d.py
```

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
| 既存ファイル変更数 | 2（`task_modules.py`, `models/active-packages.json`） | **2** | 一致 |
| 新規ファイル数 | 4（task definition JSON, tabular profile JSON, source CSV, package） | **4** | 一致 |
| 新規contract数 | 0 | **0** | 一致 |
| 新規API分岐数 | 0 | **0** | 一致 |
| 新規UI分岐数 | 0 | **0** | 一致 |
| registry追加点 | 2（`TASK_MODULES`, `_TABULAR_PROFILES`） | **2** | 一致 |
| generated schema更新 | `task-inventory.json` のみ | **`task-inventory.json` のみ** | 新しいendpointもschemaもないため `api-types.ts` は不変 |
| 専用test fixture数 | 1 | **1** | 一致 |
| 新規Python関数 | 0 | **0** | `_tabular_loader` / `_tabular_features` / `_tabular_builder` / `_tabular_starter` / `_standard_response_curve` / `_TABULAR_EXPLORER` をそのまま再利用 |

**予測の根拠**: [inventory §1.1](extensibility-inventory.md#11-task) の登録点表と、`_tabular_loader` / `_tabular_features` / `_tabular_builder` / `_tabular_starter` が `task_id` でパラメタ化済みであること。

### 実測結果

fixture: 数値5列（`process.*`）＋カテゴリ2列（`categorical.*`）＋出力2列、300行、`warpage_mm` を44行欠損。
`_prepare_app_resources()` で起動し、TestClientで本番APIを叩いた結果:

```text
[registry] availability=available stage=ready
[data] rows=300 eligible=300 warpage欠損=44
[OK] GET /api/task-definitions        [OK] task-definitionsにspike Taskが載る
[OK] starter projectが自動生成される   [OK] starter候補が3件
[OK] preview（予測）                   [OK] 2出力が返る
[OK] response curve                   [OK] 類似観測
[OK] 品質表示                          [OK] 学習データInspector
[OK] Activity一覧                      [OK] ロバストネスが利用可能
[OK] ロバストネス実行                   [OK] 範囲探索（screening）
```

欠損targetは `eligible_targets` から外れ、入力行としては有効（`eligible=300`）のまま扱われました。
学習除外はtarget単位で行われ、UI上で予測値と実測値が混ざる経路はありませんでした。

**新しく見つかったこと（予測になかった2件）**

Package構築が**登録の後**でしか動きません。順序が逆にできません。

| # | 場所 | 内容 |
| --- | --- | --- |
| A-1 | [tabular_model_builder.py:376](../../backend/src/material_workbench/modeling/tabular_model_builder.py#L376) | `build_tabular_package_from_data` が `load_task_contracts()` をroot注入なしで呼ぶ。TaskDefinition JSONを本番 `task_definitions/` に置く前はPackageを作れない |
| A-2 | [model_lifecycle.py:234](../../backend/src/material_workbench/modeling/model_lifecycle.py#L234) | `canonical_training_dataset` が `task_module()` 経由でmodule-levelの `TASK_MODULES` を直接読む。`task_modules.py` への登録前はPackageを作れない |

これは安全側の設計（未登録Taskのartifactを作れない）でもあるため、**負債とは断定しません**。
ただし「Packageを先に作って検証してから登録する」手順は取れないので、`add-prediction-task` Skillの手順順序と一致していることを確認しておく必要があります。

**予測が外れる可能性として挙げていた箇所の結果**: `_tabular_starter` の `model_family == "lightgbm_binary"` 分岐（現在は[builtin_tasks.py](../../backend/src/material_workbench/task_composition/builtin_tasks.py)）は、`ridge` を選んだため通りました。Profileの `model_family` で分岐しており `task_id` では分岐していないため、標準Taskの追加では問題になりません。

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
| 既存ファイル変更数 | 1（`task_modules.py`） | **3以上**（`task_modules.py`, `observation_regression.py`, `observation_model_builder.py`） | runtimeとbuilderが1 Taskに固定されており、パラメタ化しないと2つ目を登録できない |
| 新規ファイル数 | 4（observation profile JSON, task definition JSON, source xlsx, package） | **4**（同じ） | 一致 |
| 新規contract数 | 0 | **0** | Profile契約とTraining View契約はそのまま使えた |
| 新規API分岐数 | 0 | **0** | 一致 |
| 新規UI分岐数 | 0 | **0** | 一致 |
| registry追加点 | 1 | **1** | 一致 |
| generated schema更新 | `task-inventory.json` のみ | **同じ** | 一致 |
| 専用test fixture数 | 1 | **1** | 一致 |

**測定の範囲**: Profile宣言とTraining View構築までを実データで通し、runtime / builderは**再利用可能性をsignatureとmodule定数で判定**しました（builderがprofileを受け取れないためPackageまでは到達できません）。既存ファイル変更数「3以上」はこの判定に基づく下限です。

### 実測結果

fixture: 工程条件シート `conditions`（40条件）＋在庫シート `stock` ＋ relationシート ＋ 試験Aシート `test_alpha`（120行）＋ 試験Bシート `test_beta`（80行）。
溶接語彙は一切使わず、試験行固有入力（`test_load_kgf` / `indent_load_kgf`）と target別cohort を含みます。

```text
[OK] Observation Profileを溶接語彙なしで宣言できる
[OK] Training Viewの構築（build_observation_training_dataset）
[view] alpha: rows=120 eligible=120 split_groups=40 features=5
[view] beta:  rows=80  eligible=80  split_groups=40 features=5
[OK] target別cohortがfamilyごとに分離される
[cohort] beta.hardness_hv usable=64 入力行=80 target欠損=16 exclusion={'値なし': 16}
[OK] 欠損targetが入力行と別に不適格として数えられる
[OK] 試験行固有入力を canonical input として宣言できる
[OK] family名が契約の分岐条件になっていない
```

**再利用できたもの（Phase 2.1にとって重要）**

- `ObservationDatasetProfile` は溶接語彙なしで宣言でき、`entities` / `families` / `split_group_role` / 試験行固有入力がそのまま表現できました
- `build_observation_training_dataset` はfamily idで分岐していません（`tensile` / `charpy` / `corrosion` のリテラル分岐なし）。family名は表示・集計にのみ使われます
- target別cohortが `TargetTrainingSummary` で分離され、**入力行の適格性とtargetの適格性が別に数えられます**（入力80行有効、うちtarget欠損16行）

→ **Canonical Training View境界は新設ではなく、Observation familyの既存契約を昇格させれば足ります。**

**再利用できなかったもの**

| # | 場所 | 内容 |
| --- | --- | --- |
| B-1 | [observation_model_builder.py:225](../../backend/src/material_workbench/modeling/observation_model_builder.py#L225) | `build(source, destination, *, replace)` に profile引数がない。Observation family builderは1 Profile専用 |
| B-2 | [observation_regression.py:32](../../backend/src/material_workbench/modeling/observation_regression.py#L32)–`:36` | `TASK_ID` と `PROFILE_PATH` がmodule定数。runtime内12箇所以上が参照し、`self.task_id = TASK_ID` で固定される |
| B-3 | [observation_profile.py:452](../../backend/src/material_workbench/data/observation_profile.py#L452)–`:464` | Profileが宣言した `source_unit` → `canonical_unit` の**数値変換が適用されない**。`kgf` を `N` と宣言しても値は生のまま（`51.588` が `N` として乗る） |

B-1 / B-2 は Tabular family（`_tabular_loader(task_id)` 等で完全にパラメタ化済み）との明確な非対称です。
**Observation familyは現時点で「契約は汎用、実装は単一インスタンスの縦スライス」** という状態です。

B-3 について: 現行の溶接Profileが宣言している変換はすべて相対的なラベル付け替え（`℃`→`°C`、`%`→`mass% deposited metal`）で、数値の再スケールを宣言しているものはありません。したがって**今動いているバグではなく、契約が許してしまう潜在リスク**です。「暗黙の単位変換を行わない」という原則の裏返しとして、**宣言した変換が黙って行われない**という形の誤判断が起こり得ます。

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

### 実測結果

現行契約に対して7項目を検査し、**7項目すべてが表現できない**ことを確認しました。

| # | 検査項目 | 拒否した契約 | エラー |
| --- | --- | --- | --- |
| C-1 | `heat_pattern` 以外の系列groupを宣言する | [task_contracts.py:40](../../backend/src/material_workbench/contracts/task_contracts.py#L40) | path正規表現不一致 |
| C-2 | 系列fieldのpathを自由に付ける | 同上 | path正規表現不一致 |
| C-3 | 系列fieldに単位（`°F`）を宣言する | [task_contracts.py:77](../../backend/src/material_workbench/contracts/task_contracts.py#L77) | `heat_pattern fields cannot declare scalar ranges, choices, or a unit` |
| C-4 | 31点以上の可変長系列を候補入力に保存する | [schemas.py:247](../../backend/src/material_workbench/contracts/schemas.py#L247) | `List should have at most 30 items`（64点で拒否） |
| C-5 | timestamp重複を値を残したまま不適格として保持する | [schemas.py:258](../../backend/src/material_workbench/contracts/schemas.py#L258) | `ヒートパターンの時刻は厳密な昇順にしてください`（**保存自体が拒否されるため品質findingとして残せない**） |
| C-6 | `CanonicalCandidate` に系列の正規化provenanceを置く | [task_contracts.py:357](../../backend/src/material_workbench/contracts/task_contracts.py#L357) | `Extra inputs are not permitted`（`extra="forbid"`） |
| C-7 | 系列入力TaskをChain Stageにする | [chain_contracts.py:259](../../backend/src/material_workbench/contracts/chain_contracts.py#L259) | `has a required heat-pattern input unsupported by Chain v1` |

C-5が最も重要です。現行契約は不正な系列を**保存できない**ため、「値は残しつつ品質findingとして不適格にする」という
このリポジトリの他の場所（Tabular/Observation familyの `eligible` + `exclusion_reasons`）で採っている方針を、
系列に対しては取れません。単位変換（C-3）も同様に、Observation familyの単位宣言（[ケースB B-3](#2-ケースb複数sheet複数観測family)）と合わせて
**系列の正規化履歴をどこにも置けない**ことが確認できました。

→ §3冒頭の新型候補4件（`SeriesCandidateInputs` / `CanonicalSeries` / `SeriesQualityFinding` / `SeriesFeatureSpec`）は
妥当ですが、C-5とC-6から **`CanonicalSeries` は「点列」ではなく「点列＋元単位＋変換ID＋除外点」を持つ必要がある**ことが確定しました。

### 変更点マトリクス

| 指標 | 予測 | 実測 | 差分の理由 |
| --- | --- | --- | --- |
| 既存ファイル変更数 | 6以上（task_contracts, schemas, tabular or 新loader, runtime, UI 2件） | **未実測**（実装しないため） | 検査した契約は3ファイル（task_contracts, schemas, chain_contracts） |
| 新規ファイル数 | 5以上 | 未実測 | |
| 新規contract数 | 4（上表） | **4で足りる見込み**（C-5/C-6の要件を `CanonicalSeries` に含める前提） | 検査7項目が4型で覆えることを確認 |
| 新規API分岐数 | 1以上（候補入力surface） | 未実測 | |
| 新規UI分岐数 | 2以上（入力editor, diff表示） | 未実測 | |
| registry追加点 | 2以上（Task, Candidate Shape） | 未実測 | |
| generated schema更新 | 両方 | 未実測 | |
| 専用test fixture数 | 2以上 | 未実測 | |

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

### 実測結果

fixture: ケースAと同じ方式で標準表形式Task 2件（`spike-stage-x-v1`: 成形条件→収縮率、
`spike-stage-y-v1`: 収縮率＋焼鈍温度→平面度）を登録し、X→Yの2段Chainを組みました。
決定論的Stageなし、疎配合なし、外部入力は `candidate.process.*` / `candidate.categorical.*` のみです。

```text
[OK]   ChainDefinition検証（2段task・決定論的Stageなし）
[OK]   ChainRevision構築
[OK]   Chain Definition / Revisionの登録
[OK]   Chain Project作成
[FAIL] 候補契約API      -> v1 Chain candidateは決定論的Stageを1段だけ必要とします
[FAIL] Chain候補の保存   -> Chain候補には疎な配合明細が必要です
[FAIL] 初期候補生成      -> v1 Chain candidateは決定論的Stageを1段だけ必要とします
[FAIL] 候補の妥当性検証   -> Chain候補には疎な配合明細が必要です
[OK]   不確かさ伝播capability
[FAIL] 外部入力を welding_context 以外の名前空間で渡せる
[FAIL] 疎配合参照なしでChain snapshot identityを作れる
```

**Chain Coreとして再利用できた範囲（予測通り）**

- `ChainDefinition` を2段task構成・決定論的Stageなしで宣言でき、`validate_chain_definition` が通る
- binding検証（value_kind / quantity / basis / unit の一致）が溶接非依存で機能する
- `build_chain_revision` のdigest計算、`store.register_chain_definition` / `register_chain_revision` が通る
- `POST /api/projects` によるChain Project作成が通る（`_create_chain_project` は終端Task Stageだけを見ており疎配合を要求しない）
- `GET /chain/distribution-capability` が通る

**塞がった6点（[inventory §3](extensibility-inventory.md#3-chain-coreに残る溶接固有symbol全列挙) の番号と対応）**

| # | inventory | 場所 | 実測エラー |
| --- | --- | --- | --- |
| D-1 | #3 | [chain_execution.py:250](../../backend/src/material_workbench/application/chain_execution.py#L250) | 候補契約APIと初期候補生成が「決定論的Stage 1段」を要求 |
| D-2 | #4 | [chain_execution.py:263](../../backend/src/material_workbench/application/chain_execution.py#L263) | `prepare_candidate` が疎配合を要求（候補を保存できない） |
| D-3 | #5 | [chain_execution.py:328](../../backend/src/material_workbench/application/chain_execution.py#L328) | `_resolve` が疎配合を要求（実行・snapshot・variantの全経路） |
| D-4 | #1 | [chain_execution.py:70](../../backend/src/material_workbench/application/chain_execution.py#L70) | `_external_values` が `candidate.process.*` / `candidate.categorical.*` を**一切生成しない**。実際に生成されたのは `candidate.welding_context.*` と `candidate.test_context.*` のみ |
| D-5 | #14 | [chain_contracts.py:196](../../backend/src/material_workbench/contracts/chain_contracts.py#L196) | `ChainSnapshotIdentity` が `design_space` / `commercial_catalog` 欠落で2件のvalidation error |
| D-6 | #11 | [chain_execution.py:1002](../../backend/src/material_workbench/application/chain_execution.py#L1002) | D-5の帰結。`snapshot()` は `blend` からidentityを組む |

D-4は重要な追加知見です。**ChainDefinitionの契約層は任意の名前空間を受理する**（`candidate.process.barrel_temperature_c` で
`validate_chain_definition` が通った）のに、実行層の `_external_values` はそれを生成しません。
つまり名前空間の固定は**契約ではなく実装1関数**に閉じており、adapterへ出す対象として最も切り離しやすい箇所です。

### 変更点マトリクス

| 指標 | 予測 | 実測 | 差分の理由 |
| --- | --- | --- | --- |
| 既存ファイル変更数 | 5以上（chain_execution, chain_uncertainty, chain_contracts, api/chains, bootstrap） | **3で足りる見込み**（chain_execution, chain_contracts, +不確かさ伝播） | 塞がったのは候補層と snapshot identity のみ。`api/chains.py` と bootstrap は分岐追加不要（Project作成もcapability APIも通った） |
| 新規ファイル数 | 2以上（adapter, chain定義） | **2**（adapter, chain定義） | 一致 |
| 新規contract数 | 2以上（adapter境界, snapshot identity分離） | **2**（adapter境界, snapshot identityのdomain参照分離） | 一致 |
| 新規API分岐数 | 0（目標） | **0で到達可能**（実測でAPI層に分岐が要らないと確認） | Chain APIは全てserviceへ委譲しており、分岐はservice内のadapter解決で済む |
| 新規UI分岐数 | 1以上（BlendEditorPanelの出し分け） | **未実測**（候補が保存できずUI経路へ到達しない） | |
| registry追加点 | 1（Chain adapter） | **1** | 一致 |
| generated schema更新 | 両方 | **未実測** | |
| 専用test fixture数 | 2以上 | **2**（Task X / Y） | 一致 |

## 5. ケースE：同じ意味・同じ構造のデータ差し替え

計画§8の成功条件1を測るために追加したケースです。

### 目的

既存Taskの列構成をそのまま使い、行だけが異なる新しいsourceへ差し替える。
**Profile・Dataset Revision・Model Packageの更新だけで扱えるか**を確認する。

### 題材（fixture）

`concrete-strength-v1` の元CSV（11列）から、列名・列順・単位・カテゴリ値を保ったまま
行をブートストラップ再標本し、数値へ±3%の観測ノイズを加えた1600行の新sourceを作ります。

### 実測結果

```text
[source] 1600行 / 11列 を差し替え
[OK] Profileを変えずに新sourceからPackageを構築できる
[OK] 差し替えたsourceでTaskが利用可能になる
[OK] 学習データが差し替えたsourceを指す
[OK] Profile IDは変わらない（同じ意味・同じ構造だから）
[OK] starter project / starter候補 / preview / 類似観測 / 品質表示 / 学習データInspector
[OK] Model Package状態が新しい学習データ由来のprovenanceを返す
[OK] TaskDefinition / Profile / task_modules.py / active-packages.json を変更していない
```

**成功条件1は成立します。** source上書き（`source_env`）とPackage差し替えだけで、
契約ファイルもコードも変更していません。

### 見つかった穴（塞ぎました）

「同じ構造」の境界を確認するため、TaskDefinitionの `allowed_range` を10倍超える値を
含むデータへ差し替えたところ、**その行が適格な学習行として残りました**。
Tabular loaderはProfileの `quality_rules` / `curation` で宣言しない限り
TaskDefinitionの `allowed_range` を学習行へ適用しません。

対応方針を2つに分けました。

| 対象 | 扱い | 理由 |
| --- | --- | --- |
| `allowed_range` 超過 | **失敗させる**（`validate_training_rows_within_allowed_range`） | 候補は `allowed_range` で検証されるので、そこを超える学習行は候補から到達できない。データと契約の不一致を意味する |
| `training_range` のずれ | **報告する**（`training_range_drift`） | Task側の参考宣言のdrift。runtime表示はPackage学習Datasetから別に導出する |

loaderは行を落としません（暗黙の値判断をしない）。不一致は検証層で明示的に失敗させます。

全13Taskの学習データが `allowed_range` を満たすことを確認したうえで追加したので、
既存Taskは影響を受けません。一方、TaskDefinition側の `training_range` は
`annealed-properties-v1`（`composition.Cu`, `process.ls_mpm`）と
`hot-rolled-properties-v1`（`composition.Cu`, `process.entry_thickness_mm`）で
**すでに実データからずれていました**。runtimeの応答曲線・予測地図は
`TrainingRangeProvider` が選択Packageの学習Datasetから範囲を導出するため影響を受けません。
Task側の参考宣言を直すにはTaskDefinitionとPackageの作り直しが必要なため、
`backend/tests/test_training_range_contract.py` の `KNOWN_TRAINING_RANGE_DRIFT` に
現状として記録し、増えたら落ちるようにしています。

### 変更点マトリクス

| 指標 | 予測 | 実測 | 差分の理由 |
| --- | --- | --- | --- |
| 既存ファイル変更数 | 1（`models/active-packages.json`） | **0** | `source_env` とPackage上書きだけで差し替えられた。ファイル名を変える場合のみ `default_source` の更新が必要 |
| 新規ファイル数 | 2（source, package） | **2** | 一致 |
| 新規contract数 | 0 | **0** | 一致 |
| 新規API分岐数 | 0 | **0** | 一致 |
| 新規UI分岐数 | 0 | **0** | 一致 |
| registry追加点 | 0 | **0** | 一致 |
| generated schema更新 | なし | **なし** | 一致 |
| 専用test fixture数 | 1 | **1** | 一致 |

## 6. スパイク実行順

ケースAを最初に行いました。Aの成果物（標準表形式Task）をケースDのStage X / Yへ流用しています。

```text
A（標準表形式Task）          実行済み
  ├→ B（Observation family再利用）   実行済み
  ├→ C（可変長系列。型の特定のみ）    実行済み
  ├→ D（二段Chain。Aの成果を使う）    実行済み
  └→ E（同構造データ差し替え）        実行済み
```

## 7. 証拠にもとづくIssue分割

5ケースの実測を根拠に、次のリファクタリングを分割します。依存関係は実測により解消済みです。

### P1-a｜Decision Activityのparameter/result union化 — **完了**

- 根拠: [inventory §4](extensibility-inventory.md#4-decision-activity追加時の変更点)。2件目のActivity追加で**既存契約の破壊的変更が2件**発生する
- 実施内容:
  - `DecisionActivityRunRequest.parameters` / `DecisionActivityRun.parameters` / `.result` を `schema_version` 判別のdiscriminated unionへ
  - handler registry（`application/decision_activity_registry.py`）を追加し、`if definition != ROBUSTNESS_ACTIVITY` を削除
  - 必要条件をresource種別ごとの1関数へ（`candidate` / `comparison_candidate`）
  - 焼鈍固有だった `ls_mpm` / `heat_time_basis` の処理を `domain/candidate_inputs.py` へ移し、対象入力を
    TaskDefinitionの `time_transform = "inverse_heat_time"` から解決するよう変更（共通処理が列名を知らなくなった）
  - UIを generic shell + activity別view registry（`decisionActivities/`）へ分割
  - 2件目のActivity「候補差分の要因分解」を実装してunionを実証
- 完了条件の確認: 共通部分（service / API / `DecisionActivityPanel.tsx`）が activity_id を名指ししないことを
  `test_shared_activity_shells_do_not_name_a_specific_activity` で固定。E2Eで両Activityの実行を確認
- **union化の技術的制約**: pydanticの `Field(discriminator=)` は Union が2メンバー以上でないと使えないため、
  「union化だけ」は成立しない。2件目のActivityと同時にしか導入できない
- **破壊的変更**: リクエストの `parameters.schema_version` が必須になった（以前は既定値で省略できた）。
  discriminated unionは判別子の省略を許さない。保存済みrunは常に `schema_version` を含むため読み出しは影響なし
- Activity追加時に触る箇所は4か所（contracts / handler module / registry / UI view+registry）。
  [decision-activities.md](../contracts/decision-activities.md) に記載

### P1-b｜Chain Coreと溶接adapterの分離 — **完了**

- 根拠: [ケースD実測](#4-ケースd疎配合を使わない二段chain)。塞がったのは6点で、うち5点が `chain_execution.py` の候補層、1点が `ChainSnapshotIdentity` 契約
- 実施内容:
  1. `application/chain_candidate_adapters.py` を追加。`ScalarChainAdapter` と `SparseBlendChainAdapter` をallow-listし、
     Chain Revisionが宣言したStage構成から選ぶ（Task IDでは選ばない）
  2. 外部入力の名前空間、候補検証、初期候補のdomain payload、決定論的Stageの実行と出力形状、
     snapshotのdomain参照をadapterへ移動（D-1〜D-4）
  3. `ChainSnapshotIdentityV2` を追加し、`design_space` / `commercial_catalog` をadapter提供の
     `domain_references` へ。保存済みv1は不変のまま読める（D-5, D-6）
  4. `GET /chain/candidate-capability` を追加。UIは契約APIを叩く前に必要な入力面を判断する
  5. `actual_conditioned_variant`の必要実測、重複・不足判定、終端Stage入力への適用も
     adapterへ移した。scalar fixtureでは収縮率実測を`process.shrinkage_pct`へ適用し、
     immutable variantをDBから復元できる
- **実施中に見つかったCoreの欠陥**: `_run_stage` が `CandidateInputs` を組むとき
  `composition` グループの存在を仮定していた（`composition` は必須フィールド）。
  溶接Chainは常に `composition.*` をbindingするため露見していなかった。空dictを既定にして修正
- 手を付けずに済んだ範囲（予測通り）: `ChainDefinition` / binding検証 / `build_chain_revision` /
  store登録 / Chain Project作成 / `welding_chain_bootstrap.py`。`api/chains.py` は
  分岐追加ではなく guard の置き換えのみ
- 完了条件の確認: **`backend/scripts/experiments/spikes/spike_case_d.py` が全20項目OK**
  （capability宣言、候補保存、2 Stage実行、snapshot保存、中間実測variantとDB復元、
  名前空間、不確かさ伝播）。
  境界は `backend/tests/test_chain_candidate_adapters.py` で固定し、
  Chain Coreに `welding_context` / `material_composition` 等のdomain symbolが現れたら落ちる
- **未着手として残す範囲**: Chain Workbench画面のスカラー候補editor。
  スカラーChainを製品機能として出すときに作る

### P1-c｜Canonical Training View境界の明示 — **完了**

- 根拠: [ケースB実測](#2-ケースb複数sheet複数観測family)。Observation Profileと `ObservationTrainingDataset` は溶接語彙なしで再利用でき、family名も分岐条件になっていない
- 実測により方針が確定: **新設ではなく、Observation familyの既存契約を共通到達点として昇格させる**
- 実施内容:
  1. `QualitySurface` Protocolを追加し、`quality` / `detected_quality` / `technical_columns` を宣言。
     これまでは `DataExplorationService.quality()` が読むだけの**未宣言の構造依存**だった
  2. `DataExplorerCapability(quality=True)` を宣言したTaskの記述子がその面を満たすことを
     `TaskRegistry` が起動時に検証する（宣言だけ先に立つことを防ぐ）
  3. `DataExplorerEntry.data` の型注釈を `WorkbookData` から `DataDescriptor` へ修正（実態と一致していなかった）
- 完了条件の確認: `backend/tests/test_training_view_boundary.py` が、
  全runtime記述子が共通境界を満たすこと、品質集計が宣言済み属性しか読まないこと、
  面を満たさない記述子がregistryで弾かれることを固定
- **残す範囲**: Tabular / Workbook / FlankWear familyを `ObservationTrainingDataset` 相当へ
  到達させる経路は作っていない。`model_lifecycle.canonical_training_dataset` が
  全familyの学習データを共通形へ変換しており、学習経路では既に共通化されている。
  Inspector表示の共通化は2つ目のObservation Taskが出てから判断する

### P1-d｜Observation family実装のパラメタ化 — **完了**

- 根拠: [ケースB実測 B-1 / B-2](#2-ケースb複数sheet複数観測family)。契約は汎用だが実装が1 Taskに固定されている
- 実施内容:
  1. `modeling/observation_training_spec.py` を追加。**特徴量の並び、per-target特徴量、
     target→familyをObservation ProfileとTaskDefinitionから導出**する
     （これらは既に宣言済みのデータと重複していた）
  2. `observation_regression` から `TASK_ID` / `PROFILE_PATH` / `PIPELINE_FEATURES` /
     `TARGET_FAMILY` / `TARGET_FEATURES` / `FEATURE_DEFINITIONS` / `TEST_SOLUTIONS` /
     `OUTPUT_BOUNDS` を削除。`StageCRegressionRuntime` → `ObservationRegressionRuntime`
  3. builderに `declaration` 引数を追加（B-1）
  4. Taskごとのdata-only宣言を `task_composition/builtin_tasks.py` へ集約。残るのはprofile path、
     feature transform id/version、support policy id、output bounds のみ
- **output boundsは意図的に宣言のまま残す**。TaskDefinitionの `plausibility_range` は
  表示・検証用であり、実測すると値が違う（TS: 0–2000 vs 0–上限なし）。
  Chain samplingが使う実行時clampへ流用すると新たにclipが発生するため代用しない。
  [chain-execution.md](../contracts/chain-execution.md) の「exact allow-listで固定した物理境界」と整合する
- 完了条件の確認: `backend/tests/test_observation_training_spec.py` が、
  導出結果が**保存済みModel Packageの特徴量パイプラインと一致**すること、
  runtime moduleにtask idもprofile pathも残っていないことを固定

### P1-e｜Candidate Shapeの整理方針の確定 — **完了**

- 根拠: [inventory §1.6](extensibility-inventory.md#16-candidate-shape) と[ケースC実測](#3-ケースc可変長温度系列task)
- 成果物: [candidate-shape-policy.md](candidate-shape-policy.md)
- 決めたこと: 任意JSONにしない / 既存形状を `ScalarCandidateInputs` として保存互換で切り出す /
  shapeごとにpersistence・diff・copy・snapshotの4つの意味を定義しないと登録できない /
  UIはshape capabilityからsurfaceを選ぶ / Task IDで分岐しない
- **着手条件も明記**: pydanticの `Field(discriminator=)` は2メンバー以上でないと使えないため、
  union化だけを先行させることはできない。2形状目が必要になったときに同時に切り出す。
  最も近いのはChainのスカラー候補を製品機能として出すとき
- ケースCで確定した要件: `CanonicalSeries` は点列だけでなく**元単位・変換ID・除外点**を持つ必要がある（C-5, C-6）

### P2｜Task composition境界の分割 — **完了**

- 根拠: [ケースA実測](#1-ケースa通常の表形式task)。標準表形式Taskの追加は**新規Python関数0件・既存ファイル変更2件・API/UI分岐0件**で完了した
- 実施内容: 依存の軽いport、descriptor、built-in composition、catalogを
  `task_composition/` 配下へ分離し、Project runtime解決・Dataset登録transaction・
  workspace catalog bootstrapをapplication層へ移した
- 標準モデルは `standard_model_authoring` で学習候補とestimator allow-listを宣言し、
  特殊familyだけ `specialized_package_builder` を持つ
- A-1 / A-2の登録順序制約は維持する。再現スクリプトとData Contributor向け手順は
  現行catalogを正本として参照する

### P3｜系列・Source Connector・Project-level Design Space

- 根拠: ケースCで特定した新型4件、[inventory §1.11](extensibility-inventory.md#111-source-connector)、[§1.8](extensibility-inventory.md#18-design-space--commercial-catalog)
- 実needが出るまで着手しない

### 単発で片付けるもの

| 項目 | 根拠 | 内容 |
| --- | --- | --- |
| Observation Profileの単位宣言 | ケースB B-3 | 宣言した `source_unit` → `canonical_unit` の数値変換が適用されない。変換を実装するか、**relabelのみ許可して再スケール宣言を拒否する**かを決める。今は潜在リスクで実バグではない |

## 8. 成功条件に対する現在地

計画§8の8項目に対する実測状況です。

| # | 成功条件 | 現在地 |
| --- | --- | --- |
| 1 | 同じ意味・同じ構造のデータ差し替えがProfile / Dataset Revision / Package更新だけで済む | **達成済み**（ケースE。契約もコードも変更せず差し替えられた。宣言範囲を超えたデータが黙って通る穴も塞いだ） |
| 2 | 新しい標準表形式Taskが既存機能をTask固有実装なしで使える | **達成済み**（ケースA） |
| 3 | 新しいCandidate Shapeを既存shapeを壊さず追加できる | **方針確定**（P1-e）。実装は2形状目が必要になったとき。現行契約では7/7が表現不可 |
| 4 | 新しいDecision Activityを既存Activity serviceへ分岐追加せず登録できる | **達成済み**（P1-a完了。共通部分がactivity_idを名指ししないことをテストで固定） |
| 5 | 疎配合を使わないChainがChain Coreの変更なしで実行できる | **達成済み**（P1-b完了。ケースDのスパイクが全項目OK） |
| 6 | 異質な第二ユースケースで共通境界が実証される | **達成済み**（ケースBでProfile / Training Viewを実証し、P1-c / P1-dでruntime / builderもパラメタ化） |
| 7 | 安全性・再現性の契約を緩めず、変更ファイル数と専用分岐数が減る | **達成済み**。digest / revision / snapshot契約は緩めていない（Chain snapshot identityはv1を不変のまま残しv2を追加、特徴量パイプラインは導出結果が保存済みPackageと一致することをテストで固定）。減った分岐は下表 |
| 8 | 共通化しない方がよい特殊領域が明示されている | **達成済み**（inventory §3の分類、P2の優先度引き下げ、下記「共通化しない領域」） |

### 減った分岐と固定した境界

| 箇所 | before | after |
| --- | --- | --- |
| Decision Activity service | `if definition != ROBUSTNESS_ACTIVITY` で1件だけ実行 | handler registryで解決。共通部分はactivity_idを名指ししない（テストで固定） |
| ロバストネス解析の入力操作 | `process.ls_mpm` を直接参照 | TaskDefinitionの `time_transform = "inverse_heat_time"` から解決 |
| Chain Core | `welding_context` / `test_context` / `material_composition` / `auxiliary_features` / 疎配合必須 / 決定論的Stage 1段必須 | candidate adapterへ移動。Coreにdomain symbolが現れたら落ちる（テストで固定） |
| Observation family runtime | `TASK_ID` / `PROFILE_PATH` / 特徴量7定数をmodule定数で固定 | ProfileとTaskDefinitionから導出。宣言はprofile path / transform id / support policy / output boundsのみ |
| Data Explorerの品質面 | 未宣言の構造依存（3属性） | `QualitySurface` Protocolで宣言し、起動時に検証 |
| `DataExplorerEntry.data` | 型注釈が `WorkbookData`（実態と不一致） | `DataDescriptor` |

### 共通化しない領域（意図的に残す）

| 領域 | 理由 |
| --- | --- |
| built-in Task定義のfamily別ファイル分割 | composition境界は分離済み。family定義をさらに分けるのは、独立した変更頻度が生じた時点で判断する |
| output boundsのallow-list | TaskDefinitionの `plausibility_range` は表示・検証用で値が異なる。実行時clampへ流用すると新たにclipが発生する |
| starter candidate（Taskごとのfixture） | 科学的な代表条件は人が決める。`TaskModule.starter_project` が正本 |
| `actual_conditioned_variant` の `composition.` prefix | 中間実測が組成であるChainにしか使えない制約として明示的に残す |
| Chain Workbench画面のスカラー候補editor | スカラーChainを製品機能として出すときに作る。現在はadapter種別を表示して停止 |
| `heat_pattern` をChain Stageへ入れること | Candidate Shape拡張が前提。今は明示的に失敗させるのが正しい |

## 9. この文書の更新規則

- 実測欄を埋めるときは、予測を書き換えず**差分の理由**を書く
- 反証できなかったケース（＝既存基盤で足りたケース）も必ず記録する。共通化しない判断の根拠になる
- ケースが1つも通らないうちに「汎用基盤完成」と書かない
