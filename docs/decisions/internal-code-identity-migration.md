<!--
document-status: decision
verified-commit: 50e403c697910b699a95cf7aa3082baec30a8b42
owner: architecture
source-of-truth: approved internal identity migration decision
-->

# 内部コード identity の移行

| 項目 | 内容 |
| --- | --- |
| 状態 | 決定済み |
| 記録日 | 2026-08-01 |
| 追跡 | [#567](https://github.com/mryk814/re-process-dashboard/issues/567) |

## 決定

利用者向け名称の変更とは別に、内部コードとoperator interfaceをdomain-neutralな名称へ移行する。

Python namespaceは`decision_workbench`とする。

Python distributionとroot npm packageは`evidence-decision-workbench`とする。

npm workspace scopeは`@evidence-decision-workbench/*`とする。

sidecarのprogram名と配布物名は`decision-workbench-sidecar`とする。

Model Package overrideなど旧`MATERIAL_WORKBENCH_*`環境変数は`DECISION_WORKBENCH_*`へ置き換える。

旧環境変数を読むaliasは置かない。

起動時に旧環境変数を検出した場合は、新しい変数名を示して停止する。

この停止は、旧設定を黙って無視して別のPackageで起動することを防ぐためである。

## identityの分類

| 分類 | 対象 | 方針 |
| --- | --- | --- |
| code-only | Python namespace、import、PyInstaller data path、開発用temp名 | 完全に新名へ移行する |
| code-only | pyproject、root npm package、workspace npm scope、sidecar名 | 完全に新名へ移行する |
| operator | `MATERIAL_WORKBENCH_*` | `DECISION_WORKBENCH_*`へ置換し、旧名はfail closedにする |
| persisted | Electron appId、Windows/XDG user-data path、SQLite、`.mdwb`、localStorage key | 変更しない |
| persisted | Task、Profile、Dataset、Package IDとdigest、schema | 変更しない |
| delivery asset | `data/source/material_workbench_*.xlsx`、Compose project名 | 変更しない |
| historical evidence | report、acceptance、drift review、過去ADR | 機械置換しない |

Composeの`name: material-workbench`は既存PostgresとMinIO volumeのprefixである。

これを変更すると既存の開発infraを別volumeとして開くため、volume migrationを別判断にするまで維持する。

`material-workbench-*`という開発専用のtemp名だけは新名へ移行してよい。

## 保存済みデータの扱い

Model PackageはPythonコードを含まないdata-only artifactである。

そのためnamespace移行だけでは既存Packageの再生成やDB migrationを必要としない。

既存installerは同じappIdとuser-data pathを使い続ける。

更新後も既存Workspace、Project、Snapshot、layout設定、backupを同じ場所から開く。

source Excelのファイル名は正本assetと既存Datasetのlocatorに結び付くため変更しない。

## 開発と運用の入口

新しいPython importは`decision_workbench`から始める。

新しいPackage overrideは`DECISION_WORKBENCH_*_MODEL_PACKAGE`を使う。

`WORKBENCH_*`は製品名に依存しない既存のruntime設定なので変更しない。

旧`MATERIAL_WORKBENCH_*`をshell profileやCI設定に残している場合は、新名へ置換してから起動する。

利用者向け名称と保存identityの方針は[domain-neutralな製品境界](domain-neutral-product-boundary.md)を参照する。

## 検証

namespaceとTask登録は既存のpackage layoutとTask registry testで確認する。

既存Workspaceの保持はWindows package upgrade smokeで確認する。

同じsmokeでWorkspace backupとrestoreを一回だけ確認する。

通常CIはPRで一回だけ実行する。
