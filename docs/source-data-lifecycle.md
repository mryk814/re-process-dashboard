# Source更新と承認付きDataset lifecycle

## 信頼境界

外部sourceの更新を、再学習やactive Model Package切替へ直結させない。
実装する資産と操作は次の順で分離する。

```text
Source Connector
  → Immutable Raw Snapshot
  → Versioned Curation Recipe
  → Quality Gate / Quarantine
  → Dataset Profile Revision
  → Approved Canonical Dataset Revision
  → Explicit Training Snapshot
```

`source refresh != retraining != activation` をAPIと画面の両方で維持する。
既存Projectが固定したDataset ViewやModel Packageは更新しない。

## Source Connector

v1のallow-listは `object_storage_json_v1` である。
任意SQL editorや汎用ETL式は提供しない。
Connectorは次だけを永続化する。

- connector type
- object locator
- JSON array／JSONLのparser
- upstream row key
- 選択field
- manual-onlyまたはschedule可能というtrigger policy
- schedule ID、間隔、有効状態
- configuration digest

credentialは取得requestの一時headerであり、Connector、request body、Raw Snapshot、Fetch Attempt、ログへ保存しない。
APIは `X-Source-Credential` headerで受け、adapter境界を越えたら破棄する。
失敗attemptには固定error codeと一般化した日本語messageだけを保存する。

manual取得とscheduled取得は `trigger_kind` で区別する。
scheduleが有効でないConnectorへのscheduled取得は失敗attemptとして残し、Raw Snapshotを作らない。
このリポジトリ内にschedulerは作らず、外部schedulerが同じ明示取得APIを呼べる契約だけを持つ。

## Immutable Raw Snapshot

Raw Snapshotは次を固定する。

- connector configuration digest
- source locator
- selection digest
- object versionと取得trigger
- 取得時刻
- source内容のSHA-256
- row countとraw record
- snapshot digest
- 前回Snapshot IDと差分summary

同一Connector、同一selection、同一内容SHAの再取得は既存Snapshotへ統合し、Fetch Attemptへ `reused_existing_snapshot` を残す。
内容の異なるSnapshotは同じsourceでも別revisionとして残す。

upstream row keyが一意なら追加、変更、消失、未変更の件数を算出する。
row keyがない、欠損、重複の場合は推測で比較せず `comparable=false` と理由を返す。

## Versioned Curation Recipe

RecipeはDataset Profileとは独立したversioned resourceである。
v1で許可するstepは次だけで、任意Python、任意式、汎用joinは受けない。

- string trim
- finite number coercion
- required field判定
- target eligibility判定
- fixed equality filter
- numeric sum limit

raw recordは変更しない。
Canonical record、row status、reason code、target eligibilityをCuration Runへ別保存する。
statusは `accepted`、`warning`、`quarantined`、`blocked` のいずれかである。
重複row keyはblocked、必須欠損や数値変換不能はquarantined、target欠損はsource行を削除せずtarget-ineligibleとして残す。

同じRecipeを前回Snapshotへ適用した結果があれば、採用・注意・隔離・停止・target-ineligibleの件数差を保持する。

## 承認とTraining Snapshot

Canonical Dataset RevisionはCuration Runをactorが明示承認して初めて作る。
標準承認ではquarantined／blocked rowを理由付きで保持したまま承認対象から除外する。

quarantined rowを含めるoverrideは対象row key、actor、row理由、全体理由を必須にする。
blocked rowや重複row keyはoverrideできない。

Training Snapshotは承認後の別操作であり、承認済みかつtarget-eligibleなrowだけを固定する。
作成しても次は実行しない。

- モデル再学習
- Model Package生成・検証
- active Package切替
- 既存ProjectのDataset更新

## APIと画面

- `GET /api/data-lifecycle`
- `POST /api/data-lifecycle/connectors`
- `GET /api/data-lifecycle/connectors/{id}`
- `POST /api/data-lifecycle/connectors/{id}/fetch`
- `POST /api/data-lifecycle/recipes`
- `POST /api/data-lifecycle/raw-snapshots/{id}/curation-runs`
- `POST /api/data-lifecycle/curation-runs/{id}/approve`
- `POST /api/data-lifecycle/canonical-dataset-revisions/{id}/training-snapshots`

データライブラリの「Source更新」で、Raw差分、品質件数、理由付き隔離row、承認actor、Training Snapshotまでを一つのstage railで確認する。
取得操作で画面の他のDatasetやPackageを更新しない。

## 現時点の境界

Object storage transportは、sidecarや外部取得処理が渡したobject contentをallow-list parserで解釈する境界である。
S3／Azure／Snowflake SDK、credential vault、scheduler、汎用query editorは含まない。
承認済みTraining Snapshotを特定のPackage builderへ渡すadapterは、Taskごとの縦スライスで追加する。

最初の縦スライスは
[CALCE電池データのSourceから実測評価までの参照ループ](reference-data-loop.md)
である。複合row identityを明示し、承認済みTraining Snapshotから作った
materialized asset、Package、Project、予測Snapshot、Actualまで同じdigest chainを保持する。
品質とは無関係な評価用holdoutはCurationのquarantineにせず、
Training Snapshotのversioned selection policyへ記録する。
