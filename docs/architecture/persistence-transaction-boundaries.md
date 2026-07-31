# PersistenceとWorkspace restoreのtransaction境界

## Store facade

`persistence.store.Store`はcomposition rootであり、呼出し側が使う単一の
connection ownerである。実装はaggregateごとに次へ分ける。

| 境界 | module | transaction owner |
|---|---|---|
| ProjectとObjective | `project_repository.py` | `ProjectRepository` |
| Candidateとrevision | `candidate_repository.py` | `CandidateRepository` |
| Chain catalog、execution、distribution | `chain_repository.py` | `ChainRepository` |
| Snapshot、Actual、Activity、review | `evidence_repository.py` | `EvidenceRepository` |
| 複数aggregateを同時に検査・更新 | `store_unit_of_work.py` | `WorkbenchUnitOfWork` |

Repositoryは自分のaggregateだけを完結して更新する。
Project作成と初期Candidate作成、Project decisionとSnapshotの対応検査、
Candidate更新とChain execution generationの更新、SnapshotとActualの同時保存など、
途中状態を公開してはいけない操作だけを`WorkbenchUnitOfWork`へ置く。
各methodが開いたSQLite connectionと`with conn:`が、そのcommand全体の
commit／rollback ownerである。Repositoryをまたぐ上位の暗黙transactionは作らない。

`Store._init`は既存Workspaceのsupport floorである。
migrationの順序、履歴、archive write guard、foreign key検査を維持し、
repository分割を理由にmigrationを削除またはsquashしない。

## Workspace Bundle

`application.workspace_bundle`はpublic use-caseだけを公開する薄いfacadeである。
物理的な入口は`application/workspace_bundle/__init__.py`であり、phase実装を
facadeから再exportしない。

| phase | module | 責務 |
|---|---|---|
| manifest / evidence | `workspace_bundle/manifest.py` | DB・resource・row payloadのidentityとdigest |
| backup | `workspace_bundle/backup.py` | SQLite logical snapshotとbundle生成 |
| archive validation | `workspace_bundle/archive.py` | ZIP上限、path、symlink、digest、manifest整合 |
| restore plan | `workspace_bundle/restore_plan.py` | staging migration、参照診断、journal準備 |
| resource install | `workspace_bundle/resource_install.py` | CAS／Data Libraryの検証付き配置とcleanup |
| service | `workspace_bundle/service.py` | commit、rollback、finalize、restart recovery |

`prepare`はactive Workspaceを変更しない。
`commit`だけがresource配置とDB切替のtransaction ownerであり、resourceを先に配置し
DBを最後に切り替える。失敗時はjournalを根拠に旧DBを戻し、このrestoreが新規配置
したresourceだけを除去する。`finalize`は新Workspaceのhealth確認後に旧DBを破棄する。

保存済みSnapshot、Projectのimmutable binding、Model Package referenceのidentityは
内容として移送し、最新resourceへ暗黙更新しない。locatorだけは別user data directory
の検証済み配置先へstagingでrebindし、digestと参照identityを変えない。

## 現時点で残す大きなauthority module

行数を均すだけの分割はtransaction境界やWorkbook解釈を曖昧にするため、次のmoduleは
2026-08-01時点では残す。次に分けるなら、先に表の責任単位を独立した契約として
検証できるようにする。

| module | 行数 | 残す理由 | 分割を検討する条件 |
|---|---:|---|---|
| `persistence/store_unit_of_work.py` | 1,088 | 複数aggregateの検査・更新とSQLite commit／rollbackを一つのcommand境界として所有する。 | command familyごとに、同じconnectionとrollback ownershipを保つpublic use-caseが明示できるとき。 |
| `persistence/data_lifecycle_repository.py` | 1,062 | Raw Snapshot、Curation、承認済みDataset、Training Snapshotのprovenanceを同じlifecycle aggregateとして保存する。 | 各revisionのidentityと参照整合を跨がずに分けられるread/write境界が確立したとき。 |
| `data/importer.py` | 1,094 | Workbookのsheet読込、entity／relation解決、canonical rowとlineage生成が`WorkbookData`の一つの意味境界を作る。 | `WorkbookData`とrelation-routeの安定したportを先に定義し、profile解釈をpersistence／modelingへ漏らさず移せるとき。 |
