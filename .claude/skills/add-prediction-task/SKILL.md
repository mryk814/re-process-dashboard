---
name: add-prediction-task
description: 全く新しいExcelデータセットをMaterial Decision Workbenchに統合し、新しい予測タスク（TaskDefinition＋dataset profile＋feature pipeline＋Model Package＋Runtime＋API＋UI）を追加する手順。「新しいデータセットを追加して」「このExcelから予測モデルを作って」と言われたら参照する。
---

# 新しい予測タスクの追加

このアプリは「1つの正本Excel＋dataset profile＋TaskDefinition＋feature pipeline＋Model Package＋Runtime」の組で1予測タスクを構成する（[docs/app-charter.md](../../docs/app-charter.md)、[docs/model-package-contract.md](../../docs/model-package-contract.md)、[docs/feature-engineering.md](../../docs/feature-engineering.md)参照）。既存タスクとは独立に追加できるので、**既存タスクのコードを壊さず並存させる**のが基本方針。

flank-wear-v1（切削工具の逃げ面摩耗予測）の追加が最初の完全な実装例。迷ったらそのファイル群を横に置いて真似ること：
- dataset profile: `backend/src/material_workbench/dataset-input-profile-flank-wear-v1.json`
- feature pipeline: `backend/src/material_workbench/flank_wear_feature_pipeline.py`
- タスク定義: `backend/src/material_workbench/task_definitions/flank-wear-v1.json`
- ローダー＋Runtime: `backend/src/material_workbench/flank_wear.py`
- Package builder: `backend/scripts/build_flank_wear_model_package.py`
- テスト: `backend/tests/test_flank_wear.py`

AGENTS.mdの原則（特に1・7・8・9）はこのタスクにもそのまま適用される。**モデル種類を増やすのではなく、既存adapterのconfigで表現できないか先に検討する**（例: 目的変数が非負・裾が重い→`builtin.exact_gp.v1`にlognormal familyを足しただけで済んだ）。

## 0. 着手前に確認すること

- 新しいExcelは `data/source/` に読取専用の正本として置く（変更しない。原則6）。
- 既存タスクのAGENTS.md原則やUIに影響しない独立追加か？ 既存Excelのシート追加なら既存profileの `extends` で拡張、無関係な新Excelなら**新しいprofile・新しいloaderを作る**（後者がflank-wearのケース）。
- 予測対象は「1点のスカラー」か、「ある入力軸に沿った曲線」か？ 後者なら `curve_axis_path` をTaskDefinitionに立てる（下記6参照）。ユーザーの目的が「曲線の形そのもの」なら、その軸を特徴量として学習に使うのは正しいが、UI・APIでは「候補入力の1点」ではなく「曲線の横軸」として提示する。

## 1. データセットを理解する（推測せず実データで確認）

Windows環境では `openpyxl` の日本語ヘッダーがcp932でエラーになるので、探索スクリプトは常に `PYTHONIOENCODING=utf-8` を付けて `uv run python` で実行する。

1. 全シート名・各シートの先頭数行・行数を出力する。
2. relation（結合）シートがあれば、各entityへの参照キーとcardinalityを確認する（1試験=1材料×1工具×1条件、のような構造）。
3. 学習単位を決める。**「反復観測」と「工程条件」を混同しない**（AGENTS.md原則1）。摩耗曲線のように同じ試験内に複数測定点がある場合、CVは観測単位ではなく試験（run/parent_key）単位で切る。
4. 数値列の分布（min/max/median/std）と有効フラグ（測定状態・判定など）の値種類をCounterで確認する。物理範囲・欠測理由・カテゴリ選択肢はここで洗い出しておく（後でTaskDefinitionのtraining_range・choicesに使う）。

## 2. 特徴量セットを実験で決める（当てずっぽうにしない）

ユーザーから「特徴量は適当に取捨選択してよい」と言われても、実際に検証すること。

1. 候補特徴量をいくつかのセット（例: 条件のみ／条件+材料／条件+材料+工具／全部+成分元素）に分け、group化KFold（run単位）でRMSE/MAEを比較する簡易スクリプトを scratchpad に書いて実行する。
2. 目的変数が非負で裾が重い（今回のVBのように）なら、log(1+y)空間で学習し、線形逆変換で評価する方が明確に良いか比較する。
3. 効果が確認できない特徴量（分散ゼロ、CVを悪化させる列）は削とす。「入れておけば安全」で全部積まない。
4. 相互作用特徴（例: log距離×log速度）は精度改善が数値で確認できたものだけ残す。

## 3. dataset profile と loader

既存の `dataset_profile.py` の汎用機構（`canonicalize_workbook` / entity・relation・observation・eligibility policy宣言）を使えるなら使う。ただし：

- 新しい単位記号（例: HV, deg, mm/rev, µm, m, -）が出てきたら `dataset_profile.py` の `_UNIT_REGISTRY` に追加する必要がある。
- 完全に独立したExcelで、既存profileの `sheets`/`entities`/`relation` と語彙が重ならないなら、**新しい profile ファイル**（`task_definition_ids: ["<new-task-id>"]` で自分のタスクだけを指す）を作り、**専用のloader関数**（`load_workbook_data` を再利用せず、新しい `FlankWearData` のような dataclass を返す関数）を書く方が事故が少ない。無理に既存 `WorkbookData` の巨大構造体に押し込まない。
- ローダーは各行に `eligible` / `eligibility_reasons` / `output_warnings` を必ず持たせる（AGENTS.md原則9のscientific-validity方針に沿う）。

## 4. TaskDefinition（`task_definitions/<task-id>.json`）

- `input_groups`: composition / process / categorical のみ許可。各number fieldは `default_range` ⊆ `allowed_range` ⊇ `training_range` を満たすこと（実データのmin/maxから作る）。
- `outputs`: `goal_direction` を正しく選ぶ（摩耗量なら `at_most`）。
- **曲線予測タスクの場合**: `curve_axis_path` にその軸のcanonical pathを設定する。`task_contracts.py` 側のバリデーションで「必須・編集可能・number field」であることを強制している。
- `runtime_capability`: 使うadapterが実際に返せる情報（quantiles, uncertainty_components, goal_probability方式）と一致させる。ここが実際のPredictiveSummaryと食い違うと `validate_predictive_summary` がPackage読込時に落ちる。

## 5. feature pipeline

`FeatureDefinition(name, unit, meaning, group)` の並びを固定し、`build_X_features(candidate, defaults)` と `build_X_features_from_observation(row, defaults)` を用意する。group（composition/process/metallurgy/other等）はRuntimeの支持度（support）計算で特徴量グループ間の重み付けに使われるので、意味のある単位でグループ分けする。

## 6. Runtime（`RuntimeProtocol`実装）

`task_id`, `support_policy_id`, `output_keys`, `predict_core`, `predict`, `evidence`, `support_summary`, `similarity` は必須。曲線予測タスクなら `response_curve_result`（既存の1変数掃引）に加えて、**別変数を数水準ふりながら軸方向に掃引する `curve_family_result`** を用意すると「Cが増えると傾きがどう変わるか」のような比較ができる（`flank_wear.py` の実装を参照）。

## 7. Model Package builder

- 既存の `staged_package_destination` / `verify_model_package` / `canonical_training_dataset` をそのまま使う。
- CVの分割単位を学習単位（4章）に合わせる。leave-one-run-out相当。
- 新しいpredictive familyが必要なら、**既存adapterのconfigフラグで分岐**させ、必ず物理的な妥当性（support境界・単調な分位点変換など）をunit testで固定する（`test_builtin_exact_gp_lognormal_summary_semantics` を参照）。新runtime typeを増やす場合は [docs/model-package-contract.md](../../docs/model-package-contract.md) の許可表を更新すること。
- Packageサイズが大きくなりすぎる場合（観測点が多いGPは train_x×train_xの共分散でO(n²)）、run単位で代表点へ間引く（`MAX_ROWS_PER_RUN`のように）。

## 8. アプリへの配線（見落としやすい箇所）

以下は全部揃って初めてタスクが有効になる。1つでも欠けるとテストか起動時検証で落ちる：

1. `backend/src/material_workbench/schemas.py`: `ProjectInput.task_id` の `Literal`、`ActualMeasurementInput.property`/`unit` の `Literal` と単位対応表に新タスクのkeyを追加。
2. `backend/src/material_workbench/model_lifecycle.py`: `canonical_training_dataset` のtask_id→feature builder dispatchに追加。
3. `backend/src/material_workbench/model_package_verify.py`: task_idごとのdata loaderとRuntime分岐に追加。
4. `backend/scripts/model_workflow.py`: `TASKS` タプルと、必要ならtask固有のsource解決ロジックに追加。
5. `backend/src/material_workbench/app.py`: Runtimeをlifespanでインスタンス化し `TaskRegistry` の辞書に追加。**`/api/projects/{id}/model-package` のような「現在のタスクのruntimeを引くべき箇所」で、既存コードが焼鈍用runtimeを決め打ちしていないか確認する**（今回、`profile_path=Path(runtime().data.profile_path)` が常に焼鈍profileを見ていて新タスクで500になるバグを踏んだ。正しくは `entry.predictor_runtime.data.profile_path`）。
6. `models/active-packages.json`: 新task_idのPackage参照を追加。
7. `package.json`: `models:build:<task>` スクリプトを追加。
8. `npm run api:generate` でOpenAPI schemaとTS型を再生成する（フロントの型がずれたままPRを出さない）。

## 9. フロントエンド

- 曲線予測でなければ既存の `LiveResponseCurves` パネルがそのまま動く（`operations.response_curve` を見て出し分けている）。
- 曲線予測タスクなら `taskDefinition.curve_axis_path` の有無で専用パネルを出し分ける（`CurveFamilyPanel` を参照）。「軸に沿った曲線」と「別変数を動かした応答曲線」は**別の可視化**として両方出してよい。
- プロジェクト一覧のタスクラベル分岐（`ProjectHub.tsx`）などのUI文言も忘れずに追加する。

## 10. テストと検証

- 特徴量のgolden test（固定candidate→固定ベクトル値）。
- loaderのeligibility集計テスト（有効行数・除外理由の集合）。
- `canonical_training_dataset` がeligible行を過不足なく含むテスト。
- 新predictive familyのsemantics test（分位点の順序・support・point_statistic）。
- APIのpreview/curve/actuals/model-packageのcontractテスト。
- `backend/tests/test_task_registry.py` の `TASK_IDS` タプルに新task_idを追加（パラメータ化テストが全task_idを回る設計なので、追加を忘れると新タスクだけ未検証のまま通ってしまう）。
- 最終確認は `uv run pytest && npm run typecheck && npm run build`。

## 11. 実機で動作確認するときの注意（このマシン固有）

開発機ではポート8765(-8768)に別セッション／別worktreeの古いuvicornが残留しがちで、`npm run dev` でbindできても実際は古いコードに繋がっていることがある（詳細は memory の `dev-server-port-8765-conflict` を参照）。worktreeでの動作確認は：

```
uv run uvicorn main:app --app-dir backend/src --host 127.0.0.1 --port <空きポート>
```
を別途起動し、`apps/web/.env.local` に `VITE_API_URL=http://127.0.0.1:<そのポート>` を一時的に置いて確認する。**確認が終わったら `.env.local` を必ず削除する**（コミットしない）。他セッションの既存プロセスは勝手に落とさない。

## 12. コミット

`data/source/` の新xlsxとPackage成果物（`models/packages/<id>/`）はどちらもGit管理対象（成果物であり秘密情報ではない）。コミットメッセージには学習単位・CV方式・品質指標（MAE/RMSE/coverage）を残すと、後で見た人がPackageの信頼性を追える。
