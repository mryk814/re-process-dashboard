# GMR条件付き候補生成 PoC

Issue #453の判断材料として、Gaussian Mixture Regression（GMR）をproduction modelではなく候補生成戦略として検証した。

## 結論

**判断は保留（research-only）**とする。

今回の二経路・連続2変数の合成データでは、GMRは20個のreplay targetすべてで目標誤差1.25以内の候補を生成し、各targetへ二つの高密度modeを提示した。
制約違反と外挿候補は0件で、生成時間はtargetあたり約0.2 msだった。

一方、同じデータでは過去実績近傍も目標達成率100%である。
カテゴリ、組成和、実データの観測bias、group splitを含まないため、productionのProposal Strategy registryへはまだ追加しない。

## 検証設計

- データ: 同じ物性へ至る二つの工程経路を持つ、再現可能な合成データ360行
- 学習: 先頭280行、historical replay: 後続80行から20 target
- 入力: 正規化温度、正規化保持時間
- 目的: 正規化物性
- GMR: 入出力の結合分布へ2成分GMMをEM fitting
- 逆解析: 各成分でGaussianの条件付き分布 `p(x | y*)` を解析的に計算
- 候補: 条件付き平均を成分ごとのhigh-density modeとして提示
- 制約:
  - 各入力の上下限
  - 二工程値が反対符号で十分離れるcross-field条件 `x0 * x1 <= -0.20`
- 再評価: GMRと独立に学習した二次ridge順モデル
- 真値: 合成データを作った非線形process response
- 外挿度: 学習データまでの標準化最近傍距離

EM/GMR、制約filter、順モデル、replay runnerは
`backend/src/decision_workbench/research/gmr_inverse.py`へ隔離した。
production API、Model Package、Proposal Strategy registryは変更していない。

## Historical replay

| 戦略 | 目標達成率 | 制約充足率 | 平均mode数 | 平均最近傍距離 | 平均候補間距離 |
|---|---:|---:|---:|---:|---:|
| GMR modes | 100% | 100% | 2.00 | 0.014 | 3.919 |
| Historical neighbor | 100% | 100% | 2.00 | 0.000 | 3.934 |
| Forward optimization | 35% | 100% | 1.85 | 0.308 | 3.319 |
| BO surrogate | 45% | 100% | 1.80 | 0.381 | 2.194 |
| Manual fixed condition | 10% | 100% | 1.00 | 0.004 | 0.000 |

BO比較は、学習済みGaussian Process surrogateのmeanとuncertaintyを使うone-step acquisitionである。
active learningを複数round回した完全なBO benchmarkではないため、「GMRがBOより優れる」という一般化には使わない。

機械可読な全結果と候補例は
[`gmr-inverse-replay-2026-07-29.json`](gmr-inverse-replay-2026-07-29.json)
に保存した。

## 候補証拠

各GMR候補は次を保持する。

- 混合成分ID
- targetを条件とした成分weight
- 条件付きlog density
- 順モデル予測
- 合成真値とtarget error
- 学習データ最近傍距離と外挿判定
- mode label
- 制約違反理由

条件付き平均を一つだけ返さず、成分ごとのmodeを残す。
これは二峰の中間にある不自然な工程条件を「平均的な候補」として出す誤読を避けるためである。

## リスクと次のgate

1. GMM成分を物理メカニズムと解釈しない。
2. 観測頻度の偏りを実現可能性と同一視しない。
3. Gaussianの裾に生成可能性があっても、支持範囲の証拠なしに信頼しない。
4. カテゴリと組成制約は今回未検証である。
5. 実Taskではtarget leakageを避けたgroup splitでreplayする。

次は実Taskのimmutable Training Snapshotを使い、同じ候補数・実験budgetでHistorical neighbor、Forward optimization、BOと比較する。
GMRが目標達成率で非劣性を保ち、複数mode提示または支持範囲で明確な付加価値を示した場合にのみ、allow-listされた候補生成戦略として設計する。

## 再実行

```powershell
uv run python backend/scripts/experiments/evaluate_gmr_inverse.py `
  --output docs/research/gmr-inverse-replay-2026-07-29.json
uv run python -m pytest backend/tests/test_gmr_inverse_research.py -q
```
