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

通常のローカル取得はConnectorへ登録した `file://` locatorを使う。
API requestには元データを展開せず、アプリ側がregular fileを1 MiBずつ読み、
読込前後のsize・更新時刻・file identityが変わっていないことを確認する。
同時にbyte数とSHA-256を計算し、任意の期待SHA-256・期待行数と照合してから
Raw Snapshotへ固定する。16 MiBを超えるfile、UTF-8でないfile、読込中に変化した
fileはSnapshotを作らず失敗attemptにする。

inline JSONは5,000,000文字以下の検証専用経路として残す。
S3／Azure等のremote object transportとcredential形式はCloud spikeで決め、
このローカルfile adapterへ暗黙のfallbackを追加しない。

manual取得とscheduled取得は `trigger_kind` で区別する。
scheduleが有効でないConnectorへのscheduled取得は失敗attemptとして残し、Raw Snapshotを作らない。
このリポジトリ内にschedulerは作らず、外部schedulerが同じ明示取得APIを呼べる契約だけを持つ。

## Immutable Raw Snapshot

Raw Snapshotは次を固定する。

- schema v2（source byte countをidentityへ含める。既存v1は旧digest規則のまま読む）
- connector configuration digest
- source locator
- selection digest
- object versionと取得trigger
- 取得時刻
- source内容のSHA-256
- source byte count
- row countとraw record
- snapshot digest
- 前回Snapshot IDと差分summary

同一Connector、同一selection、同一内容SHAの再取得は既存Snapshotへ統合し、Fetch Attemptへ `reused_existing_snapshot` を残す。
内容の異なるSnapshotは同じsourceでも別revisionとして残す。

取得APIのresponseはRaw row全件を返さず、Snapshot ID、digest、byte数、row count、
差分だけのreceiptを返す。row本体は不変Snapshotとして保存し、詳細画面から参照する。
旧v1 Snapshotのduplicate receiptだけは、当時固定していなかったbyte数を
`null`として返し、現在値を推測で後付けしない。

### Row payloadの保存契約

Raw SnapshotとCuration Runのrow本体はSQLiteへ埋め込まず、Workspace内の
`row-payloads/sha256/<先頭2文字>/<SHA-256>.jsonl` に保存する。
各行はUTF-8・LF終端のcanonical NDJSONであり、参照はrecord kind、media type、
SHA-256、byte数、row数を固定する。SQLiteにはresourceのidentity、公開digest、
summary、承認状態、索引用列とこの参照だけを残す。

このCAS digestは保存表現の整合性を検査する内部digestであり、
Raw／Curation／Canonical／Trainingの公開digestを置き換えない。
既存Workspaceのinline rowは起動時migrationでCASへ完全移行し、旧inline経路へ
fallbackしない。解釈できない旧resource、欠損file、size・digest・row数の不一致は
対象resourceだけを利用不能として記録し、別Connectorや別Projectの起動を妨げない。

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

Training Snapshotは承認後の別操作である。
v2は承認済みrowから、目的変数ごとの観測済みrow key、cohort digest、group field、fold数、groupからfoldへの完全な割当を固定する。
全目的変数が揃うrowだけへ狭めず、targetごとに異なるcohortを保存する。
Snapshot digestはrowの和集合、target cohort、split assignment、selection policy、actor、purposeを対象にする。

Feature Pipelineの入力path、変換、特徴量名と順序はTraining Snapshotへ入れない。
これらはModel Packageが定義とdigestを固定し、PackageのprovenanceがTraining Snapshot digestを参照する。
したがって、同じTraining SnapshotからFeature Pipelineの異なるPackageを作ると、Snapshot digestは同じままPackage digestが変わる。

legacyの`approved-training-snapshot/v1`は旧digest規則のまま読み取る。
target cohortやsplitを後付けしてv2として再解釈せず、新しい学習実験ではv2 Snapshotを明示作成する。
作成しても次は実行しない。

- モデル再学習
- Model Package生成・検証
- active Package切替
- 既存ProjectのDataset更新

## APIと画面

- `GET /api/data-lifecycle`
- `POST /api/data-lifecycle/connectors`
- `GET /api/data-lifecycle/connectors/{id}`
- `GET /api/data-lifecycle/raw-snapshots/{id}/rows?offset=&limit=`
- `GET /api/data-lifecycle/curation-runs/{id}/rows?offset=&limit=&status=&reasoned_only=`
- `POST /api/data-lifecycle/connectors/{id}/fetch`
- `POST /api/data-lifecycle/recipes`
- `POST /api/data-lifecycle/raw-snapshots/{id}/curation-runs`
- `POST /api/data-lifecycle/curation-runs/{id}/approve`
- `POST /api/data-lifecycle/canonical-dataset-revisions/{id}/training-snapshots`

データライブラリの「Source更新」で、Raw差分、品質件数、理由付き隔離row、承認actor、Training Snapshotまでを一つのstage railで確認する。
取得操作で画面の他のDatasetやPackageを更新しない。
Connector detailはrowを含まないsummaryで、SQLite側で対象Connectorへ絞ってから
小さなprojectionだけをdecodeする。Raw／Curation rowは選択された版について
最大200件ずつ遅延取得し、source orderまたは`raw_row_index,row_key`の安定順、
親resource ID、固定digest、総件数を各pageへ残す。Canonicalのrow key集合と
Trainingのsplit assignmentも初期summaryへ展開しない。
SQLiteのrow indexはlogical position、CAS byte range、選択行のSHA-256を保持し、
resource manifestはCAS SHA-256、row数、status／理由行件数を固定する。
offset位置から対象行だけをseekし、完全性とtotalはmanifest 1行で確認する。
Curationはstatus別／理由行positionも持つため、承認画面は先頭pageの内容に
依存せず隔離行を直接取得し、監査表示はwarning／quarantined／blockedを含む
理由付き行だけを別pageで取得する。
対象ConnectorのCAS payloadが欠損・改ざんされている場合、詳細APIはresource IDと
固定error codeを含む503を返す。無関係Connectorの一覧・詳細は引き続き参照できる。

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
