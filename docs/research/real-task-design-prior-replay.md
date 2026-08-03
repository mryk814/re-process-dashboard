# MPEA room-tensile Design Prior replay

<!-- generated from real-task-design-prior-replay/v1; result-digest: sha256:056d9f52f5b290d7255e903c0ff315cb3269a5670122f5e3dc1da745998ebcb6 -->

## 判断

- `kNN local`: **experimental**。composition-preserving local interpolation is feasible and plausible, but one public Task does not establish cross-Task production value
- `Gaussian rank copula`: **no_adopt**。rank dependence alone does not preserve the 14-component composition constraint; rejection/shortfall is material
- production昇格は行わず、Proposal registry、UI、保存済みRunを変更しない。

## Taskと固定protocol

`mpea-room-tensile-v1`を選んだ。公開MPEA文献データに相関した14元素組成、工程category、
組成合計constraint、論文group holdout、同梱active Packageが揃い、機密データを外部送信しない。

- 再生成: `uv run python backend/scripts/experiments/run_real_task_design_prior_replay.py`
- 数値正本: [`real-task-design-prior-replay-report.json`](real-task-design-prior-replay-report.json)
- seed: `17, 41, 83`
- candidate budget: `96` / batch: `8`
- holdout: SHA-256で固定した文献groupの20% bucket。候補のrealized outcomeは最近傍holdout実測TYS。
- predictive replay Packageとsupport referenceもnon-holdout groupだけで構築し、
  active Packageと同じallow-list済みridge estimator、feature contract、alphaを
  standard Package builderで固定（training unitはstandard builderの契約）。
- source、Profile、Training Snapshot、Task contract、Feature Recipe、Validation Plan、
  Model Package、Design Space、generator parameter、selection policy、holdout identityをreportへ固定。

## 比較

| generator | policy | hard violation | mean shortfall | prediction-holdout gap (MPa) | best realized TYS (MPa) |
| --- | --- | ---: | ---: | ---: | ---: |
| latin_hypercube | direct_objective | 0.951 | 3.33 | 318.1 | 682.3 |
| latin_hypercube | conservative_diverse | 0.951 | 3.33 | 318.1 | 682.3 |
| sobol | direct_objective | 0.969 | 5.00 | 150.3 | 884.3 |
| sobol | conservative_diverse | 0.969 | 5.00 | 150.3 | 884.3 |
| empirical_rows | direct_objective | 0.000 | 0.00 | 279.1 | 856.3 |
| empirical_rows | conservative_diverse | 0.000 | 0.00 | 262.6 | 856.3 |
| knn_local | direct_objective | 0.000 | 0.00 | 289.2 | 856.3 |
| knn_local | conservative_diverse | 0.000 | 0.00 | 250.3 | 857.7 |
| gaussian_rank_copula | direct_objective | 0.889 | 0.00 | 180.4 | 855.0 |
| gaussian_rank_copula | conservative_diverse | 0.889 | 0.00 | 180.3 | 855.0 |

feasibility、plausibility、predictive support、objective gap、diversityは別々に保存し、
一つのscoreへ畳んでいない。LHS／Sobolの独立samplingとcopulaは組成合計を自動的には
守らない。hard validatorによるrejectを、generator likelihoodやclipで置き換えていない。

## 限界

- 最近傍holdout実測はhistorical replay用proxyであり、新しい材料実験ではない。
- 単一の公開Taskはcross-domainのproduction安全性を証明しない。
- wall-clockは環境依存のためreportへ残すがresult digestから除外する。
- deep generator、online active learning、自動generator選択は評価していない。
