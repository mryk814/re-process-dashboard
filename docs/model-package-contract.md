# モデルPackage契約（Model Package Contract）

モデルPackageは、学習済み重みと明示的なメタデータだけを配るデータ成果物です。
PackageはPythonコード、import path、callback、pickle、joblibを含めません。
実行できるのはアプリ本体に実装し、Registryの許可リストへ登録したアダプターだけです。

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

## 許可する実行環境と資産形式

| 実行環境の種類 | 安全な資産 | 制約 |
|---|---|---|
| `builtin.linear.v1` | `.npz` | `weights/bias/lower_offset/upper_offset`のみ |
| `builtin.additive_terms.v1` | `.npz`、`allow_pickle=False` | identity linkのlinear / B-spline / categorical lookup項。寄与は型付き説明契約で返す |
| `builtin.exact_gp.v1` | `.npz`、`allow_pickle=False` | `exact_rbf_grouped_v1`または`exact_rbf_ard_v1`の既知array schemaだけ。`predictive_family`は`normal`または`lognormal`（後者は`config.latent_transform=log1p`必須で、GPは`log(1+target)`空間、予測は単調変換で元単位へ戻す） |
| `builtin.quantile_linear.v1` | `.npz`、`allow_pickle=False` | 固定分位点ごとの係数と切片。中央値必須、分位点交差は並べ替えず拒否する |
| `builtin.posterior_linear.v1` | `.npz`、`allow_pickle=False` | 係数・切片・観測noiseのposterior draw。raw sampleはAPIへ出さず、経験分位点またはモーメントマッチした正規要約を返す |
| `sklearn.skops.v1` | `.skops` | アプリ固定の`estimator_family` allow-list外を拒否。manifestによる型自己申告、custom transformerは禁止 |
| `lightgbm.booster.v1` | LightGBM native text | sklearn wrapperのpickleは禁止 |
| `gpytorch.static_exact_rbf.v1` | `.safetensors` | `exact_rbf_v1`の既知tensor schemaだけ |
| `numpyro.dense_posterior.v1` | `.npz`、`allow_pickle=False` | `dense_mlp_v1`のposterior arrayだけ |

NumPyro adapterは学習用Python関数を復元しない。許可likelihoodは
`normal`、`student_t`、`lognormal`、`bernoulli_logit`、`poisson_log`、
`negative_binomial_log`、`zero_inflated_poisson_log`、`ordinal_logit`である。

## NumPyro全結合ネットワークの事後分布

`w0..wN`は`[posterior_draw, input, output]`、`b0..bN`は`[posterior_draw, output]`の浮動小数点tensorです。
中間層の活性化関数は`tanh`または`relu`だけです。
`obs_scale`、`df`、`dispersion`は、必要な尤度だけが任意で保持できます。

- NormalとStudent-t：実数値の予測分位点
- LogNormal：正の値を取る中央値と分位点
- Bernoulli logit：事象確率
- Poisson、Negative Binomial、ZIP：負にならない個数の分位点
- Ordinal logit：`config.thresholds` で定義する有限個の順序カテゴリ

事後予測サンプリングは `seed` で決定的に再現します。
現在の詳細予測スナップショットは、`seed`やサンプリング方針を独立した項目として保存しません。

npzはentry数、展開後総量、圧縮率、posterior draw数、layer数、tensor要素数に固定上限を設ける。圧縮後のartifact sizeだけを信頼しない。

## 特徴量パイプラインと再現性

特徴量パイプラインは、JSONで宣言した組込み操作（単位正規化、欠損方針、標準化、one-hot、ヒートパターン要約）だけを使います。
モデルPackageは特徴量名、順序、パイプラインのバージョンを固定します。
スナップショットには元候補、アプリ共通入力（canonical input）、予測結果、予測時のモデルメタデータを保存します。
モデルメタデータには、Package ID、Packageバージョン、manifestのSHA-256、実行環境の種類、Feature PipelineのIDとバージョン、入力schema、特徴量名、学習ソースのpathとSHA-256、レコード件数を記録します。
焼鈍と熱延の実行環境は、Package manifestの学習データIDも学習データの識別情報へ追加します。
過去のスナップショットは、新しいPackageで自動再評価しません。

## TaskDefinitionとの境界

TaskDefinitionは利用者が扱う入力group、field、output、単位、目標方向を定義する。モデルPackageは一つの `task_id` と `input_schema_version` を参照し、そのtaskのCanonicalCandidateを特徴量へ変換して予測する。Package manifestやruntime capabilityに画面配置、カード、テーブル列などのUIレイアウト情報を含めない。変数ごとの表示桁数は利用者向け契約である `TaskDefinition.display_decimals` を既定値とし、モデルPackageの再学習やdigest変更を伴わせない。

各OutputDefinitionはcanonical unitで `plausibility_range` と `preferred_display_range` を明示する。前者は予測値・予測区間・実測値・系譜観測・探索結果へ共通の物理範囲外警告を付ける契約であり、値の削除・丸め・学習除外を指示しない。後者はグラフの初期表示だけを決め、範囲外の点や区間は境界記号と実値への導線を残す。表示範囲の切替は推論入力やcache identityを変更しない。

正本の出力は次の通りとする。

- `annealed-properties-v1`: TS / YS / EL / lambda
- `hot-rolled-properties-v1`: TS
- `flank-wear-v1`: VB_mean / VB_max（µm。切削距離は候補入力の1フィールドだが、意味的には摩耗曲線の横軸であり、応答曲線APIで曲線として提示する）

Packageのpredictor targetは対応するTaskDefinitionのoutputに含まれなければならない。TaskDefinitionを変更して既存Packageの意味を暗黙に変えず、互換性のない変更はschema versionまたはtask idを更新する。

`feature_pipeline.output_features` はPackage全体で生成可能な特徴量の和集合とする。各predictorの`feature_names`はその部分列でよく、pipelineで宣言された順序を保つ。これにより、同じTaskの出力ごとに観測ファミリーや試験条件が異なる場合も、不要な特徴量を別の予測器へ渡さない。

TaskDefinitionは予測意味を固定するcontextとfield間制約も保持する。熱延v1では設備・試験片方向を固定contextにせず、仕上げ温度は均熱温度以下、出側板厚は入側板厚未満とする。これらをruntime固有コードだけに埋め込まない。

## 実行能力（Runtime capability）

モデルが返せる情報は実行環境ごとに異なるため、UIが推測せずに済むよう、能力をデータとして宣言します。
目的変数ごとに、代表値の種類（point statistic）、標準偏差、分位点、サンプル、確率分布、不確かさの内訳、学習範囲による裏付け、警告、目標達成確率の計算方式を定義します。
複数目的変数の同時サンプル可否は、タスク実行環境全体の能力として分けます。

能力宣言は「すべて返せる」という共通最小形式を強制するものではない。宣言されていない表現を擬似生成せず、利用可能な表現だけを返す。平均と標準偏差だけから目標達成確率を計算できるのは、`normal_approximation` を明示した場合だけである。

TaskDefinition、CanonicalCandidate、runtime capabilityの機械検証可能な共通契約は `backend/src/material_workbench/contracts/task_contracts.py`、タスクごとのJSON正本は `backend/src/material_workbench/tasks/task_definitions/` に置く。production registryとcontract testは同じJSONを読み込む。

熱延PackageはTaskDefinitionの単一出力TSに一致し、production registry起動時にtask、pipeline、feature順序、predictor targetを照合する。出力契約が一致しないPackageは起動時に拒否する。

## 必須テスト

- パストラバーサル、hashやサイズの改ざん、未知のフィールドや実行環境、未列挙artifactの拒否
- 特徴量ゴールデンとアダプターのtensor形状検証
- 各尤度について、値域、分位点、確率の意味を確認するテスト
- 本番Packageごとのスモーク入力と期待出力
- 予測をスナップショットへ保存し、Package更新後も過去結果が変わらないことを確認するE2E
