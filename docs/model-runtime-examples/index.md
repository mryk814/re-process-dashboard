# 入出力契約別のモデルランタイム例

学習ライブラリ名ではなく、モデルの入出力表現から選びます。
明示されていない限り、これらの例は有効化されていません。
安全なモデル成果物、アダプター契約、PredictiveSummaryの意味、不足している能力、スモークテスト、品質評価の証拠を固定することが目的です。

| 入出力契約 | 代表的な経路 | 状態とカード |
|---|---|---|
| 疎な配合明細 → whole-wire材料成分 | `builtin.deterministic_linear.v1` | 学習済みscalar predictorではない決定論的transform。科学masterとcompiler単位をPackageへ固定し、Stage Aで使用。[Package契約](../contracts/model-package-contract.md#許可する実行環境と資産形式) |
| 固定ベクトル → 決定論的スカラー | `builtin.linear.v1` | 利用可能だが、現行のactive Packageでは未使用。[既存ランタイムカード](existing-runtimes.md#固定ベクトルから決定論的スカラーへ) |
| 固定ベクトル → 許可リスト登録済みsklearn推定器 | `sklearn.skops.v1` | 任意の信頼済み型ランタイム。[既存ランタイムカード](existing-runtimes.md#固定ベクトルから許可リスト登録済みsklearn推定器へ) |
| 固定ベクトル → ネイティブ木予測 | `lightgbm.booster.v1` | 任意のネイティブランタイム。[既存ランタイムカード](existing-runtimes.md#固定ベクトルからネイティブ木予測へ) |
| 固定ベクトル → パラメトリック正規分布または対数正規分布 | `builtin.exact_gp.v1`, `gpytorch.static_exact_rbf.v1` | `builtin.exact_gp.v1`は焼鈍後特性と逃げ面摩耗で使用中。GPyTorch経路は任意。[既存ランタイムカード](existing-runtimes.md#固定ベクトルからパラメトリック正規分布または対数正規分布へ) |
| 固定ベクトルと観測ごとの分散 → 異分散正規分布 | `builtin.heteroscedastic_exact_gp.v1` | 個々の観測を保持する焼鈍後特性Packageで使用中。平均側とノイズ側を別々のRBF GPで表現します。[既存ランタイムカード](existing-runtimes.md#固定ベクトルからパラメトリック正規分布または対数正規分布へ) |
| 固定ベクトル → 事後予測 | `numpyro.dense_posterior.v1` | 安全な固定全結合グラフ。[既存ランタイムカード](existing-runtimes.md#固定ベクトルから事後予測へ) |
| 固定ベクトル → 複数出力を共有するモデル成果物 | PR #44の構想 | 設計は承認済みで、コードは未採用。[判断記録](../decisions/shared-multi-output.md) |
| 固定ベクトル → 加算スコアと項別寄与 | `builtin.additive_terms.v1` | 検査済みの点予測例と正規分布例。[入出力カード](additive-terms.md) |
| 固定ベクトル → 線形事後予測 | `builtin.posterior_linear.v1` | 熱延後特性では正規分布へのモーメントマッチングを使用中。経験分位点を返す未有効化の例もある。[入出力カード](sparse-bayesian.md) |
| 固定ベクトル → 固定経験分位点 | `builtin.quantile_linear.v1` | 分位点だけを返す検査済みの例。[入出力カード](quantile-only.md) |
| 固定ベクトル → 二値、カウント、順序尤度 | `numpyro.dense_posterior.v1` | 3種類すべての目的変数種別について検査済みの例。[入出力カード](non-continuous-targets.md) |
| 各構成要素の予測分布 → アンサンブル | ランタイムなし | Model Package v1では実装を見送り。[判断記録](../decisions/predictive-ensemble-decision.md) |

## 新しいモデル要求に対応する最短経路

1. FeatureBundleの形状、予測表現、モデル成果物の形式、ランタイム依存関係が最も近い行を選びます。
2. 意味を失わずにモデル成果物を書き出せる場合は、既存ランタイムを再利用します。
   学習ライブラリが異なるという理由だけでランタイムを追加することはできません。
3. リンク先のビルダーまたは検証用一式をコピーし、対応していない意味をすべて明記します。
   欠けている能力を、正規近似、0の標準偏差、交差した分位点の並べ替え、作成した標本で補うことはできません。
4. 本番での有効化を検討する前に、例示用検証器を実行します。

```powershell
uv run python backend/scripts/operations/verify_model_package.py <package-directory> --example
```

## 変更対象ファイルの対応表

ランタイムを再利用する場合、想定される変更範囲は、ビルダーまたは書き出しスクリプト、検査済みモデルパッケージ、入出力カード、アダプター契約とスモークテスト、目的変数に合った品質レポートです。
Registry、本番TaskDefinition、有効な選択状態は編集しません。

新しい安全なモデル成果物構造が本当に必要な場合に限り、次のアプリケーションファイルを追加します。

- `backend/src/decision_workbench/adapters/<adapter>.py`
- `backend/src/decision_workbench/modeling/model_package_contracts.py` の `RUNTIME_TYPES`・`PredictorSpec`構造検証と、`model_adapter_registry.py` の `AdapterRegistry`
- `backend/tests/` 内のアダプター契約テストと敵対的テスト
- `docs/contracts/model-package-contract.md` 内のランタイム表
- この索引と1個の入出力カード

新しいPredictiveSummaryの意味がAPI、スナップショット、UIまで伝達されていない場合は、`Prediction` を更新し、OpenAPI型とTypeScript型を再生成して、表示契約テストを1個追加します。
すでに伝達されている場合、これらの画面や境界は対象外です。

本番での有効化は、独立した明示的な判断として残ります。
有効化には、実在するTaskDefinitionとの一致、データセットとプロファイルの来歴、タスク固有の品質レビュー、ライフサイクル検証、`models/active-packages.json` の更新も必要です。
例示作業で、偽の本番タスクを作成する、学習器オブジェクトを読み込む、任意のPythonプラグインを追加する、汎用実験追跡を構築する、すべての例を自動的に有効化することはできません。
