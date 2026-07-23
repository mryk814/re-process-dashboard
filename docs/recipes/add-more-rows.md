# Recipe B：行が増えただけのExcel

同じ構造と意味なら同じProfileを再利用します。ファイル内容SHAが変わるため、新しいData AssetとDataset Revisionになります。

```powershell
npm run dev:doctor -- --source path/to/file.xlsx
uv run python backend/scripts/profile_workbench.py validate path/to/file.xlsx --profile <existing-profile>
uv run python backend/scripts/profile_workbench.py register path/to/file.xlsx --profile <existing-profile> --database data/workbench.db --library data/data-library
npm run model:build -- --task <task-id> --source path/to/file.xlsx --output models/packages/<new-id>
npm run model:verify -- --task <task-id> --source path/to/file.xlsx --package models/packages/<new-id>
```

既存Projectは旧Dataset Revisionと旧Packageに固定されたままです。新しいデータを使う検討は新Projectとして作成し、Snapshotを再計算しません。
