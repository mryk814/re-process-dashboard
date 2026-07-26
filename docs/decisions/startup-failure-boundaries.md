# 起動失敗の境界

Material Decision Workbench は、保存データの整合性を守るために停止すべき失敗と、
一部機能だけを隔離して起動を継続できる失敗を分ける。

## 起動を停止する失敗

次の失敗では FastAPI を起動しない。

- SQLite を開けない、または schema migration を完了できない
- workspace catalog、Dataset Profile、Model Package の固定参照を検証できない
- Package の digest、artifact、adapter allow-list などのセキュリティ契約に違反する
- 共通の Project／Candidate 保存層を安全に読み書きできない

これらを握りつぶすと、別の版での予測、保存済み証跡の破壊、または安全でない
artifact の実行につながるためである。

## 機能を隔離して起動を続ける失敗

個別 Task、Chain、決定論的 Transform、Chain 評価成果物の読込失敗は、対象の
resource id 単位で `unavailable` として記録する。別 Task と共通保存層は起動を
継続する。

| 障害 | 停止する操作 | 継続する操作 |
|---|---|---|
| 決定論的 Transform | 対象 Transform を使う配合編集、依存 Chain 実行 | 別 Task、保存済み Project／履歴の参照 |
| Chain 定義・binding | 対象 Chain の候補編集と実行 | 別 Task、保存済み Chain Project／候補／Snapshot の参照 |
| Chain 評価成果物 | 対象 Chain の段単体／通し評価 | Chain 候補編集・実行、保存済み証跡、別 Task |

依存先が停止した場合は、依存元も `dependency_unavailable` として隔離する。
旧経路へフォールバックしたり、別の Package で自動再計算したりはしない。

## API と UI の契約

- `/api/health` と `/api/readiness` は API 自体が利用可能なら成功応答を返し、
  `degraded` と `optional_subsystems` で隔離状態を示す。
- `/api/subsystem-availability` は resource ごとの `stage`、`cause`、`impact`、
  `recovery_hint` を返す。
- 隔離された操作 API は `503 subsystem_unavailable` と同じ構造を返す。
- UI は対象画面だけを利用不可にし、原因、影響範囲、復旧方法を表示する。
- 保存済み証跡の読取 API は実行 service に依存させない。

## 回帰テスト

障害注入テストでは、壊れた個別 artifact を与えても FastAPI と無関係な Project
履歴が利用できることを確認する。同時に、Store／schema の障害は引き続き起動を
失敗させることを確認する。
