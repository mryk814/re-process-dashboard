# Inference Policy

`inference-policy-definition/v1`は、モデルの科学仮説とは別に、事後分布を
どのreview済みalgorithmで計算するかを固定するallow-list契約です。

```text
model hypothesis / recipe
  + latent structure / differentiability / dimension
  + posterior requirement / compute budget
              ↓
inference policy resolution
              ↓
effective inference identity + diagnostics
```

## 責務

- `InferenceRequirements`はcontinuous／discrete／mixed／state-space latent、
  differentiability、posterior dimension、multimodality、sample要件を表す
- resolverは固定catalogから一つを選ぶか、理由付き`unavailable`を返す
- `InferenceIdentity`はalgorithm、version、parameterization、seed、draw／particle数、
  resource limit、convergence criteria、diagnosticsを保存する
- model recipe digestとInference Identity digestは分離する
- failure後に別algorithmへ同名fallbackしない。別algorithmを使う場合は新しい
  resolutionとInference Identityを発行する

catalogにはanalytic Gaussian、NUTS、finite discrete enumeration、Gibbs、SMC、
Laplace、bounded variational policyがある。catalog登録は実装済みまたはproduction-readyを
意味しない。各definitionの`lifecycle_status`を確認し、`experimental`を`ready`と表示しない。

## Sampling Identityとの関係

Inference Identityはモデルfit時のalgorithm、Sampling Identityは保存済みposteriorから
予測要約を作るoperation条件です。sample-based runtimeの新しい予測は、Packageに
Inference Identityがある場合、そのpolicy IDとidentity digestをSampling Identityへ
参照として固定します。

既存Packageと既存SnapshotはInference Identityを持たないまま読み続けます。
欠落値からsampler、seed、draw数を推測せず、保存済みmanifestやSnapshotを書き換えません。

## Diagnostics

共通diagnosticsは、適用できるalgorithmに限ってR-hat、ESS、divergence、
particle ESS、approximation failureを保持します。analytic methodへR-hat等を捏造せず
`not_applicable`とします。failure findingを保存しないまま`passed`にはしません。

## 安全境界

- arbitrary sampler pluginや任意codeを実行しない
- optional dependency不足時に別policyへ同名fallbackしない
- approximationをexact posteriorと呼ばない
- Model LibraryはPackageに保存されたInference Identityだけを投影する
- Model Playgroundはこのtyped resolution／identityをRunへ固定する。現時点では
  Playground自体の永続Run UIは #800 の責務である
