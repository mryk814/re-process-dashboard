# Generative Design Lab adoption memo

<!-- generated from generative-design-lab-report/v1; result-digest: sha256:eccdd6e3c5f09c2b24605455a61204f9e63797887340f23f577e50091d485f12 -->

## 判断

- `kNN local`: **experimental継続**。少量・混合modeでhard violationを出さず、最も観測近傍に留まる。一方でmode間探索は弱い。
- `Gaussian rank copula`: **experimental継続**。混合category内で非重複候補を作り、kNNより広げられる。ただしsimplex／total constraintはcopula likelihoodと別に検証し、違反をrejectする必要がある。
- `tiny VAE`: **no-adopt**。小さなmixed fixtureで実学習・samplingは再現できたが、一つのfixtureではseed安定性、constraint安全、校正を示せず、安全なdata-only artifact／allow-list runtimeもない。
- `conservative_diverse`: **experimental継続**。予測値だけの選抜よりOOD攻略を抑え、batch diversityを残した。production registryは変更しない。

## 固定protocol

- 再生成: `uv run python backend/scripts/experiments/run_generative_design_lab.py`
- 数値正本: [`generative-design-lab-report.json`](generative-design-lab-report.json)
- fixture: correlated continuous / mixed categorical modes / constrained composition / offline optimization trap
- seed: `17, 41, 83`
- candidate budget: `128` / batch: `8`
- generator、selection、Dataset View、Task、Feature Recipe、Validation Plan、Design Space、Prior、Predictive Model、Objective、hidden oracleのdigestをrunごとに固定
- hard feasibility、plausibility、predictive support、novelty、objective、batch diversityは別指標

## kNNとcopula

| fixture | generator | hard violation | mean nearest distance |
| --- | --- | ---: | ---: |
| mixed modes | kNN | 0.000 | 0.002 |
| mixed modes | copula | 0.000 | 0.005 |
| composition | kNN | 0.000 | 0.021 |
| composition | copula | 0.036 | 0.030 |

kNNは観測mode内の補間なので、今回のcomposition fixtureではtotal constraintを保った。
copulaは各categoryのrank相関と周辺分布を保ちながら重複を避けたが、joint densityはhard constraintの代替ではない。

## OOD optimization trap

値は「選抜batch中、観測supportから離れ、予測とhidden oracleが大きく乖離した候補」の比率。

| generator | direct objective | conservative + diversity |
| --- | ---: | ---: |
| latin_hypercube | 0.375 | 0.042 |
| sobol | 0.417 | 0.083 |

保守的penaltyは予測値と一体化したsupport scoreではない。
保存されたnearest distanceから独立に計算し、hard validatorを通った候補へだけ適用した。

## Deep candidate

NumPyだけの小さなVAEをmixed fixtureの各seedで240 epoch学習し、同じcandidate budgetを生成した。
hard violationは平均0.042、mean nearest distanceは
0.034、parameter artifact相当は
832 bytesだった。
これは依存とartifact境界を具体的に評価するspikeであり、production runtimeではない。
学習済みweightを任意Pythonとして読み込まず、Lab結果からregistryを自動変更しない。

## 限界

- synthetic hidden oracleは実材料のscience／equipment feasibilityを証明しない。
- wall-clock値は環境依存なのでresult digestへ入れていない。bounded epoch、候補数、parameter bytesをoperation evidenceとした。
- generator likelihoodをhard feasibilityまたはpredictive supportに使用していない。
- GPU、privacy保証、active learning、予測モデル再学習は評価していない。
