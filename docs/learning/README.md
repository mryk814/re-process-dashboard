# Material Decision Workbench 開発教材

このディレクトリは、Issue #274の試作から育てている開発者向け教材の正本です。

教材は、一般概念を説明するだけではなく、現行実装の契約、API、生成型、React、テストを一つの処理として読みます。
型付き契約の章では、「検討アクティビティ」がPythonから画面まで届く経路を扱います。
Source data lifecycleの章では、外部Sourceの取得、品質判定、承認、Training Snapshot、Model Package active化を分けます。

## 読み始める

- HTMLやPDFで読む場合は、次節の手順でbookを生成します。
- GitHub上で読む場合は [`index.qmd`](index.qmd) から始めます。
- 実装箇所を先に探す場合は [`code-map.qmd`](code-map.qmd) を使います。
- 学ぶ順番を選ぶ場合は `learning-paths/` の3ルートを使います。
- 編集する場合は [`AGENTS.md`](AGENTS.md) と [`writer-persona.md`](writer-persona.md) を先に読みます。

## Windowsで生成する

前提は次の二つです。

- [Quarto 1.10.18](https://github.com/quarto-dev/quarto-cli/releases/tag/v1.10.18)
- [Typst 0.15.1](https://github.com/typst/typst/releases/tag/v0.15.1)

systemへinstallせず、今回と同じ固定版binaryを利用する場合は、PowerShellで次を一度実行します。

```powershell
$bookToolRoot = Join-Path $env:LOCALAPPDATA "material-workbench-book-tools"
New-Item -ItemType Directory -Force -Path $bookToolRoot | Out-Null

Invoke-WebRequest `
  "https://github.com/quarto-dev/quarto-cli/releases/download/v1.10.18/quarto-1.10.18-win.zip" `
  -OutFile (Join-Path $bookToolRoot "quarto.zip")
Expand-Archive -Force `
  (Join-Path $bookToolRoot "quarto.zip") `
  (Join-Path $bookToolRoot "quarto")

Invoke-WebRequest `
  "https://github.com/typst/typst/releases/download/v0.15.1/typst-x86_64-pc-windows-msvc.zip" `
  -OutFile (Join-Path $bookToolRoot "typst.zip")
Expand-Archive -Force `
  (Join-Path $bookToolRoot "typst.zip") `
  (Join-Path $bookToolRoot "typst")

$env:Path = @(
  (Join-Path $bookToolRoot "quarto\bin")
  (Join-Path $bookToolRoot "typst\typst-x86_64-pc-windows-msvc")
  $env:Path
) -join ";"

quarto --version
typst --version
```

version表示が`1.10.18`と`typst 0.15.1`になれば準備完了です。
このPATH変更は現在のPowerShell processだけへ適用されます。
別terminalでbuildする場合は、`$env:Path`の設定をもう一度実行します。

PowerShellでリポジトリ直下から実行します。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File docs/learning/build.ps1
```

生成先は `docs/learning/_build/` です。

- HTML：`docs/learning/_build/html/index.html`
- PDF：`docs/learning/_build/typst/material-decision-workbench-learning.pdf`

HTMLとPDFは確認用の生成物であり、正本ではありません。
手作業で修正せず、`*.qmd`、`*.bib`、`styles/`を変更して再生成します。

## 現行実装との整合を確認する

教材参照と主要契約を確認する最短手順は次です。

```powershell
uv run python -m pytest backend/tests/test_decision_activities.py backend/tests/test_openapi_contract.py
uv run python -m pytest backend/tests/test_data_lifecycle.py backend/tests/test_openapi_contract.py
npm run api:check
powershell -NoProfile -ExecutionPolicy Bypass -File docs/learning/check-references.ps1
```

リポジトリ全体の完了判定では、ルートの `AGENTS.md` に従ってfull test、typecheck、buildも実行します。

## 更新ルール

1. 章のfront matterにある `verified_commit` と `code_references` を確認する。
2. 参照先の契約や期待出力が変わった場合は、本文と演習を同じPRで直す。
3. 実装事実、設計意図、教材上の解釈、将来案を混ぜない。
4. コード全文を転載せず、判断に必要な短い断片と正本へのリンクを置く。
5. HTMLとPDFを生成し、コード折返し、表、callout、相互参照を確認する。
6. 維持コストがproduction開発を圧迫する場合は、章を増やす前に [`evaluation.qmd`](evaluation.qmd) の判断を更新する。

## 教材レーン

継続編集は長期branch `learning/textbook` と専用worktreeで行います。
mainを取り込む前に、次のread-only checkで`verified_commit`以降の参照実装差分を確認します。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File docs/learning/check-main-drift.ps1
```

編集者向けの詳細規約、main吸収、検証手順は [`AGENTS.md`](AGENTS.md) を正本とします。
