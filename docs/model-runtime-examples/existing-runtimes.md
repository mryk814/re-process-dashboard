# 既存モデルランタイムの入出力カード

## 固定ベクトルから決定論的スカラーへ

- アプリ共通入力は、タスク固有の順序を持つFeatureBundleへ変換されます。
- `builtin.linear.v1` は安全なNPZから `weights`、`bias`、固定された区間のずれを読み込みます。
- 平均と経験分布の `q05`、`q50`、`q95` を返しますが、パラメトリック分布やネイティブ標本はありません。
- 学習時とランタイムの依存関係はNumPyだけです。
  任意の推定器、コールバック、動的コードには対応しません。
- このランタイムは利用可能ですが、現行のactive Packageでは使われていません。
  `backend/scripts/generators/build_default_model_package.py` が生成する焼鈍後特性Packageも、現在は `builtin.exact_gp.v1` を使います。

## 固定ベクトルからネイティブ木予測へ

- `lightgbm.booster.v1` はLightGBMのネイティブテキスト成果物を読み込み、pickleやsklearnラッパーは読み込みません。
- アダプターは、点予測を中心に置いた経験分布の要約を返します。
  新しい明示的な成果物契約を追加しない限り、標準偏差、標本、パラメトリック確率は利用できません。
- 学習時とランタイムにはLightGBMが必要です。
  任意依存関係がない場合は、別のモデルへ切り替えず、そのモデルパッケージを利用不可にします。
- 契約例は `backend/tests/test_optional_adapters.py` にあります。

## 固定ベクトルから許可リスト登録済みsklearn推定器へ

- `sklearn.skops.v1` は、アプリケーションが所有する推定器族と信頼済み型の許可リストに登録されたskops成果物だけを読み込みます。
- これらの推定器族で表現できる固定点予測器に適しています。
  独自変換器、コールバック、任意のクラスグラフ、pickle、joblibは禁止されたままです。
- 学習時とランタイムにはsklearnとskopsの任意依存関係が必要です。
  依存関係がない場合は、代替手段を選ばず、そのモデルパッケージを利用不可にします。
- 現在の要約は、点予測を中心に置いた経験分布の分位点です。
  パラメトリックな不確かさがあることは意味しません。

## 固定ベクトルからパラメトリック正規分布または対数正規分布へ

- `builtin.exact_gp.v1` は、固定されたグループ化exact-RBF構造向けの、上限を定めた安全なNPZ配列を読み込みます。
  `gpytorch.static_exact_rbf.v1` は、固定構造向けの許可リスト登録済みsafetensorsを読み込みます。
- 正規分布の出力は、平均、標準偏差、`q05`、`q50`、`q95`、不確かさの構成要素を持ちます。
  対数正規分布は、宣言済みのlog1p潜在変換と正の目的変数という意味がある場合に限って利用できます。
- 学習には汎用の数値計算ライブラリやGPライブラリを使用できます。
  組み込みの本番ランタイムはNumPyだけを使い、GPyTorch経路には明示的な任意依存関係があります。
- 未知のテンソル構造、非有限値、互換性のない形状、未宣言の変換は拒否されます。
  学習器オブジェクトと任意のカーネルには対応しません。
- 現行active Packageのビルダーとスモークテストは、焼鈍後特性の `backend/scripts/generators/build_default_model_package.py` と逃げ面摩耗の `build_flank_wear_model_package.py` にあります。

## 固定ベクトルから線形事後予測へ

- `builtin.posterior_linear.v1` は、係数、切片、観測ノイズスケールの事後ドローを安全なNPZから読み込みます。
- 熱延後特性のactive Packageは、このランタイムを `predictive_family=normal` と `output_representation=moment_matched_normal` で使います。
  事後予測の平均と分散を正規分布へモーメントマッチングし、乱数シードに依存しない要約を返します。
- 検査用の疎ベイズ例は同じランタイムを `predictive_family=empirical_quantiles` で使います。
  観測ノイズを標本化して経験分位点を返すため、乱数シードを固定して再現します。
- どちらの経路も、生の事後ドローをAPIへ公開しません。
  正規近似を使うactive経路と、経験分位点を返す例示経路は、同じ予測表現として扱いません。
- 熱延後特性のビルダーとスモークテストは `backend/scripts/generators/build_hot_rolling_model_package.py` にあります。
  未有効化の例は[線形事後予測のカード](sparse-bayesian.md)から構築できます。

## 固定ベクトルから事後予測へ

- `numpyro.dense_posterior.v1` は、事後分布の重み配列とバイアス配列、および許可リスト登録済みの尤度から、固定全結合MLPを評価します。
- 安全なNPZでは、要素数、展開サイズ、圧縮率、テンソル数、標本抽出回数、層数、形状、有限値に上限や条件を設けています。
- 正規、Student-t、対数正規、Bernoulli、Poisson、NB、ZIP、順序尤度は、それぞれの目的変数の台と代表値統計量を維持します。
  乱数シードを指定した事後予測評価は決定論的です。
- NumPyroとJAXは学習時だけ使用し、本番推論にはNumPyを使います。
  PredictiveSummaryは生の標本を公開しません。
- 検査済みのスモークテスト、品質、能力の例と検証コマンドは、`examples/model-packages/numpyro/` と[非連続目的変数のカード](non-continuous-targets.md)にあります。
