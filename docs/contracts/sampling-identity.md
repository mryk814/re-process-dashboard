# Sampling Identity

`sampling-identity/v1`は、sample-basedな予測が実際に使用した再現条件の正本です。
Model Packageはposterior artifactとruntime configurationを固定し、operation policyは
seedとsample budgetを要求し、runtime adapterが実効identityを返します。

Model fitに使ったalgorithmは別契約の
[Inference Policy](inference-policy.md)／`inference-identity/v1`が正本です。
新しいPackageがInference Identityを宣言する場合、Sampling Identityはそのpolicy IDと
identity digestを参照します。既存Packageで欠落している値は推測しません。

## Sampling Request

sample-based runtimeの全呼出は、operationごとにversion固定された
`SamplingRequest`を明示的に渡します。requestは次を含みます。

- operationとpolicy ID
- method ID／version
- seed
- requested sample count
- 上記全体のcanonical digest

preview、詳細予測、Response Surface、Prediction Graph Stage、Decision Activity、
Screening／Proposal、Missing Completion、候補export、Package verificationは
それぞれ独立したpolicyです。呼出側がrequestを省略した場合、sample-based runtimeは
暗黙の既定値で続行せず失敗します。Sampling Identityを運搬できないResponse Curve経路は、
sample-based runtimeに対して明示的に利用不可とします。

## 現在の適用範囲

`numpyro.dense_posterior.v1`のPredictive Summaryは、次を必ず返します。

- operation、request policy ID／digest
- runtime、method、version
- seed
- requested／effective sample count
- Package内のposterior draw count
- draw selection／resampling／aggregation policy
- approximation／fallback
- 全parameterのcanonical digest

sample countがposterior draw数より少ない場合はseed付き非復元抽出、多い場合はseed付き
復元抽出、等しい場合は全drawを使用します。同じ入力、Package、seed、sample count、
policyは同じsummaryとidentityを返します。

predictive resampling policyはlikelihood family別です。continuous／count／ordinal familyは
seed付きlikelihood resamplingを記録し、`bernoulli_logit`はposterior probabilityの集約だけを
行うため、likelihood resamplingなしと記録します。

決定論的runtimeはSampling Identityを持ちません。呼出側がSampling Requestを渡しても、
adapterが使わないseedやsample countを結果へ捏造しません。

## 保存とcache

canonical `Prediction`がSampling Identityを保持するため、詳細予測Snapshot、
Decision Activityのcanonical Prediction、Prediction Graph Stage resultへ同じidentityが
保存されます。Missing Completion Labはtarget別のprediction Sampling IdentityをReportへ
固定します。

process-local inference cache、Decision Activityのsemantic identity、Prediction Graph Stageの
input digestは、sample-based runtimeにだけSampling Request全体を含めます。operation、
method version、seed、sample count、policy digestのいずれかが異なるrequestは別keyです。
superseded Graph executionの保存規約は既存のcompare-and-swap契約を維持します。

## Legacy

既存Snapshotの値やdigestは書き換えません。target別runtime authorityがあり、
`numpyro.dense_posterior.v1`のPredictionにSampling Identityがない場合だけ、read projectionで
`sampling-identity/unavailable-legacy`を返します。決定論的targetは利用不可扱いにしません。

target別runtime authorityがないmixed-runtime Snapshotでは、個々のtargetを推測せず、
Snapshot全体のstatusをunknown／unavailable legacyとして返します。seedは推測せず、
legacy Runを再計算しません。
