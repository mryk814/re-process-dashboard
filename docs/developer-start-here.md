# Developer Start Here

この文書は「何を変更したいか」から、変更する契約、生成物、検証を決める入口です。

最初に [現行システム基準](current-system-baseline.md) で、現在のProject mode、再利用できる境界、v1固有前提を確認してください。実装一覧は [task-inventory.json](task-inventory.json)、個別契約は [Dataset Input Profile](dataset-input-profile.md)、各Profile、[特徴量](feature-engineering.md)、[Model Package](model-package-contract.md)、[Chain実行](chain-execution.md)、[検討アクティビティ](decision-activities.md) を正本とします。

実装を読みながら学ぶ場合は、[開発教材](learning/README.md)のコードマップと三つの学習ルートを使います。最初の試作章は、検討アクティビティの契約をPydanticからOpenAPI、TypeScript、React、テストまで追います。

## 最初の判断表

| やりたいこと | 主に変える場所 | 原則変えない場所 | 必要な成果物 |
| --- | --- | --- | --- |
| Excel／CSVを参照・探索データとして追加 | Data Asset、対応Profile | TaskDefinition、Feature Pipeline、Package | Dataset Revision |
| 同じ意味の学習データへ差し替え | Dataset登録、Package builder | TaskDefinition、意味が同じFeature Pipeline | Dataset Revision、新Model Package |
| 列名・シート名だけ変更 | 対応するDataset Profile | TaskDefinition、Feature Pipeline | Profile Revision、Dataset Revision |
| 単位表記のみ変更 | Profileのsource／canonical unit対応 | canonical quantityとunit | Profile Revision |
| 観測family、行固有入力、split groupを追加 | Observation系Profile、training view | 既存Taskの意味 | Profile Revision、必要なら新Package |
| 入力の意味を追加 | TaskDefinition、Profile、Feature Pipeline | 既存Package | 新契約、新Package |
| 出力を追加 | TaskDefinition、観測mapping、Package、Runtime、UI | 旧Snapshot | 新契約、新Package |
| モデル手法だけ変更 | builder、Package、必要ならadapter | Profile、TaskDefinition | 新Package |
| 全く別の予測問題 | Task縦一式、`task_modules.py` | 既存Task | 新Task一式 |
| 新しいCandidate Shape | candidate contract、persistence、diff／copy／snapshot、入力UI | 既存shape | 型付きshape一式 |
| 新しいDecision Activity | Activity definition、parameter／result contract、service、UI | Task固有ID分岐 | 新Activity一式 |
| 新しいChain | Chain Definition／Revision、binding、必要なdomain adapter | 再利用するTask | 新Chain Revisionと検証fixture |
| 表示のみ変更 | `apps/web/src` | データ・モデル契約 | frontend build |
| Project identityや保存証跡を変更 | scientific identity、migration、API、docs | 既存recordの意味 | migration、compatibility evidence |

判断に迷ったら `npm run dev:doctor -- --source path/to/file.xlsx` を実行します。Doctorは変更や学習を行わず、差分と次のコマンドだけを示します。Doctorが対応しないProfile familyやCandidate Shapeでは、自動判定を期待せず契約を先に確認します。

## 先にProject modeを決める

### Single-task Project

一つのDataset View、Task contract、Model Packageを固定する。通常の予測、比較、応答曲線、Snapshot、実測照合、Decision Activityはこのmodeを基準にする。

### Chain Project

Chain Revisionを固定し、複数のTask／deterministic transformをbindingする。Candidate preparation、Stage実行、部分再計算、Snapshot、不確かさ伝播はsingle-task previewと別の実行経路を持つ。

新しいデータが複数段に見えても、単に一つのTaskで表現できるものを安易にChainへしない。各Stageが単独で意味を持ち、版、実測、精度または再計算単位を分離する必要がある場合にChainを使う。

## 先にデータの用途と形状を決める

同じsourceでも用途によって変更範囲が異なります。

| 用途 | 何に使うか | Model Package再構築 |
| --- | --- | --- |
| Projectで参照・探索する | Data Library、Project固定参照、類似実績、品質確認 | 不要 |
| モデルを学習する | 学習行、target cohort、split、品質評価 | 必要 |
| 候補入力として使う | canonical入力または型付きCandidate Shapeへ変換 | Task／Candidate契約との対応を確認 |
| Chain Stage間で渡す | canonical portとbinding | Stage契約、quantity、basis、unitを確認 |
| 実測として照合する | SnapshotまたはStage結果との比較 | 不要。予測入力へ黙って代入しない |

Profile候補が見つかることは、TaskDefinitionやFeature Pipelineの意味が同じことを保証しません。構造差分だけでなく、入力、目的変数、学習単位、観測family、split groupを人が判断します。

### 現在のProfile family

- Dataset Input Profile：Workbookのentity、relation、工程、観測を解釈する
- Tabular Dataset Profile：独立したCSV／表形式Taskを解釈する
- Observation Dataset Profile：複数観測familyと行固有入力を保持する
- Stage B Workbook Profile：特定の多段学習cohortを構築する

新しいsourceを既存familyへ無理に合わせない。一方、新Profile familyを作る前に、既存familyのparameter追加で意味を保てないか確認する。

## 変更リスク

### 比較的安全

文言、レイアウト、表示桁数、既存契約内の入力・表示範囲、optional metadata、同じ意味の列名対応、Dataset登録、検証済みPackageの切替、既存adapterを使うPackage再生成。

### ガイドとレビューが必要

Profile継承、単位変換追加、新カテゴリ値、既存Feature Pipelineの小変更、Package builder調整、TaskDefinition version更新、Activity parameter追加、Chain binding追加。

### 専門的レビューが必要

新Task、新目的変数、学習単位変更、relation／observation family変更、新Feature Pipeline、新adapter／Runtime、新Candidate Shape、新Profile family、新Chain candidate adapter、Project scientific identity、保存済みProject／Snapshot／Runの互換性変更。

## やりたい変更別の案内

| # | 変更の分類 | 主に変更 | 変更しない | 再生成・Revision | 主な検証 | よくある誤り |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | sourceの行や値だけ増えた | 参照用途なら新Data Asset。学習用途なら同じTaskで再学習 | TaskDefinition、Profile、Feature Pipeline | Dataset Revision。学習用途だけ新Package | Profile validate、Package verify | 元Assetを上書きする |
| 2 | シート名や列名が違う | 対応Profile | TaskDefinition、Feature Pipeline | Profile Revision、Dataset Revision | Profile tests | canonical名まで変える |
| 3 | 単位表記が違う | Profileのunit mapping | canonical quantity／unit | Profile Revision。値変換が変わればPackageも再構築 | golden、contract tests | 表記差と物理量変更を混同する |
| 4 | optional補助列が増減 | metadata／technical／optional mapping | 入出力契約 | Profile Revision | Profile Workbench | 必須入力をoptionalへ逃がす |
| 5 | 既存入力の範囲変更 | TaskDefinitionのallowed／default／training range | canonical path、旧Snapshot | Task contract digest。必要なら新Package | contract、API | training rangeを科学的許容範囲とみなす |
| 6 | 予測入力を追加 | Task、Profile、Feature Pipeline、Package、capability | 既存Package | 新contract、新Package、生成型 | feature golden、smoke、API | Profileだけ足して暗黙にモデル入力を変える |
| 7 | 予測出力を追加 | Task、観測mapping、builder、Runtime、UI | 旧Snapshot | 新contract、新Package、OpenAPI | output semantics、snapshot | 旧Snapshotを再計算する |
| 8 | 特徴量変更 | Feature Pipeline、builder、Package | source、旧Package | Pipeline version、新Package | feature golden、Package verify | 列順だけ合わせて旧artifactを流用する |
| 9 | モデル手法だけ変更 | builder、Package、必要ならallow-list adapter | Profile、Task、Feature Pipeline | 新Package | build、verify、smoke | 任意コードやunsafe artifactを入れる |
| 10 | 同じTaskで新データ学習 | Dataset登録、builder | Task、意味が同じPipeline | Dataset Revision、新Package | cohort、split、quality | 既存Projectを自動で最新へ移す |
| 11 | 新しい標準Tabular Task | data-only Task／Profile／Package、TaskModule entry | 既存Task | 新Task一式 | inventory、contract、smoke、API／E2E | 複数の中央`if task_id`へ配線する |
| 12 | 新しい特殊Task | Task縦一式と明示integration | 既存標準経路 | 新Task一式 | end-to-end fixture | 無理にTabular Profileへ押し込む |
| 13 | 新しいDecision Activity | typed parameter／result、registry、service、UI | TaskDefinition | Activity version、OpenAPI | availability、identity、stale response | 既存Activity型を直接流用する |
| 14 | 新しいChain | Stage surface、binding、Revision、domain adapter | Task内部 | Chain Definition／Revision | 別ユースケースfixture、partial recompute | 溶接固有candidate前提をChain Coreへ増やす |
| 15 | Candidate Shape追加 | typed union、migration、UI surface | 既存shape | schema version、migration | copy／diff／snapshot／archive | 任意JSONで済ませる |
| 16 | UI表示だけ変更 | frontend presentation | Dataset、Profile、Package | frontend buildのみ | typecheck、UI test | 表示都合でAPI意味を変える |
| 17 | 分からない | Doctorとこのガイドで分類 | まだ契約を編集しない | 診断次第 | focused investigation | 自動修正や自動学習を始める |

## 接続と不変性

### Single-task

```text
Source Asset
  ↓
Dataset Profile Revision
  ↓
Dataset Revision
  ↓
Dataset View Revision
  ↓
Project scientific identity: single_task
  ├─ Task contract digest
  └─ Model Package manifest digest
        ↓
Project Runtime
```

### Chain

```text
Chain Definition
  + Stage contracts
  + Stage Package／Dataset／Profile locks
  + binding／unit conversion
        ↓
Chain Revision
        ↓
Project scientific identity: chain
        ↓
Candidate Revision
        ↓
Stage execution／partial recomputation／Snapshot
```

Projectは作成時の参照を保持します。Asset、Profile、Dataset、View、Package、Chain Revisionを上書きせず、新しい組合せは新Projectまたは明示的な新Revisionとして扱います。SnapshotやRunは最新Packageで再計算しません。

```text
canonical input
  ↓
Feature Pipeline
  ↓
allow-list済みModel Package adapter
  ↓
Predictive Summary
  ↓
preview／detailed／curve／snapshot／activity／chain stage
```

Feature Pipelineの意味や順序が変わればversionとPackageを新しくします。adapterはPackage内のdata artifactだけを読み、Pythonコード、pickle、joblibを読みません。

## 前提を変えたときの追従確認

次のいずれかを変更した場合は、実装だけでなく前提文書を確認します。

- Project scientific identity
- Candidate Shape
- Dataset Profile family
- TaskModule integration point
- Runtime／Application Capability
- Decision Activity parameter／result
- Chain Stage kind、binding、candidate adapter
- 新しいimmutable Run／Snapshot
- 自動実行と明示実行の境界

確認対象：

- `docs/app-charter.md`
- `docs/current-system-baseline.md`
- この文書
- `docs/README.md`
- `docs/task-inventory.json`
- FastAPI OpenAPIと`apps/web/src/generated/`（更新は `npm run api:generate`）
- 関連ADRの状態欄と追跡先

## レシピ

- [Recipe A：列名だけ違うExcel](recipes/add-similar-workbook.md)
- [Recipe B：行が増えただけのExcel](recipes/add-more-rows.md)
- [Recipe C：入力変数を1つ追加](recipes/add-input-field.md)

## 読む順番

```text
Developer Start Here
  ↓
現行システム基準
  ↓
該当する個別契約文書
  ↓
必要なら過去のADR
  ↓
.claude/skills の実装手順
```
