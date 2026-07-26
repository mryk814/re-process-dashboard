# Workspaceバックアップ／復元

## 目的

Desktop版の「ワークスペース」から、判断履歴と再現に必要な資源を単一の
`.mdwb` bundleへ退避し、別の空のuser data directoryへ復元できる。
SQLiteファイルを直接コピーせず、動作中でも整合したsnapshotを取得する。

## bundle v1

`workspace-bundle/v1` は次を含む。

- SQLite Backup APIで取得した `workbench.db`
- schema migrationのIDとchecksum
- 全業務テーブルの行数とcanonical digest
- Data Asset本体と、Profileが相対パスで宣言する観察画像
- DBに登録されたModel Package本体
- app version、active Package参照、診断結果

Data AssetとModel Packageはcontent digestで索引し、復元先Data Libraryへ
配置する。Model PackageからPythonコード、pickle、joblibを読み込まない。
決定論的Transformはアプリ本体のallow-listされた実行資源であり、bundleへ
複製せず、復元準備時に固定digestが現行アプリと一致するか診断する。

## 復元手順

1. bundleのentry数、展開サイズ、圧縮率、パス、symlink、空き容量を検査する。
2. stagingへstream展開し、各ファイルのsizeとSHA-256を照合する。
3. bundle記載のmigrationだけを確認し、アプリのallow-list済みmigrationで
   staging DBを現行schemaへ上げる。
4. `integrity_check`、foreign key、表ごとの行数／digestを確認する。
5. Projectが固定したDataset、Model Package、Chain Revision、Chain Stageの
   参照を診断する。
6. APIを停止し、Data Library資源を追加した後、最後にDBを切り替える。
7. 新WorkspaceでAPI healthを確認してから旧DBを破棄する。

prepare、commit、API再起動のいずれかに失敗した場合は旧DBを維持または
自動で切り戻す。復元途中の状態はjournalへ残し、次回起動時にも回収する。

## 互換性と信頼境界

- 既知の旧schemaはstagingでmigrationし、未知のmigration IDやchecksum違いは
  理由を明示して拒否する。
- SHA-256は破損・取り違えの検出であり、配布者の真正性を証明する署名ではない。
- 固定参照を現行runtimeで解決できない場合も、保存済み判断証拠は削除しない。
  復元前の確認画面へ警告を出し、実行不能な理由を残す。
- `data/source/` は読取専用の正本であり、backup／restoreは変更しない。

## 検証

契約テストは、同時書込み、別user dataへの復元、旧schema、欠損asset、
改ざん、危険なZIP、空き容量不足、切替失敗時の原状維持を対象にする。
配布前は `npm run package:windows` のpackaged Desktop smokeで、native
file dialogを経由したbackup／restoreと復元後のAPI起動を確認する。
