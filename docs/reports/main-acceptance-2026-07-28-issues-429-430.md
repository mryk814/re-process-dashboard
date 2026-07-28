# Issues #429・#430 Release受入（2026-07-28）

## 結果

- status: passed
- tested commit: `373309a632ee671933b06d6593b818b5108f0ff2`
- duration: 1491.222秒
- 検証カタログ SHA-256: `e35bfa8e9268de3a293d52366f41ecb8df2a84757dfe8f9f0f9968a71681dcd1`
- 自動gate: 16/16 passed
- dependency audit policy・本体監査: passed
- security boundary tests: passed
- model Package contract tests: passed
- full pytest: 960 passed、2 skipped
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
| `Material-Decision-Workbench-Setup-0.1.0.exe` | `aea47292536c78fc63b9f32028bde4bd9b9103ea6e42fdee117f7aa0af022423` |
| `Material-Decision-Workbench-folder-0.1.0.zip` | `90f83a2a417633aff048800f7d3196f8ca5a652f8e16dd108bd52642390f342f` |

## 今回実行しなかったgate

- Compose integration: 今回はCompose境界を変更していない。直近証拠は `docker-compose-local-development-2026-07-27.md`。
- Shared Lab integration: 今回はShared Lab境界を変更していない。直近証拠は `shared-workbench-lab-v1-2026-07-28.md`。
- model Package release evidence: 今回は配布対象のmodel Packageを変更していないため、手動証拠は採取していない。model Package契約テストは自動gateとして実行した。
- 教材の全page目視: 今回は文書の役割別移動と参照追従で、本文・図・組版を変更していない。直近証拠はPR #432の全page contact-sheet reviewとbrowser interaction。

これらは成功扱いせず、JSONでは `not_run` と理由・既存証拠を記録した。
