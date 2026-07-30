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

Level 1:

```text
npm.cmd run verify:pr -- \
  backend/tests/test_workspace_bundle.py \
  backend/tests/test_persistence_boundaries.py \
  backend/tests/test_project_lifecycle.py \
  backend/tests/test_legacy_workspace_acceptance.py

36 backend passed
286 web passed
1 desktop passed
docs-check / typecheck / application-build / diff gates passed
```

Windows delivery:

```text
npm.cmd run package:windows

Setup、folder ZIP生成成功
portable smoke成功
installed smoke成功
tampered bundle拒否、portableからinstalledへの実 .mdwb restore成功
```

| artifact | SHA-256 |
|---|---|
| `Material-Decision-Workbench-Setup-0.1.0.exe` | `A129F1E12A7E7445210C184D8E771192B43284C99944FA67DE73F5E8BDC506D4` |
| `Material-Decision-Workbench-folder-0.1.0.zip` | `C13EA28803325DDA53954F5F2AD0AD10A40E6FB0161E574702BE964DB84A12D4` |

Release acceptance on commit `c115b1125696108f24827c0fb63de3205358b4d3`:

```text
dependency audit policy / dependency audit: passed
security boundary tests: passed
model package contract tests: passed
full pytest: 1067 passed, 4 skipped
web: 286 passed
desktop: 1 passed
typecheck / application build: passed
legacy Workspace migration / restore: 1 passed
failure-state: cleanup policy 4, accessibility/API offline 16,
  startup diagnostic 1, degraded task 1, catalog conflict 1 passed
chain-degraded: 3 passed
```

統合`acceptance:release`はdefault Playwrightで停止した。結果は
95 passed / 1 skipped / 10 failedで、下記baselineと同一である。
このため、本変更のrequired gateであるlegacy WorkspaceとWindows delivery、
および停止後のfailure-state / chain-degradedは個別に実行した。

## Browser suite context

既定Playwright全体は、本変更前のdetached baselineでも既知失敗が残るため、
Issue #502のrestore受入判定には使わない。比較実行ではbaselineが
95 passed / 1 skipped / 10 failedで、最終release runも
95 passed / 1 skipped / 10 failedだった。本branch固有の新規失敗は0件である。

この変更の正本となる受入証拠は、legacy Workspace acceptance、
Workspace Bundle adversarial tests、Level 1、およびportable / installed appからの
実 `.mdwb` restore smokeとする。
