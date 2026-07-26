# Material Decision Workbench

材料組成、工程条件、切削条件、疎な原料配合の候補を比較し、予測特性、予測幅、学習範囲、類似する過去実験、実測とのずれを同じ判断面で確認するローカルアプリです。

一つのPrediction Taskを扱うProjectに加え、再利用可能なTask／決定論的transformをbindingした多段Chain Projectを扱います。Chainでは段別実行、変更段以降だけの再計算、段単体／通し評価、中間実測variant、明示的な不確かさ伝播を利用できます。

変更箇所や再生成物を判断するときは [Developer Start Here](docs/developer-start-here.md) から始めてください。現在のProject mode、再利用境界、v1固有前提は [現行システム基準](docs/current-system-baseline.md)、個別文書は [ドキュメント索引](docs/README.md) から参照できます。

## Project mode

- **single-task**：Dataset View、Task contract、Model Packageを固定し、候補比較、予測、応答曲線、Snapshot、実測照合、検討アクティビティを行います。
- **chain**：Chain Revisionを固定し、順序付きStageとbindingを段別に実行します。現在のproduction縦切りは、疎な原料配合から材料成分、溶着金属成分、特性へ進む溶接材料A→B→Cです。

現在のTask、source、Profile、active Package、runtime／application capabilityは [生成済みTask inventory](docs/task-inventory.json) を正本とします。READMEへ件数や全Task一覧を手書きで複製しません。

## 開発起動

```powershell
uv sync --extra dev
npm install
npm run dev
```

- Web UI: <http://127.0.0.1:5180>
- API docs（dev proxy経由）: <http://127.0.0.1:5180/docs>

停止は起動したターミナルで `Ctrl+C` です。
既定portが使用中なら、`WORKBENCH_DEV_API_PORT`と`WORKBENCH_DEV_WEB_PORT`で変更できます。

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

GitHubのPRと`main`へのpushでも同じ全体検証を実行します。Actionsが利用できない場合は、ローカルのfull gateと変更リスクに応じたbrowser／packaged smokeをPR本文へ記録します。
CIはNode `22.20.0`、npm `11.4.2`、uv `0.9.15`を固定し、`package-lock.json`と`uv.lock`から依存関係を導入します。

モデルPackageを更新した場合は、対象Taskのbuilderでartifact、品質レポート、manifestを必ず同時に再生成します。新しいPackageの作成、検証、使用対象への切替、ロールバックは [モデルPackageのライフサイクル](docs/model-package-lifecycle.md) の手順を使います。

`npm run task:inventory:check` はTask登録、source／Profile、active Package、capabilityのdriftを検出します。

### フロントエンドAPI契約

FastAPIのOpenAPIを正本として、`apps/web/src/generated/` のschemaとTypeScript型を生成します。生成物は手編集しません。

```powershell
npm run api:generate  # backendの契約変更後
npm run api:check     # schema・生成型のdrift検出
```

`npm run typecheck` はdrift checkも含みます。production UIのHTTPアクセスは `apps/web/src/shared/api/workbench-api.ts` を経由します。

## データ

`data/source/` のExcelとCSVは読取専用の正本として扱います。
同梱するsourceと、それを使うTask、Profile、active Packageの対応は [生成済みTask inventory](docs/task-inventory.json) で確認できます。

外部シート、列、単位、entity、relation、観測familyとアプリ内部の意味との対応は、データ形状に応じたDataset Profileで管理します。Profile schemaを万能な一種類へ押し込まず、すべての派生学習行でtarget eligibility、split group、provenance、除外理由を保持します。

最小教材を使ってExcelからModel Packageまで追う場合は [開発者向け教材ガイド](docs/tutorial-data-pipeline.md) を参照してください。新しいsourceの構造と契約を確認する場合は、対応するProfile Workbench／verification commandを使います。

```powershell
uv run python backend/scripts/verify_dataset_source.py path/to/new-source.xlsx --json
```

候補、Candidate Revision、Project、Prediction Snapshot、Screening Run、Decision Activity Run、実測、Chain実行・Snapshot・不確かさRunは `data/workbench.db` に保存します。保存済み結果を新しいsourceやPackageで自動再計算しません。

対応Taskでは候補一覧を画面からXLSXで入出力でき、焼鈍特性ではヒートパターンも往復保持されます。Chainの疎配合候補は、Projectが固定した科学master、商用catalog、Design Spaceと照合して保存します。

## モデルPackage

Model Packageはdata-onlyであり、allow-list済みadapterだけが読み込みます。予測時にmanifest、artifact hash、Task／Feature Pipeline契約、smoke inputを検証します。

既定で使用するPackageは、`models/active-packages.json` でTaskごとに固定します。active Packageは新規Projectの既定候補であり、既存Projectが固定したPackageへ暗黙fallbackしません。

開発中に検証済みPackageを一時的に試す場合だけ、信頼できるローカルPackageの絶対パスをTask対応のenvironment variableへ指定します。

同じ外部Predictive Summary契約で、線形モデル、LightGBM、exact GP、posterior linear、静的確率モデルなどのallow-list済みruntimeを利用できます。新しいモデルは [I/O契約別のModel Runtime事例索引](docs/model-runtime-examples/index.md) から近い経路を選びます。

契約と安全境界は [Model Package契約](docs/model-package-contract.md)、特徴量は [Feature Engineering](docs/feature-engineering.md)、Chainは [Chain実行](docs/chain-execution.md) と [多段Chain ADR](docs/decisions/multistage-chain-architecture.md) を参照してください。

## 現在の拡張境界

現在の実装は、同じ意味・同じ構造のデータ差し替え、新しい標準Tabular Task、Model Package差し替えには強い一方、画像、一般的な可変長系列、新しいCandidate Shape、溶接以外のChain、新しいDecision Activityには明示的な型付き拡張が必要です。

任意pluginや任意JSONで柔軟性を得るのではなく、科学的意味と履歴を厳格に保ったまま、二つ目の異なるユースケースで共通境界を反証します。詳細は [現行システム基準](docs/current-system-baseline.md) を参照してください。
