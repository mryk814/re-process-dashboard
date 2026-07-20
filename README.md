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

終了はアプリのウィンドウを閉じます。API用ポート `8765` が使用中の場合は、既存の開発サーバーを `Ctrl+C` で終了してから起動してください。

## 確認

実装中は変更箇所のtestと型だけを確認します。test pathや`-k`式を`--`以降へ渡せるため、全suiteは走りません。Windowsでは`npm.cmd`を使えます。

```powershell
npm.cmd run verify:focused -- backend/tests/test_screening_score.py
```

PRをreadyにする直前とmerge前だけ、full gateを1回通します。pytest、typecheck、build、working treeと`origin/main...HEAD`のdiff checkを順に実行します。

```powershell
npm run verify:full
```

GitHubのPRと`main`へのpushでも同じfull gateが自動実行されます。CIはNode `22.20.0`、npm `11.4.2`、uv `0.9.15`を固定し、`package-lock.json`と`uv.lock`から依存を導入します。browser確認、packaged desktop、実DB migrationなどは変更リスクに応じて手動実施し、PR本文へ結果を記録します。

モデルPackageを更新した場合は `npm run models:build:annealed` または `npm run models:build:hot-rolling` で、artifact・quality report・manifestを必ず同時に再生成します。新しいPackageの作成・検証・有効化・rollbackは [Model Package lifecycle](docs/model-package-lifecycle.md) の一本道を使います。

## データ

`data/source/process_dashboard_realistic_excel_v2.xlsx` を読取専用の正本として扱います。初回起動時に工程、観測、系譜、データ品質を構築します。元Excelは変更しません。

Excelの外部sheet・列とアプリ内部の意味の対応はDataset Input Profileで一元管理します。契約とデータ差替え手順は `docs/dataset-input-profile.md` を参照してください。

候補・プロジェクト・予測スナップショット・実測値は `data/workbench.db` に保存します。候補一覧は画面からXLSXで入出力でき、ヒートパターンも往復保持されます。

## モデルPackage

既定の学習済みPackageは `models/packages/annealed-gp-2026-07` です。ガウス過程回帰が90%予測区間を返し、モデル由来の不確かさと反復測定由来のばらつきを分けて表示します。予測時にmanifest・artifact hash・特徴量順序・smoke inputを検証し、画面の「プロジェクト」で有効なPackageとruntimeを確認できます。

熱延タブは独立した `hot-rolled-properties-v1` タスクで、`models/packages/hot-rolled-gp-2026-07` を使用します。熱延v1は `HR-LINE-1`・L方向引張を推定対象に固定し、物理範囲外の観測を学習から除外します。

既定のactive Packageは `models/active-packages.json` でタスクごとに固定します。開発中に検証済みPackageを一時的に試す場合だけ、信頼できるローカルPackageの絶対パスを指定します。

```powershell
$env:MATERIAL_WORKBENCH_MODEL_PACKAGE = "C:\models\annealed-bnn"
$env:MATERIAL_WORKBENCH_HOT_ROLLING_MODEL_PACKAGE = "C:\models\hot-rolling-gp"
npm run dev
```

同じ契約で `sklearn/skops`、LightGBM native Booster、GPyTorch static RBF、NumPyro BNN posteriorを利用できます。optional runtimeをまとめて検証する場合:

```powershell
uv sync --extra dev --extra runtime-sklearn --extra runtime-lightgbm --extra runtime-gpytorch
uv run pytest backend/tests/test_optional_adapters.py
```

NumPyroのNormal、Student-t、LogNormal、Bernoulli、Poisson、Negative Binomial、zero-inflated Poisson、ordinal logitの8つの実Package例は `examples/model-packages/numpyro` にあります。契約と安全境界は `docs/model-package-contract.md`、冶金・ヒートパターン特徴は `docs/feature-engineering.md` を参照してください。
