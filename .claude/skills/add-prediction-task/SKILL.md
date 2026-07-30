---
name: add-prediction-task
description: 現行TaskModule Registryへ新しい予測Taskを安全に追加する。似たExcelの追加ではなく、入力・出力・学習単位が異なる予測問題が対象。
---

# Add Prediction Task

最初に [Developer Start Here](../../../docs/developer-start-here.md) を読み、「同じ予測契約へ新しいExcelを追加」と「新しい予測問題」を分ける。

## 同じ予測契約へ新しいExcelを追加

- TaskDefinitionとFeature Pipelineは原則変更しない。
- `backend/src/material_workbench/data/dataset-input-profile-*.json` でシート、列、単位、key、relation、optional列の差を吸収する。
- Profile Workbenchでinspect、validate、registerする。
- 学習データが変わる場合だけ、新しいModel Packageを構築する。
- 既存Data Asset、Revision、Project、Snapshot、Packageを上書きしない。

```powershell
npm run dev:doctor -- --source path/to/file.xlsx
uv run python backend/scripts/operations/profile_workbench.py inspect path/to/file.xlsx
uv run python backend/scripts/operations/profile_workbench.py validate path/to/file.xlsx --profile <profile>
```

## 新しい予測問題を追加

次を縦一式で実装する。

1. `backend/src/material_workbench/tasks/task_definitions/<task-id>.json`
2. Dataset Input Profileとdata loader
3. Feature Pipeline、固定feature order、golden test
4. Runtime / support / PredictiveSummary
5. data-only Model Package builderとallow-list済みadapter
6. `backend/src/material_workbench/task_modules.py` の`TaskModule` entry
7. `models/active-packages.json`
8. contract / loader / golden / Package smoke / APIまたはE2E
9. `npm run task:inventory` と、API変更時の `npm run api:generate`

`TaskModule`はsource解決、loader、runtime factory、builder、application capability、Data Explorer capability、response curve / curve family handlerを所有する正本である。`TaskRegistry`がTaskDefinition、runtime、capability、Packageとの集合一致と契約一致を起動時に検証する。

## 禁止する旧配線

- `app.py`へTask固有Runtimeを直接追加しない。
- workflow、verifier、lifecycleへ同じtask dispatchを個別追加しない。
- 複数の中央`if task_id == ...`を登録機構として増やさない。
- `backend/src/material_workbench/contracts/schemas.py`の共通schemaへTask固有Literalを増やさない。
- Profileだけで新しい物理入力・出力を追加したことにしない。

既存共通処理にTask固有分岐が残る場合は、まず`TaskModule`のcallable／capabilityへ移せるか確認する。adapterを増やす前に既存runtime typeの安全なdata-only artifactで表現できるか検討する。

## 人間が決めること

目的変数、学習単位、反復観測、Feature Pipeline、分布、科学的範囲、新Taskかversion更新か、モデル採否は自動決定しない。元Excelは読取専用、Snapshotは不変、PackageからPythonコード・pickle・joblibを読み込まない。

## 検証

```powershell
npm.cmd run verify:edit -- backend/tests/test_task_registry.py backend/tests/test_task_contracts.py
npm run task:inventory:check
npm run api:check
npm run typecheck
npm run build
```

迷ったら現行の `task_modules.py`、`TaskRegistry`、`flank-wear-v1` 実装を同じcommitの正本として読む。古いSkillや過去PRの配線を復元しない。
