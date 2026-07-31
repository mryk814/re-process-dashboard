# Repository structure audit — 2026-07-30

> #613 update: 2026-08-01. ここでは単なる行数の平準化ではなく、責任名から
> 到達できるpackage-firstのauthority mapを記録する。

## 結論

現在の主な問題は総ファイル数ではなく、責任境界がflat module名から読みにくくなることと、
transaction／Workbook解釈のような一体のauthorityを行数だけで分割してしまうことである。

`backend/src/decision_workbench/`の第一階層は維持する。下位責任が複数あるときだけpackageを
掘り、旧pathを互換shimとして残さない。source dataやModel Packageは利用頻度で削除せず、
provenanceと再生成性を先に確認する。

## #613 再計測

2026-08-01に、`*.py`の直接の子と再帰的な子を数えた。直接の子が減ったこと自体を目的には
せず、どのdirectoryがどのauthorityを持つかを確認するための測定である。

| path | direct Python files | recursive Python files | authority |
|---|---:|---:|---|
| `application/` | 37 | 56 | application use-case。複数phaseのものは下位packageへ置く。 |
| `application/chain/` | 5 | 5 | plan、stage execution、全体execution、snapshot。`__init__.py`はre-exportしない。 |
| `application/workspace_bundle/` | 8 | 8 | public facade、manifest、backup、archive validation、restore plan、resource install、service、shared。 |
| `persistence/` | 41 | 41 | SQLite schema／migration、aggregate repository、cross-aggregate command。 |
| `data/` | 8 | 14 | Workbook／Profile／canonicalizationとsource由来の解釈。 |

## Package-first authority map

### Chain

`application/chain/`は次の一方向のuse-case群である。

| module | responsibility |
|---|---|
| `plan.py` | Candidate input定義、bindingと実行planの作成 |
| `stage_execution.py` | canonical input、memo key、単段の実行 |
| `execution.py` | request競合、部分再計算、段実行の調停 |
| `snapshot.py` | immutable snapshotとactual-conditioned variant |

旧`chain_execution_*.py`は残していない。利用側は`decision_workbench.application.chain.<role>`を
直接importする。これにより、候補shapeのdomain解釈をChain Coreへ戻さない。

### Workspace Bundle

`application/workspace_bundle/__init__.py`は、画面／sidecarが使うbackup・prepare・commit・
rollback・recoveryの公開use-caseだけをまとめる。実装phaseは次のpathに直接置く。

| module | responsibility |
|---|---|
| `manifest.py` | DB、resource、row payloadのidentity／digest |
| `backup.py` | logical SQLite snapshotとbundle生成 |
| `archive.py` | ZIP安全性、path、symlink、digest、manifest検証 |
| `restore_plan.py` | staging migration、参照診断、journal準備 |
| `resource_install.py` | CAS／Data Libraryの検証付き配置とcleanup |
| `service.py` | commit、rollback、finalize、restart recovery |
| `shared.py` | phase共通の小さな型・定数 |

`prepare`はactive Workspaceを変更しない。`commit`だけがresource配置とDB切替のtransaction
ownerであり、失敗時はjournalを根拠に新規resourceを取り除き旧DBへ戻す。詳細は
[PersistenceとWorkspace restoreのtransaction境界](persistence-transaction-boundaries.md)を正本とする。

## 残す大きなmodule

次の3ファイルは2026-08-01時点で1,000行を超えるが、line-count-onlyの分割は行わない。
いずれも同じidentityまたはtransactionを保持するauthorityである。

| module | lines | いま残す理由 | 次に分ける条件 |
|---|---:|---|---|
| `persistence/store_unit_of_work.py` | 1,088 | 複数aggregate commandの検査・更新とSQLite commit／rollbackを同じ境界で所有する。 | command familyごとに同じconnectionとrollback ownershipを守る契約が明示できるとき。 |
| `persistence/data_lifecycle_repository.py` | 1,062 | Raw Snapshot、Curation、承認済みDataset、Training Snapshotを一つのlifecycle provenanceとして保存する。 | revision identityと参照整合をまたがないread/write境界が先に確立したとき。 |
| `data/importer.py` | 1,094 | Workbook読込、entity／relation解決、canonical rowとlineage生成が`WorkbookData`の意味境界を形成する。 | `WorkbookData`とrelation-routeの安定portを定義し、profile解釈を他層へ漏らさず移せるとき。 |

## 分割の原則

- public import pathを先に決め、移動と振る舞い変更を同じ変更に混ぜない。
- schemaやrepositoryをファイル行数が均等になるようには分けない。保存・復元・不変条件を共有する
  unitでのみ分ける。
- 元Excel、保存済みSnapshot、Package contractにmigrationを発生させない。
- package化した後は旧moduleをre-exportまたは互換shimとして残さない。
- 構造変更は、依存方向、代表的なuse-case、rollback／recoveryの順にfocused checkを選び、
  関係しない全体テストを常に要求しない。

## Backend scriptsと入口

`backend/scripts/README.md`をscriptの用途、owner、output、参照の索引とする。日常のoperations、
成果物authoring、acceptance、experimentをdirectoryと索引で区別し、source dataへ書き込む
authoring scriptは置かない。

データを追加する人は`docs/operations/data-contributor-start-here.md`、アプリを開発する人は
`docs/developer-start-here.md`を入口にする。データ利用レーンには、アプリ変更用のtest一式、
Issue、branch、PRを既定で要求しない。
