# Journey log schema

一行一操作のUTF-8 JSONLとする。
値がないidentityは省略せず`null`を記録する。
時刻はUTCのRFC 3339、sequenceは1から始まる連番にする。

```json
{
  "schema_version": "scenario-journey-log/v1",
  "scenario_id": "concrete-slump-journey",
  "scenario_revision": 1,
  "sequence": 1,
  "timestamp": "2026-01-01T00:00:00Z",
  "user_intent": "過去実績の分布を見る",
  "action": "Data Explorerを開いた",
  "expected": "現在のDatasetの実績が表示される",
  "observed": "103件の実績が表示された",
  "wait_or_backtrack": {
    "wait_seconds": 0,
    "backtracked": false,
    "reason": null
  },
  "evidence": [
    {
      "kind": "screenshot",
      "path": "screenshots/004-data-explorer.png",
      "viewport": "1280x900"
    }
  ],
  "current_identity": {
    "project_id": "replace-me",
    "candidate_id": null,
    "candidate_revision": null,
    "run_id": null,
    "snapshot_id": null
  },
  "outcome": "continued",
  "notes": null
}
```

`outcome`は`continued`、`completed`、`blocked`のいずれか。
blocked時は`observed`へvisible error、`notes`へ最後に成功したstepと次に試した操作を記録する。
evidence pathはoutput root内の相対パスにする。
