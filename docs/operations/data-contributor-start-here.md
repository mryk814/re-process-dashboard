# 自分のデータで使い始める

この文書は、既存の予測タスクへ手元のExcel／CSVを接続し、探索または学習してアプリで使う人の入口です。
アプリ本体を開発するための手順ではありません。

## この入口で扱う作業

次の条件を満たす作業は、**データ利用レーン**です。

- 既存のPrediction Taskと同じ入力、出力、学習単位を使う
- 既存のProfile familyでシート、列名、単位表記を対応付けられる
- 既存のModel Runtimeとadapterで学習できる
- 手元のデータをData Libraryへ登録し、Projectで探索する
- 手元のデータからModel Packageを作り、Projectで予測する

データ利用レーンでは、新しいunit testやE2Eを書く必要はありません。
GitHub Issue、branch、PR、commit、アプリ全体の検証gateも、データを利用するだけなら不要です。
ProfileとModel Packageの検証は、リポジトリに用意されたコマンドが担当します。

## 最初に目的を選ぶ

| 目的 | 作業 | Model Package |
| --- | --- | --- |
| 過去データを探索し、類似実績として参照する | Data LibraryへDatasetを登録する | 不要 |
| 既存モデルと新しい参照データを使う | Datasetを登録し、既存Packageと組み合わせた新Projectを作る | 不要 |
| 手元のデータでモデルを学習して予測する | Datasetを確認し、既存Task向けPackageを作る | 必要 |
| 新しい入力や目的変数を追加する | アプリの契約を変更する | [Developer Start Here](../developer-start-here.md)へ移る |

既存Taskへ同じ意味のデータを差し替えられるか判断できない場合は、ファイルを変更しない診断を実行します。

```powershell
npm run model:diagnose -- --source C:\path\to\data.xlsx
```

診断が`existing_task_replacement`を示した場合は、この文書の範囲で進められます。
`new_task_or_profile`を示した場合でも、列名やシート構造だけの違いなら、探索用Datasetは既存Profile schema内のmappingで登録できます。
そのmappingを使ったモデル学習は現在のCLIでは未接続です。
入力の意味、目的変数、学習単位、relationの意味が違う場合はアプリ開発です。

## ExcelをDatasetとして登録して探索する

初回だけ依存関係を準備し、アプリを起動します。

```powershell
uv sync --extra dev
npm install
npm run dev
```

画面で「データライブラリ」から「新しいDatasetを準備」を開き、次の順に進めます。

1. Excelを選ぶ
2. 既存Profile候補を選ぶ
3. 構造差分とcanonical previewを確認する
4. Data Libraryへ登録する
5. 登録したDatasetからProjectを作る

参照・探索用Datasetの登録だけなら、モデルを再学習する必要はありません。
登録後にアプリを再起動する必要もありません。
新しいDatasetを登録しても、既存Projectが固定するDataset、Package、保存済みSnapshotは変わりません。
登録したDatasetを使う新Projectを作るか、Project設定から明示的に参照を切り替えます。

元ファイルは`data/source/`へ置かなくてもかまいません。
任意のローカルパスから選択でき、登録時に現在のWorkspaceが管理するData Libraryへ内容ハッシュ付きでコピーされます。
製品へ同梱する意図がないファイルをgitへ追加しないでください。

現在の画面経路が受け付けるのは`.xlsx`です。
CSVは画面ではなく、対応する表形式Profileを指定してCLIから検証・登録します。

Profileを自分で用意する場合は、元ファイルを変更せず、リポジトリ外の任意のパスに置いたProfileをCLIで検査、登録できます。

```powershell
uv run python backend/scripts/profile_workbench.py inspect C:\path\to\data.xlsx `
  --profile C:\path\to\profile.json

uv run python backend/scripts/profile_workbench.py validate C:\path\to\data.xlsx `
  --profile C:\path\to\profile.json

uv run python backend/scripts/profile_workbench.py register C:\path\to\data.csv `
  --profile C:\path\to\profile.json `
  --database C:\path\to\workspace.db `
  --library C:\path\to\data-library
```

Profileの書式と登録CLIの詳細は[Dataset Input Profile](dataset-input-profile.md)を参照してください。
同じProfileパスを`model:diagnose`、`model:build`、`model:verify`、
`model:promote`へ渡すと、探索と学習で列の解釈が分岐しません。
Package provenanceには実効Profileのdigestが固定されます。
追跡済みProfileを編集して同梱する場合は、データ利用ではなくアプリ開発として扱います。

## 既存Task向けのモデルを学習する

既存Taskと同じ入力、出力、学習単位であり、そのTaskに登録済みのProfileで読み取れることを`model:diagnose`で確認してからPackageを作ります。

```powershell
$task = "heat-treatment-tradeoff-v1"
$source = "C:\path\to\data.csv"
$profile = "C:\path\to\profile.json"
$packageId = "heat-treatment-my-data-2026-07"
$datasetOutput = "artifacts/model-data/$packageId.json"

npm run model:diagnose -- --task $task --source $source --profile $profile

npm run model:estimators -- --task $task

npm run model:build -- `
  --task $task `
  --source $source `
  --profile $profile `
  --estimator ridge.v1 `
  --package-id $packageId `
  --package-version 1.0.0 `
  --dataset-output $datasetOutput
```

`model:build`は学習だけでなく、manifest、provenance、特徴量順序、artifact、smoke predictionを既存の製品ローダーで検証します。
`model:estimators`に候補があるTaskは、Estimator名を替えてもTask用builderファイルを
追加する必要はありません。
一覧にないEstimatorは、予測分布や不確かさの契約を満たさないため拒否されます。
データ利用者が同じ検証を新しいテストとして書き直す必要はありません。

Packageを使うProjectを作成した後は、データライブラリで対象モデルの
「学習データの採否を見る」を開けます。
画面では、元データ、目的変数ごとの採用データ、Feature Pipelineと反復集約を通った
実際のモデル入力を同じ行数フローで確認できます。
「思ったよりデータを使っていない」と感じたときは、先にここで不採用理由と
モデル入力行数を確認してください。

Packageをアプリの選択肢へ追加する手順と、起動中のアプリへ再読込する方法は[Model Packageのライフサイクル](model-package-lifecycle.md#fresh-cloneから新しいデータを使うgolden-path)を参照してください。
現在の`model:promote`はリポジトリ内の`models/packages/`とPackage一覧を更新します。
製品へ同梱しない個人用Packageも、現状ではworking tree内に置かれます。
自動的にcommitせず、clean、branch切替、削除の対象を確認する前に`git status`で保護してください。
リポジトリ外のtrusted storeはIssue #490で追跡しています。

## このレーンで行う確認

| 作業 | 必須の確認 | 不要な確認 |
| --- | --- | --- |
| Dataset登録 | Profile validate、canonical preview、行数と除外理由、Projectでの表示 | unit test、Playwright、`verify:pr` |
| 既存Taskでの学習 | `model:diagnose`、`model:build`のPackage検証、品質レポート | 新しいmodel contract test |
| アプリでの利用 | Package再読込、Project作成、代表候補の予測smoke | アプリ全体のE2E |
| 個人利用 | source／Packageが意図せずgit対象になっていないこと | Issue、PR、release acceptance |

検証失敗を回避するためにテストを弱めたり、元データを書き換えたりしないでください。
失敗は、データの意味、Profile mapping、Task契約、Package provenanceのどこが一致しないかを示しています。

## アプリ開発へ移る境界

次の変更が必要になった時点で、作業は**アプリ開発レーン**へ移ります。

- 既存Taskにない入力または出力を追加する
- canonical quantityまたはcanonical unitを変える
- 工程条件、反復観測、relation、学習一行の意味を変える
- ProfileのJSON mappingでは表現できず、parserまたはschemaを変える
- 新しいModel Runtimeまたはartifact adapterを追加する
- API、UI、保存形式、migrationを変える
- 既存コマンドの不具合を修正する

この場合は[Developer Start Here](../developer-start-here.md)で変更範囲と検証を決めます。
データ利用の途中で見つけたアプリの不具合だけをIssueへ分け、データ追加そのものへアプリ開発の責任を広げません。
