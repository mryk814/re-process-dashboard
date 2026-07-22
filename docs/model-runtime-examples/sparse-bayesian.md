# 疎ベイズ事後分布のI/Oカード

## 使用する場面

本番環境でPyMC/NumPyroオブジェクトを読み込まず、係数縮小の不確かさと事後予測の不確かさを出力後も保持する必要がある場合に、この経路を使用する。
この例では、変数選択レポートを予測応答および予測アンサンブルとBMAから分離する。

## 契約

| 境界 | 値 |
|---|---|
| 正規化入力 | `composition.x0` から `composition.x7` |
| FeatureBundle | 同じ8個の数値特徴量を固定順で格納 |
| ランタイム | `builtin.posterior_linear.v1` / `posterior_linear_v1` |
| モデル成果物 | 安全なNPZ。`beta_draws [D,F]`、`intercept_draws [D]`、正の `noise_scale_draws [D]`、任意の正の `local_scale_draws [D,F]` または二値の `indicator_draws [D,F]` |
| PredictiveSummary | 平均値、経験分位点q05/q50/q95、事後予測標準偏差、モデル知識の不足による不確かさ（epistemic）と観測自体のばらつきによる不確かさ（aleatoric）の構成要素 |
| 能力 | quantiles、std、componentsはtrue。samplesはfalse。パラメトリック分布と目標達成確率は利用不可 |
| 学習時の依存関係 | 生成処理内のNumPyro/JAXホースシュー |
| ランタイムの依存関係 | NumPyのみ |

推論の前に、ドロー数、特徴量の数と順序、有限値、正のノイズスケールとローカルスケール、二値の指示変数、正確なテンソルスキーマを検査する。
乱数シードで観測ノイズのサンプリングを制御するため、スモークテストは決定的に実行できる。
モデル成果物には事後ドローを保存するが、`PredictiveSummary` は生のドローを公開しないため、`samples=false` とする。

## 選択レポートと品質レポート

`reports/selection-report.json` には、係数の平均、標準偏差、分位点、符号確率、ROPE外確率、ローカルスケールの平均、宣言された選択規則が含まれる。
ホースシュー縮小は連続的である。
このレポートでは、これらの値を包含確率とは呼ばず、選択されなかったFeature Pipelineの入力も削除しない。

検証データには、2個の信号特徴量、6個のノイズ特徴量、相関した信号候補、小さな親条件ブロックを意図的に含めている。
`quality-report.json` は、検証用に除外した親ブロックごとに、残りのブロックで全特徴量のホースシューモデルを学習し、その学習分割内でROPE選択規則を適用する。
続いて、選択した縮小ホースシューモデルを再学習し、両方の検証RMSEを報告する。
全特徴量のリッジ回帰スコアは、独立した基準値としてのみ保持する。

係数と縮小の要約は、因果的な重要度を表さない。
相関した特徴量は、事後分布の証拠を共有する場合がある。
任意のベイズグラフ、ランタイムでのJAX/PyTensor復元、特徴量の自動削除、UIでの事前分布編集、全部分集合BMAは、この契約の対象外である。

## 有効化せずに構築して検証する

```powershell
npm run models:build:posterior-linear-example
uv run python backend/scripts/verify_model_package.py examples/model-packages/posterior-linear --example
```

構築コマンドは任意の依存関係であるNumPyroを使って学習し、数値配列を出力して、NumPyの本番用アダプターで検証する。
`models/active-packages.json` は変更しない。
