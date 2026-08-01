<!--
document-status: compatibility
verified-commit: 50e403c697910b699a95cf7aa3082baec30a8b42
owner: desktop, workspace, and web maintainers
source-of-truth: intentional persisted identity and migration policy
-->

# 互換 identity 台帳

この台帳は、rename後にも残る旧名を「消し忘れ」と誤読しないための正本です。
コード名の移行判断は[内部コード identity の移行](../decisions/internal-code-identity-migration.md)を、
保存データの個別schemaは対応するcontractとmigrationを優先します。

| identity | 区分 | 方針 | 残す理由／削除条件 | 確認するtest・証拠 |
| --- | --- | --- | --- | --- |
| Electron appId `jp.local.material-decision-workbench` | persisted | **must preserve** | 同一install/update identityを維持する。変更は既存installerの移行計画とupgrade smokeを伴う別決定にする | `backend/tests/test_windows_packaging_contract.py`、`scripts/smoke-windows-upgrade.ps1` |
| `%LOCALAPPDATA%\\Material Decision Workbench` とportableの`user-data` | persisted | **must preserve** | 既存Workspace、DB、ログ、Data Library、個人Profile／Task／Modelの場所である。明示的なdata migrationとrollbackを設計するまで変えない | `backend/tests/test_launch_token.py`、`backend/tests/test_model_lifecycle.py`、Windows package upgrade smoke |
| `material-workbench-*` localStorage key | persisted | **must preserve** | Project選択、layout、比較基準、navigationの利用者状態を更新後も読めるようにする。key versionを上げる変更は読取り／移行／画面確認を同じ変更で行う | Web unit、該当Playwright spec、`apps/web/src/features/workbench/useWorkbenchSession.ts` |
| Workspace、Task、Profile、Dataset、Package IDとdigest、`.mdwb` schema | persisted | **must preserve** | Project、Snapshot、Package、backupの科学的identityである。変更はadditive migrationまたは新revisionで表し、旧recordを自動推測で書き換えない | contract／migration pytest、Package verify、legacy Workspace acceptance |
| migration readerとschema migration ID／checksum | persisted | **must preserve** | 適用済みDBを現在のschemaとして信頼できるか判定する証拠である。readerを外す前に、対象DBが存在しないことと復旧経路を別途確認する | `backend/tests/test_candidate_migration.py`、`backend/tests/test_workspace_catalog_migration.py` |
| `MATERIAL_WORKBENCH_*` operator environment variable | code-only removed | **code-only removed** | 旧名を読むaliasは置かない。検出したら新名を示して停止し、意図しないPackageでの起動を防ぐ | `backend/src/decision_workbench/bootstrap/operator_identity.py` とそのfocused test |
| `material-workbench-*` temp directory・spike作業名 | code-only | **may migrate later** | 保存場所でも利用者契約でもない。作業時の可読性を優先して残っているため、renameは保存互換と無関係な小さなcode cleanupとして扱える | `rg`でpersisted pathと混同していないこと、affected smoke |
| Compose project name `material-workbench` | compatibility | **must preserve** | 既存Postgres／MinIO volume prefixを指す。変更するならvolume migration、backup、restoreを伴うinfra変更にする | `npm run compose:check`、isolated Compose integration |

`must preserve`は名前を永久固定する意味ではありません。
削除・renameには、既存データの場所またはIDを失わないmigration、rollback、対象環境での復旧確認を先に追加します。
