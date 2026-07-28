# ドキュメント索引

このdirectoryの直下は、文書へ入るための入口だけです。

- [Developer Start Here](developer-start-here.md) — 変更したいことから正本、成果物、検証へ進む
- [プロダクト](product/README.md) — 何のアプリか、現在のscope、UIとnavigationの方針
- [実装契約](contracts/README.md) — identity、invariant、保存・実行・モデル境界
- [運用手順](operations/README.md) — 開発、検証、build、配布、backup／restore
- [実装事例](examples/README.md) — 特定Dataset、Task、Chain、Packageによる非正本の具体例
- [開発教材](learning/README.md) — 実装を題材にしたreader、site、演習、評価

過去の採否判断は [`decisions/`](decisions/)、条件付き計測は [`benchmarks/`](benchmarks/)、特定時点の監査・受入・実験結果は [`reports/`](reports/) にあります。
`architecture/`は構造の測定記録であり、現在の実装契約ではありません。

## authorityの読み方

directoryは文書の役割を示します。
細かな実装状態はcode、test、生成済み[Task inventory](contracts/task-inventory.json)を優先し、過去のADRやreportを現在の正本として扱いません。

移動前のroot文書と現在path、authority、owner、更新契機は [`inventory/root-documents.json`](inventory/root-documents.json) に記録しています。

## 配置規則

`docs/`直下に置けるファイルは、この`README.md`と`developer-start-here.md`だけです。
新しい文書は役割に応じたdirectoryへ追加し、対応するdirectory READMEを更新します。

```powershell
npm run docs:check
```

この検査は相対link、root allow-list、inventory targetを確認します。
