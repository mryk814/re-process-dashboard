# Recipe B：行が増えただけのExcel

同じ構造と意味なら同じProfileを再利用します。ファイル内容SHAが変わるため、新しいData AssetとDataset Revisionになります。

```powershell
npm run dev:doctor -- --source path/to/file.xlsx
uv run python backend/scripts/operations/profile_workbench.py validate path/to/file.xlsx --profile <existing-profile>
uv run python backend/scripts/operations/profile_workbench.py register path/to/file.xlsx --profile <existing-profile> --database data/workbench.db --library data/data-library
npm run model:build -- --task <task-id> --source path/to/file.xlsx --profile <existing-profile> --package-id <new-id> --package-version <new-version>
npm run model:verify -- --task <task-id> --source path/to/file.xlsx --profile <existing-profile> --package artifacts/model-package-candidates/<new-id>
npm run model:promote -- --task <task-id> --source path/to/file.xlsx --profile <existing-profile> --package artifacts/model-package-candidates/<new-id>
```

`model:promote`は、既定ではリポジトリ外の個人用Model StoreへPackageを昇格します。
Windowsの既定保存先は`%LOCALAPPDATA%\Material Decision Workbench\models`です。
別の保存先を使う場合は、`WORKBENCH_MODEL_STORE_PATH`または`--store`を指定します。

起動中のData Libraryで「個人モデルを再読込」を実行し、新しいDataset RevisionとPackageを選んでProjectを作成します。
既存Projectは旧Dataset Revisionと旧Packageに固定されたままであり、Snapshotも再計算しません。
