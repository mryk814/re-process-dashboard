# 二値、カウント、順序目的変数の入出力カード

## 使用する場面

新しいモデルが連続値の材料特性ではなく、確率、非負のカウント、順序付きカテゴリを予測する場合は、リポジトリに含まれるこれらの検証用一式を使います。
偽の本番タスクを登録したりモデルパッケージを有効化したりせずに、既存の `numpyro.dense_posterior.v1` アダプターを動かせます。

## 契約マトリクス

| 種類 | 予測分布族 | 代表値統計量 | 必須の意味 | 検証用成果物 |
|---|---|---|---|---|
| `binary` | `bernoulli_logit` | 確率 | 代表値と事象確率が一致して `[0,1]` の範囲に収まり、単位は無次元 | `examples/model-packages/numpyro/bernoulli_logit` |
| `count` | `poisson_log`, `negative_binomial_log`, `zero_inflated_poisson_log` | 率 | 代表値と分位点が非負で、カウントの台が明示されている | `examples/model-packages/numpyro/poisson_log` と同種の検証用成果物 |
| `ordinal` | `ordinal_logit` | 期待カテゴリ | 有限で単調増加するしきい値、一意で順序を持つラベル、カテゴリ添字の範囲内に収まる出力 | `examples/model-packages/numpyro/ordinal_logit` |

すべての例は、アプリ共通入力として `composition.C` と `composition.Mn` を共有し、順序を持つ2値のFeatureBundleと、固定全結合ネットワークの事後配列だけを含む安全なNPZを使います。
学習にはNumPyroとJAXを使用できますが、本番推論ではNumPyだけを使い、学習器オブジェクト、Pythonグラフ、pickle、インポートパスは読み込みません。

## 機能と表示

- `binary` はネイティブの事象確率を公開します。
  `count` と `ordinal` の目標達成確率は、将来の契約で事象が定義されない限り利用できません。
- 分位点は、乱数シードを指定した決定論的な事後予測評価から得ます。
  生の標本は公開しないため、`samples=false` です。
- パラメトリック分布族の識別情報、目的変数種別、代表値統計量、分位点水準、順序カテゴリのメタデータは明示された状態を保ちます。
- UIは `binary` の代表値を百分率として、`ordinal` の代表値を期待カテゴリとして表示し、どちらにも材料単位を付けません。
  目標達成確率がない場合は、作成した正規近似ではなく `利用不可` と表示します。
- 回帰精度ダッシュボード、混同行列、任意のカテゴリ、生存時間目的変数、新しい本番タスクは、この検証用一式の対象外です。

## 有効化せずにビルドして検証する

```powershell
npm run models:build:examples
uv run python backend/scripts/operations/verify_model_package.py examples/model-packages/numpyro/bernoulli_logit --example
uv run python backend/scripts/operations/verify_model_package.py examples/model-packages/numpyro/poisson_log --example
uv run python backend/scripts/operations/verify_model_package.py examples/model-packages/numpyro/ordinal_logit --example
```

各モデルパッケージには、ハッシュ化されたスモークテストの入力と期待出力、一致する `TargetRuntimeCapability`、明示的な契約数が含まれます。
これらの読み込み検証用一式には観測値が含まれないため、精度指標は意図的に省略しています。
コマンドは `models/active-packages.json` を変更しません。
