# Task compositionの依存方向

Prediction Taskの追加点は、責任ごとに四つへ分けます。

```text
contracts / data profile
          ↓
task_composition/ports.py
          ↓
task_composition/descriptors.py
          ↓
task_composition/builtin_tasks.py
          ↓
task_composition/catalog.py
          ↓
TaskRegistry / model authoring / startup
```

- `ports.py`：Dataset descriptor、Prediction Runtime、support、curve handlerの境界
- `descriptors.py`：`TaskModule`、標準モデルauthoring、starter projectの宣言
- `builtin_tasks.py`：同梱Taskのloader、runtime factory、capability、starterの明示的配線
- `catalog.py`：同梱Taskを不変allow-listとして公開し、source pathを解決する

旧`task_modules.py`は残しません。
利用側は必要な責任のmoduleを直接importし、catalogを万能な再export hubにはしません。
同様に、Projectから固定Dataset／Profile／Packageを解決する処理は
`application/project_runtime.py`、Dataset登録transactionは
`application/dataset_registration.py`が所有します。

依存方向は`backend/tests/test_dependency_directions.py`で検査します。
特にTask compositionからapplication、modeling、persistence、tasksへの
top-level importと、tasks／dataによるapplication transactionの所有を禁止します。

新しいPrediction Taskを追加するときは
[Add Prediction Task](../../.claude/skills/add-prediction-task/SKILL.md)と
[Developer Start Here](../developer-start-here.md)を参照してください。
