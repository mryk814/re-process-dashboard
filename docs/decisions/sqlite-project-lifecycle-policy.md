# SQLite接続とProjectライフサイクル

## 決定

Material Decision WorkbenchのSQLite接続は
`persistence/sqlite_connection.py`だけで生成する。

- DB起動時に一度だけ `journal_mode=DELETE` を固定する
- 各接続で `foreign_keys=ON`、`busy_timeout=5000`、
  `synchronous=FULL` を設定・検証する
- 接続は操作単位で作り、共有context managerで必ずcloseする
- 全migration後に `foreign_key_check` を実行する
- 既存の孤児は自動削除・架空Projectによる補完をせず、対象を表示して起動を拒否する

`DELETE`は、installer版のローカルDBとportable版の実行ファイル横DBを
`-wal` / `-shm` の取り残しなしで移動できることを優先した選択である。
ネットワーク共有上の同一DBを複数PCから同時利用する運用は対象外とする。

## Projectの通常操作

通常UIの「削除」は廃止し、復元可能なarchiveへ置換する。
archive中のProjectは通常一覧と書込経路から外すが、判断証跡は変更しない。
進行中のChain execution claimだけはarchive時に失効させ、遅れて完了した処理が
状態を書き戻すことを防ぐ。

| 領域 | archive | 明示purge |
|---|---|---|
| Project、候補、候補revision | 保持 | 削除 |
| 予測snapshot、実測、screening、判断活動、確認メモ、目標revision | 保持 | 削除 |
| Chain snapshot、分布run、分析variant、execution state | 保持 | 削除 |
| Chain execution claim | 失効 | 削除 |
| Chain定義・revision・stage memo | 保持 | 保持 |
| Data Library、系列資産、データ更新履歴、検討グループ | 保持 | 保持 |

完全purgeは、archive済みProjectに対してProject IDを再指定するAPIだけで行う。
予約Project、後続ProjectがあるProject、別Projectの候補revisionから参照される
Projectはpurgeしない。purge後は `foreign_key_check` が空であることを契約テストで確認する。

## portable / installer確認

両配布形態で次を確認する。

1. 起動後のDBが `journal_mode=delete`、`foreign_keys=1`、
   `busy_timeout=5000`、`synchronous=2` である
2. 終了後にDBファイルを移動でき、`-wal` / `-shm` が残らない
3. 移動先で再起動してProjectを読み書きできる

Developer diagnosticsのDatabase migrationチェックは、上記PRAGMAと
`quick_check`、migration markerを同時に検査する。
