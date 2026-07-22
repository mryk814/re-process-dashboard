# 加算項モデルの入出力カード

## 使用する場面

予測が固定切片と監査可能な加算項の和で表される、GAMやEBMに似たモデルには、この経路を使います。
ランタイム成果物は学習ライブラリから独立しています。
説明が表すものはモデルの局所的な加算分解であり、SHAP、部分依存、因果効果ではありません。

## 契約

| 境界 | 値 |
|---|---|
| アプリ共通入力 | `composition.x`, `categorical.route_code`, `process.z` |
| FeatureBundle | 順序が定められた数値 `x`、符号化済みの `route_code`、`z` |
| ランタイム | `builtin.additive_terms.v1` / `additive_terms_v1` |
| モデル成果物 | スカラー切片と、許可リストに登録された各項の固定配列を含む安全なNPZ |
| 代表的な項 | 数値の `bspline_univariate` 項2個（非線形応答を含む）と `categorical_lookup` 1個 |
| リンク関数 | 恒等関数のみ。説明の合計は予測と同じ尺度になる |
| PredictiveSummary | 点予測のみの経験分布族、または明示的に指定された正規近似 |
| 説明 | 切片、型付きの項別寄与、リンクスコア、予測を含む型付きの `AdditiveExplanation` |
| 学習時の依存関係 | ビルダーはNumPyの最小二乗法を使用。別の学習器でも同じ配列を書き出せる |
| ランタイムの依存関係 | NumPyのみ |

各B-splineでは、固定ノットベクトル、1から3までの次数、幅が正の定義域、境界値を一定とする外挿を使います。
カテゴリ値は、書き出された数値符号と厳密に一致する必要があります。
未知の種類、フィールド、カテゴリ、非有限テンソル、退化したノット、互換性のない形状は拒否されます。

## 機能別のバリエーション

- `point/` は分位点、標準偏差、目標達成確率を返しません。
  存在しない不確かさを作って追加することはできません。
- `normal/` は正の残差尺度を含み、`predictive_family=normal` として平均、標準偏差、`q05`、`q50`、`q95` を返します。
- 同じFeatureBundleを渡した場合、どちらのバリエーションも同じ加算スコアと説明を返します。
- 入力同士に相関があると、個々の項が不安定になることがあります。
  寄与は当てはめたスコアを説明するものであり、独立した効果や因果効果として提示することはできません。

検査済みの応答曲線ゴールデンテストでは、`composition.x` を変化させ、宣言済みFeature Pipelineの順序に従って正規パスを対応付け、加算ランタイムを評価します。
2変数間の交互作用、任意の基底プラグイン、因果的な解釈、SHAP、学習器の可視化オブジェクトは最初の契約の対象外です。

## 有効化せずにビルドして検証する

```powershell
npm run models:build:additive-examples
uv run python backend/scripts/verify_model_package.py examples/model-packages/additive-terms/point --example
uv run python backend/scripts/verify_model_package.py examples/model-packages/additive-terms/normal --example
```

品質レポートには、固定された合成データに対する学習RMSE、説明の再構成誤差、応答曲線の変動幅が記録されます。
どちらのモデルパッケージも `models/active-packages.json` には追加されません。
