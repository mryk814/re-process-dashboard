---
name: add-model-runtime
description: このリポジトリに新しいモデル種類（runtime adapter + モデルPackage）を追加し、開発者が選べるモデルの選択肢を増やすための手順。「新しいモデルを追加したい」「◯◯回帰／GP／ブースティング／ベイズモデルを使いたい」「adapterを増やす」「runtime_typeを追加」「モデルPackageを作る」のような依頼が来たら、ファイルを触る前に必ずこのスキルを読むこと。既存adapterの改修や新しいpredictive familyの追加にも該当する。
---

# 新しいモデル・runtimeの追加手順

モデルPackageは「学習済み重み＋明示的メタデータだけのデータ成果物」であり、実行できるコードはアプリ本体にallow-listされたadapterだけ（AGENTS.md 原則7）。新しいモデル種類を足す作業は必ず「adapterをアプリに実装 → 固定Registryに登録 → builderでPackage生成 → 契約テスト」の順で行う。Packageにpickle/joblib/import path/callbackを入れる設計は最初から却下する。

実装前に `docs/model-package-contract.md` を読むこと。直近の実例（マルチタスクGP追加）が最良の参照になる: `git log --oneline --all -- backend/src/material_workbench/adapters/builtin_multitask_gp.py` で該当コミットを見つけ、その差分全体を手本にする。

## 1. Adapter実装 — `backend/src/material_workbench/adapters/<name>.py`

- `runtime_type = "<family>.<kind>.v1"` を持つAdapterクラスと、`predict(values, *, seed=0) -> PredictiveSummary` を持つpredictorクラスを書く。
- artifactは安全な形式のみ（`.npz`は必ず `allow_pickle=False`、torch系は `.safetensors`）。読込直後にarray schemaを固定集合で検証し、形状・有限性・正定値性など数学的前提を`PackageContractError`で拒否する。**すべての**配列を有限性チェックに含める（`alpha`類の漏れが過去にレビューで指摘された）。
- 正規分布出力なら `adapters/base.py` の `normal_predictive_summary()` を使う。分位点・不確かさ内訳のキー名を独自に再発明しない（UIとruntime capabilityが同じ契約を見ている）。
- 予測は`seed`で決定的に。optional依存を使う場合は既存の `gpytorch_static.py` のimportパターン（`MissingOptionalDependency`）に従う。
- 複数predictorが1つのartifactを共有する設計なら、adapter内でロード済み配列をキャッシュする（`builtin_multitask_gp.py`参照）。

## 2. 登録 — 触るべき固定リスト

| 場所 | 内容 |
|---|---|
| `model_packages.py` `RUNTIME_TYPES` | runtime_typeを追加。Registryと完全一致しないと起動失敗 |
| `model_packages.py` `PredictorSpec.static_architecture_only` | runtimeが許すarchitecture_idを固定 |
| `model_packages.py` `AdapterRegistry.__init__` | adapterインスタンスを追加 |
| `app.py` `optional_dependencies` | optional依存があるruntimeだけ追記（それ以外は`RUNTIME_TYPES`から自動導出される） |
| `runtime.py` `_model_meta` | GP系（predictive distributionで区間を出すruntime）ならinterval metaの集合に追加 |
| `docs/model-package-contract.md` | 許可runtime表に行を追加し、array schemaの節を書く |

## 3. Builder — `backend/scripts/build_<name>_model_package.py`

`build_default_model_package.py` / `build_multitask_model_package.py` の構造を踏襲する。必須要素:

- `staged_package_destination()` でビルドし、最後に `verify_model_package()` を通してから確定する（検証失敗したPackageをディスクに残さない）。
- manifestに provenance digests（`training_data_id` / `feature_dataset_id` / `dataset_profile_id` / `input_contract_digest` / `runtime_capability_digest`）を必ず入れる。activeにする際 `validate_lifecycle_metadata` が照合する。
- `reports/quality-report.json`（leave-one-parent-condition-out）、`reference/training_stats.json`（`composition_defaults`必須）、`smoke/input.json` + `expected.json` を生成。smoke期待値はadapterと**ビット互換の同じfloat演算**で計算する関数をbuilder内に書く（`_gp_point` / `_mtgp_point` 参照）。
- 学習行の扱い: `relation`の一行を直接使わず、親条件でグループ化して工程条件と反復観測を分離する（AGENTS.md 原則1）。

生成先は `models/packages/<id>/`。既存activeを置き換えない — `models/active-packages.json` はそのままにして、切替は `scripts/model_workflow.py activate` の一手順として利用者に委ねる。

## 4. テスト — 網羅ではなく契約に絞る（AGENTS.md 原則9）

- `backend/tests/test_<name>_adapter.py`: fixture Packageを組み立て、(a) 決定性とsemantic整合（分位点順序・不確かさ内訳の和）、(b) 数学的に既知の縮退ケースとの一致（例: block対角ICM＝単一タスクGP）、(c) schema改竄の拒否をparametrizeで並べる。
- `test_model_packages.py`: Registry集合のassertとチェックイン済みPackageのparametrizeに新エントリを追加。
- builderテスト: 実Excelから一度ビルドし、manifest構造・quality report・再ビルド拒否を確認。
- optional依存adapterなら `test_optional_adapters.py` にimportskip付き実artifactテスト。

## 5. 完了条件

```
uv run pytest && npm run typecheck && npm run build
```

に加えて、builderを実際に実行してPackageを生成し（builder内のverifyがPASSすること）、Packageをリポジトリにコミットする。既存の予測スナップショットは不変（原則8）なので、新モデルで過去結果が変わらないことを疑う必要が出たら `test_step4_to_6.py` 系のE2Eを確認する。
