# Material Decision Workbench

材料組成、工程条件、切削条件の候補を比較し、予測特性、予測幅、学習範囲、類似する過去実験を同じ判断面で確認するローカルアプリです。

プロダクト、データ、モデル、実行、配布に関する文書は [ドキュメント索引](docs/README.md) から参照できます。

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
現在は次のソースを併存させています。

- v2：`process_dashboard_realistic_excel_v2.xlsx`
- v3：`process_dashboard_realistic_excel_v3.xlsx`
- v5：`process_dashboard_two_equipment_v5.xlsx`
- v7：`process_dashboard_two_equipment_v7.xlsx`
- 切削逃げ面摩耗：`cutting_tool_flank_wear_synthetic_dataset.xlsx`

v2、v3、v5、v7は焼鈍特性と熱延特性に使う同じタスク契約へ正規化します。
切削逃げ面摩耗は、独立したタスク契約、Dataset Input Profile、特徴量パイプラインを使います。
現行3タスクが実際に参照するソースとProfileは [生成済みタスク一覧](docs/task-inventory.json) で確認できます。

Excelの外部シートや列と、アプリ内部の意味との対応はDataset Input Profileで一元管理します。
起動時はソースの構造から対応するProfileを選び、工程、観測、系譜、データ品質を構築します。
契約とソース追加の手順は [データセット入力プロファイル](docs/dataset-input-profile.md) を参照してください。

焼鈍特性と熱延特性をv7データで起動する場合は、検証済みのソースとPackageをまとめて指定するスクリプトを使えます。

```powershell
npm run dev:v7
```

焼鈍特性と熱延特性へ新しいソースを追加するときは、アプリを起動する前に構造と契約を確認します。

```powershell
uv run python backend/scripts/verify_dataset_source.py path/to/new-source.xlsx --json
```

ソースに対応するPackageの作成、検証、切替は [モデルPackageのライフサイクル](docs/model-package-lifecycle.md) を参照してください。

候補・プロジェクト・予測スナップショット・実測値は `data/workbench.db` に保存します。対応タスクでは候補一覧を画面からXLSXで入出力でき、焼鈍特性ではヒートパターンも往復保持されます。

## モデルPackage

既定の学習済みPackageは `models/packages/annealed-gp-2026-07` です。ガウス過程回帰が90%予測区間を返し、モデル由来の不確かさと反復測定由来のばらつきを分けて表示します。予測時にmanifest・artifact hash・特徴量順序・smoke inputを検証し、画面の「プロジェクト」で有効なPackageとruntimeを確認できます。

熱延後特性は独立した `hot-rolled-properties-v1` タスクで、`models/packages/hot-rolled-horseshoe-2026-07` の正則化Horseshoe回帰を使用します。熱延v1は設備・試験片方向を推定条件として区別せず、利用可能な熱延引張観測をまとめて学習し、物理範囲外の観測だけを除外します。事後係数の縮小結果はPackage内の `reports/selection-report.json`、学習健全性は `reports/training-diagnostics.json` に保存します。

切削逃げ面摩耗は `flank-wear-v1` タスクで、`models/packages/flank-wear-gp-2026-07` のexact GPを使用します。切削距離に対する`VB_mean`と`VB_max`の応答曲線を、材料、工具、切削条件とともに比較します。

既定で使用するPackageは、`models/active-packages.json` でタスクごとに固定します。
開発中に検証済みPackageを一時的に試す場合だけ、信頼できるローカルPackageの絶対パスを指定します。

```powershell
$env:MATERIAL_WORKBENCH_MODEL_PACKAGE = "C:\models\annealed-bnn"
$env:MATERIAL_WORKBENCH_HOT_ROLLING_MODEL_PACKAGE = "C:\models\hot-rolling-gp"
$env:MATERIAL_WORKBENCH_FLANK_WEAR_MODEL_PACKAGE = "C:\models\flank-wear-gp"
npm run dev
```

同じ契約で `sklearn/skops`、LightGBM native Booster、GPyTorch static RBF、NumPyro BNN posteriorを利用できます。
任意導入の推論環境をまとめて検証する場合は、次を実行します。

```powershell
uv sync --extra dev --extra runtime-sklearn --extra runtime-lightgbm --extra runtime-gpytorch
uv run python -m pytest backend/tests/test_optional_adapters.py
```

NumPyroのNormal、Student-t、LogNormal、Bernoulli、Poisson、Negative Binomial、zero-inflated Poisson、ordinal logitの8つの実Package例は `examples/model-packages/numpyro` にあります。新しいモデルは [I/O契約別のModel Runtime事例索引](docs/model-runtime-examples/index.md) から最も近い経路を選びます。契約と安全境界は `docs/model-package-contract.md`、冶金・ヒートパターン特徴は `docs/feature-engineering.md` を参照してください。
