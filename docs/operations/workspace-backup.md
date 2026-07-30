# Workspaceバックアップ／復元

## 目的

Desktop版の「ワークスペース」から、判断履歴と再現に必要な資源を単一の
`.mdwb` bundleへ退避し、別の空のuser data directoryへ復元できる。
SQLiteファイルを直接コピーせず、動作中でも整合したsnapshotを取得する。

## 開発・レビュー用Workspace

`npm run dev` は判断台帳の `data/workbench.db` ではなく、git branchごとの
`.dev-workspaces/<branch名>-<短いhash>.db` を既定にする。branch名を正規化した
表示名に元のbranch名のhashを加えるため、`feature/a`と`feature-a`も衝突しない。
`WORKBENCH_DB_PATH` が指定されて
いる場合はそのパスを優先し、`npm run dev:main-workspace` は明示的に
`data/workbench.db` を開く。現在のDBとData Libraryのパスは起動ログと
「ワークスペース」画面で確認する。

レビュー開始点を揃える場合はserverを停止して次を実行する。

```powershell
npm run workspace:seed
```

このcommandは意味内容を固定したseedから一時的な `.mdwb` bundleを生成し、通常の
prepare／commit／readiness確認／finalize経路でbranch Workspaceへ復元する。
初期候補IDとsystem timestampも固定し、Project・候補・入力の内容digestは
繰り返し実行しても同一になる。`WORKBENCH_DB_PATH`または
`WORKBENCH_DATA_LIBRARY_PATH`が指定された状態では実行を拒否し、長寿命Workspaceを
review seedで置き換えない。
SQLiteの直接コピーはしない。生成したbundleは一時領域だけに置き、
リポジトリへ保存しない。

起動前の整合検査はDBをread-onlyで開き、catalog、Project binding、Chain Revision
を現行repoと突き合わせる。

```powershell
npm run workspace:check
```

不整合時は原因resource、登録済み／現行digest、影響、次の操作を表示し、
DBの行やdigestを自動修復しない。

APIを起動できないWorkspaceのPackage登録は、serverを停止したまま保守CLIで
確認する。

```powershell
npm run workspace:maintenance -- inspect
```

未参照の登録だけは `deactivate --package-ref <id> --reason <理由>` で利用停止
できる。Project、Prediction Snapshot、Chain Stage memoなど保存済み判断証拠から
参照中なら場所を示して拒否する。利用停止と、次回bootstrapでの
現行contract再登録は監査行へ残る。`--main-workspace`を付けた場合だけ
`data/workbench.db`を対象にするため、判断台帳へ保守操作を行う前に
`inspect`の表示パスを必ず確認する。

branch Workspaceの一覧はDBをread-onlyで扱う次のcommandで確認する。

```powershell
npm run workspace:list
```

path、対応branch、最終更新時刻、DB/WAL/SHMの合計sizeと保護理由を表示する。
`main`、現在branch、登録済みgit worktree、対応branchが不明なDBはprune不可である。
孤児DBは自動判定で消さず、必要なら由来を確認して手動で退避する。
削除するときはdev serverを止め、一覧に表示された未参照DBのpathを省略せず指定する。

```powershell
npm run workspace:prune -- --database C:\path\to\repo\.dev-workspaces\<name>.db
```

`npm run clean`はbuild生成物、`npm run clean:evidence`はtest／Playwright／
acceptance evidenceだけを対象にする。どちらもWorkspace、`data/source/`、
`models/packages/`を削除しない。branch DBの削除は`workspace:prune`に限定する。

## bundle v2

`workspace-bundle/v2` は次を含む。

- SQLite Backup APIで取得した `workbench.db`
- schema migrationのIDとchecksum
- 全業務テーブルの行数とcanonical digest
- Raw Snapshot／Curation Runが参照するcontent-addressed row payload
- Data Asset本体と、Profileが相対パスで宣言する観察画像
- DBに登録されたModel Package本体
- app version、active Package参照、診断結果

Data AssetとModel Packageはcontent digestで索引し、復元先Data Libraryへ
配置する。Model PackageからPythonコード、pickle、joblibを読み込まない。
決定論的Transformはアプリ本体のallow-listされた実行資源であり、bundleへ
複製せず、復元準備時に固定digestが現行アプリと一致するか診断する。
row payloadもmanifestへsizeとSHA-256を列挙し、DBが参照する全fileとmanifestの
集合が一致することをstagingで検証する。欠損・余剰・改ざんがあればDB切替前に
復元を拒否する。
移行時に解釈できなかった旧inline payloadは原文をSHA-256名のquarantine fileへ
退避し、findingとともにbundleへ含める。`workspace-bundle/v1`は読込互換を保ち、
inline rowをstagingでCASへ移した前後のsemantic identityを照合する。

実装のphase境界とtransaction ownerは
[PersistenceとWorkspace restoreのtransaction境界](../architecture/persistence-transaction-boundaries.md)
を正本とする。`application.workspace_bundle`はpublic use-caseのfacadeであり、
archive検査やresource配置を直接呼び出さない。

## 復元手順

1. bundleのentry数、展開サイズ、圧縮率、パス、symlink、空き容量を検査する。
2. stagingへstream展開し、各ファイルのsizeとSHA-256を照合する。
3. bundle記載のmigrationだけを確認し、アプリのallow-list済みmigrationで
   staging DBを現行schemaへ上げる。
4. `integrity_check`、foreign key、表ごとの行数／digestを確認する。
5. Projectが固定したDataset、Model Package、Chain Revision、Chain Stageの
   参照を診断する。
6. APIを停止し、Data Library資源とrow payloadを追加した後、最後にDBを切り替える。
7. 新WorkspaceでAPI healthを確認してから旧DBを破棄する。

prepare、commit、API再起動のいずれかに失敗した場合は旧DBを維持または
自動で切り戻す。今回の復元で新規配置したrow payloadもrollback時に除去する。
既存CAS fileは共有されうるため削除しない。復元途中の状態はjournalへ残し、
次回起動時にも回収する。

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
