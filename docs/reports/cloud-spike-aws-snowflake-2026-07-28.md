# AWS最小deployとSnowflake連携境界の調査記録

## 調査結果

2026年7月28日時点では、AWSとSnowflakeのresourceを作成できる認証経路がこの作業環境にありません。

このため、temporary deploymentを推測やmockで代替せず、外部権限が必要な実験を保留しました。
本調査でAWSまたはSnowflakeのresourceを作成、変更、削除するcommandは実行していません。

local Docker Composeの検証は、PostgreSQLとS3互換object storageの契約を確認する前提実験です。
MinIOの成功をAmazon S3の成功とは扱いません。

## 実験項目の状態

| 実験項目 | 状態 | 根拠 |
|---|---|---|
| Option AとOption Bの比較 | 完了 | 本文の構成比較に記録 |
| AWS temporary deployment | 外部阻害で保留 | account、credential、課金権限がない |
| managed PostgreSQLのread/write | 未実施 | AWS resourceを作成していない |
| Amazon S3のput/get | 未実施 | local MinIOのsmokeだけを確認済み |
| localとremoteのsecurity差 | 完了 | 本文のsecurity差分に記録 |
| Snowflakeの役割比較 | 完了 | OLTP、Hybrid Tables、Snowflake Postgres、analyticsを比較 |
| Approved DatasetのSnowflake接続 | 外部阻害で保留 | Snowflake accountとroleがなく、interface設計まで実施 |
| cost、setup、teardown | 完了 | resource未作成のため0またはN/Aとして記録 |
| resource削除と残存確認 | 一部確認不能 | 本調査由来のresourceは0、account全体は認証できず未確認 |
| 採用、保留、非採用の判断 | 完了 | 本文の採否判断に記録 |

## 実行権限の確認

secretの値は検索、表示していません。
存在する認証経路だけを次のread-only確認で調べました。

| 確認対象 | 結果 |
|---|---|
| `AWS_*`、`SNOWFLAKE_*`、`SNOWSQL_*`、`S3_*`環境変数名 | 0件 |
| `aws` command | 未導入 |
| `snowsql` command | 未導入 |
| `snow` command | 未導入 |
| `%USERPROFILE%\.aws\credentials` | なし |
| `%USERPROFILE%\.aws\config` | なし |
| `%USERPROFILE%\.snowflake\config.toml` | なし |
| `%USERPROFILE%\.snowsql\config` | なし |
| GitHub repository secrets | 0件 |
| GitHub repository variables | 0件 |
| GitHub repository environments | 0件 |

この状態では、AWS account、Snowflake account、利用可能region、quota、budget、既存resourceを認証付きAPIで確認できません。
課金を伴うresourceの作成権限もユーザーから委任されていません。

したがって、「account内にresourceが一つも残っていない」とは確認できません。
確認できるのは、本調査がcloud resourceを作成しておらず、本調査由来の削除対象がないことまでです。

## AWS構成の比較

| 項目 | Option A：小さなVMとDocker Compose | Option B：ECS Fargate |
|---|---|---|
| application実行 | LightsailまたはEC2上でcontainerを実行する | ECR imageをECS taskとして実行する |
| PostgreSQL | Lightsail managed databaseまたはRDS PostgreSQL | private subnetのRDS PostgreSQL |
| object storage | Amazon S3 | Amazon S3 |
| HTTPS | reverse proxyとcertificateの運用が必要 | Load Balancerとcertificateへ分離できる |
| identity | VM roleとapplication認証を設計する | task role、task execution role、application認証を分離する |
| secret | VMへの安全な注入とrotationを設計する | Secrets Manager等からtaskへ注入する |
| network | firewall、security group、公開portを自分で管理する | VPC、subnet、security group、load balancerを管理する |
| 運用責務 | OS update、container lifecycle、reverse proxyを含む | task lifecycleはmanagedだがAWS resourceの種類が増える |
| spikeでの理解しやすさ | 構成を追いやすい | 小規模実験でもIAMとnetworkの前提が多い |
| 現時点の判断 | **保留**。実cloud spikeの第一候補 | **初回は非採用**。Option Aの結果後に再評価 |

Option Aでも、現行の`compose.yaml`をそのままcloudへ置けばよいわけではありません。
現行Composeはloopback向けのPostgreSQLとMinIO fixtureだけを所有し、applicationはhost上でSQLiteとlocal filesを使います。
APIとWebのcontainer image、およびshared persistenceの縦切りを先に用意する必要があります。

## localとremoteのsecurity差

| 境界 | local mode | remote modeで必要な変更 |
|---|---|---|
| transport | loopback HTTPとElectron launch token | HTTPS終端と内部通信の暗号化 |
| Actor | local Actorと起動単位のtoken | 認証済み主体から固定Actorを解決する仕組み |
| credential | local開発専用の`.env` | secret managerまたは安全なenvironment injection |
| AWS権限 | なし | application用taskまたはinstance roleへ最小権限を付与 |
| database | local processまたはloopback fixture | non-public subnet、security group、TLS、credential rotation |
| object storage | local MinIO bucket | S3 Block Public Access、限定bucket policy、必要に応じた暗号化 |
| browser origin | loopbackまたはDesktop token付き`Origin: null` | 許可originの固定、CORSのdeny-by-default |
| abuse対策 | single-user前提 | request rate、body size、upload size、timeoutの上限 |
| log | local診断 | credential、token、dataset内容を除外した集中log |
| resource lifecycle | processまたはCompose projectを停止 | owner、purpose、expiry tag、budget alert、teardown checklist |

remote modeでlocal launch tokenをそのまま利用者認証へ転用しません。
launch tokenは同一端末上のElectronとsidecarを結ぶ境界であり、network越しの利用者identityを証明しないためです。

## Snowflakeの役割比較

| 選択肢 | transaction | analytics | portability | 現時点の判断 |
|---|---|---|---|---|
| Standard TableをOLTP正本にする | applicationの細かなCRUDとrevision conflictの正本には適さない | 大量scanと集計に適する | Snowflake固有のwarehouseと権限へ結合する | **非採用** |
| Hybrid TableをOLTP正本にする | primary key、index、row-oriented read/writeを持つ | Standard Tableと組み合わせられる | 対応cloud、load方法、cost、table semanticsの確認が要る | **初期spikeでは非採用** |
| Snowflake PostgresをOLTP正本にする | PostgreSQL clientとtransaction contractを再利用できる | Snowflake連携の運用上の利点を評価できる | region、network policy、cost、backup、移行性の確認が要る | **保留** |
| PostgreSQLを正本、Snowflakeを分析先にする | Project、Candidate、Run、Actor、AuditをPostgreSQLに保つ | 承認済みDatasetだけをloadまたは外部参照する | application runtimeをSnowflakeから分離できる | **採用候補** |

Hybrid TablesはAWSとMicrosoft Azureのcommercial regionで利用できますが、warehouseとSnowflake固有の制約を伴います。
Snowflake PostgresはAWSとMicrosoft Azureの複数regionで提供され、東京と大阪も現在の対応一覧に含まれます。
対応regionであることは、このrepositoryが利用可能なaccount、edition、quotaを持つことを意味しません。

## Approved Datasetの連携interface

Snowflakeへ渡す単位は、承認前のRaw Snapshotでもapplication DBのtableでもなく、**Approved Canonical Dataset Revision**です。
export後も元revisionを再解釈せず、同じidentityを追跡できる形にします。

### S3 object

Parquet objectは次の不変keyへ置きます。

```text
approved-datasets/sha256/<dataset-digest>/<content-sha256>.parquet
approved-datasets/sha256/<dataset-digest>/manifest.json
```

Parquetは`row_key`と承認済みcanonical fieldだけを含めます。
quarantined、blocked、excluded rowは含めません。

manifestは少なくとも次を固定します。

- schema version
- Canonical Dataset Revision ID
- dataset digest
- source Curation Run ID
- Parquet content SHA-256
- schema digest
- row count
- media type
- object key
- export actor
- export timestamp

applicationはS3 access keyを保存しません。
export jobへ割り当てたIAM roleだけに、対象prefixへの必要最小限の`PutObject`、`GetObject`、`ListBucket`権限を与えます。

### Snowflake access

Snowflake側はprivate S3 bucketを参照するstorage integrationとnamed external stageを使います。
長期AWS keyをstage定義、SQL、application設定へ埋め込みません。

最小検証では、ParquetをStandard Tableへ`COPY INTO`する経路か、External Tableからread-onlyで参照する経路を一つ選びます。
どちらの経路でも、dataset digest、content SHA-256、row countをmanifestと照合してから分析します。

Snowflakeからapplicationへ返す場合は、承認済みrevisionを参照するread-only summaryに限定します。
Snowflake tableをProject、Candidate、Run、Actor、Auditの更新先にはしません。

## costとteardown

| 項目 | 実測値 |
|---|---:|
| 本調査が作成したcloud resource | 0 |
| 本調査に帰属するcloud利用料 | 0 |
| cloud setup時間 | N/A |
| cloud teardown時間 | N/A |
| 本調査由来の削除対象 | 0 |
| account全体の残存resource | 認証権限がないため未確認 |

参考として、local Composeはimage削除後の`compose:test`が32.5秒、image再利用時の`compose:check`と`compose:test`が9.1秒、対象imageが約787 MBでした。
この値はDocker Desktop上のlocal fixtureの測定値であり、AWSの起動時間、転送量、storage量、料金を推定する根拠には使いません。

## 採否判断

- **Option A**：保留。
  disposable AWS account、budget上限、region、teardown権限が明示された後の第一候補とします。
- **Option B**：初回spikeでは非採用。
  Option Aでapplication imageとremote securityの境界を確認した後に比較します。
- **Snowflake Standard TableまたはHybrid TableをOLTP正本にする案**：非採用。
  application CRUDと分析基盤を結合する必要性が確認されていません。
- **Snowflake Postgres**：保留。
  shared PostgreSQL adapterを先に通し、同じcontractを移せるかを評価します。
- **PostgreSQL正本とSnowflake analyticsの分離**：採用候補。
  Snowflake accountを使える場合はApproved Datasetのread-only分析から試します。
- **local mode**：維持。
  cloud spikeを理由にSQLite、local files、Electronの既定経路を変更しません。

## 実cloud spikeの開始条件

別Issueで実行する場合は、次を開始前に明示します。

1. disposable AWS accountまたは隔離されたsandbox account
2. 利用を許可されたAWS region
3. 上限額とbudget alert
4. resource作成、一覧、削除に必要なleast-privilege role
5. owner、purpose、expiryのtag規則
6. public accessを避けるnetwork方針
7. teardownと残存確認を実行できる権限
8. Snowflake account、role、warehouse、region、storage integration作成権限
9. APIとWebのcontainer image
10. PostgreSQLとobject storageを使うshared applicationの最小縦切り

これらが揃うまでは、AWS固有またはSnowflake固有のbehaviorをmockで作り込みません。

## 公式資料

- [Amazon ECS task IAM role](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-iam-roles.html)
- [Best practices for IAM roles in Amazon ECS](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/security-iam-roles.html)
- [Create an S3 stage](https://docs.snowflake.com/en/user-guide/data-load-s3-create-stage)
- [Create hybrid tables](https://docs.snowflake.com/en/user-guide/tables-hybrid-create)
- [Snowflake Postgres](https://docs.snowflake.com/en/user-guide/snowflake-postgres/about)
- [Snowflake Postgres networking](https://docs.snowflake.com/en/user-guide/snowflake-postgres/postgres-network)
