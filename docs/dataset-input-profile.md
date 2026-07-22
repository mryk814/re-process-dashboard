# データセット入力プロファイル（Dataset Input Profile）

各Excelデータフローの外部シート、列、単位、エンティティキー、リレーション、適格性、技術メタデータは、`backend/src/material_workbench/dataset-input-profile-*.json` で管理します。
既存のv2フローは `dataset-input-profile-v1.json`、別名の列と未加工の履歴を持つv3フローは `dataset-input-profile-v3.json`、具体的な2設備の工程名を持つv5フローは `dataset-input-profile-v5.json` です。
部分欠損、リレーションによる親解決、測定点マスターを持つv7フローは `dataset-input-profile-v7.json` です。
切削逃げ面摩耗フローは `dataset-input-profile-flank-wear-v1.json` です。
このフローは材料、工具、切削条件、摩耗履歴を対応付け、`flank-wear-v1` だけを対象とします。
v5はv3の正規化契約と予測契約を継承し、焼鈍履歴の工程名だけを標準工程カテゴリへ対応付けます。
v2、v3、v5、v7は、起動時にワークブックのシート構成とソースマーカーから、最も具体的に一致するプロファイルを自動選択します。
切削逃げ面摩耗は専用ローダーが `dataset-input-profile-flank-wear-v1.json` を選択します。
プロファイルを明示する場合は、`load_workbook_data(..., profile_path=...)` または検証コマンドの `--profile` を使います。

本番タスクの契約は `backend/src/material_workbench/task_definitions/` に置きます。
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
- 適格性ポリシーには受け付けるソース値を明記します。
- ワークブックに受理可能な信号が一つもないポリシーは、事前検証で拒否します。
- リレーションの親整合性は、結合が `parent_consistency: exactly_one` を宣言した場合だけ強制します。
- プロファイルが `allow_many` を宣言した既知のソース品質異常は、確認可能な状態で残します。
- プロファイルは `extends` で別のプロファイルを継承できます。
- オブジェクトの対応付けは統合し、配列は置換するため、列名を変更したワークブックや新しいワークブックでもアプリ共通契約を複製せずに再利用できます。
- ソースに該当する信号が本当に存在しない場合は、`optional_roles`、`optional_technical_fields`、明示的な `policy_defaults` を宣言できます。
- 既定値はプロファイル契約の一部であり、欠損セルから推測しません。
- 観測測定値には、単一のソース `column` または優先順を持つ `columns` の一覧を宣言できます。
- `columns` の一覧は、降伏点と0.2%耐力などの代替測定値から、最初の数値を明示的に採用する契約です。
- 観測に `parent_column` がない場合は、宣言済みのリレーション結合から親を解決します。
- 親を解決できない行は暗黙に結び付けず、確認可能かつ学習対象外の状態で残します。
- 派生済みの `anneal_features` シートがないソースは、その役割を任意にできます。
- その場合、importerはアプリ共通形式のLSと順序付き温度履歴から表示メタデータと特徴量化の適格性を導出します。
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

v3フローでは、`dataset-input-profile-v3.json` がこの手順を表します。
このプロファイルはv2のタスク定義と特徴量パイプラインを再利用し、名前が変わったシートとヘッダーを対応付け、整備済みの `quality` シートと熱延学習フラグがv3には存在しないことを明示します。

v5フローの `dataset-input-profile-v5.json` は、v3を置き換えずに継承します。
ソースマーカーは `CGL-1` と、`予熱1`、`加熱1`、`均熱出口`、`水冷` などの具体的な履歴ラベルを識別します。
工程対応には確認可能な温度点の `stage_category` と `mapping_status` を追加します。
これにより、元の `stage_name` を保ったまま、系譜画面の時間軸に沿った工程トラックへ具体的なソースラベルを表示できます。

v7フローの `dataset-input-profile-v7.json` は、括弧付きの組成名、リレーションから解決する引張試験と穴広げ試験の親、代替の降伏値列を対応付けます。
`焼鈍特徴量` シートは意図的に持ちません。
LSは `焼鈍条件-3CGL` から取得し、キャッシュ済みの数値時間温度系列と26個の工程対応からモデル用の履歴を構築します。
目的変数の欠損は特性ごとに保持するため、TSがあり伸びがない行はTSの学習だけに使います。

切削逃げ面摩耗フローの `dataset-input-profile-flank-wear-v1.json` は、材料、工具、切削条件を摩耗試験へ結び付け、切削距離ごとの反復観測を学習行として構築します。
焼鈍特性と熱延特性とは入力、出力、エンティティが異なるため、既存Profileを継承せず、独立した `flank-wear-v1` の契約を使用します。

## 説明変数や目的変数が異なるデータを追加する

新しい物理量や科学的な量を既存の本番タスクへ無理に組み込みません。
次の縦一式を新しく追加します。

1. `backend/src/material_workbench/task_definitions/<task-id>.json` を追加します。入力グループ、単位、編集範囲、許容範囲、学習範囲、出力対象、制約、実行能力を定義し、ファイル名をタスクIDと一致させます。
2. `backend/src/material_workbench/dataset-input-profile-<flow>.json` に新しいプロファイルを追加します。`task_definition_ids` には、そのフローが実際に対応するタスクだけを指定します。共有するエンティティ契約とリレーション契約が本当に同じ場合だけ `extends` を使い、それ以外は独立したプロファイルを作ります。
3. 新しいパイプラインIDとバージョンを持つ特徴量パイプラインモジュールを追加するか、既存モジュールを拡張します。元のソース列、アプリ共通入力（canonical input）、派生特徴量は個別に確認できる状態を保ちます。列数が偶然同じという理由だけで、古い特徴量ベクトルを再利用しません。
4. 許可リストへ登録した実行環境とモデルアダプター、またはタスク専用の実行環境を追加します。新しいタスクID、入力契約ダイジェスト、プロファイルダイジェスト、特徴量パイプラインのバージョン、ソースダイジェスト、出力対象をmanifestへ記録したModel Packageを構築します。
5. 対象を絞った契約テスト、特徴量ゴールデン、ソース事前検証、Packageスモーク、APIまたはE2Eテストを1本追加します。すべて通過した後で、そのタスクのPackageを `models/active-packages.json` に追加します。既存のv2とv3のPackageは変更しません。

現在のアプリには、本番用TaskModuleが3つあります。
新しいタスクでは、`task_modules.py` の許可リストへ明示的なエントリーを一つ追加します。
起動、Package検証、モデル処理、ソースとプロファイルの選択、能力宣言、生成済みインベントリは、すべてこのエントリーから解決します。
プロファイルとTaskDefinitionだけを追加して、未対応タスクを実行可能に見せてはいけません。

## 再現可能なソース事前検証

実行環境やデータベースを変更する前に、次のコマンドを実行します。
このコマンドは元ワークブックへ書き込みません。

```powershell
uv run python backend/scripts/verify_dataset_source.py data/source/process_dashboard_realistic_excel_v3.xlsx
uv run python backend/scripts/verify_dataset_source.py path/to/new-source.xlsx --profile backend/src/material_workbench/dataset-input-profile-new.json --json
```

### Profile Workbench

新しいExcelのシート・列と、選択されたProfileでの正規化結果をまとめて確認できます。

```powershell
uv run python backend/scripts/profile_workbench.py inspect path/to/new-source.xlsx
uv run python backend/scripts/profile_workbench.py inspect path/to/new-source.xlsx --profile backend/src/material_workbench/dataset-input-profile-new.json
uv run python backend/scripts/profile_workbench.py validate path/to/new-source.xlsx --profile backend/src/material_workbench/dataset-input-profile-new.json
```

Profileが確定したら、元Excelを変更せずmanaged libraryへ内容ハッシュ単位でコピーし、Data Asset、Profile Revision、Dataset Revision、単一Dataset Viewをまとめて登録します。

```powershell
uv run python backend/scripts/profile_workbench.py register path/to/new-source.xlsx `
  --profile backend/src/material_workbench/dataset-input-profile-new.json `
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
