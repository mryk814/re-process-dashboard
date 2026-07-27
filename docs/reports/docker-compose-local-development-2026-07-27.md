# Docker Compose local開発基盤の検証記録

## 対象

- revision：`f33294d161633c1238a570e79c5004407b70b850`から開始したIssue #347 branch
- OS：Windows 11
- Docker Client：28.5.1
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

2026-07-27の初回確認ではDocker DesktopのLinux Engineへ接続できませんでした。
最初の`docker version`はnamed pipe `//./pipe/dockerDesktopLinuxEngine`が存在しないと報告しました。

Docker Desktopを起動した後はbackend processが開始しましたが、Engine API `/v1.51/version`がHTTP 500を返しました。
50秒間のstatus確認では`starting`のまま変わらず、`docker desktop restart`も60秒でtimeoutしました。
restart後の`npm run compose:test`は0.9秒で停止し、Linux Engineのnamed pipeが存在しないためMinIO imageを取得できないと報告しました。
そのため、image build／pull、`compose up`、healthcheck、migration実行、object roundtrip、`compose down`は未検証です。

Engineが利用可能なWindows環境では次を順に実行します。

```powershell
npm run compose:check
npm run compose:up
npm run compose:smoke
npm run compose:down
npm run compose:test
```

## setup時間とdisk使用量

静的なCompose正規化とcontract検査は実行できました。
image pullを含むclean setup時間とDocker disk使用量は、Engine APIのHTTP 500により測定できませんでした。

測定時はcacheを消した値と既存imageを再利用した値を分けます。
disk使用量は`docker system df -v`で対象imageとvolumeを記録し、他projectの総量をIssue #347の使用量として扱いません。

## 既知問題

- Docker Desktop processが動いていてもLinux Engine APIがreadyとは限らない。
- Windows filesystemからLinux containerへのbind mountは、WSL filesystemよりmetadata accessが遅い場合がある。
- `.env.example`のcredentialはlocal開発専用であり、共有環境へ転用できない。
- MinIO community imageは固定tagのlocal fixtureであり、production storageの選定結果ではない。
- 現行applicationはPostgreSQLとS3へ接続せず、SQLiteとlocal filesを既定のまま使う。
