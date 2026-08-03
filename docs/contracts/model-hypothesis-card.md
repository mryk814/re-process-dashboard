# Model Hypothesis Card

`model-hypothesis-card/v1`は、model名ではなく、観測過程・潜在量・共有構造・
検証方法を固定して候補modelを比較するためのdata-only契約です。

CardはPython code、import path、callback、任意formula、PPL graphを保持しません。
LLMまたは開発者が作ったpayloadは`extra=forbid`のtyped schemaで検証し、実行可能性は
Card内の文字列ではなく、アプリ本体のallow-list済みrecipe／specialized builderへ
解決します。

## 必須の意味分離

- `observation_protocol`: entity、観測値、独立単位、replicate、group、time、測定protocol
- `latent_process`: 潜在量、観測link、観測noise
- `sharing_structure`: 全体共有、partial pooling、shared curve等の共有範囲
- `prior_policy`／`inference_policy`: allow-list済み方針のidentity
- `identifiability_risks`／`known_failure_modes`: 採用前に確認する破綻条件
- `validation_protocol`: 同一cohortの比較方法
- `required_capabilities`: Cardを実行可能にするruntime能力

model名だけのCard、観測値と潜在量を同一記述にしたCard、識別性riskやdiagnosticが
空のCardは受理しません。

未採用の仮説Cardは`recipe_identity`を持たずに検証できます。ただしbundled
allow-list catalogへ載せるCardはreview済みrecipe identityを必須とします。
これにより仮説作成と実行可能recipeへの昇格を混同しません。

## Evidence

全Cardは次を`required`として宣言します。

1. `synthetic_recovery`: 既知の生成構造を回復できる条件と限界
2. `counterexample`: 仮定が崩れるデータで誤採用を防げること

この宣言はevidenceを実施済みに見せるものではありません。結果の保存ownerは
Model PlaygroundのRun contractへ接続した後に実装します。

## Catalogと比較

`model-hypothesis-catalog/v1`はbundled allow-listです。現時点では次を投影します。

- Ridge linear baseline
- Bayesian additive spline
- Exact RBF Gaussian process
- 既存Stage C grouped RidgeによるWelding Charpy observation family

研究候補だけを選びbaselineを含めない比較は拒否せず、比較不能を避ける警告を返します。
単一scoreやautomatic winnerは生成しません。

## LLM／開発者workflow

LLMまたは開発者は、Cardをmodel名の提案へ短絡させず、次の順序で扱います。

1. entity、observation、replicate、group、timeを特定する
2. 観測値と潜在量を分離する
3. measurement protocolとtarget supportを確認する
4. 共有構造とconstraintを列挙する
5. baselineを含む2〜4件のHypothesis Cardを生成する
6. synthetic recoveryとcounterexampleを作成する
7. compatibleなValidation Planで比較する
8. adoption memoを保存する
9. 採用候補だけをversioned recipeへ昇格する

現在のCard validatorとcatalogは1〜7の契約を表します。8〜9の保存と昇格は
Model Playground Run ownerへ接続した後に実行し、Card validationをadoption済みの
証拠として扱いません。

## Handoffの現在地

現行の実在surfaceはModel Libraryの固定Package確認です。#800の
`model-exploration-run/v1`は未実装であるため、Card presentationは
`future_status=not_implemented`と
`blocked_reason=model_exploration_run_contract_unavailable`を返します。

したがって、現時点ではCardを保存済みPlayground Runへ固定した、build／compareした、
adoption memoを保存した、とは主張しません。#800がRun ownerを導入した後、Cardと
recipe identityをimmutable Runへ固定します。
