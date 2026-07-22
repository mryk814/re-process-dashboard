# Material Decision Workbench

材料組成と焼鈍ヒートパターンの候補を比較し、予測特性・予測幅・学習範囲・類似する過去実験を同じ判断面で確認するローカルアプリです。

## 開発起動

```powershell
uv sync --extra dev
npm install
npm run dev
```

- Web UI: <http://127.0.0.1:5180>
- API: <http://127.0.0.1:8765/docs>

停止は起動したターミナルで `Ctrl+C` です。

## デスクトップアプリとして起動

初回だけ依存関係を準備します。

```powershell
uv sync --extra dev
npm install
npm run build
```

その後は次でElectronアプリを起動できます。Python APIはElectronが同時に起動・終了します。

```powershell
npm run dev:desktop
```

終了はアプリのウィンドウを閉じます。Electronは起動ごとに空きloopback portとlaunch tokenを作り、同時起動したAPIだけへ接続します。

自己完結型のユーザー単位installerとフォルダZIPは `npm run package:windows` で生成します。
Pythonやuvを必要としない配布物、保存先、削除方法、配布版のスモーク確認は [Windows配布](docs/windows-distribution.md) を参照してください。

## 確認

実装中は変更箇所のテストと型だけを確認します。
テストパスや`-k`式を`--`以降へ渡せるため、全テストは実行しません。
Windowsでは`npm.cmd`を使えます。

```powershell
npm.cmd run verify:focused -- backend/tests/test_screening_score.py
```

PRをレビュー可能にする直前とマージ前だけ、全体検証を1回実行します。
pytest、型検査、build、作業ツリー、`origin/main...HEAD` の差分検査を順に実行します。

```powershell
npm run verify:full
```

GitHubのPRと`main`へのpushでも同じ全体検証が自動実行されます。
CIはNode `22.20.0`、npm `11.4.2`、uv `0.9.15`を固定し、`package-lock.json`と`uv.lock`から依存関係を導入します。
ブラウザ確認、配布版デスクトップ、実データベース移行などは変更リスクに応じて手動実施し、PR本文へ結果を記録します。

モデルPackageを更新した場合は、`npm run models:build:annealed`、`npm run models:build:hot-rolling`、`npm run models:build:flank-wear` の対応するコマンドで、artifact、品質レポート、manifestを必ず同時に再生成します。
新しいPackageの作成、検証、使用対象への切替、ロールバックは [モデルPackageのライフサイクル](docs/model-package-lifecycle.md) の手順を使います。
現行3タスクのソース、プロファイル、推論環境、能力は [生成済みタスク一覧](docs/task-inventory.json) で確認できます。
`npm run task:inventory:check` は実装とのずれを検出します。

### フロントエンドAPI契約

FastAPIのOpenAPIを正本として、`apps/web/src/generated/` のschemaとTypeScript型を生成します。生成物は手編集しません。

```powershell
npm run api:generate  # backendの契約変更後
npm run api:check     # schema・生成型のdrift検出
```

`npm run typecheck` はdrift checkも含みます。production UIのHTTPアクセスは `apps/web/src/shared/api/workbench-api.ts` を経由します。

## データ

`data/source/` のExcelは読取専用の正本として扱います。
現在はv2（`process_dashboard_realistic_excel_v2.xlsx`）、別名の列と未加工の履歴を持つv3（`process_dashboard_realistic_excel_v3.xlsx`）、具体的な2設備の工程名を持つv5（`process_dashboard_two_equipment_v5.xlsx`）を併存させています。
起動時にシート構成とソースマーカーから対応するDataset Input Profileを選び、工程、観測、系譜、データ品質を構築します。
元Excelは変更しません。

Excelの外部シートや列と、アプリ内部の意味との対応はDataset Input Profileで一元管理します。
契約とデータ差替え手順は [データセット入力プロファイル](docs/dataset-input-profile.md) を参照してください。

新しいソースを追加したときは、まず次のコマンドでプロファイル選択、列契約、単位、観測親、学習対象としての適格性、件数を確認します。

```powershell
uv run python backend/scripts/verify_dataset_source.py data/source/process_dashboard_realistic_excel_v3.xlsx --json
uv run python backend/scripts/verify_dataset_source.py data/source/process_dashboard_two_equipment_v5.xlsx --json
uv run python backend/scripts/verify_dataset_source.py data/source/process_dashboard_two_equipment_v7.xlsx --json
```

次は、v3用Packageを既存のv2 Packageへ上書きせずに作る例です。
Packageはソースダイジェストとプロファイルダイジェストに結び付くため、ソースを替えたら必ず再生成します。

```powershell
uv run python backend/scripts/build_default_model_package.py --source data/source/process_dashboard_realistic_excel_v3.xlsx --output output/v3-model-packages/annealed-gp-2026-07 --replace
uv run --extra runtime-numpyro python backend/scripts/build_hot_rolling_model_package.py --source data/source/process_dashboard_realistic_excel_v3.xlsx --output output/v3-model-packages/hot-rolled-horseshoe-2026-07 --replace
```

v5用Packageは既存Packageと併存させます。現在の検証済みPackageは次の場所にあります。

```powershell
uv run python backend/scripts/build_default_model_package.py --source data/source/process_dashboard_two_equipment_v5.xlsx --output models/packages/annealed-gp-2026-07-v5 --replace
uv run --extra runtime-numpyro python backend/scripts/build_hot_rolling_model_package.py --source data/source/process_dashboard_two_equipment_v5.xlsx --output models/packages/hot-rolled-horseshoe-2026-07-v5 --replace
```

v5を起動確認するときは、ソースと2つのPackageを同じフローで指定します。
既定の使用Packageは変更しません。

```powershell
$env:WORKBENCH_SOURCE_PATH = "data/source/process_dashboard_two_equipment_v5.xlsx"
$env:MATERIAL_WORKBENCH_MODEL_PACKAGE = "models/packages/annealed-gp-2026-07-v5"
$env:MATERIAL_WORKBENCH_HOT_ROLLING_MODEL_PACKAGE = "models/packages/hot-rolled-horseshoe-2026-07-v5"
npm run dev
```

一時Packageでv3を起動確認する場合は、上記2つの絶対パスを `MATERIAL_WORKBENCH_MODEL_PACKAGE` と `MATERIAL_WORKBENCH_HOT_ROLLING_MODEL_PACKAGE` に指定します。
新しい説明変数や目的変数を追加する手順は、[データセット入力プロファイル](docs/dataset-input-profile.md)の「説明変数や目的変数が異なるデータを追加する」を参照してください。

v7は名称差、目的変数の部分欠損、relation経由の観測親、詳細な焼鈍履歴をProfileで正規化します。検証済みのv7 sourceと両Packageをまとめて起動する場合は次だけでよいです。

```powershell
npm run dev:v7
```

個別に再学習する場合はPackage IDもv7固有にします。

```powershell
uv run python backend/scripts/build_default_model_package.py --source data/source/process_dashboard_two_equipment_v7.xlsx --output models/packages/annealed-gp-2026-07-v7 --package-id annealed-gp-2026-07-v7 --replace
uv run --extra runtime-numpyro python backend/scripts/build_hot_rolling_model_package.py --source data/source/process_dashboard_two_equipment_v7.xlsx --output models/packages/hot-rolled-horseshoe-2026-07-v7 --package-id hot-rolled-horseshoe-2026-07-v7 --replace
```

候補・プロジェクト・予測スナップショット・実測値は `data/workbench.db` に保存します。候補一覧は画面からXLSXで入出力でき、ヒートパターンも往復保持されます。

## モデルPackage

既定の学習済みPackageは `models/packages/annealed-gp-2026-07` です。ガウス過程回帰が90%予測区間を返し、モデル由来の不確かさと反復測定由来のばらつきを分けて表示します。予測時にmanifest・artifact hash・特徴量順序・smoke inputを検証し、画面の「プロジェクト」で有効なPackageとruntimeを確認できます。

熱延タブは独立した `hot-rolled-properties-v1` タスクで、`models/packages/hot-rolled-horseshoe-2026-07` の正則化Horseshoe回帰を使用します。熱延v1は設備・試験片方向を推定条件として区別せず、利用可能な熱延引張観測をまとめて学習し、物理範囲外の観測だけを除外します。事後係数の縮小結果はPackage内の `reports/selection-report.json`、学習健全性は `reports/training-diagnostics.json` に保存します。

既定で使用するPackageは、`models/active-packages.json` でタスクごとに固定します。
開発中に検証済みPackageを一時的に試す場合だけ、信頼できるローカルPackageの絶対パスを指定します。

```powershell
$env:MATERIAL_WORKBENCH_MODEL_PACKAGE = "C:\models\annealed-bnn"
$env:MATERIAL_WORKBENCH_HOT_ROLLING_MODEL_PACKAGE = "C:\models\hot-rolling-gp"
npm run dev
```

同じ契約で `sklearn/skops`、LightGBM native Booster、GPyTorch static RBF、NumPyro BNN posteriorを利用できます。
任意導入の推論環境をまとめて検証する場合は、次を実行します。

```powershell
uv sync --extra dev --extra runtime-sklearn --extra runtime-lightgbm --extra runtime-gpytorch
uv run pytest backend/tests/test_optional_adapters.py
```

NumPyroのNormal、Student-t、LogNormal、Bernoulli、Poisson、Negative Binomial、zero-inflated Poisson、ordinal logitの8つの実Package例は `examples/model-packages/numpyro` にあります。新しいモデルは [I/O契約別のModel Runtime事例索引](docs/model-runtime-examples/index.md) から最も近い経路を選びます。契約と安全境界は `docs/model-package-contract.md`、冶金・ヒートパターン特徴は `docs/feature-engineering.md` を参照してください。
