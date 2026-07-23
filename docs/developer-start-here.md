# Developer Start Here

この文書は「何を変更したいか」から、変更する契約、生成物、検証を決める入口です。実装一覧は [task-inventory.json](task-inventory.json)、個別契約は [データセット入力Profile](dataset-input-profile.md)、[特徴量](feature-engineering.md)、[Model Package](model-package-contract.md) を正本とします。

## 最初の判断表

| やりたいこと | 主に変える場所 | 原則変えない場所 | 必要な成果物 |
| --- | --- | --- | --- |
| Excelを参照・探索データとして追加 | Dataset登録 | TaskDefinition、Feature Pipeline、Package | Dataset Revision |
| Excelを学習データとして変更 | Dataset登録、Package builder | TaskDefinition、意味が同じFeature Pipeline | Dataset Revision、新Model Package |
| 列名・シート名だけ変更 | `backend/src/material_workbench/data/dataset-input-profile-*.json` | TaskDefinition、Feature Pipeline | Profile Revision、Dataset Revision |
| 単位表記のみ変更 | Profileの単位対応 | canonical unit | Profile Revision |
| 補助列の追加・削除 | Profile metadata / technical fields | 予測契約 | Profile Revision |
| 入力の意味を追加 | TaskDefinition、Feature Pipeline | 既存Package | 新契約、新Package |
| 出力を追加 | TaskDefinition、Package、Runtime、UI | 旧Snapshot | 新契約、新Package |
| モデル手法のみ変更 | builder、Package、必要ならadapter | Profile、TaskDefinition | 新Package |
| 全く別の予測問題 | Task縦一式、`task_modules.py` | 既存Task | 新Task一式 |
| 表示のみ変更 | `apps/web/src` | データ・モデル契約 | frontend build |

判断に迷ったら `npm run dev:doctor -- --source path/to/file.xlsx` を実行します。Doctorは変更や学習を行わず、差分と次のコマンドだけを示します。

## 先にデータの用途を決める

同じExcelでも用途によって変更範囲が異なります。

| 用途 | 何に使うか | Model Package再構築 |
| --- | --- | --- |
| Projectで参照・探索するデータ | Data Library、Project固定参照、類似実績の確認 | 不要 |
| モデルを学習するデータ | 学習行・目的変数・品質評価 | 必要 |
| 候補入力として使うデータ | canonical入力へ変換して候補化 | TaskDefinition・Feature Pipelineとの対応を要確認 |

Profile候補が見つかることは、TaskDefinitionやFeature Pipelineの意味が同じことを保証しません。Doctorは構造差分を提示しますが、入力・目的変数・学習単位の意味は人が判断します。

## 変更リスク

### 比較的安全

文言、レイアウト、表示桁数、入力・表示範囲、optional metadata、既存Taskと同じ意味の列名対応、Dataset登録、検証済みPackageの切替、既存adapterを使うPackage再生成。

### ガイドとレビューが必要

Profile継承、単位変換追加、新カテゴリ値、既存Feature Pipelineの小変更、Package builder調整、TaskDefinitionのversion更新。

### 専門的レビューが必要

新Task、新目的変数、学習単位変更、relation構造変更、新Feature Pipeline、新adapter / Runtime、保存済みProjectやSnapshotの互換性変更。DoctorとUIはこれらを自動決定しません。

## やりたい変更別の案内

| # | 変更の分類 | 主に変更 | 変更しない | 再生成・Revision | コマンドと対象テスト | Project / Snapshotと誤り |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Excelの行や値だけ増えた | 参照用途なら新Data Assetを登録。学習用途なら同じTaskで再学習 | TaskDefinition、Profile、Feature Pipeline | Dataset Revision。学習用途だけ新Model Package | `profile_workbench.py validate`。学習用途は`model:build`とPackage smoke | 既存Projectは旧Revision固定。元Assetを上書きしない |
| 2 | シート名や列名が違う | Dataset Input Profile | TaskDefinition、Feature Pipeline | Profile Revision、Dataset Revision | `dev:doctor -- --source ...`、Profile tests | canonical名まで変えない |
| 3 | 単位表記が違う | Profileのsource/canonical unit | canonical unit、意味 | Profile Revision。値が変わればPackageも再構築 | Profile validate、golden | 表記差と物理量変更を混同しない |
| 4 | optional補助列が増減 | Profile metadata / technical / optional宣言 | 入出力契約 | Profile Revision | Profile Workbench、dataset profile tests | 必須入力をoptionalに逃がさない |
| 5 | 既存入力の範囲変更 | TaskDefinitionのallowed/default/training range | canonical path、既存Snapshot | Task contract digest。必要なら新Package | contract tests、`npm run api:generate` | 学習範囲を科学的妥当範囲とみなさない |
| 6 | 予測入力を追加 | TaskDefinition、Profile、Feature Pipeline、Package、capability | 既存Package | 新contract、新Package、生成型 | feature golden、Package smoke、API | Profileだけ足してモデル入力を暗黙変更しない |
| 7 | 予測出力を追加 | TaskDefinition、観測mapping、builder、Runtime、UI | 旧Snapshot | 新contract、新Package、OpenAPI | output semantics、preview/snapshot tests | 旧Snapshotを再計算しない |
| 8 | 特徴量変更 | Feature Pipeline、builder、Package | 元Excel、旧Package | Pipeline version、新Package | feature golden、Package verify | 列順だけ合わせて旧artifactを流用しない |
| 9 | モデル手法だけ変更 | builder、Package、必要ならallow-list adapter | Profile、TaskDefinition、Feature Pipeline | 新Package | `model:build`、`model:verify` | pickle/joblibや任意コードを入れない |
| 10 | 同じTaskで新データ学習 | Dataset登録、builder | TaskDefinition、意味が同じFeature Pipeline | Dataset Revision、新Package | validate、build、verify | 既存Projectは自動で最新へ移さない |
| 11 | 新しい予測Task | TaskDefinitionからTaskModuleまで縦一式 | 既存Task | 新Task一式 | registry、contract、golden、smoke、API/E2E | 複数の中央`if task_id`へ個別配線しない |
| 12 | UI表示だけ変更 | frontend presentation | Dataset、Profile、Package | frontend buildのみ | `npm run typecheck`、対象UI test | API契約を表示都合で変えない |
| 13 | 分からない | DoctorとChange Guideで分類 | まだ契約を編集しない | 診断結果次第 | `npm run dev:doctor -- --source ...` | 自動修正や自動学習を始めない |

## 接続と不変性

```text
Excel
  ↓
Data Asset（内容SHA。元ファイルを上書きしない）
  ↓
Dataset Profile Revision（外部構造→canonical意味。内容変更で新Revision）
  ↓
Dataset Revision（Asset＋Profile＋canonicalization契約）
  ↓
Dataset View Revision（単一Datasetまたは明示的な比較集合）
  ↓
Project（View、Task contract digest、Package manifest digestを固定）
  ├─ Prediction Task
  └─ Model Package
        ↓
Project Runtime
```

Projectは作成時の参照を保持します。Data Asset、Profile Revision、Dataset Revision、View Revision、Packageを上書きせず、新しい組合せを使うときは新Projectを作ります。SnapshotはPackage・Pipeline・学習データの版を持つ不変記録であり、最新Packageで再計算しません。

```text
canonical input
  ↓
Feature Pipeline（意味のある固定特徴量順）
  ↓
allow-list済みModel Package adapter
  ↓
PredictiveSummary
  ↓
preview / detailed / curve / snapshot
```

Feature Pipelineの意味や順序が変わればversionとPackageを新しくします。adapterはPackage内のデータ成果物だけを読み、Pythonコード、pickle、joblibを読みません。

## レシピ

- [Recipe A：列名だけ違うExcel](recipes/add-similar-workbook.md)
- [Recipe B：行が増えただけのExcel](recipes/add-more-rows.md)
- [Recipe C：入力変数を1つ追加](recipes/add-input-field.md)

## 読む順番

```text
Developer Start Here
  ↓
該当する個別契約文書
  ↓
tutorial-data-pipeline.md
  ↓
.claude/skills の実装手順
```
