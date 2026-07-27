# Docker Composeによるlocal開発基盤

## Composeが所有する範囲

この構成は、hostで動くMaterial Decision Workbenchから外部依存を分離して試すための開発fixtureです。
`infra` profileはPostgreSQL、S3互換object storage、bucket初期化、最小schema migrationを起動します。

現行applicationはSQLiteとlocal filesを使い続けます。
`WORKBENCH_PERSISTENCE_BACKEND=postgres`または`WORKBENCH_ARTIFACT_BACKEND=s3`を指定しても、現行applicationが自動でshared modeへ切り替わるわけではありません。
これらの値はshared modeの契約を先に固定するための予約値であり、application接続は後続の縦切りで実装します。

Electron、Vite、FastAPIはhostで起動します。
Windows installerのPython sidecarをcontainerへ置き換えず、既存のlaunch tokenとloopback境界も変更しません。

## 必要な環境

WindowsではDocker DesktopとLinux container engineを使います。
次のcommandがClientとServerの両方を返すことを確認します。

```powershell
docker version
docker compose version
```

`docker compose config`だけが成功し、`docker version`のServer欄がerrorになる場合は、Docker DesktopのLinux engineが起動していません。
Docker Desktopを起動し、contextが`desktop-linux`であることを確認します。

repositoryはWindows filesystemでもWSL filesystemでも利用できます。
ただし、Windows側の多数fileをLinux containerへbind mountするとmetadata accessが遅くなることがあります。
この構成はmigrationとsmoke scriptだけをread-only mountし、source data、Model Package、`node_modules`をmountしません。

shell scriptはLFへ固定しており、実行bitには依存しません。
Composeはscriptを`/bin/sh`へ明示的に渡します。

## 設定

必要に応じて`.env.example`を`.env`へcopyします。
`.env`はGitの対象外です。

```powershell
Copy-Item .env.example .env
```

例に含まれるpasswordとaccess keyはloopback上のlocal開発専用です。
共有環境またはproductionで使えるsecretではありません。
Compose commandとsmoke scriptはcredentialを出力せず、診断にはservice名、endpoint、bucket、object digestだけを使います。

既定のhost endpointは次です。

| 対象 | endpoint |
|---|---|
| PostgreSQL | `127.0.0.1:54321` |
| S3 API | `http://127.0.0.1:59000` |
| MinIO console | `http://127.0.0.1:59001` |

portが使用中なら`.env`の`WORKBENCH_POSTGRES_PORT`、`WORKBENCH_S3_PORT`、`WORKBENCH_S3_CONSOLE_PORT`を変更します。

## 永続infraを起動する

構成と固定image、healthcheck、network、volume、migration、smoke契約を先に検査します。

```powershell
npm run compose:check
```

PostgreSQLとobject storageを起動し、healthcheck後にschemaとbucketを準備します。

```powershell
npm run compose:up
```

host applicationは従来どおり起動します。

```powershell
npm run dev
```

object smokeは小さなtext objectをcontent digest由来のkeyへputし、get後のSHA-256を比較します。
同じ内容を再実行した場合は既存objectを読み、別内容は別keyになります。

```powershell
npm run compose:smoke
```

状態を確認するときはcredentialを表示しない次のcommandを使います。

```powershell
docker compose --profile infra ps
docker compose --profile infra logs postgres object-storage migration bucket-init
```

## 停止と破棄

containerとnetworkだけを止め、PostgreSQLとobject storageのnamed volumeを残します。

```powershell
npm run compose:down
```

次のcommandはnamed volumeを削除します。
PostgreSQL schemaと保存objectを復元できないため、破棄してよいlocal fixtureだと確認してから実行します。

```powershell
docker compose --profile infra down --volumes
```

## ephemeral integration profile

`test` profileはPostgreSQLとobject storageのdata directoryをtmpfsへ置きます。
migrationは8 tableを作成したことを検査し、object smokeはput、get、digest一致を検査します。
runnerはprocessごとに独立したCompose project名を使い、成功時も失敗時もそのtest projectだけを`down --remove-orphans`します。
test dataはtmpfsなので、開発用infraのserviceや永続volumeには触れません。

```powershell
npm run compose:test
```

このprofileはapplication全体のintegration testではありません。
shared persistenceとartifact storageの最小fixtureが同じnetwork上で初期化できることを検査します。

## migrationの責務

`001_shared_fixture.sql`は、Workspace、Actor、Project、Candidate Revision、Activity Run、Review Run、Artifact Referenceだけを作ります。
既存SQLite schemaの全面移植ではありません。

この段階ではAlembicを導入しません。
application runtimeがPostgreSQL schemaを所有しておらず、単一のidempotentなfixture migrationだけだからです。
後続のshared mode実装では、並行migration、rollback、revision履歴、SQLite migrationとの責務分離を比較してtoolを選びます。

Artifact Referenceはcontent digest、content type、size、metadata、immutable object keyを保存します。
object smokeは`smoke/sha256/<digest>.txt`というversioned keyを使いますが、MinIO bucket全体へobject lockを設定するものではありません。

## 既知の制約

- API containerとWeb containerは追加していません。
  現行のdebug、Electron、Windows packagingをhostに残すためです。
- local fixtureはloopbackへだけpublishします。
  shared networkやcloud deploymentの認証を表しません。
- pinned MinIO community imageはlocal検証用です。
  production採用時にはmaintenance状況とsecurity updateを再評価します。
- Docker DesktopのEngineが停止している場合も`npm run compose:check`は構成を検査できますが、image pull、health、migration、object roundtrip、disk使用量は確認できません。
