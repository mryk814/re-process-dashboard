# データセット入力プロファイル（Dataset Input Profile）

各Excelデータフローの外部シート、列、単位、エンティティキー、リレーション、適格性、技術メタデータは、`backend/src/decision_workbench/data/dataset-input-profile-*.json` で管理します。
最小教材は `dataset-input-profile-tutorial.json`、工程データは `dataset-input-profile-process-v1.json` です。
切削逃げ面摩耗フローは `dataset-input-profile-flank-wear-v1.json` です。
このフローは材料、工具、切削条件、摩耗履歴を対応付け、`flank-wear-v1` だけを対象とします。
最小教材と工程データは、起動時にワークブックのシート構成とソースマーカーからプロファイルを自動選択します。
切削逃げ面摩耗は専用ローダーが `dataset-input-profile-flank-wear-v1.json` を選択します。

## 観測が参照する画像

組織観察のように、行が画像ファイルを指す観測があります。
**画像パスを持つ列名はProfileが宣言します。** アプリ側に列名を書きません。

`shared.technical` へ役割ごとに `name: "evidence_image"` を宣言します。

```json
{"role": "anneal_microstructure", "name": "evidence_image", "column": "画像path"}
```

宣言のないroleでは画像を探しに行きません。
`evidence_image` はentity identity、relation、モデル入力、実測targetではなく補助証拠なので、
列が無くてもDataset登録を止めません。列名が異なる場合はProfile Workbenchで明示対応し、
対応した列はLineage／Evidence参照として保持します。

画像パスは元データ由来で信頼できないため、解決は次のとおり狭く固定しています
（[data/evidence_images.py](../../backend/src/decision_workbench/data/evidence_images.py)）。

- パスは**Datasetファイルと同じディレクトリからの相対**として解決する
- 絶対パス、ドライブレター、`..` を含む参照、そのディレクトリの外へ出る参照は拒否する
- allow-listした拡張子（`.png` / `.jpg` / `.jpeg`）だけを配信する
- 宣言はあるがファイルが無い場合は「見つからない」として扱う。別の画像で代替しない

配信は `GET /api/projects/{project_id}/lineage/{entity_key}/evidence-image` で、
`X-Content-Type-Options: nosniff` を付けます。画像ライブラリは通しません。

系譜のノード詳細は `evidence_image` として参照先と取得可否を返します。
取り込み漏れは画像なしとして表示し、**観測が無かったことにはしません**。
プロファイルを明示する場合は、`load_workbook_data(..., profile_path=...)` または検証コマンドの `--profile` を使います。

本番タスクの契約は `backend/src/decision_workbench/tasks/task_definitions/` に置きます。
起動時にバックエンドがプロファイルと各本番用 `TaskDefinition` の整合性を検証し、モデルとデータベースを初期化する前にワークブックを事前検証します。

```text
元ワークブック + Dataset Input Profile + TaskDefinition
                         |
                         v
          アプリ共通データセット（Canonical Dataset）
                         |
                         v
       系譜 / 特徴量パイプライン / モデル実行環境 / API
```

## 契約規則

- 必須シート、対応付け済みヘッダー、単位、数値信号、カテゴリ選択肢、観測の親、観測対象が不足している場合は処理を停止します。
- ヘッダーの順序と未対応の追加列は、モデル入力契約に含めません。
- 未知のフィールド、暗黙の単位変換、重複した対応付け、タスクプロファイルの欠落、不完全な出力対応は拒否します。
- エンティティの同一性は `(entity_type, key)` で判定するため、異なるエンティティ種別に同じソースキーがあっても統合しません。
- 実行可能な正規化処理は `median_by_parent/v1` と `stage_local_clock/v1` だけです。
- プロファイルに任意のコードは記述できません。
- tabular Profileは学習器を選びません。`model_family`、`ridge_alpha`、
  `num_boost_round`、monotone設定はTraining Recipeの責任です。
  既存Profileにある値は不変Packageとのdigest互換のため読み取りますが、新しい学習では使いません。
- 適格性ポリシーには受け付けるソース値を明記します。
- ワークブックに受理可能な信号が一つもないポリシーは、事前検証で拒否します。
- リレーションの親整合性は、結合が `parent_consistency: exactly_one` を宣言した場合だけ強制します。
- プロファイルが `allow_many` を宣言した既知のソース品質異常は、確認可能な状態で残します。
- プロファイルは `extends` で別のプロファイルを継承できます。
- オブジェクトの対応付けは統合し、配列は置換するため、列名を変更したワークブックや新しいワークブックでもアプリ共通契約を複製せずに再利用できます。
- 継承を解決したstandalone JSONが必要なときは、単独の変換scriptではなく
  `uv run python backend/scripts/operations/profile_workbench.py materialize <profile> <output>`を使います。
  既存出力の置換には`--replace`が必要です。
- ソースに該当する信号が本当に存在しない場合は、`optional_roles`、`optional_technical_fields`、明示的な `policy_defaults` を宣言できます。
- 既定値はプロファイル契約の一部であり、欠損セルから推測しません。
- 観測測定値には、単一のソース `column` または優先順を持つ `columns` の一覧を宣言できます。
- `columns` の一覧は、降伏点と0.2%耐力などの代替測定値から、最初の数値を明示的に採用する契約です。
- 観測に `parent_column` がない場合は、宣言済みのリレーション結合から親を解決します。
- 親を解決できない行は暗黙に結び付けず、確認可能かつ学習対象外の状態で残します。
- 学習行は工程キーだけでなく、観測を含むrelation行の経路から成分を解決します。同じ工程キーが複数成分で再利用されても、別relation行の試験と成分を混ぜません。
- 同一の観測キーそのものが複数成分の経路へ属する場合は、自動複製しません。配合比などProfileで宣言した集約規則がない限り、複合入力として診断し学習対象外にします。
- 派生済みの `anneal_features` シートがないソースは、その役割を任意にできます。
- その場合、importerは順序付き温度履歴から表示メタデータと特徴量化の適格性を導出します。明示履歴が2点以上あればLSは不要です。
- `ordered_heat_series` は、明示的な時間–温度履歴に加えて、型付きの `measurement_point_fallback` を宣言できます。
- 明示履歴が2点以上ある親では履歴を優先します。履歴がない親では、測定点マスタの入口距離、焼鈍条件のLS、工程ごとの温度から `到達時間[s] = 60 × 入口距離[m] / LS[mpm]` を計算します。
- この補完は統計推定ではなく設備位置に基づく単位変換です。生成点は `mapping_status: 測定点マスタ補完` として識別でき、候補化後の時間基準は `line_speed` になります。
- モデルはワークブックの数式ではなく、バージョン管理した特徴量パイプラインを使用します。
- `task_definition_ids` は、そのプロファイルが対応するタスク集合を固定します。
- この指定により、既存データフローへ未対応タスクを見せかけで追加せず、説明変数や目的変数が異なる新しいタスクを追加できます。

## 同じアプリ共通タスク契約へ新しいワークブックを追加する

入力と出力の意味、単位、制約が既存のTaskDefinitionと同じ場合は、TaskDefinitionやFeature Pipelineを変更しません。
新しいワークブックの構造差だけをDataset Input Profileで吸収します。

1. 新しいDataset Input Profileを追加し、既存の `task_definition_ids` を指定します。
2. シート、列、単位、エンティティ、リレーション、適格性を新しいソースへ対応付けます。
3. ソース事前検証でProfile選択と正規化結果を確認します。
4. 新しいソースでモデルを再学習し、別のModel Packageとして構築します。
5. データセットProfile、Package契約、特徴量ゴールデン、スモークの各テストを実行します。

TaskDefinitionまたはFeature Pipelineを変更するのは、入力や出力の意味、単位、制約、特徴量の計算が変わる場合です。
その変更は単なるソース追加ではなく、後述する新しいタスク契約または既存契約の版更新として扱います。

工程データでは、TaskDefinitionの入力と出力に直結する列は必須です。一方、`optional_auxiliary_keys`、`optional_metadata_keys`、`optional_technical_fields` に指定した探索・表示用の列は、存在すれば取り込み、欠けていてもDataset登録を止めません。
relationも同じで、Profileが対応を宣言する全Taskの入力、実測、親entityへの経路に必要なjoinだけを登録の必須条件にします。
Task未使用の補助entityは、relation列が存在すればlineageとして保持し、なくても登録可能です。
`焼鈍特徴量` シートは意図的に持ちません。
LSは `焼鈍条件-3CGL` から取得しますが、キャッシュ済みの数値時間温度系列をモデル入力の正本として優先します。LSは明示履歴がないときの時間軸補完にだけ必要です。
`焼鈍履歴` がない場合は、同じ焼鈍条件行の工程別温度と `測定点マスタ` の設備位置から時間軸を補完します。
目的変数の欠損は特性ごとに保持するため、TSがあり伸びがない行はTSの学習だけに使います。

切削逃げ面摩耗フローの `dataset-input-profile-flank-wear-v1.json` は、材料、工具、切削条件を摩耗試験へ結び付け、切削距離ごとの反復観測を学習行として構築します。
焼鈍特性と熱延特性とは入力、出力、エンティティが異なるため、既存Profileを継承せず、独立した `flank-wear-v1` の契約を使用します。

## 説明変数や目的変数が異なるデータを追加する

新しい物理量や科学的な量を既存の本番タスクへ無理に組み込みません。
次の縦一式を新しく追加します。

1. `backend/src/decision_workbench/tasks/task_definitions/<task-id>.json` を追加します。入力グループ、単位、編集範囲、許容範囲、学習範囲、出力対象、制約、実行能力を定義し、ファイル名をタスクIDと一致させます。
2. `backend/src/decision_workbench/data/dataset-input-profile-<flow>.json` に新しいプロファイルを追加します。`task_definition_ids` には、そのフローが実際に対応するタスクだけを指定します。共有するエンティティ契約とリレーション契約が本当に同じ場合だけ `extends` を使い、それ以外は独立したプロファイルを作ります。
3. 新しいパイプラインIDとバージョンを持つ特徴量パイプラインモジュールを追加するか、既存モジュールを拡張します。元のソース列、アプリ共通入力（canonical input）、派生特徴量は個別に確認できる状態を保ちます。列数が偶然同じという理由だけで、古い特徴量ベクトルを再利用しません。
4. 許可リストへ登録した実行環境とモデルアダプター、またはタスク専用の実行環境を追加します。新しいタスクID、入力契約ダイジェスト、プロファイルダイジェスト、特徴量パイプラインのバージョン、ソースダイジェスト、出力対象をmanifestへ記録したModel Packageを構築します。
5. 対象を絞った契約テスト、特徴量ゴールデン、ソース事前検証、Packageスモーク、APIまたはE2Eテストを1本追加します。すべて通過した後で、そのタスクのPackageを `models/active-packages.json` に追加します。

現在の本番用TaskModule一覧は [生成済みTask inventory](../contracts/task-inventory.json) を参照します。
新しいタスクでは、対応する `task_composition/builtin/<family>.py` のTask一覧へ
明示的なエントリーを一つ追加します。参照側は
`task_composition/catalog.py` の不変catalogだけを読みます。
起動、Package検証、モデル処理、ソースとプロファイルの選択、能力宣言、生成済みインベントリは、すべてこのエントリーから解決します。
プロファイルとTaskDefinitionだけを追加して、未対応タスクを実行可能に見せてはいけません。

## 再現可能なソース事前検証

実行環境やデータベースを変更する前に、次のコマンドを実行します。
このコマンドは元ワークブックへ書き込みません。

```powershell
uv run python backend/scripts/operations/profile_workbench.py validate data/source/material_workbench_process_v1.xlsx
uv run python backend/scripts/operations/profile_workbench.py validate path/to/new-source.xlsx --profile backend/src/decision_workbench/data/dataset-input-profile-new.json
```

### Profile Workbench

新しいExcelのシート・列と、選択されたProfileでの正規化結果をまとめて確認できます。

```powershell
uv run python backend/scripts/operations/profile_workbench.py inspect path/to/new-source.xlsx
uv run python backend/scripts/operations/profile_workbench.py inspect path/to/new-source.xlsx --profile backend/src/decision_workbench/data/dataset-input-profile-new.json
uv run python backend/scripts/operations/profile_workbench.py validate path/to/new-source.xlsx --profile backend/src/decision_workbench/data/dataset-input-profile-new.json
```

Profileが確定したら、元Excelを変更せずmanaged libraryへ内容ハッシュ単位でコピーし、Data Asset、Profile Revision、Dataset Revision、単一Dataset Viewをまとめて登録します。

```powershell
uv run python backend/scripts/operations/profile_workbench.py register path/to/new-source.xlsx `
  --profile backend/src/decision_workbench/data/dataset-input-profile-new.json `
  --database path/to/workspace.db `
  --library path/to/data-library
```

同じExcelと同じ実効Profileを再度登録しても、別Datasetには増えません。Profileの実効内容が変われば新しいProfile RevisionおよびDataset Revisionになります。

続いて、対象を絞った契約確認と通常の全体検証を実行します。

```powershell
uv run python -m pytest backend/tests/test_dataset_profile.py backend/tests/test_importer.py
uv run python -m pytest
npm run typecheck
npm run build
```

事前検証レポートには、選択したプロファイルID、ソースのSHA-256、シートごとの行数、リレーション数、観測の適格件数、検出した構造上の問題が含まれます。
選択したソースとプロファイルに合わせてModel Packageを再構築する必要があります。
別のソースで学習したPackageは、来歴検証で拒否します。

Packageローダーは、TaskDefinition、パイプライン文書、Package manifest、予測器の特徴量順序にあるアプリ共通入力の並びを照合します。
古い契約のPackageは、実行環境の起動時に拒否します。

フロントエンドの入力欄描画はIssue #7でTaskDefinition基準へ移行済みです。
実行環境Registryの有効化はIssue #5で対応し、再現可能な学習とPackage有効化の経路はIssue #19で完成しています。
