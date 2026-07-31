# Journey log schema

一行一操作のUTF-8 JSONLとする。値がないidentityも`null`を記録する。
時刻はUTCのRFC 3339、sequenceは1から始める。

```json
{
  "schema_version": "scenario-journey-log/v2",
  "scenario_id": "replace-me",
  "scenario_revision": 1,
  "sequence": 1,
  "timestamp": "2026-01-01T00:00:00Z",
  "phase": "goal_formulation",
  "user_intent": "過去実績から数値目標を具体化する",
  "assumption_before_action": null,
  "ui_action": "Data Explorerを開いた",
  "ui_target": "過去実績",
  "expected_result": "現在のDatasetの分布が表示される",
  "observed_result": "103件の実績が表示された",
  "interpretation": "中心域と裾を見て初期目標を置ける",
  "next_decision": "goal formulation v1を保存する",
  "why_next": "候補生成前に判断基準を固定するため。",
  "alternatives_considered": [],
  "open_questions": [],
  "friction": null,
  "capability_status": "ui_available",
  "fallback_authorized": false,
  "evidence": [
    {
      "kind": "screenshot",
      "path": "screenshots/004-data-explorer.png",
      "viewport": "1280x900"
    }
  ],
  "current_identity": {
    "project_id": "replace-me",
    "task_id": "replace-me",
    "dataset_revision_id": "replace-me",
    "profile_digest": "sha256:replace-me",
    "model_package_id": "replace-me",
    "model_package_digest": "sha256:replace-me",
    "objective_revision": "goal-formulation-v1",
    "candidate_id": null,
    "candidate_revision": null,
    "run_id": null,
    "snapshot_id": null
  },
  "outcome": "continued",
  "notes": null
}
```

`capability_status`は`ui_available`、`ui_blocked`、`ui_missing`、`setup_only`、`fallback_used`。
`outcome`は`continued`、`completed`、`blocked`。
blocked時はvisible error、最後に成功したstep、次に試した操作を残す。
evidence pathはoutput root内の相対パスにする。
