# 分位点だけを返す予測器のI/Oカード

## 使用する場面

学習済みモデルが固定された分位点を返す一方で、標準偏差、パラメトリック分布、サンプルを返さない場合に、この経路を使用する。
リポジトリに含まれる例は意図的に無効化されており、APIとUIが暗黙に正規分布を仮定することを防ぐために存在する。

## 契約

| 境界 | 値 |
|---|---|
| 正規化入力 | `composition.x`, `process.scale` |
| FeatureBundle | 順序が固定された数値ベクトル `x`, `scale` |
| ランタイム | `builtin.quantile_linear.v1` / `quantile_linear_v1` |
| モデル成果物 | `quantile_levels [Q]`, `coefficients [Q,F]`, `intercepts [Q]` を持つ安全なNPZ |
| PredictiveSummary | 中央値による点予測、元の経験分位点、`predictive_family=empirical_quantiles` |
| 能力 | quantilesはtrue。std、samples、componentsはfalse。パラメトリック分布と目標達成確率は利用不可 |
| 学習時の依存関係 | 固定係数を出力できる任意の分位点学習器。検証データの生成処理はNumPyだけを使用 |
| ランタイムの依存関係 | NumPyのみ |

アダプターは、`(0, 1)` 内で重複せず昇順に並んだ水準、`0.5` の水準、正確な特徴量数、有限値だけを持つ配列、`crossing_policy=reject` を要求する。
要求された入力で分位点の交差が発生した場合は、予測を並べ替えてモデルの欠陥を隠さず、その入力を拒否する。

## 値の意味と未対応の意味論

- `q05–q95` は分位点区間であり、当てはめた正規分布の90%区間ではない。
- 中央値を平均値と呼び替えない。
- 分位点は、`quantiles`、`point_statistic`、`target_kind`、`predictive_family` を通じてAPI応答とスナップショットに保持される。
- 標準偏差と目標達成確率は利用できないため、UIに `±0`、正規近似、終了しない計算中状態を表示してはならない。
- 任意の分位点数、暗黙のCDF補間、分布の当てはめ、較正サービスは、この例の対象外である。

## 有効化せずに構築して検証する

```powershell
npm run models:build:quantile-example
uv run python backend/scripts/verify_model_package.py examples/model-packages/quantile-linear --example
```

品質レポートは、親条件ブロックごとにまとめた固定の不均一分散を持つ合成観測値を使い、分位点別のピンボール損失、中央値のMAE、区間の被覆率と幅、交差数を計算する。
生成処理は固定グリッド上の交差をすべて拒否する。
`models/active-packages.json` は変更しない。
