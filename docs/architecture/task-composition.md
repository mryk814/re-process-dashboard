# Task compositionの依存方向

Prediction Taskの追加点は、責任ごとに四つへ分けます。

```text
contracts / data profile
          ↓
task_composition/ports.py
          ↓
task_composition/descriptors.py
          ↓
task_composition/builtin/<family>.py
          ↓
task_composition/builtin/catalog.py
          ↓
task_composition/catalog.py
          ↓
TaskRegistry / model authoring / startup
```

- `ports.py`：Dataset descriptor、Prediction Runtime、support、curve handlerの境界
- `descriptors.py`：`TaskModule`、標準モデルauthoring、starter projectの宣言
- `builtin/<family>.py`：Task familyごとのloader、runtime factory、builder、starterの明示的配線
- `builtin/catalog.py`：family moduleを既存順に集めるだけのcomposition catalog
- `catalog.py`：同梱Taskを不変allow-listとして公開し、source pathを解決する

## Starter Projectの公開区分

Task、Dataset、Model Packageの同梱範囲と、ユーザーへ見せるProjectサンプルの
ポートフォリオは分けます。`StarterProject.distribution`が公開区分の正本です。

- `quickstart`：fresh Workspaceへ最初から入る1件
- `gallery`：役割が重ならない代表例としてSample Galleryから追加できるもの
- `legacy_hidden`：既存Workspaceと内部検証のため宣言を残すが、新規追加はできないもの

`WORKBENCH_DEMO_SEED=all`は全Starterを使う内部fixtureです。公開Galleryの一括追加は
`gallery`だけを対象にし、テスト都合で公開サンプルを増やしません。既存Workspaceに
入っている`legacy_hidden`は作業を勝手に消さず、未変更ならGallery管理から取り除けます。

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
