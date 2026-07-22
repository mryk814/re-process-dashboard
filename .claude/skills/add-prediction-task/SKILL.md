---
name: add-prediction-task
description: 特徴量設計とモデルの方針が既に決まっているデータセットを、Material Decision Workbenchの新しい予測タスク（TaskDefinition＋dataset profile＋feature pipeline＋Model Package＋Runtime＋API＋UI）として実装・配線する手順。「このデータセットをアプリに載せて」「新しい予測タスクとして統合して」と言われたら参照する。
---

# 新しい予測タスクをアプリに載せる

## 前提（このSKILLの範囲）

**このSKILLは「何を特徴量にするか」「どのモデルを使うか」を決める手順ではない。** それらは既に決まっている（ユーザーから指示された、または別途分析済み）という前提で、「決まった設計をこのアプリの構造にどう落とし込むか」だけを扱う。

決まっているべきこと（決まっていなければ、まずそれを詰めてから着手する。当てずっぽうで進めない）：
- 目的変数は何か、単位は何か、非負／裾が重いなど分布上の癖はあるか（log変換要否）。
- 特徴量として使う列（Excelのどのシートのどの列か）と、その単位。
- 学習単位（1行=1観測でよいか、それとも同じ試験内に複数観測がある「反復観測」を親条件でまとめる必要があるか）。
- 使うモデルの種類（既存adapterで表現できるか。できないなら何を変えれば表現できるか）。
- 予測対象が「1点のスカラー」か「ある入力軸に沿った曲線」か（後者なら本SKILLの4章で `curve_axis_path` を設定する）。

このアプリは「1つの正本Excel＋dataset profile＋TaskDefinition＋feature pipeline＋Model Package＋Runtime」の組で1予測タスクを構成する（[docs/app-charter.md](../../docs/app-charter.md)、[docs/model-package-contract.md](../../docs/model-package-contract.md)、[docs/feature-engineering.md](../../docs/feature-engineering.md)参照）。既存タスクとは独立に追加できるので、**既存タスクのコードを壊さず並存させる**のが基本方針。

flank-wear-v1（切削工具の逃げ面摩耗予測）の追加が最初の完全な実装例。迷ったらそのファイル群を横に置いて真似ること：
- dataset profile: `backend/src/material_workbench/dataset-input-profile-flank-wear-v1.json`
- feature pipeline: `backend/src/material_workbench/flank_wear_feature_pipeline.py`
- タスク定義: `backend/src/material_workbench/task_definitions/flank-wear-v1.json`
- ローダー＋Runtime: `backend/src/material_workbench/flank_wear.py`
- Package builder: `backend/scripts/build_flank_wear_model_package.py`
- テスト: `backend/tests/test_flank_wear.py`

AGENTS.mdの原則（特に1・7・8・9）はこのタスクにもそのまま適用される。**モデル種類を増やすのではなく、既存adapterのconfigで表現できないか先に検討する**（例: 目的変数が非負・裾が重い→`builtin.exact_gp.v1`にlognormal familyを足しただけで済んだ。新しいruntime typeを追加するのは最後の手段）。

## 1. Excelの実際の構造を確認する（マッピングを書くために必要）

特徴量設計は決まっていても、それが「Excelのどのシートのどの列か」まで正確に把握していないとdataset profileが書けない。推測せず実データで確認する。

Windows環境では `openpyxl` の日本語ヘッダーがcp932でエラーになるので、探索スクリプトは常に `PYTHONIOENCODING=utf-8` を付けて `uv run python` で実行する。

1. 全シート名・各シートの先頭数行・行数を出力する。
2. relation（結合）シートがあれば、各entityへの参照キーとcardinalityを確認する（1試験=1材料×1工具×1条件、のような構造）。
3. 決まっている学習単位（反復観測をまとめる親キーは何か）が、実データのどの列に対応するか確認する。**「反復観測」と「工程条件」を混同しない**（AGENTS.md原則1）。
4. 使う予定の列の実際の値域（min/max/median）、有効フラグ（測定状態・判定など）の値種類、カテゴリ選択肢を確認する。TaskDefinitionの `training_range`・`choices` にそのまま使う。

## 2. dataset profile と loader

既存の `dataset_profile.py` の汎用機構（`canonicalize_workbook` / entity・relation・observation・eligibility policy宣言）を使えるなら使う。ただし：

- 新しい単位記号（例: HV, deg, mm/rev, µm, m, -）が出てきたら `dataset_profile.py` の `_UNIT_REGISTRY` に追加する必要がある。
- 完全に独立したExcelで、既存profileの `sheets`/`entities`/`relation` と語彙が重ならないなら、**新しい profile ファイル**（`task_definition_ids: ["<new-task-id>"]` で自分のタスクだけを指す）を作り、**専用のloader関数**（`load_workbook_data` を再利用せず、新しい `FlankWearData` のような dataclass を返す関数）を書く方が事故が少ない。無理に既存 `WorkbookData` の巨大構造体に押し込まない。
- ローダーは各行に `eligible` / `eligibility_reasons` / `output_warnings` を必ず持たせる（AGENTS.md原則9のscientific-validity方針に沿う）。

## 3. feature pipeline（決まった設計をコードに落とす）

`FeatureDefinition(name, unit, meaning, group)` の並びを固定し、`build_X_features(candidate, defaults)` と `build_X_features_from_observation(row, defaults)` を用意する。group（composition/process/metallurgy/other等）はRuntimeの支持度（support）計算で特徴量グループ間の重み付けに使われるので、意味のある単位でグループ分けする。決まった特徴量セット・log変換の有無をここでそのまま実装する（設計判断はここでは行わない）。

## 4. TaskDefinition（`task_definitions/<task-id>.json`）

- `input_groups`: composition / process / categorical のみ許可。各number fieldは `default_range` ⊆ `allowed_range` ⊇ `training_range` を満たすこと（1章で確認した実データのmin/maxから作る）。
- `outputs`: `goal_direction` を正しく選ぶ（摩耗量なら `at_most`）。
- **曲線予測タスクの場合**: `curve_axis_path` にその軸のcanonical pathを設定する。`task_contracts.py` 側のバリデーションで「必須・編集可能・number field」であることを強制している。
- `runtime_capability`: 使うモデルが実際に返せる情報（quantiles, uncertainty_components, goal_probability方式）と一致させる。ここが実際のPredictiveSummaryと食い違うと `validate_predictive_summary` がPackage読込時に落ちる。

## 5. Runtime（`RuntimeProtocol`実装）

`task_id`, `support_policy_id`, `output_keys`, `predict_core`, `predict`, `evidence`, `support_summary`, `similarity` は必須。曲線予測タスクなら `response_curve_result`（1変数掃引）に加えて、**別変数を数水準ふりながら軸方向に掃引する `curve_family_result`** を用意すると「Cが増えると曲線の傾きがどう変わるか」のような比較ができる（`flank_wear.py` の実装を参照）。`curve_family_result` はカテゴリカル変数（`categorical.<name>`）にも対応させられる：数値のように水準を等間隔生成するのではなく、選択肢すべてを1系列ずつ生成する（同ファイルの分岐を参照）。

## 6. Model Package builder（決まったモデルをPackage化する）

- 既存の `staged_package_destination` / `verify_model_package` / `canonical_training_dataset` をそのまま使う。
- CVの分割単位を学習単位（1章）に合わせる。反復観測がある場合はleave-one-run-out相当。
- 既に決まったモデルが既存adapterのruntime_typeで表現できない場合だけ、**既存adapterのconfigフラグで分岐**させる形で対応する（新runtime type追加は最後の手段）。物理的な妥当性（support境界・単調な分位点変換など）を必ずunit testで固定する（`test_builtin_exact_gp_lognormal_summary_semantics` を参照）。configで新しい挙動を足した場合は [docs/model-package-contract.md](../../docs/model-package-contract.md) の許可表を更新すること。
- Packageサイズが大きくなりすぎる場合（観測点が多いGPは train_x×train_xの共分散でO(n²)）、学習単位あたりの代表点へ間引く（`MAX_ROWS_PER_RUN`のように）。

## 7. アプリへの配線（見落としやすい箇所）

以下は全部揃って初めてタスクが有効になる。1つでも欠けるとテストか起動時検証で落ちる：

1. `backend/src/material_workbench/schemas.py`: `ProjectInput.task_id` の `Literal`、`ActualMeasurementInput.property`/`unit` の `Literal` と単位対応表に新タスクのkeyを追加。
2. `backend/src/material_workbench/model_lifecycle.py`: `canonical_training_dataset` のtask_id→feature builder dispatchに追加。
3. `backend/src/material_workbench/model_package_verify.py`: task_idごとのdata loaderとRuntime分岐に追加。
4. `backend/scripts/model_workflow.py`: `TASKS` タプルと、必要ならtask固有のsource解決ロジックに追加。
5. `backend/src/material_workbench/app.py`: Runtimeをlifespanでインスタンス化し `TaskRegistry` の辞書に追加。**`/api/projects/{id}/model-package` のような「現在のタスクのruntimeを引くべき箇所」で、既存コードが焼鈍用runtimeを決め打ちしていないか確認する**（今回、`profile_path=Path(runtime().data.profile_path)` が常に焼鈍profileを見ていて新タスクで500になるバグを踏んだ。正しくは `entry.predictor_runtime.data.profile_path`）。
6. `models/active-packages.json`: 新task_idのPackage参照を追加。
7. `package.json`: `models:build:<task>` スクリプトを追加。
8. `npm run api:generate` でOpenAPI schemaとTS型を再生成する（フロントの型がずれたままPRを出さない）。

## 8. フロントエンド

- 曲線予測でなければ既存の `LiveResponseCurves` パネルがそのまま動く（`operations.response_curve` を見て出し分けている）。
- 曲線予測タスクなら `taskDefinition.curve_axis_path` の有無で専用パネルを出し分ける（`CurveFamilyPanel` を参照）。「軸に沿った曲線」と「別変数を動かした応答曲線」は**別の可視化**として両方出してよい。
- 「ふる変数」のドロップダウンには数値（`numericTaskInputs`）とカテゴリカル（`categoricalTaskInputs`）の両方を候補として出す。カテゴリカルを選んだときは水準数セレクタを隠す（選択肢数がそのまま系列数になる）。
- プロジェクト一覧のタスクラベル分岐（`ProjectHub.tsx`）などのUI文言も忘れずに追加する。

## 9. テストと検証

- 特徴量のgolden test（固定candidate→固定ベクトル値）。
- loaderのeligibility集計テスト（有効行数・除外理由の集合）。
- `canonical_training_dataset` がeligible行を過不足なく含むテスト。
- 新predictive familyのsemantics test（分位点の順序・support・point_statistic）。
- APIのpreview/curve/curve-family/actuals/model-packageのcontractテスト。曲線ファミリは数値vary・カテゴリカルvary・vary未指定の3パターンを確認する。
- `backend/tests/test_task_registry.py` の `TASK_IDS` タプルに新task_idを追加（パラメータ化テストが全task_idを回る設計なので、追加を忘れると新タスクだけ未検証のまま通ってしまう）。
- 最終確認は `uv run python -m pytest && npm run typecheck && npm run build`。

## 10. 実機で動作確認するときの注意（このマシン固有）

開発機ではポート8765(-8768)に別セッション／別worktreeの古いuvicornが残留しがちで、`npm run dev` でbindできても実際は古いコードに繋がっていることがある（詳細は memory の `dev-server-port-8765-conflict` を参照）。worktreeでの動作確認は：

```
uv run python -m uvicorn main:app --app-dir backend/src --host 127.0.0.1 --port <空きポート>
```
を別途起動し、`apps/web/.env.local` に `VITE_API_URL=http://127.0.0.1:<そのポート>` を一時的に置いて確認する。**確認が終わったら `.env.local` を必ず削除する**（コミットしない）。他セッションの既存プロセスは勝手に落とさない。

**`--reload` は付けない。** このリポジトリは `D:\OneDrive\...` 配下にあり、OneDriveの同期がファイル変更検知を妨げるため、`uvicorn --reload` がbackend/srcの変更を拾わずに古いコードのまま応答し続けることがある（ログに `Reloading...` が出ない）。原因不明のエラーが直らないときは、まずコードのせいではなく**プロセスを再起動していないだけ**を疑う。コードを変更したら毎回プロセスをkillして立ち上げ直す。

## 11. コミット

`data/source/` の新xlsxとPackage成果物（`models/packages/<id>/`）はどちらもGit管理対象（成果物であり秘密情報ではない）。コミットメッセージには学習単位・CV方式・品質指標（MAE/RMSE/coverage）を残すと、後で見た人がPackageの信頼性を追える。
