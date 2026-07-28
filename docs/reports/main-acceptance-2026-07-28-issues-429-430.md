# Issues #429・#430 Release受入（2026-07-28）

## 結果

- status: passed
- tested commit: `0820c588c168a92f21fecb59e995ff31cd0eddd1`
- duration: 1400.309秒
- 検証カタログ SHA-256: `92c146586c1c098b85778a69e94cc1f807264ccc73826a86a44e9466ccfa3514`
- 自動gate: 16/16 passed
- dependency audit policy・本体監査: passed
- security boundary tests: passed
- model Package contract tests: passed
- full pytest: 959 passed、4 skipped
- Web unit: 271 passed
- default Playwright: 84 passed、1 skipped
- failure-state E2E: accessibility 16件、startup、degraded、catalog conflictを通過
- Chain degraded E2E: 3 passed
- legacy Workspace: passed
- Windows installer／folder ZIP build・別配置smoke: passed
- 教材clean build: HTML 44文書、reader PDF、参照・概念・図・Lab・drift検査を通過

機械可読なgate別結果、所要時間、環境、artifact SHA-256は `main-acceptance-2026-07-28-issues-429-430.json` を正本とする。

## 配布artifact

| artifact | SHA-256 |
|---|---|
| `Material-Decision-Workbench-Setup-0.1.0.exe` | `b7360e69126ee75e69ff3dfbe6e8d79b8ba875a195f16f8a75934092ea3c0736` |
| `Material-Decision-Workbench-folder-0.1.0.zip` | `2cc055d25c745ace97e370d9cb7069d2a9443d7ebde4e2eaeb84774efcc731a9` |

## 今回実行しなかったgate

- Compose integration: 今回はCompose境界を変更していない。直近証拠は `docker-compose-local-development-2026-07-27.md`。
- Shared Lab integration: 今回はShared Lab境界を変更していない。直近証拠は `shared-workbench-lab-v1-2026-07-28.md`。
- model Package release evidence: MPEA 2 Taskをimmutableなv2 Packageへ切り替えた。builder、Windows／Linux verifier、smoke、active／previous、rollback、portable digestの証拠は `model-package-portable-digest-v2-2026-07-28.md` に記録した。JSONでは自動成功へ混ぜず、manual gateの`priorEvidence`として参照する。
- 教材の全page目視: 今回は文書の役割別移動と参照追従で、本文・図・組版を変更していない。直近証拠はPR #432の全page contact-sheet reviewとbrowser interaction。

これらは成功扱いせず、JSONでは `not_run` と理由・既存証拠を記録した。
