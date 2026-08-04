# Student-t線形回帰を明示選択の標準候補として採用する

- 状態: accepted
- 対象: `student-t-linear-regression.v1`
- runtime: `numpyro.dense_posterior.v1`

## 判断

Student-t線形回帰を、continuous target向けのproduction
`distribution_candidate`として採用する。
これはRidgeを置き換える既定値や自動選択される勝者ではない。
利用者がheavy-tail仮説を持つときに、同一cohort・同一Validation Planで
明示比較する候補である。

## 固定した意味

- locationは線形で、代表値はStudent-t分布のmeanとする。
- observation scaleと自由度を含むposteriorをNUTSで推定する。
- 自由度はBeta(2, 5)を`2.1 < df <= 30`へ写像する固定policyとし、
  無制限探索や任意prior UIを設けない。
- q05–q95は新しい一観測のposterior predictive intervalであり、
  latent meanのcredible intervalではない。
- posterior draw、scale、df、seed、chain、warmup、draw数、診断を
  `inference-identity/v1`とsafe NPZへ固定する。
- unit mismatch、不可能値、parse error、duplicate identity conflict、
  明らかな入力ミスは学習前のData Quality failureのままとする。
  robust likelihoodで救済・削除・黙認しない。

## 比較証拠

固定synthetic fixtureでは、Student-t residualと少数の妥当なlarge residualを
含むcohortで、large residual以外のOOF MAEがRidgeより小さかった。
normal residual fixtureではStudent-tのMAE lossを35%以内とする反証条件を満たした。

bundled `heat-treatment-tradeoff-v1`を3-fold・同一cohortで比較した。
自動winner selectionは行っていない。

| target | recipe | OOF MAE | OOF RMSE | q05–q95 coverage |
| --- | --- | ---: | ---: | ---: |
| hardness_hv | Ridge | 19.3157 | 23.9257 | 90.29% |
| hardness_hv | Student-t | 19.3236 | 23.9312 | 90.33% |
| charpy_j | Ridge | 6.8562 | 8.6102 | 90.46% |
| charpy_j | Student-t | 6.8604 | 8.6135 | 89.96% |

実Taskでのclean-data efficiency lossはMAEで0.04〜0.06%に留まり、
posterior diagnosticsは全foldと最終fitで保存基準を満たした。
この結果はStudent-tが常に優れるという意味ではない。
Ridge、Additive等と同じcohortで比較し、tail仮説、interval、計算費用、
診断を別々に読む。

## 採用しないもの

- malformed dataの自動救済
- automatic outlier deletion
- mixture likelihood
- arbitrary robust loss／prior／df editor
- sampler failure後の暗黙fallback
- target間joint posteriorの捏造
- 自動active Package切替
