# Recipe A：列名だけ違うExcel

例：`C[%] → C[mass%]`、`焼鈍条件 → Annealing`、`引張強さ → TS`。

意味とcanonical unitが同じならTaskDefinitionとFeature Pipelineは変更しません。Dataset Input Profileでシート、列、source unit、key、relationを対応付けます。

```powershell
npm run dev:doctor -- --source path/to/file.xlsx
uv run python backend/scripts/profile_workbench.py inspect path/to/file.xlsx --profile backend/src/material_workbench/data/dataset-input-profile-example.json
uv run python backend/scripts/profile_workbench.py validate path/to/file.xlsx --profile backend/src/material_workbench/data/dataset-input-profile-example.json
uv run python backend/scripts/profile_workbench.py register path/to/file.xlsx --profile backend/src/material_workbench/data/dataset-input-profile-example.json --database data/workbench.db --library data/data-library
```

値や学習行が既存Packageの学習データと変わる場合だけ、新しいPackageを学習・検証します。単なる表示名変更をcanonical入力変更として扱わないでください。
