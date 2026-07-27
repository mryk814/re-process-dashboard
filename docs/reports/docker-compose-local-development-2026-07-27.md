# Docker Compose local開発基盤の検証記録

## 対象

- revision：`2f02f202f11dd9f93985e9a1c7dc358e624dbbc2`
- OS：Windows 11
- Docker Client：28.5.1
- Docker Engine：28.5.1（Linux／amd64）
- Docker Compose：2.40.0-desktop.1
- Docker context：`desktop-linux`

## 静的検証

`docker compose --profile infra --profile test config --format json`は成功しました。
展開後の構成は10 service、2 network、2 named volumeです。

`npm run compose:check`は次を検査して成功しました。

- `infra`と`test`以外のprofileがない
- PostgreSQLとMinIO imageが具体的なtagへ固定されている
- 永続infraはnamed volumeを使い、test fixtureはtmpfsを使う
- host portが`127.0.0.1`だけへbindされる
- migrationとbucket初期化がhealthcheckを待つ
- integration smokeがmigrationとbucket初期化の成功を待つ
- 最小schemaが8 tableを持つ
- object smokeがput、get、SHA-256比較を行う
- source dataとModel Packageをmountしない

## Engine検証

初回確認ではDocker DesktopのLinux Engineが未起動で、named pipe不在とEngine APIのHTTP 500を確認しました。
Engine起動後に同じWindows環境で再検証し、次のcommandが成功しました。

```powershell
npm run compose:check
npm run compose:up
npm run compose:smoke
npm run compose:down
npm run compose:test
```

`compose:up`は長時間稼働するPostgreSQLとMinIOだけを`--wait`で待ち、正常終了するone-shot containerを別に実行します。
これにより、migrationとbucket初期化がexit 0でもComposeが起動失敗と判定する問題を避けています。
既存imageを使った起動は8.3秒で完了し、両serviceがhealthy、migration成功、bucket作成成功を確認しました。
続く`compose:smoke`はobjectのput／get／digest一致に成功し、`compose:down`後にcontainerとnetworkが残らないことを確認しました。

`compose:test`は隔離されたprocess固有のCompose projectを作り、次を実機で検査しました。

- PostgreSQLとMinIOがhealthcheckを通過する
- migrationが8 tableを作成する
- bucketを初期化する
- 35 byteのobjectをput／getし、SHA-256 digestが一致する
- 成功後にtest containerとnetworkを削除する
- test用dataはtmpfsだけを使い、named volumeを作らない

再検証後、`material-workbench-test-*`のcontainer、network、volumeが残っていないことを確認しました。

## setup時間とdisk使用量

対象3 imageを明示的に削除した状態から`npm run compose:test`を実行し、pull、healthcheck、migration、object smoke、cleanupを含めて32.5秒でした。
imageを再利用した`npm run compose:check`と`npm run compose:test`の連続実行は9.1秒でした。

`docker system df -v`で確認した対象imageのdisk使用量は合計約787 MBです。

| image | tag | size |
|---|---|---:|
| PostgreSQL | `17.10-alpine` | 423 MB |
| MinIO server | `RELEASE.2025-09-07T16-13-09Z` | 241 MB |
| MinIO client | `RELEASE.2025-08-13T08-35-41Z-cpuv1` | 124 MB |

test終了後のcontainer使用量は0 Bで、test project由来のvolume使用量も0 Bです。
時間はnetwork速度とDocker Desktopのcache状態で変動します。

## 既知問題

- Docker Desktop processが動いていてもLinux Engine APIがreadyとは限らない。
- Engineの初回起動時にnamed pipe不在またはEngine APIのHTTP 500が続く場合は、`docker version`でServerが返るまで待ってから再実行する。
- Windows filesystemからLinux containerへのbind mountは、WSL filesystemよりmetadata accessが遅い場合がある。
- `.env.example`のcredentialはlocal開発専用であり、共有環境へ転用できない。
- MinIO community imageは固定tagのlocal fixtureであり、production storageの選定結果ではない。
- 現行applicationはPostgreSQLとS3へ接続せず、SQLiteとlocal filesを既定のまま使う。
