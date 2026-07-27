# Shared Workbench Lab v1実験記録

## 今回知りたかったこと

single-user SQLite application全体を移植せず、二つのActorが同じProjectをPostgreSQLで共有し、Candidateの競合、Run provenance、object storageとの整合性を一つのtyped APIで扱えるかを確認しました。

結論は、対象をProject、Candidate Revision、Activity Run、Artifact Reference、Audit Eventへ限定すれば通せます。
既存local modeは置換せず、Shared Labを明示的な別appとして分離しました。

## 実装した最小境界

```text
HTTP client A / human-a
HTTP client B / human-b
        |
        v
Shared Lab typed API
        |
        +-- PostgreSQL
        |   Project / current Candidate / immutable Revision
        |   Activity Run / Artifact Reference / Audit Event
        |
        +-- MinIO
            content-addressed artifact
```

request bodyのActor自己申告は受け付けません。
`X-Workbench-Workspace`と`X-Workbench-Actor`を、migrationでseedした固定Actorと照合します。
request IDとcorrelation IDはauditへ残します。

`human-a`と`human-b`はLab内のread/writeを持ちます。
`ai-reviewer`はreadとreviewに限定し、Candidate更新やartifact登録を許可しません。

## Candidate concurrency

二Actorがrevision 1を読んだ後、同じ`expected_revision=1`で更新しました。

| Actor | 結果 |
|---|---|
| 一方 | HTTP 200、revision 2を作成 |
| 他方 | HTTP 409 `revision_conflict`、current revision 2を返却 |

PostgreSQLのconditional updateにより、二つのwriteが同時に成功しません。
敗者の409はcommit済みのconflict auditを残し、revision履歴は1、2の二件だけでした。
敗者がrevision 2を読み直してretryするとrevision 3を作成できました。

Candidate Revisionはimmutable rowです。
current pointerの更新と新revisionのinsertは同じtransactionでcommitされ、外部キーはtransaction終端までdeferします。

## RunとActor provenance

`human-a`がrevision 3を参照するActivity Runを作成し、`human-b`が同じRunを取得しました。
RunにはProject、Candidate ID、Candidate revision、Activity ID、作成Actor、payload、時刻が残ります。

revision、Run、artifactのActorは必須です。
database triggerはActorとProjectのWorkspace不一致を拒否します。
Review Runが別ProjectのActivity Runを参照することも複合外部キーで拒否します。

## Artifactの整合性

artifactは次の順序で登録します。

1. contentのSHA-256と不変object keyを決める
2. MinIOへputする
3. getしてsizeとdigestを再検証する
4. 検証時刻とActorを持つ`ready` metadataをPostgreSQLへcommitする

object storageが利用できない場合、metadataは0件のままです。
object作成後にmetadata insertが失敗した場合、新規objectを補償削除します。
既存のcontent-addressed objectは別referenceが使う可能性があるため削除しません。
保存後にobjectを改変したfailure injectionでは、getが`artifact_digest_mismatch`になりました。

DBとobject storageを一つのdistributed transactionにはしていません。
小さなLabでは、verify-before-register、content address、補償削除、auditを組み合わせる方が境界を追いやすいためです。

## 実測したscenario

Windows 11、Docker Desktop 4.48.0、Docker Engine 28.5.1で`npm run shared:test`を実行しました。

- migration 001と002を適用
- 同じmigrationを再実行
- 固定WorkspaceとActorを解決
- 二Actorの競合とretry
- revision履歴を確認
- Runを作成し別Actorから参照
- artifactをput／get／digest verify
- storage outageでmetadata 0件を確認
- metadata conflict後の新規object削除を確認
- object改変後のdigest mismatchを確認
- auditにActor、request、conflictを確認
- test専用container、network、named volumeを削除

全scenarioは12.7秒で成功しました。
時間はDocker Desktopとimage cacheの状態で変動します。

## local modeとの共存

既存の`material_workbench.app`、SQLite Store、local files、Electron起動は変更していません。
Shared Labは`material_workbench.shared_lab.app`を明示的に起動した場合だけPostgreSQLとMinIOへ接続します。
local Projectを自動同期せず、`.env.example`のlocal mode既定値も維持します。

## 意図的に作らなかったもの

- login、password、OIDC、member管理
- realtime collaboration
- SQLite Project全体のPostgreSQL移植
- localとsharedのautomatic sync
- Data Lifecycle、Chain、Model Package buildの共有化
- background worker
- production deployment
- large artifact upload

固定header Actorはdevelopment experiment専用です。
remote公開時のauthenticationには利用できません。

## 得られた判断

Store全体を先にgeneric repositoryへ変える必要はありません。
shared化で先に必要になった契約は、Actorを伴うrequest identity、transactional revision pointer、immutable history、typed conflict、audit、artifact verificationと補償です。

次のcloud spikeではこの境界をそのまま運べるかを確認し、local launch tokenをremote identityへ転用しません。
