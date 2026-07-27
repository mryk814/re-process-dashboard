# Material Decision Workbench 開発教材

このディレクトリは、Issue #274の試作から育てている開発者向け教材の正本です。

教材は、一般概念を説明するだけではなく、現行実装の契約、API、生成型、React、テストを一つの処理として読みます。
型付き契約の章では、「検討アクティビティ」がPythonから画面まで届く経路を扱います。
Source data lifecycleの章では、外部Sourceの取得、品質判定、承認、Training Snapshot、Model Package active化を分けます。
revisionとdigestの章では、Candidate ID、受理順、内容identity、request sequenceを分け、保存競合と遅いresponseを追います。
Workspace restoreの章では、SQLite snapshot、bundle検証、staging migration、DB切替、API health確認、rollbackを分けます。
Decision Safetyの章では、点予測、予測区間、support、入力ばらつき、警告、提案の採用境界を分けます。

## 読み始める

- 順に学ぶ場合は、学習者向けPDFまたは統合HTMLの「学習者向け教材」から始めます。
- 実装箇所を探す場合は、統合HTMLの「編集と保守のガイド」にある [`code-map.qmd`](code-map.qmd) を使います。
- 担当別に読む順番を選ぶ場合は、同じガイドにある `learning-paths/` の三つのルートを使います。
- 概念の前提関係から入口を選ぶ場合は、[`concept-map.qmd`](concept-map.qmd) を使います。
- 用語の意味、混同しやすい語、現行実装への接続を確かめる場合は、[`glossary.qmd`](glossary.qmd) を使います。
- 編集する場合は [`AGENTS.md`](AGENTS.md) と [`writer-persona.md`](writer-persona.md) を先に読みます。
- mainの変更が教材へ与える影響を判定する場合は [`drift-reviews/index.qmd`](drift-reviews/index.qmd) を使います。
- 章の複眼レビューと代理読者taskの記録は [`reviews/index.qmd`](reviews/index.qmd) から確認します。

## Windowsで生成する

PowerShellでリポジトリ直下から、固定版の組版toolを準備して生成します。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File docs/learning/scripts/bootstrap-book-tools.ps1
powershell -NoProfile -ExecutionPolicy Bypass `
  -File docs/learning/build.ps1 -Clean
```

bootstrapは`tools.lock.json`のversion、取得先、file size、SHA-256と一致するarchiveだけを展開します。
systemへのinstallと永続的なPATH変更は行いません。
二回目以降は検証済みcacheを再利用します。

既定の保存先は`$env:LOCALAPPDATA\material-workbench-book-tools`です。
別の場所を使う場合は、両方のcommandへ同じ`-ToolRoot`を渡します。
`MATERIAL_WORKBENCH_BOOK_TOOLS`環境変数でも既定値を変更できますが、明示した`-ToolRoot`が優先されます。

検証済みcacheまたはarchiveだけで準備できるかを確認する場合は、bootstrapへ`-Offline`を付けます。
同じassetを再取得して検証し直す場合は`-Force`を付けます。
詳しい供給経路と更新手順は [`tooling.qmd`](tooling.qmd) と [`AGENTS.md`](AGENTS.md) にあります。

生成先は `docs/learning/_build/` です。

- 統合HTML：`docs/learning/_build/site/index.html`
- 学習者向けPDF：`docs/learning/_build/reader/material-decision-workbench-reader.pdf`

統合HTMLは、学習者向け教材と編集と保守のガイドを二つのpartに分け、両方を一つの検索索引から探せる成果物です。
学習者向けPDFは、教材設計、書き手のペルソナ、コードマップ、組版手順、評価記録を目次へ含めません。
どちらも同じQMDを参照し、本文を出力ごとに複製しません。

演習の解答も同じ原稿を共有します。
統合HTMLでは各問題の直後にある「解答例を見る」から開き、学習者向けPDFでは用語集の前にある「演習解答」章から確認します。
問題文、成功条件、任意のヒントは、解答を開かなくても読めます。

HTMLとPDFは確認用の生成物であり、正本ではありません。
手作業で修正せず、`*.qmd`、`*.bib`、`styles/`を変更して再生成します。

## 現行実装との整合を確認する

教材参照と主要契約を確認する最短手順は次です。

```powershell
uv run python -m pytest backend/tests/test_decision_activities.py backend/tests/test_openapi_contract.py
uv run python -m pytest backend/tests/test_data_lifecycle.py backend/tests/test_reference_data_loop_acceptance.py backend/tests/test_openapi_contract.py
uv run python -m pytest backend/tests/test_candidate_safety.py backend/tests/test_inference_work_graph.py
uv run python -m pytest backend/tests/test_workspace_bundle.py backend/tests/test_windows_packaging_contract.py -k "workspace or desktop_startup_recovery"
node --test apps/web/tests/latestSaveQueue.test.mjs apps/web/tests/inferenceSurfaceState.test.mjs apps/web/tests/workbenchIdentity.test.mjs
node --test apps/web/tests/workspaceBackupPresentation.test.mjs apps/web/tests/workspaceNotice.test.mjs
npm run api:check
powershell -NoProfile -ExecutionPolicy Bypass -File docs/learning/check-references.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File docs/learning/test-main-drift.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File docs/learning/check-exercise-solutions.ps1
node docs/learning/check-drift-reviews.mjs
node docs/learning/reviews/check-reviews.mjs
node docs/learning/test-concepts.mjs
```

リポジトリ全体の完了判定では、ルートの `AGENTS.md` に従ってfull test、typecheck、buildも実行します。

## 更新ルール

1. 章のfront matterにある `verified_commit` と `code_references` を確認する。
2. 章の概念欄と `concepts/concepts.json` を同じ変更で更新する。
3. `node docs/learning/check-concepts.mjs --write`で用語集と概念表を再生成する。
4. 参照先の契約や期待出力が変わった場合は、本文と演習を同じPRで直す。
5. 実装事実、設計意図、教材上の解釈、将来案を混ぜない。
6. コード全文を転載せず、判断に必要な短い断片と正本へのリンクを置く。
7. HTMLとPDFを生成し、コード折返し、表、callout、相互参照を確認する。
8. 維持コストがproduction開発を圧迫する場合は、章を増やす前に [`evaluation.qmd`](evaluation.qmd) の判断を更新する。

Quartoの構成は `_quarto.yml` が共通設定、`_quarto-site.yml` が統合HTMLの章順、`_quarto-reader.yml` が学習者向けPDFの章順を管理します。
profileのchapter listへ同じ本文を登録し、出力ごとのコピーは作りません。
`filters/exercise-solutions.lua`は、同じ解答blockをHTMLのdisclosureとPDFの巻末解答へ変換します。

## 教材レーン

継続編集は長期branch `learning/textbook` と専用worktreeで行います。
mainを取り込む前に、次のread-only checkで`verified_commit`以降の参照実装差分を確認します。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File docs/learning/check-main-drift.ps1
```

編集者向けの詳細規約、main吸収、検証手順は [`AGENTS.md`](AGENTS.md) を正本とします。
差分を検出した後のclaim分類と記録方法は [`drift-reviews/index.qmd`](drift-reviews/index.qmd) にあります。
