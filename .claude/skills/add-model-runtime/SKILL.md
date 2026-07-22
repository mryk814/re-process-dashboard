---
name: add-model-runtime
description: 新しい学習済みモデルを、I/O契約に最も近い安全なModel Package事例から実装・検証する。
---

# Add a Model Runtime from an I/O contract

最初に [I/O契約別の事例索引](../../../docs/model-runtime-examples/index.md) を読み、手法名やtrainer libraryではなく、FeatureBundle・予測表現・artifact・runtime依存が最も近い行を選ぶ。

## 既存runtimeを再利用できる場合

adapterやRegistryを増やさない。trainer objectを保存せず、既存の固定artifactへexportするbuilder、inactive fixture Package、I/Oカード、adapter smoke/改竄test、意味に合うquality reportだけを追加する。

```powershell
uv run python backend/scripts/verify_model_package.py <package-directory> --example
```

このverifyはactivationを行わない。例示のために架空のproduction TaskDefinitionを登録しない。

## 新しいartifact schemaが必要な場合

変更候補は次に限定する。

1. `backend/src/material_workbench/adapters/<adapter>.py`: safe data-only loader、shape/finite/support検証、PredictiveSummary。
2. `backend/src/material_workbench/model_packages.py`: `RUNTIME_TYPES`、固定`architecture_id`、`AdapterRegistry`。
3. `backend/tests/`: golden、deterministic smoke、unknown schema、non-finite、shape、feature order、supportの拒否。
4. `backend/scripts/`: trainer/export builder。training dependencyをPackageへ漏らさない。
5. `examples/model-packages/`: inactive Package、hashed smoke、capability、target-specific quality report。
6. `docs/model-package-contract.md` と `docs/model-runtime-examples/`: runtime表とI/Oカード。
7. optional dependencyが必要な場合は`pyproject.toml`のdependency groupと`backend/src/material_workbench/app.py`のavailability map。

API/snapshot/UIを触るのは、新しい意味が既存`Prediction`で保持されない場合だけ。その場合はOpenAPI/TypeScript型を再生成し、擬似std・暗黙normal・永続的な「計算中」を出さないpresentation testを追加する。

## 必ず列挙する非スコープ

- active Package切替とproduction採用判断
- 任意Python plugin、pickle/joblib、trainer import path
- 自動ハイパーパラメータ探索・experiment tracking server
- capabilityにないstd/sample/goal probabilityの擬似生成
- Feature Pipelineからの自動変数削除
- UIへ大量の学習診断を混ぜること

production採用を依頼された場合だけ、TaskDefinition/dataset profile/provenance/qualityを実データで照合し、`model:verify`→明示的な`model:activate`へ進む。保存済みsnapshotは再計算しない。
