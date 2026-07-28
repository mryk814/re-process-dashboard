# Issues #429・#430 Release受入（2026-07-28）

## 結果

- status: passed
- tested commit: `f65dd3d7ede398321ef07dff62737bd01bd28f66`
- duration: 1361.188秒
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
| `Material-Decision-Workbench-Setup-0.1.0.exe` | `131893d86a19ef287ed739953f76a4c613f953b5644c17c7d9eed5ddd0071ace` |
| `Material-Decision-Workbench-folder-0.1.0.zip` | `6c539a58767207a4d4d6124e9065c212a8d4443355dbc9b0d5706c901aeec5e2` |

## 今回実行しなかったgate

- Compose integration: 今回はCompose境界を変更していない。直近証拠は `docker-compose-local-development-2026-07-27.md`。
- Shared Lab integration: 今回はShared Lab境界を変更していない。直近証拠は `shared-workbench-lab-v1-2026-07-28.md`。
- 教材の全page目視: 今回は文書の役割別移動と参照追従で、本文・図・組版を変更していない。直近証拠はPR #432の全page contact-sheet reviewとbrowser interaction。

これらは成功扱いせず、JSONでは `not_run` と理由・既存証拠を記録した。
