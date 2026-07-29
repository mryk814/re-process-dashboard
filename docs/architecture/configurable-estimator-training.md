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
- 現在は`ridge.v1`と`exact-gp-rbf.v1`を提供する。
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

## 移行

最初の縦切りとして、熱延Taskは従来のHorseshoe authoring workflowを維持したまま、
同じFeatureDatasetから`exact-gp-rbf.v1`を選択できるようにしました。

次の段階では、既存Packageを新しいIDで再生成しながら次を進めます。
残作業は
[#506](https://github.com/mryk814/re-process-dashboard/issues/506)
で追跡します。

1. tabular Ridge/LightGBMのfitとPackage組立を共通経路へ移す。
2. predictor表示文言を`PredictorSpec.config`の標準training metadataへ統合する。
3. Dataset Profileから`model_family`などの学習設定を分離する。
4. 移行済みTaskから旧builderと`TaskModule.model_builder`を削除する。

Profileと保存済みPackageはデータ契約なので、一括削除せず新Packageへの移行確認後に
旧設定を除去します。
