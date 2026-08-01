# Feature Recipe

`feature-recipe/v1` は、Profileが作るcanonical inputからモデル入力featureを作るdata-only契約です。
Profileはsource列名、単位解釈、canonical pathへのmappingを所有し、Recipeはその後の変換だけを所有します。

## Authority

- schema: `backend/src/decision_workbench/contracts/feature_recipe_contracts.py`
- fit／transform runner: `backend/src/decision_workbench/modeling/training/feature_recipe.py`
- Package検証: `backend/src/decision_workbench/modeling/packages/verification.py`
- developer trace: `POST /api/developer/feature-recipe/inspect`

operationは記載順に評価し、後続operationは先行operationの出力を参照できます。
最終feature名、unit、意味、group、順序は`features`が固定します。

P0 allow-listは次だけです。

- `passthrough`
- `standardize`
- `robust_scale`
- `log1p`
- `polynomial_degree_2`
- `one_hot`
- `missing_indicator`
- `impute`（`constant`／`median`）
- `pairwise_interaction`
- `cyclic`

未知operation、任意Python callback、import path、pickle、joblibは受け付けません。
clip／winsorizeはraw値の補正と誤解されるため含めません。
材料式、熱履歴要約、series representationは型付きdomain pipelineのauthorityに残し、汎用operationへ移しません。

## fitと評価

fitが必要な`standardize`、`robust_scale`、median imputationは、同じrunnerを使います。
評価時は各foldのtraining rowだけでstateをfitし、held-out rowにはそのstateを適用します。
最終Packageのstateは、評価完了後にcanonical training cohortへfitします。
Validation Planのgroup／time割当は変更せず、preprocessingだけをfold-localにします。

## Package artifact

Recipeとfit stateはそれぞれUTF-8 JSON artifactとして保存します。
stateはRecipe digest、fit row数、operation順、parameter shape、feature順、自身のsemantic digestを含みます。
manifestのartifact hashに加えて、runtime loaderはRecipe／state／feature orderの一致をfail closedで検証します。

P0 stateは有限scalarだけなのでJSONを使います。
将来、allow-list済みoperationが固定shapeの配列stateを必要とする場合だけ、`allow_pickle=False`で読める数値NPZを使い、object arrayや実行可能payloadは許可しません。

`feature_recipe`を持たない既存Packageは保存済みpipeline versionの従来runnerで再現します。
新規PackageだけがRecipe artifactを明示的に選びます。
Profile内のlegacy `transform`／axis interactionは既存Package再現用として読み続けますが、新operationをそこへ追加せず、Feature Recipeへ記述します。
