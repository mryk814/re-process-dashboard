# Task単位のdegraded startup

## 決定

アプリ基盤と利用者の保存データを信頼できない障害は、起動全体を停止する。
一つのPrediction Taskに閉じたデータソース、Model Package、予測runtimeの初期化失敗は、そのTaskだけを `unavailable` として起動を継続する。

| 障害 | 動作 |
|---|---|
| active Package設定全体を読めない、Task登録集合が一致しない | 全体停止 |
| DBを開けない、migration・整合性検査に失敗 | 全体停止 |
| Data Library catalogを安全に構築できない | 全体停止 |
| 一つのTaskのsource読込・Package検証・runtime構築に失敗 | そのTaskのみ利用停止 |

例外を握りつぶして利用可能に見せない。
Task Registryは、全Taskについて `available` または `unavailable` のどちらか一つを必ず保持する。
利用停止理由は `source`、`package`、`runtime` のstageと日本語messageで返す。

## 利用停止中に残す操作

- Project一覧・概要
- 候補とarchive済み候補の参照
- 保存済み予測、実測、採用判断を含むProject履歴
- 保存済みsnapshotの詳細
- TaskDefinitionとTask catalog

候補の作成・編集・削除、Project設定・判断の変更、推論、探索、snapshot復元などの変更操作は停止する。
新しいProjectの選択肢には利用停止中のTaskを出さない。

Projectが固定しているPackageが個別には読める場合でも、active Taskが利用停止中なら推論へ迂回しない。
Task状態と画面の説明が食い違わないことを優先する。

## API契約

- `/api/health`: DBとAPIが応答できれば `ok: true`。一つ以上のTaskが停止中なら `degraded: true`
- `/api/readiness`: 基盤が要求を受けられる状態を `ready: true` とし、利用可能Taskと停止中Taskを分けて返す
- `/api/task-definitions` と ProjectのTaskDefinition: `availability` を返す
- 利用停止中Taskの推論・変更API: `503 runtime_unavailable`
- Project・候補・履歴・snapshotの読み取りAPI: 保存済みデータだけで応答する

## 復旧

sourceまたはPackageを正しい版へ戻してアプリを再起動する。
保存済みProjectやsnapshotを最新モデルで自動再計算せず、固定済みprovenanceを保持する。
