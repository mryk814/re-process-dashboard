# Exact GP capacity contract

Issue #780 の capacity 判定は、Estimator を自動選択する仕組みではありません。
選択済みの `exact-gp-rbf.v1` recipe が、固定された Training Snapshot に対してどれだけの計算を行うかを build 前に説明し、実行可能性を返す preflight です。

## Count semantics

- `raw_observation_count` は replicate 集約前の source observation 数です。
- `effective_replicate_context_count` と `effective_training_rows` は、`condition_context_id`（なければ observation）ごとに集約した後、Estimator に実際に渡す行数です。
- `independent_validation_group_count` は `parent_key` の distinct 数です。
- Exact GP の `max_rows=500` は effective replicate context 数に対する上限です。
- raw row の切り捨て、暗黙 subsample、fold 数の暗黙縮退は行いません。

この identity は `training-recipe.json`、`training_stats.json`、predictor の `standard-training-metadata/v1`、manifest provenance に保存されます。

## Typed resolution

`exact-gp-capacity/v1` は、次の load を versioned contract として返します。

- `effective_training_rows`、features、validation strategy、requested folds
- quality fit 数、final fit 数、total fit 数
- optimizer restarts、固定 `maxiter=90`、seed、recipe `max_rows`
- estimated wall、peak memory、artifact size、prediction latency
- `automatic_switch=false`、`row_reduction=forbidden`、`fold_reduction=forbidden`

decision は次の三つです。

- `exact`: 既定 capacity envelope 内。
- `exact_expensive`: 実行可能だが benchmark warning budget を超える、または benchmark grid 外の fold/restart なので明示的に高コスト。
- `approximate_required`: effective rows、features、fit budget、memory、wall、artifact の hard boundary を超える。Exact build は開始せず、行や fold を変えません。

`approximate_required` では、同じ cohort を使う `fixed-random-feature-gp-spike.v1` を experimental/no-adopt として記録し、互換性があれば production の `ridge.v1` を代替経路として推薦します。
いずれも自動切替しません。

benchmark の実測 memory は `process_peak_working_set_bytes` です。
Windows では `GetProcessMemoryInfo(...PeakWorkingSetSize)` を使うため、Python の `tracemalloc` だけでは見えない NumPy native allocation を含みます。
これは bounded benchmark process 全体の高水位であり、caseごとに分離したRSSではありません。
未実測 cross-combination の `estimated_peak_memory_bytes` は versioned policy のモデル値で、実測 working set と混同しません。
capacity policy の hard判断には後者の versioned estimate を使い、working set は evidence として保存します。

## Uncertainty and artifact boundary

Exact GP の production predictor は従来どおり `normal predictive distribution` です。
予測平均、観測値に対する predictive interval、latent/model uncertainty の意味を capacity estimate と混ぜません。
Approximate spike の interval は同一cohort比較用の `approximate predictive observation interval` に過ぎず、production の latent uncertainty や calibration を主張しません。

Package は data-only のままです。
既存の `builtin.exact_gp.v1` runtime は変更せず、Package から Python、pickle、joblib、任意 callback を読みません。
spike artifact は安全性の確認用に数値配列だけの bounded `.npz` とし、allow-listed production registry には登録しません。

## Evidence

machine-readable な matrix と同一cohort比較は
[`docs/benchmarks/exact-gp-capacity-v1.json`](../benchmarks/exact-gp-capacity-v1.json) にあります。
matrix は effective rows `100,250,500,750,1000`、features `8,32,64`、folds `3,5`、restarts `1,3` の60点です。
共通 baseline に対する one-factor-at-a-time の rows 5点、features 3点、folds 2点、restarts 2点を実測します。
750/1000 は hard preflight を実測して exact fit を開始せず、未実測の cross-combination だけを `projected_from_versioned_policy` と明記します。
hardware、library、commit identity も固定記録します。

比較結果は `adoption_decision=no_adopt` です。
同一cohortで approximate spike を実行した証拠はありますが、production adapter がなく、1 cohort の結果だけで predictive uncertainty の production calibration を承認できないためです。
上限超過時の安全な経路は、明示的な production baseline recommendation（通常は `ridge.v1`）であり、silent fallback ではありません。
