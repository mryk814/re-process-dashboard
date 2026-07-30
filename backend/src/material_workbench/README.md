# Backend package layout

`material_workbench` 直下には、アプリの起動点と予測タスクの拡張点だけを置きます。

| Package | Responsibility |
| --- | --- |
| `app.py` | FastAPI の組み立てと起動時の依存解決 |
| `task_composition/ports.py` | Runtime／Datasetの依存方向を固定するport |
| `task_composition/descriptors.py` | TaskModule descriptor |
| `task_composition/builtin_tasks.py` | 同梱Prediction Taskの明示的composition |
| `task_composition/catalog.py` | allow-list済みTask catalog |
| `api/` | HTTP routing、依存注入、HTTP error への変換 |
| `application/` | プロジェクト・候補・推論などのユースケース |
| `contracts/` | API schema、Task contract、Feature contract |
| `data/` | Dataset Profile、Excel 読み込み、Dataset 登録 |
| `domain/` | 候補変換、目標判定、時間・screening の計算 |
| `execution/` | 推論処理の重複排除・実行調停 |
| `modeling/` | Feature Pipeline、Model Package、予測 runtime |
| `persistence/` | SQLite Store、Workspace Catalog、migration |
| `tasks/` | Task definition、registry、project-runtime 解決 |
| `adapters/` | allow-list されたモデル artifact adapter |

依存の向きは原則として `api -> application -> domain/modeling/persistence` です。
`contracts` は各層から参照できますが、HTTP や SQLite の実装へ依存させません。
新しい予測タスクはpackage直下にモジュールを増やさず、
`task_composition/builtin_tasks.py` と `tasks/task_definitions/` から登録します。
