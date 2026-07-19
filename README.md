# Material Decision Workbench

材料組成と焼鈍ヒートパターンの候補を比較し、予測特性・予測幅・学習範囲・類似する過去実験を同じ判断面で確認するローカルアプリです。

## 開発起動

```powershell
uv sync --extra dev
npm install
npm run dev
```

- Web UI: <http://127.0.0.1:5180>
- API: <http://127.0.0.1:8765/docs>

停止は起動したターミナルで `Ctrl+C` です。

## デスクトップアプリとして起動

初回だけ依存関係を準備します。

```powershell
uv sync --extra dev
npm install
npm run build
```

その後は次でElectronアプリを起動できます。Python APIはElectronが同時に起動・終了します。

```powershell
npm run dev:desktop
```

終了はアプリのウィンドウを閉じます。API用ポート `8765` が使用中の場合は、既存の開発サーバーを `Ctrl+C` で終了してから起動してください。

## 確認

```powershell
uv run pytest
npm run typecheck
npm run build
```

## データ

`data/source/process_dashboard_realistic_excel_v2.xlsx` を読取専用の正本として扱います。初回起動時に工程、観測、系譜、データ品質を構築します。元Excelは変更しません。

候補・プロジェクト・予測スナップショット・実測値は `data/workbench.db` に保存します。候補一覧は画面からXLSXで入出力でき、ヒートパターンも往復保持されます。
