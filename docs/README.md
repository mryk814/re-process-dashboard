<!--
document-status: current
verified-commit: 50e403c697910b699a95cf7aa3082baec30a8b42
owner: docs architecture
source-of-truth: documentation navigation and placement policy
-->

# ドキュメント索引

このdirectoryの直下は、文書へ入るための入口だけです。

- [自分のデータで使い始める](operations/data-contributor-start-here.md) — 既存の仕組みへ手元のDataset／Profile／Model Packageを追加する
- [Developer Start Here](developer-start-here.md) — アプリ、契約、共通toolingを変更する
- [プロダクト](product/README.md) — 何のアプリか、現在のscope、UIとnavigationの方針
- [実装契約](contracts/README.md) — identity、invariant、保存・実行・モデル境界
- [運用手順](operations/README.md) — 開発、検証、build、配布、backup／restore
- [実装事例](examples/README.md) — 特定Dataset、Task、Chain、Packageによる非正本の具体例
- [開発教材](learning/README.md) — 実装を題材にしたreader、site、演習、評価
- [互換 identity 台帳](compatibility/identity-ledger.md) — 保存済みの旧名と移行条件

過去の採否判断は [`decisions/`](decisions/)、条件付き計測は [`benchmarks/`](benchmarks/)、特定時点の監査・受入・実験結果は [`reports/`](reports/) にあります。
`architecture/`は構造の測定記録であり、現在の実装契約ではありません。

## 文書の状態

| 状態 | 読み方 | 主な入口 |
| --- | --- | --- |
| `current` | 現在のscope、契約、手順。code／generated inventoryとの不一致は修正対象 | `product/`、`contracts/`、`operations/` |
| `decision` | 採否理由と制約を残す。現在の件数や画面一覧には使わない | `decisions/` |
| `compatibility` | 意図的に残す保存identityと、その変更条件 | `compatibility/identity-ledger.md` |
| `historical` | report、spike、measurement。記録時点の証拠であり現在能力の一覧ではない | `architecture/`、`benchmarks/`、`reports/` |
| `learning` | 実装を読む教材。章ごとの`verified_commit`へ固定された説明で、current behaviorの正本ではない | `learning/README.md` |

## authorityの読み方

directoryは文書の役割を示します。
細かな実装状態はcode、test、生成済み[Task inventory](contracts/task-inventory.json)を優先し、過去のADRやreportを現在の正本として扱いません。

移動前のroot文書と現在path、authority、owner、更新契機は [`inventory/root-documents.json`](inventory/root-documents.json) に記録しています。
主要文書の状態、owner、source of truth、確認commitは [`inventory/document-authority.json`](inventory/document-authority.json) に記録しています。

## 配置規則

`docs/`直下に置けるファイルは、この`README.md`と`developer-start-here.md`だけです。
新しい文書は役割に応じたdirectoryへ追加し、対応するdirectory READMEを更新します。

```powershell
npm run docs:check
```

この検査は相対link、root allow-list、inventory targetを確認します。
