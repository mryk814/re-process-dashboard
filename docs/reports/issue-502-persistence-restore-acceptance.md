# Issue #502 Persistence / restore acceptance

## Scope

`Store`とWorkspace BundleのGod objectを、aggregate transaction境界と
restore trust phaseに沿って分割した。

- `Store`はaggregate repositoryを束ねるcomposition rootとした。
- 複数aggregateを原子的に更新するcommandは`WorkbenchUnitOfWork`が所有する。
- Workspace Bundleはmanifest、backup、archive validation、restore plan、
  resource install、commit / recoveryへ分離した。
- 保存済みProject、Snapshot、Model Package、学習データのidentityとdigestは
  復元前後で維持する。

詳細な所有境界は
[`docs/architecture/persistence-transaction-boundaries.md`](../architecture/persistence-transaction-boundaries.md)
を正本とする。

## Adversarial acceptance

次の失敗条件を自動テストで固定した。

- Project archive中にProject更新が失敗すると、実行claimの取消もrollbackされる。
- 既存CAS resourceを再利用する前に、primary fileだけでなくmanifestで宣言した
  全ファイル、完全なtree inventory、bundle digestを再検証する。
- 補助画像が改変された既存CAS resourceは復元を拒否し、active DBを変更しない。
- `bundle_root="."`などcanonical Data Library root外へのresource差し替えを
  extraction前に拒否する。
- stagingに宣言外ファイルを差し込んでも、resource installは宣言済みファイルだけを
  配置する。
- archive、backup、resource install、serviceの低レベルphaseが、Project runtimeや
  task registryなどの高レベルapplication serviceへ逆依存しないことをASTで検査する。

## Verification

Focused acceptance:

```text
uv run --extra dev python -m pytest \
  backend/tests/test_workspace_bundle.py \
  backend/tests/test_persistence_boundaries.py \
  backend/tests/test_project_lifecycle.py \
  backend/tests/test_legacy_workspace_acceptance.py -q

36 passed
```

Level 1、portable / installed Windows smoke、最終artifact digestは最終commitで追記する。

## Browser suite context

既定Playwright全体は、本変更前のdetached baselineでも既知失敗が残るため、
Issue #502のrestore受入判定には使わない。比較実行ではbaselineが
95 passed / 1 skipped / 10 failed、本branchが98 passed / 1 skipped / 7 failedで、
本branch固有の新規失敗は0件だった。

この変更の正本となる受入証拠は、legacy Workspace acceptance、
Workspace Bundle adversarial tests、Level 1、およびportable / installed appからの
実 `.mdwb` restore smokeとする。
