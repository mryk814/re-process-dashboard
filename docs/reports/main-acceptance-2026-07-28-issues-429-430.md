# Issues #429・#430・#434・#435 Release受入（2026-07-28）

## 結果

- status: passed
- tested commit: `91c4e8a0de8bff89b0a9131118c77ff330fcfdc8`
- duration: 1179.235秒
- 検証カタログ SHA-256: `39565b1ca46a2aa2793d43e914ee55b9122ede0b65adeb68e041a6bec1262541`
- 自動gate: 15/15 passed
- dependency audit policy・本体監査: passed
- security boundary tests: passed
- model Package contract tests: passed
- full pytest: 960 passed、4 skipped
- Web unit: 271 passed
- default Playwright: 84 passed、1 skipped
- failure-state E2E: accessibility 16件、startup、degraded、catalog conflictを通過
- Chain degraded E2E: 3 passed
- legacy Workspace: passed
- Windows installer／folder ZIP build・別配置smoke: passed
- 教材clean build: 208.227秒、HTML 44文書、reader PDF、参照・概念・図・Lab・drift検査を通過
- Source lifecycleの初期catalog競合: 決定論的E2Eを3回反復し、全体Playwrightでも通過
- Windows改行差: BOM・LF・CRLFを同一内容として扱うself-testと通常CRLF checkoutでのclean buildを通過

機械可読なgate別結果、所要時間、環境、artifact SHA-256は `main-acceptance-2026-07-28-issues-429-430.json` を正本とする。

## 配布artifact

| artifact | SHA-256 |
|---|---|
| `Material-Decision-Workbench-Setup-0.1.0.exe` | `a65ebfd232ea5deba5ff6f0290f881a6c46c648c5d79ad0df8a641390f8ba199` |
| `Material-Decision-Workbench-folder-0.1.0.zip` | `3c46fd14d1c5d7fa0ca41996e47718c765befd04d07516e65e17464ce9a56543` |

## 今回実行しなかったgate

- Compose integration: 今回はCompose境界を変更していない。直近証拠は `docker-compose-local-development-2026-07-27.md`。
- Shared Lab integration: 今回はShared Lab境界を変更していない。直近証拠は `shared-workbench-lab-v1-2026-07-28.md`。
- model Package release evidence: MPEA 2 Taskをimmutableなv2 Packageへ切り替えた。builder、Windows／Linux verifier、smoke、active／previous、rollback、portable digestの証拠は `model-package-portable-digest-v2-2026-07-28.md` に記録した。JSONでは自動成功へ混ぜず、manual gateの`priorEvidence`として参照する。
- 教材の全page目視: 全pageの再確認はしていない。追記したModel Package runtimeとMulti-stage chainは `learning-focused-visual-review-2026-07-28.md` でclean build後のdesktop表示を確認した。残るページの直近証拠はPR #432の全page contact-sheet reviewとbrowser interaction。

これらは成功扱いせず、JSONでは `not_run` と理由・既存証拠を記録した。
