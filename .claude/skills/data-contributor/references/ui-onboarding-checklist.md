# UI onboarding checklist

重要操作の直後に記録する。説明可能な根拠は一〜三文に留め、最後に記憶から再構成しない。

```yaml
timestamp:
phase:
user_intent:
current_identity:
  dataset_revision:
  profile_digest:
  task_id:
  package_id:
  project_id:
ui_action:
ui_target:
expected_result:
observed_result:
meaning_decision:
assumption:
next_action:
why_next:
capability_status: ui_available | ui_blocked | ui_missing | setup_only | fallback_used
fallback_authorized: false
evidence:
```

## Checkpoints

- [ ] source、license、provenance、用途を確認した
- [ ] 三経路を意味で選び、理由を記録した
- [ ] input／output／role／unitを確認した
- [ ] row grainとrelationを確認した
- [ ] physical、default、training observed rangeを分けた
- [ ] Dataset Revisionを記録した
- [ ] estimator、build、verify、Package identityを追跡した
- [ ] 再読込後にTask、Dataset、Packageを画面で選んだ
- [ ] 新Projectと代表予測を確認した
- [ ] UI不足、fallback、未解決事項を隠していない
- [ ] Scenario Journey handoffを作った
