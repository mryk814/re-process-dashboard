# Configurable estimator training

## 目的

Feature Pipelineまで同じなら、Ridge、Gaussian Process、LightGBMなどの
通常の表形式Estimatorを交換するたびにTask専用builderファイルを作らない。

交換境界は既存の`canonical-training-dataset/v1`です。
この形式はTask、Feature Pipeline ID/version、特徴量名と順序、観測ID、
training context、targetを保持しています。

```text
source + Profile + Task
        ↓
Feature Pipeline
        ↓
canonical-training-dataset/v1
        ↓
allow-listed Estimator Recipe
        ↓
artifact + quality + diagnostics
        ↓
common Model Package assembler + production smoke
```

## 責任

### FeatureDataset

- Task固有の対象行、Feature Pipeline、target対応を保持する。
- `condition_context_id`がある観測だけを反復単位として平均し、なければsource rowを保持する。
- CVの依存groupには別途`parent_key`を使い、同じ論文・母材をfold間へ分割しない。
- 同一反復単位を平均する前に、特徴量とvalidation groupが一致することを検証する。
- source/profile/feature dataset digestをEstimator間で変えない。

### Estimator Recipe

- 固定allow-list IDと境界付きparameterだけを持つ。
- Task ID、source path、Python import path、callback、任意コードを持たない。
- 現在は`ridge.v1`、`exact-gp-rbf.v1`、
  `lightgbm-regression.v1`、`lightgbm-binary.v1`を提供する。
- TaskのRuntime Capabilityを満たさない組合せはfit前に拒否する。

### Estimator trainer

- targetごとの`X / y / training context`だけを受け取る。
- runtime adapterが読むdata-only artifact、PredictorSpec、quality、diagnosticsを返す。
- Feature PipelineやPackage activationを変更しない。

### Package assembler

- pipeline spec、training recipe、artifact digest、quality report、smoke、
  provenance、manifestを一度だけ組み立てる。
- 本番loader/runtimeでsmokeを再現する。
- immutable Package IDを上書きせず、active化しない。

## 標準経路と高度な経路

標準経路は、固定長のFeatureDatasetから各targetを学習でき、Runtime Capabilityが
既存adapterで表現できる場合に使います。

高度な経路として残す例:

- Horseshoeなどposterior drawと選択診断を成果物にするモデル
- 反復を平均せずheteroscedastic/random effectを学習するモデル
- 逃げ面摩耗のrun系列、`log1p` target、専用評価
- targetごとに異なるFeature Viewを使うmulti-stage/observation model
- deterministic transform

高度なモデルを標準経路へ見せかけるために情報を落としません。

## 安全境界

- Packageはdata-only。pickle、joblib、trainer import path、任意Python pluginは禁止。
- EstimatorはTaskの入力、出力、単位、Feature Pipeline、Runtime Capabilityを変更できない。
- Exact GPは`max_rows`を超えたら停止し、暗黙sampling/truncationをしない。
- `build`は候補Packageを作るだけで、available/active/Project/Snapshotを暗黙更新しない。
- AutoML的な自動winner採用は行わない。同じsplitで比較する仕組みを整えてから、
  人がqualityと科学的妥当性を確認して採用する。

## 評価identityと明示比較

targetごとの学習cohort、validation groupから作るfold assignmentは
FeatureDataset境界で一度だけ決めます。
`cohort_digest`と`fold_digest`をTraining Recipe、predictorの標準training
metadata、学習統計へ保存するため、Estimatorが変わっても同じ評価対象かを機械的に
照合できます。

qualityはこのfold identityを表示するだけではなく、外側foldを完全に未観測のまま
評価します。Exact GPは外側foldごとに標準化、平均、反復ノイズ、kernel
hyperparameter、precisionを学習subsetだけで再推定します。RidgeとLightGBMの
残差区間、LightGBM二値分類のPlatt校正は、外側学習subset内でもう一段inner
OOF予測を作って校正し、外側foldの目的値を残差bankやcalibratorへ混ぜません。
最終artifactだけを全cohortで学習し、deploy用区間・calibratorには全行のhonest
outer OOF予測を使います。

`npm run model:compare`は同じFeatureDataset・cohort・foldで複数Estimatorの候補Packageを
作り、target別qualityを並べた`standard-model-comparison/v1`を出力します。
`selection`は常に空で、active Package、Project、保存済みSnapshotを変更しません。

## 移行済みの標準経路

tabular TaskのRidge、LightGBM回帰、LightGBM二値分類は共通trainer/assemblerへ
移行済みです。
これらのTaskは`TaskModule.model_builder`を持たず、Estimatorを省略すると
Taskに宣言した既定Training Recipeを使います。
学習設定の正本は`model-training-recipe/v1`であり、Dataset Profileは列、単位、
行の意味、curationだけを担います。

既存Profileに残る`model_family`、`ridge_alpha`、`num_boost_round`、
`monotone_decreasing_paths`は、既存Profile/Package digestを壊さないための
互換読み取り専用です。
新しいbuildでは参照しません。
