# 範囲探索のsampling方式と逐次追加の比較

Issue #546の判断材料として、同じ合成Design Space、評価budget、seedで
Latin Hypercube Sampling（LHS）、Sobol、2D以下専用の格子helperを比較した。
これはLab実験であり、production UIの選択肢は変更していない。

## 結論

**逐次追加の生成基盤にはSobolを採用候補とし、LHSはone-shot用途のまま残す。
2D格子helperはproductionへ採用しない。**

LHSとSobolのone-shot性能には、今回のfixtureだけで一方を常に優位とする差はない。
一方、Sobolは64点の後へ64点を追加しても、最初から128点を生成した場合と
feasible point集合が全fixtureで一致した。
LHSは追加trancheのseedを分離すれば既存sample identityと重複回避を守れるが、
128点one-shotとは別の集合になる。

したがって、方式をユーザーに選ばせる根拠はまだない。
将来の逐次追加は、Sobolのprefixを不変Run family revisionとして保存する設計を
優先して検討する。
高棄却条件では、どちらも128生成に対し実評価できた点が中央値9.5–11点だけだった。
budgetから有効点数を保証してはならず、生成数、実行可能な一意点数、棄却率を
分けて表示する必要がある。

## 検証設計

- budget: 128 generated points
- seed: 546–553の8反復
- 変数数: 1、2、6、10
- mixed fixture: 数値4、カテゴリ1、離散list1、条件付き入力1
- high-rejection fixture: 数値6、条件付き入力1、3成分和制約1、関係制約1
- productionと同じsampler:
  - `latin_hypercube` v1
  - scrambled seeded `sobol` v1
- Lab限定helper:
  - 1D/2D格子
- 実行可能性:
  - 通常fixtureは2変数間の関係制約
  - high-rejection fixtureは3成分和、関係制約、条件付き制約の積
- objective: fixture内の固定targetに対する重み付き二乗距離
- support: 全数値軸が学習域相当の`[0.10, 0.90]`内にある割合
- diversity: objective最良点から距離を確保して8点まで選んだ集合の平均点間距離
- runtime: sampling、制約filter、metrics計算を含むLab runnerのwall time
- model calls: 実行可能な一意点を評価した回数。外部model runtimeの速度ではない

カテゴリとlistはunit sampleをそれぞれ3水準、4水準へ写像した。
条件付き入力はcontrollerの水準に応じてinactive valueへ固定した。
カテゴリは順序を持たないnominal値として、一致を0、不一致を1とする。
listは順序を持つ数値離散水準として水準間隔を使う。
距離は各軸差の二乗平均平方根であり、次元数による尺度増加を避けた。
coverageは数値軸の8 bin、カテゴリの3水準、listの4水準とinactive値を
それぞれの定義域に対して計算する。
2Dの図やmarginal bin coverageは高次元の充足を証明しないため、判断根拠は
最近傍距離、support、objective、棄却率と併記する。

## one-shot比較

表は8 seedの中央値である。`有効点`はfeasibleかつ一意な点、
`近傍距離`は各点の最近傍までの正規化距離、`最良値`は小さいほどよい。

| fixture | 方式 | 有効点 | 棄却率 | marginal coverage | 近傍距離 | 最良値 | runtime ms | support | diversity |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1D | LHS | 115 | 10.2% | 1.000 | 0.006 | 0.000 | 18.77 | 88.7% | 0.380 |
| 1D | Sobol | 115 | 10.2% | 1.000 | 0.005 | 0.000 | 18.89 | 88.7% | 0.376 |
| 1D | grid helper | 115 | 10.2% | 1.000 | 0.008 | 0.000 | 18.56 | 88.7% | 0.367 |
| 2D | LHS | 116 | 9.4% | 1.000 | 0.035 | 0.001 | 19.64 | 67.3% | 0.461 |
| 2D | Sobol | 115 | 10.2% | 1.000 | 0.044 | 0.002 | 20.35 | 67.3% | 0.456 |
| 2D | grid helper | 114 | 10.9% | 1.000 | 0.059 | 0.005 | 19.11 | 72.3% | 0.479 |
| 6D numeric | LHS | 116 | 9.4% | 1.000 | 0.152 | 0.174 | 19.36 | 27.5% | 0.489 |
| 6D numeric | Sobol | 116 | 9.4% | 1.000 | 0.168 | 0.149 | 19.94 | 26.8% | 0.489 |
| 6D mixed | LHS | 116 | 9.4% | 1.000 | 0.146 | 0.177 | 23.95 | 42.5% | 0.572 |
| 6D mixed | Sobol | 116 | 9.4% | 1.000 | 0.160 | 0.158 | 23.64 | 43.3% | 0.568 |
| 10D | LHS | 116 | 9.4% | 1.000 | 0.210 | 0.426 | 20.58 | 11.6% | 0.476 |
| 10D | Sobol | 115.5 | 9.8% | 1.000 | 0.221 | 0.401 | 21.84 | 11.4% | 0.471 |
| 6D high rejection | LHS | 11 | 91.4% | 0.719 | 0.218 | 0.269 | 1.42 | 41.7% | 0.399 |
| 6D high rejection | Sobol | 9.5 | 92.6% | 0.740 | 0.252 | 0.340 | 1.47 | 36.9% | 0.413 |

runtime差は1 ms未満であり、方式選択の根拠にはしない。
6D/10DではSobolの近傍距離と最良objectiveがやや良いが、
8 seedの合成fixtureだけから一般的優位性は主張しない。

## 一括128点と64 + 64点

逐次追加は同じ`family_id`、strategy version、seedを保持し、
新しい`revision_id`と`parent_revision_id`を作った。
既存revisionは変更せず、既存sample IDはそのまま先頭に残し、
追加分だけ別trancheのsample IDを付けた。
保存済みproposal snapshot IDも追加前後で同一であり、再計算していない。

| fixture | 方式 | one-shot有効点 | 逐次有効点 | point集合一致 | 重複 | 追加点 |
|---|---|---:|---:|---|---:|---:|
| 1D | LHS | 115 | 115 | no | 0 | 58 |
| 1D | Sobol | 115 | 115 | yes | 0 | 58 |
| 1D | grid helper | 115 | 115 | no | 0 | 57 |
| 2D | LHS | 115 | 116 | no | 0 | 60 |
| 2D | Sobol | 114 | 114 | yes | 0 | 58 |
| 2D | grid helper | 113 | 114 | no | 0 | 56 |
| 6D numeric | LHS | 115 | 116 | no | 0 | 60 |
| 6D numeric | Sobol | 115 | 115 | yes | 0 | 58 |
| 6D mixed | LHS | 115 | 116 | no | 0 | 60 |
| 6D mixed | Sobol | 115 | 115 | yes | 0 | 58 |
| 10D | LHS | 115 | 116 | no | 0 | 60 |
| 10D | Sobol | 116 | 116 | yes | 0 | 58 |
| 6D high rejection | LHS | 9 | 8 | no | 0 | 4 |
| 6D high rejection | Sobol | 9 | 9 | yes | 0 | 5 |

LHSの逐次有効点は通常fixtureでone-shotより1点多く、
high-rejectionでは1点少ない。
これは追加trancheが別の層化標本だからであり、性能差の証拠ではない。
同じbudgetでも評価集合が変わること自体が、逐次Runの再現説明を難しくする。

qualityとcostもone-shotから暗黙に引き継がず、追加後の集合に対して再計算した。
各セルは`one-shot → 逐次`である。

| fixture | 方式 | coverage | 近傍距離 | 最良値 | runtime ms | model calls | support | diversity |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 1D | LHS | 1.000 → 1.000 | 0.005 → 0.004 | 0.000 → 0.000 | 20.99 → 28.12 | 115 → 115 | 0.887 → 0.887 | 0.367 → 0.372 |
| 1D | Sobol | 1.000 → 1.000 | 0.005 → 0.005 | 0.000 → 0.000 | 25.59 → 27.90 | 115 → 115 | 0.887 → 0.887 | 0.381 → 0.381 |
| 1D | grid helper | 1.000 → 1.000 | 0.008 → 0.005 | 0.000 → 0.000 | 19.02 → 46.38 | 115 → 115 | 0.887 → 0.896 | 0.367 → 0.366 |
| 2D | LHS | 1.000 → 1.000 | 0.038 → 0.036 | 0.001 → 0.003 | 19.21 → 33.69 | 115 → 116 | 0.687 → 0.664 | 0.464 → 0.456 |
| 2D | Sobol | 1.000 → 1.000 | 0.045 → 0.045 | 0.001 → 0.001 | 20.52 → 29.52 | 114 → 114 | 0.675 → 0.675 | 0.455 → 0.455 |
| 2D | grid helper | 1.000 → 1.000 | 0.059 → 0.038 | 0.005 → 0.005 | 24.49 → 42.81 | 113 → 114 | 0.726 → 0.667 | 0.433 → 0.458 |
| 6D numeric | LHS | 1.000 → 1.000 | 0.150 → 0.150 | 0.185 → 0.143 | 27.53 → 43.29 | 115 → 116 | 0.330 → 0.241 | 0.483 → 0.495 |
| 6D numeric | Sobol | 1.000 → 1.000 | 0.179 → 0.179 | 0.102 → 0.102 | 23.43 → 29.31 | 115 → 115 | 0.261 → 0.261 | 0.500 → 0.500 |
| 6D mixed | LHS | 1.000 → 1.000 | 0.148 → 0.142 | 0.171 → 0.147 | 27.07 → 40.11 | 115 → 116 | 0.443 → 0.388 | 0.571 → 0.566 |
| 6D mixed | Sobol | 1.000 → 1.000 | 0.170 → 0.170 | 0.066 → 0.066 | 22.84 → 36.07 | 115 → 115 | 0.409 → 0.409 | 0.545 → 0.545 |
| 10D | LHS | 1.000 → 1.000 | 0.209 → 0.204 | 0.289 → 0.347 | 20.43 → 37.99 | 115 → 116 | 0.130 → 0.129 | 0.477 → 0.485 |
| 10D | Sobol | 1.000 → 1.000 | 0.226 → 0.226 | 0.505 → 0.505 | 21.64 → 31.72 | 116 → 116 | 0.112 → 0.112 | 0.482 → 0.482 |
| 6D high rejection | LHS | 0.708 → 0.708 | 0.218 → 0.217 | 0.185 → 0.480 | 1.00 → 1.37 | 9 → 8 | 0.667 → 0.500 | 0.340 → 0.345 |
| 6D high rejection | Sobol | 0.729 → 0.729 | 0.242 → 0.242 | 0.338 → 0.338 | 1.20 → 1.87 | 9 → 9 | 0.444 → 0.444 | 0.413 → 0.413 |

Sobolは全fixtureでpoint集合だけでなく、runtime以外のquality指標も一致した。
runtimeは逐次経路が二つのrevision生成とidentity計算を含むため増えるが、
実model inferenceを含まない短時間のLab値であり、優劣判定には使わない。

## 採否

1. **Sobol prefixを逐次追加の第一候補として採用する。**
   ただし、このIssueではproduction API/UIへ追加しない。
   実装時はRun family、revision、offset、生成数、実行可能数、追加点IDを保存する。
2. **LHSを廃止しない。**
   one-shotではSobolと同程度であり、既存のseeded sequenceを変える根拠がない。
   逐次追加する場合は一括Runと同一集合だと表示しない。
3. **2D格子helperは不採用。**
   2Dの説明用previewには読みやすいが、3次元以上へ拡張できず、
   現実のTaskでsampling strategyとして見せる価値がない。
4. **高棄却時の自動増量は見送る。**
   「必要点数が得られるまで生成」はmodel callとruntimeを隠す。
   まず生成数、実行可能な一意点、棄却理由を観測可能にする。

## 限界

- 合成fixtureであり、実Taskの予測model、カテゴリ不均衡、組成境界を網羅しない。
- `best objective`は固定関数への1回評価で、Bayesian optimizationの比較ではない。
- mixed fixtureのlistは順序を持つ数値離散値という仮定であり、
  nominalなlistを一般に連続距離へ写像してよいとは示していない。
- marginal coverageは各軸だけを見ており、joint coverageを保証しない。
- runtimeはローカルPC上の短い処理で、model inference時間を含まない。
- sequential comparisonは追加後の再rankingやfocus-region探索を扱わない。
  将来focus regionを導入する場合は領域と選択理由をrevisionへ保存する。

## 再実行

機械可読な全seed、各revision、sample identityは
[`sampling-strategy-comparison-2026-07-31.json`](sampling-strategy-comparison-2026-07-31.json)
に保存した。

```powershell
uv run --extra dev python backend/scripts/experiments/evaluate_sampling_strategies.py `
  --budget 128 `
  --output docs/research/sampling-strategy-comparison-2026-07-31.json
uv run --extra dev python -m pytest backend/tests/test_sampling_experiment.py -q
```
