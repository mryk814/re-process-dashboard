# Issues #429・#430・#434 Release受入（2026-07-28）

## 結果

- status: passed
- tested commit: `888b596ace6ef3d2ecb7e0f22a9eaeaadf46beba`
- duration: 1362.157秒
- 検証カタログ SHA-256: `39565b1ca46a2aa2793d43e914ee55b9122ede0b65adeb68e041a6bec1262541`
- 自動gate: 16/16 passed
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
- 教材clean build: HTML 44文書、reader PDF、参照・概念・図・Lab・drift検査を通過

機械可読なgate別結果、所要時間、環境、artifact SHA-256は `main-acceptance-2026-07-28-issues-429-430.json` を正本とする。

## 配布artifact

| artifact | SHA-256 |
|---|---|
| `Material-Decision-Workbench-Setup-0.1.0.exe` | `8dba55ba86576491338098fb383e92c625e4b4617c2d1acf6e7a32a79dde797b` |
| `Material-Decision-Workbench-folder-0.1.0.zip` | `4ee8fcc7c50bd466b769a732600970fccb5e09877108514341c5e5907a3c4932` |

## 今回実行しなかったgate

- Compose integration: 今回はCompose境界を変更していない。直近証拠は `docker-compose-local-development-2026-07-27.md`。
- Shared Lab integration: 今回はShared Lab境界を変更していない。直近証拠は `shared-workbench-lab-v1-2026-07-28.md`。
- model Package release evidence: MPEA 2 Taskをimmutableなv2 Packageへ切り替えた。builder、Windows／Linux verifier、smoke、active／previous、rollback、portable digestの証拠は `model-package-portable-digest-v2-2026-07-28.md` に記録した。JSONでは自動成功へ混ぜず、manual gateの`priorEvidence`として参照する。
- 教材の全page目視: 全pageの再確認はしていない。追記したModel Package runtimeとMulti-stage chainは `learning-focused-visual-review-2026-07-28.md` でclean build後のdesktop表示を確認した。残るページの直近証拠はPR #432の全page contact-sheet reviewとbrowser interaction。

これらは成功扱いせず、JSONでは `not_run` と理由・既存証拠を記録した。
