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

| phase | module | 責務 |
|---|---|---|
| manifest / evidence | `workspace_bundle_manifest.py` | DB・resource・row payloadのidentityとdigest |
| backup | `workspace_bundle_backup.py` | SQLite logical snapshotとbundle生成 |
| archive validation | `workspace_bundle_archive.py` | ZIP上限、path、symlink、digest、manifest整合 |
| restore plan | `workspace_bundle_restore_plan.py` | staging migration、参照診断、journal準備 |
| resource install | `workspace_bundle_resource_install.py` | CAS／Data Libraryの検証付き配置とcleanup |
| service | `workspace_bundle_service.py` | commit、rollback、finalize、restart recovery |

`prepare`はactive Workspaceを変更しない。
`commit`だけがresource配置とDB切替のtransaction ownerであり、resourceを先に配置し
DBを最後に切り替える。失敗時はjournalを根拠に旧DBを戻し、このrestoreが新規配置
したresourceだけを除去する。`finalize`は新Workspaceのhealth確認後に旧DBを破棄する。

保存済みSnapshot、Projectのimmutable binding、Model Package referenceのidentityは
内容として移送し、最新resourceへ暗黙更新しない。locatorだけは別user data directory
の検証済み配置先へstagingでrebindし、digestと参照identityを変えない。
