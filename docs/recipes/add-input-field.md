# Recipe C：入力変数を1つ追加

入力の意味を増やす変更はProfileだけでは完結しません。次を一組として変更します。

```text
TaskDefinition
Dataset Input Profile
Feature Pipelineとversion
Model Package builder / Package
Runtime capability
TaskModule entry（新Taskの場合）
OpenAPI / frontend generated types
contract / golden / smoke / API tests
```

既存Taskのversion更新か新Taskか、学習単位や科学的意味を人間がレビューします。DoctorやUIは決定しません。

```powershell
npm run api:generate
npm run task:inventory
npm run model:build -- --task <task-id> --source <source> --output models/packages/<new-id>
npm run model:verify -- --task <task-id> --source <source> --package models/packages/<new-id>
npm.cmd run verify:focused -- backend/tests/test_task_registry.py backend/tests/test_feature_pipeline.py
```

