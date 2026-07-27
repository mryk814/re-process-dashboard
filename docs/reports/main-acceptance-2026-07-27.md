# Main acceptance — 2026-07-27

- Status: **passed**
- Tested commit: `684cb5eff9139d3c16101c698e1225f5e4ae7a30`
- Run ID: `20260727T172328Z`
- Duration: 998.265 seconds
- Isolated Playwright database: yes
- Tracked changes at start: none

| Gate | Result | Duration | Evidence |
|---|---:|---:|---|
| Backend pytest | passed | 228.444 s | 911 passed, 3 skipped, 58 warnings |
| Web unit tests | passed | 1.585 s | 271 passed |
| Desktop unit tests | passed | 1.558 s | 1 passed |
| Generated contracts and typecheck | passed | 16.210 s | import boundaries and typecheck passed |
| Web and Desktop build | passed | 6.732 s | both builds passed |
| Default Playwright | passed | 183.533 s | 83 passed, 1 skipped |
| Failure-state Playwright | passed | 129.184 s | API offline 16 passed; startup diagnostic 1 passed; unavailable task 1 passed; catalog conflict passed |
| Chain degraded Playwright | passed | 37.542 s | 3 passed |
| Legacy workspace migration smoke | passed | 5.974 s | catalog-era single-task and Chain histories migrated and remained readable |
| Windows installer and moved portable delivery | passed | 386.504 s | folder ZIP and per-user installer smoke passed |

## Delivery artifacts

| Artifact | Bytes | SHA-256 |
|---|---:|---|
| `Material-Decision-Workbench-Setup-0.1.0.exe` | 164,273,399 | `2f93cc97d3b2c9bbe6c7fe06fcec3d0b0b98986edc076fe6919c2dcfa74c76ba` |
| `Material-Decision-Workbench-folder-0.1.0.zip` | 223,120,111 | `0b2ecb1b03fe25ebd2ce47397df4b54f7fcb441dca4aa48da7c44915ac66a95d` |

The machine-readable report is
[`main-acceptance-2026-07-27.json`](main-acceptance-2026-07-27.json).
