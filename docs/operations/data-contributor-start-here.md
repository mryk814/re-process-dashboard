# 自分のデータで使い始める

この文書は、既存の予測タスクへ手元のExcel／CSVを接続し、探索または学習してアプリで使う人の入口です。
アプリ本体を開発するための手順ではありません。
AIにread-onlyの仕分けからDataset登録、個人Package、Project作成まで任せる場合は
[Data Contributor Skill](../../.claude/skills/data-contributor/SKILL.md)を使います。

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
| 新しい参照データを既存モデルの判断材料にする | Datasetを登録し、Data Library／Data Explorerで別の根拠として参照する | 不要 |
| 手元のデータでモデルを学習して予測する | Datasetを確認し、既存Task向けPackageを作る | 必要 |
| 新しい入力や目的変数を追加する | アプリの契約を変更する | [Developer Start Here](../developer-start-here.md)へ移る |

既存Taskへ同じ意味のデータを差し替えられるか判断できない場合は、ファイルを変更しない診断を実行します。

```powershell
npm run model:diagnose -- --source C:\path\to\data.xlsx
```

診断が`existing_task_replacement`を示した場合は、この文書の範囲で進められます。
`new_task_or_profile`を示した場合でも、列名やシート構造だけの違いなら、探索用Datasetは既存Profile schema内のmappingで登録できます。
Profile Workbenchで保存した同じProfileを、Data Libraryのモデル更新手順が学習まで引き継ぎます。
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
2. 既存Taskの意味に合うBase Profileを選ぶ
3. 未知のシート名・列名があれば、Excel側の名前を明示的に対応付ける
4. 保存したProfileでcanonical previewを再確認する
5. Data Libraryへ登録する
6. Projectで予測する場合だけ、同じsource／ProfileからModel Packageを作ってからProjectを作る

提案された対応は自動確定されません。
シート、キー、値、単位、relation roleを確認して選んだものだけがProfileへ保存されます。
列名に`[K]`や`[MPa]`のような単位があれば画面が検出し、canonical unitへ変換できる組合せだけを受け付けます。
列名に単位がなければ、対応表でExcel側の単位を明示的に選びます。
未解決の必須項目がある間はDataset登録へ進まず、元Excelも書き換えません。
保存先はリポジトリ外の`%LOCALAPPDATA%\Material Decision Workbench\profiles`で、
`WORKBENCH_PROFILE_STORE_PATH`を設定すると変更できます。
画面の「JSONを出力」は、継承を解決したstandalone Profileを製品へ採用する開発者向け経路です。

参照・探索用Datasetの登録だけなら、モデルを再学習する必要はありません。
登録後にアプリを再起動する必要もありません。
新しいDatasetを登録しても、既存Projectが固定するDataset、Package、保存済みSnapshotは変わりません。
source SHA-256とProfile digestが一致しない既存Packageを、新しいDatasetへ組み合わせることはできません。
登録したDatasetをProjectで予測に使う場合は、同じsource／Profileから新Packageを作り、
そのDataset RevisionとPackageを明示的に選んで新Projectを作ります。

元ファイルは`data/source/`へ置かなくてもかまいません。
任意のローカルパスから選択でき、登録時に現在のWorkspaceが管理するData Libraryへ内容ハッシュ付きでコピーされます。
製品へ同梱する意図がないファイルをgitへ追加しないでください。

現在の画面経路が受け付けるのは`.xlsx`です。
CSVは画面ではなく、対応する表形式Profileを指定してCLIから検証・登録します。

Profileを自分で用意する場合も、元ファイルを変更せず、リポジトリ外の任意のパスに置いたProfileをCLIで検査、登録できます。

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
`model:promote`は検証済みPackageをリポジトリ外の個人用Model Storeへ昇格します。
Windowsの既定保存先は`%LOCALAPPDATA%\Material Decision Workbench\models`です。
保存先は`WORKBENCH_MODEL_STORE_PATH`または`model:promote -- --store <path>`で変更できます。
起動中のアプリで同じ保存先を再読込する場合は、アプリ起動前にも同じ`WORKBENCH_MODEL_STORE_PATH`を設定します。

昇格後はData Libraryで「個人モデルを再読込」を実行します。
同梱Packageと個人Packageが区別して表示されるため、登録したDatasetと組み合わせて新しいProjectを作るか、既存Projectの設定で明示的に選択します。
個人利用の通常経路は`models/packages/`やPackage設定を変更せず、git working treeを汚しません。
`model:activate`は製品へ同梱する既定Packageを切り替えるアプリ開発者向けコマンドです。

## このレーンで行う確認

| 作業 | 必須の確認 | 不要な確認 |
| --- | --- | --- |
| Dataset登録 | Profile validate、canonical preview、行数と除外理由、Projectでの表示 | unit test、Playwright、`verify:pr` |
| 既存Taskでの学習 | `model:diagnose`、`model:build`のPackage検証、品質レポート | 新しいmodel contract test |
| アプリでの利用 | Package再読込、Project作成、代表候補の予測smoke | アプリ全体のE2E |
| 個人利用 | 個人用Model Storeへの昇格、Data Library再読込、Projectでの明示選択 | Issue、PR、release acceptance |

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
