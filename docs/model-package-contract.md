# Model Package Contract

モデルパッケージは、学習済み重みと明示的なメタデータだけを配るデータ成果物です。
パッケージはPythonコード、import path、callback、pickle、joblibを含めません。
実行できるのはアプリ本体に実装され、Registryでallow-list化されたadapterだけです。

## 読込手順

1. `manifest.json` をPydanticの`extra=forbid`契約で検証する。
2. 全artifactが相対パス・package root内・通常ファイルであることを検証する。
3. sizeとSHA-256を検証する。
4. `runtime_type` と `architecture_id` を固定Registryから選ぶ。
5. adapterがtensor/モデルの形状を検証し、packageのsmoke testを実行する。

現在のloaderはローカルで信頼できる提供者から置いたPackageを対象とする。hash検証は破損・取り違えの検出であり、manifestとartifactを同時に改竄できる相手の認証にはならない。署名検証は未実装なので、現時点で署名済み配布Packageを主張しない。

## `manifest.json` の最小例

```json
{
  "schema_version": "model-package/v1",
  "package_id": "annealed-properties-2026-07",
  "package_version": "2026.07.0",
  "task_id": "annealed-properties-v1",
  "input_schema_version": "candidate-v1",
  "feature_pipeline": {
    "id": "anneal-summary-v1",
    "version": "1.0.0",
    "spec": "feature-pipeline/pipeline.json",
    "output_features": ["C", "peak_temperature_c"],
    "artifacts": []
  },
  "predictors": [{
    "id": "ts",
    "target": "TS",
    "unit": "MPa",
    "target_kind": "continuous",
    "runtime_type": "numpyro.dense_posterior.v1",
    "architecture_id": "dense_mlp_v1",
    "artifact": "model-artifacts/ts.npz",
    "predictive_family": "student_t",
    "feature_names": ["C", "peak_temperature_c"],
    "config": {"activation": "tanh", "obs_scale": 5.0, "df": 6.0}
  }],
  "provenance": {
    "training_data_id": "sha256:...",
    "feature_dataset_id": "sha256:...",
    "training_code_revision": "git:..."
  },
  "artifacts": [],
  "smoke_test": {"input": "smoke/input.json", "expected": "smoke/expected.json"}
}
```

全ての`feature_pipeline.spec`、pipeline artifact、predictor artifactは`artifacts`配列にpath/hash/bytesとして列挙する。

## 許可runtimeと資産形式

| runtime type | 安全な資産 | 制約 |
|---|---|---|
| `builtin.linear.v1` | `.npz` | `weights/bias/lower_offset/upper_offset`のみ |
| `sklearn.skops.v1` | `.skops` | アプリ固定の`estimator_family` allow-list外を拒否。manifestによる型自己申告、custom transformerは禁止 |
| `lightgbm.booster.v1` | LightGBM native text | sklearn wrapperのpickleは禁止 |
| `gpytorch.static_exact_rbf.v1` | `.safetensors` | `exact_rbf_v1`の既知tensor schemaだけ |
| `numpyro.dense_posterior.v1` | `.npz`、`allow_pickle=False` | `dense_mlp_v1`のposterior arrayだけ |

NumPyro adapterは学習用Python関数を復元しない。許可likelihoodは
`normal`、`student_t`、`lognormal`、`bernoulli_logit`、`poisson_log`、
`negative_binomial_log`、`zero_inflated_poisson_log`、`ordinal_logit`である。

## NumPyro dense posterior

`w0..wN`は`[posterior_draw, input, output]`、`b0..bN`は
`[posterior_draw, output]`のfloat tensorである。中間層activationは`tanh`または`relu`だけである。
`obs_scale`、`df`、`dispersion`は必要なlikelihoodだけが任意で持てる。

- Normal / Student-t: real-valued predictive quantiles
- LogNormal: positive-valued median and quantiles
- Bernoulli logit: event probability
- Poisson / Negative Binomial / ZIP: nonnegative count quantiles
- Ordinal logit: `config.thresholds`による有限カテゴリー

posterior predictive samplingは`seed`で決定的に再現する。詳細予測のsnapshotにはPackage digest、adapter version、seed、draw policyを保存する。

npzはentry数、展開後総量、圧縮率、posterior draw数、layer数、tensor要素数に固定上限を設ける。圧縮後のartifact sizeだけを信頼しない。

## Feature pipeline と再現性

feature pipelineはJSON宣言の組込み操作（単位正規化、欠損方針、標準化、one-hot、ヒートパターン要約）だけを使う。
モデルPackageはfeature名・順序・pipeline versionを固定する。snapshotにはraw candidate、canonical input、Package manifest SHA-256、pipeline hash、training/support provenanceを残し、過去snapshotを新Packageで自動再評価しない。

## TaskDefinitionとの境界

TaskDefinitionは利用者が扱う入力group、field、output、単位、目標方向を定義する。モデルPackageは一つの `task_id` と `input_schema_version` を参照し、そのtaskのCanonicalCandidateを特徴量へ変換して予測する。Package manifestやruntime capabilityに画面配置、カード、テーブル列などのUIレイアウト情報を含めない。変数ごとの表示桁数は利用者向け契約である `TaskDefinition.display_decimals` を既定値とし、モデルPackageの再学習やdigest変更を伴わせない。

正本の出力は次の通りとする。

- `annealed-properties-v1`: TS / YS / EL / lambda
- `hot-rolled-properties-v1`: TS

Packageのpredictor targetは対応するTaskDefinitionのoutputに含まれなければならない。TaskDefinitionを変更して既存Packageの意味を暗黙に変えず、互換性のない変更はschema versionまたはtask idを更新する。

TaskDefinitionは予測意味を固定するcontextとfield間制約も保持する。熱延v1は `HR-LINE-1`・L方向に固定し、仕上げ温度は均熱温度以下、出側板厚は入側板厚未満とする。これらをruntime固有コードだけに埋め込まない。

## Runtime capability

モデルの出力情報量はruntimeごとに異なるため、UIが推測せずに済むよう能力をデータとして宣言する。targetごとに、利用可能なpoint statistic、standard deviation、quantile、sample、parametric distribution、不確実性内訳、support、warning、目標達成確率の計算方式を持つ。マルチターゲット間のjoint sample可否はtask runtime全体の能力として分ける。

能力宣言は「すべて返せる」という共通最小形式を強制するものではない。宣言されていない表現を擬似生成せず、利用可能な表現だけを返す。平均と標準偏差だけから目標達成確率を計算できるのは、`normal_approximation` を明示した場合だけである。

TaskDefinition、CanonicalCandidate、runtime capabilityの機械検証可能な共通契約は `backend/src/material_workbench/task_contracts.py`、タスクごとのJSON正本は `backend/src/material_workbench/task_definitions/` に置く。production registryとcontract testは同じJSONを読み込む。

熱延PackageはTaskDefinitionの単一出力TSに一致し、production registry起動時にtask、pipeline、feature順序、predictor targetを照合する。出力契約が一致しないPackageは起動時に拒否する。

## テスト必須項目

- path traversal、hash/size改竄、unknown field/runtime、未列挙artifactの拒否
- feature goldenとadapter tensor shape検証
- 各likelihoodのsupport/quantile/probability semantic test
- production Packageごとのsmoke input/expected output
- 予測→snapshot→Package更新後も過去結果が変わらないE2E
