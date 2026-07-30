---
name: data-contributor
description: 新しいExcel／CSVをMaterial Decision Workbenchの既存Prediction Taskへ接続する実務を支援する。既存データの更新版、列名やシート名だけが異なる類似データ、完全に新しい予測問題をread-onlyで仕分け、個人用Profile、Dataset登録、Model Package構築、再読込、Project作成まで進めるときに使う。
---

# Data Contributor

[自分のデータで使い始める](../../../docs/operations/data-contributor-start-here.md)を正本として読み、アプリコードではなく手元のデータを扱う。

## 1. 変更せずに仕分ける

元Excel／CSVを移動、修正、登録、学習する前に、用途と既存契約への適合を確認する。
既存Taskは[Task inventory](../../../docs/contracts/task-inventory.json)で確認する。

```powershell
$source = "C:\path\to\data.xlsx"
$task = "<existing-task-id>"
$profile = "C:\path\to\personal-profile.json"

npm run dev:doctor -- --source $source
uv run python backend/scripts/profile_workbench.py inspect $source
npm run model:diagnose -- --task $task --source $source --profile $profile
```

`.xlsx`では3コマンドを使う。
CSVでは対応する表形式Profileを先に指定し、`model:diagnose`だけを使う。
Profileがまだ無い場合は`--profile $profile`を外してdiagnoseする。
`profile_workbench.py inspect`とProfile Workbench画面での保存前inspectionは元データもWorkspaceも変更しない。

次の三つへ仕分ける。

1. **existing replacement**: 入力、出力、単位、学習一行、relationの意味が同じで、`model:diagnose`が`existing_task_replacement`を返す。既存Taskのまま進む。
2. **profile mapping**: 意味は同じで、シート名、列名、単位表記、任意列だけが違う。Profile Workbenchで既存Base Profileへの対応付けを作る。提案を自動確定せず、シート、キー、値、単位、relation roleを確認する。保存した個人Profileを指定して`model:diagnose`を再実行し、`existing_task_replacement`になるまで学習へ進まない。
3. **new task**: 既存Taskにない入力／出力、canonical quantity、学習一行、relationの意味、Feature Pipeline、Runtimeが必要になる。[Developer Start Here](../../../docs/developer-start-here.md)と`$add-prediction-task`へ移り、データ利用レーンを終了する。

列名が似ているだけで意味の一致を決めない。
判断できない物理量、目的変数、反復集約、relationは人へ確認し、推測でProfileへ押し込まない。

## 2. Datasetを登録する

`.xlsx`はアプリを起動し、「データライブラリ」→「新しいDatasetを準備」から次の順に進める。

1. 元Excelを選ぶ。
2. 意味が一致するBase Profileを選ぶ。
3. 未解決のシートと列を明示的に対応付ける。
4. 個人Profileを保存し、そのProfileでcanonical previewを再確認する。
5. Datasetを登録する。
6. 登録結果のsource SHA-256、Profile digest／locator、Dataset Revisionを控える。

個人Profileは既定の
`%LOCALAPPDATA%\Material Decision Workbench\profiles`
または`WORKBENCH_PROFILE_STORE_PATH`で指定したリポジトリ外へ保存する。
CSVとCLI登録は[Dataset Input Profile](../../../docs/operations/dataset-input-profile.md#profile-workbench)の現行コマンドを使う。

参照・探索だけが目的なら、Data Libraryへ登録したDatasetを確認してここで止める。
新しいDatasetは、source SHA-256とProfile digestが一致しない既存Packageとは組み合わせられない。
Projectで予測する場合は、次の手順でそのDatasetと同じsource／Profileから新Packageを作る。
アプリの再起動と既存Projectの変更は行わない。

## 3. 同じProfileでPackageを作る

登録結果またはData Libraryのモデル更新手順に表示されたProfile locatorを、そのまま`$profile`へ入れる。
diagnose、build、verify、promoteの全工程で同じ`$source`と`$profile`を渡す。

```powershell
$task = "<existing-task-id>"
$source = "C:\path\to\data.xlsx"
$profile = "C:\path\to\saved-personal-profile.json"
$packageId = "<new-immutable-package-id>"
$packageVersion = "1.0.0"
$package = "artifacts/model-package-candidates/$packageId"

uv run python backend/scripts/profile_workbench.py validate $source --profile $profile
npm run model:diagnose -- --task $task --source $source --profile $profile
npm run model:estimators -- --task $task

npm run model:build -- `
  --task $task `
  --source $source `
  --profile $profile `
  --estimator <supported-estimator-id> `
  --package-id $packageId `
  --package-version $packageVersion

npm run model:verify -- `
  --task $task `
  --source $source `
  --profile $profile `
  --package $package

npm run model:promote -- `
  --task $task `
  --source $source `
  --profile $profile `
  --package $package
```

`model:estimators`に標準Estimatorが無いTaskでは、`--estimator`を省略して現行Task固有workflowを使う。
必要なTask別の前提は
[Model Packageのライフサイクル](../../../docs/operations/model-package-lifecycle.md#fresh-cloneから新しいデータを使うgolden-path)
で確認する。
同じPackage IDを上書きせず、内容が変わるたびに新しいIDを使う。

## 4. 再読込してProjectでsmokeする

Data Libraryで「個人モデルを再読込」を実行する。
アプリを再起動せず、登録済みDatasetと昇格した個人Packageを選んで新しいProjectを作る。
Projectで代表候補を一つ予測し、Dataset Revision、Package ID/version、Profile digest、予測値、支持範囲の表示を確認する。

既存Project、active Package、保存済みSnapshotを暗黙に切り替えたり再計算したりしない。

## 5. 失敗を分類する

回避策を実装する前に、最初に失敗したコマンド、入力パス、Task ID、Profile locator、エラー全文を残し、次へ分類する。

- **data**: ファイル破損、必須値欠損、単位不明、キー重複、対象行ゼロなど、Profileを正しく適用しても入力内容が契約を満たさない。元データの管理者へ返し、修正版を別revisionとして扱う。
- **profile**: シート／列／単位／key／relation roleの対応、選択Profile、Task宣言が違う。個人Profileだけを直し、validateとdiagnoseを再実行する。
- **tooling**: `uv`、Node、依存関係、権限、ポート、環境変数、Model Storeの到達性で処理前に失敗する。`uv sync --extra dev`、`npm install`、`npm run dev:doctor`で環境を切り分ける。
- **app bug**: 入力とProfileが契約を満たし、環境も利用可能なのに、既存コマンドや画面が未捕捉例外、誤判定、不整合な結果を再現する。または同じsource／Profileで先行検証が成功するのに、後続のDataset登録、Package再読込、Project作成、予測smokeが失敗する。再現手順と証拠をIssueへ分け、アプリ開発レーンへ移る。

意味や契約が違う場合は失敗扱いで回避せず、`new task`へ仕分け直す。
`dev:doctor`はsource診断が成功しても、依存関係など別の環境checkが失敗すれば終了code 1になる。
各checkの結果を読み、データ診断とtooling failureを混同しない。

## データ利用レーンを守る

- 元データ、個人Profile、個人Packageをgitへ追加しない。
- `data/source/`、追跡済みProfile、`models/packages/`、Package設定を変更しない。
- unit test、E2E、`verify:edit`、`verify:pr`、Issue、branch、PRをデータ追加の完了条件にしない。
- Profile validate、`model:diagnose`、Package build／verify、Project smokeだけを使う。
- `model:activate`を実行しない。
- 検証を通すために元データを上書きしたり、テストや契約を弱めたりしない。
