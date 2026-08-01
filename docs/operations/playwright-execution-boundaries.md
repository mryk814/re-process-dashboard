<!--
document-status: current
owner: developer experience
source-of-truth: e2e/suite-inventory.mjs
-->

# Playwright実行境界

通常のE2Eは、同じProjectやWorkspaceを偶然共有して通すものではない。
実行区分と理由、cleanup ownerの正本は[`e2e/suite-inventory.mjs`](../../e2e/suite-inventory.mjs)である。
この文書は実行者が選ぶcommandと、失敗したときの読み方を示す。

| 区分 | 実行方法 | stateの扱い |
| --- | --- | --- |
| `shared-read-only` | `node scripts/run-parallel-e2e.mjs` のread-only段 | seeded Workspaceを読むだけ。spec単位で2 workerまで許可する |
| `isolated` | `node scripts/run-isolated-e2e.mjs` | specごとにfresh API、Web、SQLite、Model/Profile/Task storeを持つ |
| `serial-journey` | `npx playwright test` | `default` Projectのmutationまたは文脈継続を意図して検証するためworkers: 1 |
| `dedicated-runtime` | 個別config／runner | degraded、startup、sample galleryなど専用process条件を持つ |
| `blocked` | default serial suiteのまま | fresh実行でも既存failureがある。retryやparallel化で隠さず、先にfailureを直す |

## 並列回帰

```powershell
node scripts/run-parallel-e2e.mjs
```

前半はread-only specをPlaywright worker 2で実行する。spec内のlarge lineage readは順序を保つので、`fullyParallel`にはしない。
後半はmutableでもProject／Candidate／Runをspec外へ漏らせるものを、別API/Web processとして最大2本動かす。
read-only段も固定portを持たず、`PLAYWRIGHT_API_PORT`／`PLAYWRIGHT_WEB_PORT`が未指定ならOSから別portを取得する。明示指定した二つが同じportなら起動前に停止する。
各実行はOSから別々に取得したAPI/Web portを使い、`test-results/isolated-e2e-*/report.json`へspec、port、exit code、output directoryを記録する。

mutable specはretryを許可しない。runnerは`PLAYWRIGHT_ISOLATED_RETRIES`が`0`以外ならserverを起動する前に停止し、run IDをreportへ残す。これにより、失敗した最初のmutationを同じresource identityへ二重適用するretryを「たまたま通る」結果にしない。

cleanupはglobal teardownの終了時に行う。DB、WAL/SHM、Model/Profile/Task storeの結果は各runの`owned-e2e-cleanup-*.jsonl`に`removed`、`busy`、`failed`として残す。cleanup問題をserver stderrだけで判断しない。

## 順序依存guard

```powershell
node scripts/run-e2e-order-guard.mjs
```

read-only代表群をsingle、逆順、固定seedのshuffled順でfresh serverへ実行する。
結果とseedは`test-results/e2e-order-guard-*/report.json`に残る。
このguardが落ちたspecは、根本原因が分かるまで`shared-read-only`へ置かない。

## 2026-08-01の基準試行

`c0be18e`をbaseにfresh serverで測定した。timeには起動を含む。実行中の既存プロセス数に影響されるため、速度目標ではなく再現可能性の基準として読む。

| 実行 | 結果 | elapsed | flake試行 |
| --- | --- | ---: | --- |
| shared read-only、2 workers | 10/10 passed | 107.7s | 1/1 passed |
| isolated mutable、2 processes | 12/12 passed | 108.2s | 1/1 passed |
| read-only order guard | single 3/3、reverse 10/10、shuffle 10/10 | 108.0s | 3/3 passed |

初回の2 worker試行では`data-library-structure.spec.ts`を誤ってread-onlyに分類して5件失敗した。個人Task／Packageを作成することを確認後、`blocked`へ戻した。この失敗は成功件数へ含めない。
