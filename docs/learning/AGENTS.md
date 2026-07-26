# 開発教材の編集規約

このディレクトリは、Material Decision Workbenchの実装を題材にした開発教材の正本です。
後継の編集者は、文章量を増やすことより、現mainの実装へ正確に接続し続けることを優先します。

## 作業レーン

- 長期branchは `learning/textbook` とする。
- 専用worktreeはcanonical checkoutと分け、既定pathを `C:\Users\ootan\projects\re-process-dashboard-learning` とする。
- Issue単位の変更は `learning/textbook` から短命branchを切ってもよい。
- productionのmainへ反映する成果物はPull Requestでreviewし、mergeする。
- 教材レーンへmainを取り込むときは、履歴を見える形にするため `git merge origin/main` を使う。
- 教材レーンの公開済み履歴をrebaseまたはforce-pushしない。
- mainの吸収前に、worktreeの未commit変更と他worktreeの所有branchを確認する。

専用worktreeを初めて作る場合は、canonical checkoutで次を実行する。

```powershell
git fetch origin
git worktree add C:\Users\ootan\projects\re-process-dashboard-learning learning/textbook
```

`learning/textbook`がまだ存在しない場合は、現在の`origin/main`から作る。

```powershell
git worktree add -b learning/textbook `
  C:\Users\ootan\projects\re-process-dashboard-learning `
  origin/main
```

## 作業開始時

1. `git status --short --branch`と`git worktree list`を確認する。
2. `git fetch origin`を実行する。
3. `powershell -File docs/learning/check-main-drift.ps1`を実行する。
4. 参照実装が変わっていれば、原稿編集より先に差分を読む。
5. Issue、ADR、契約文書で設計意図を確認し、コードから推測した意図と混ぜない。

## 原稿の根拠

記述は必要に応じて次の四種類へ分ける。

- `CONFIRMED`：現mainのコード、テスト、生成物で確認した実装事実
- `DECISION`：ADR、Issue、契約文書で確認した設計意図
- `INTERPRETATION`：理解を助ける教材上の読み方
- `FUTURE`：未実装の候補

確認していない設計意図を、実装事実として書かない。
将来案を現在利用できる機能として書かない。

各章のfront matterには次を持たせる。

```yaml
verified_commit: "<full commit sha>"
code_references:
  - "path/to/contract.py"
  - "path/to/test.py"
```

`verified_commit`は、参照実装とtestを実際に確認したcommitへ更新する。
教材だけを編集したcommitへ機械的に置き換えない。

## 文章と構成

- 書き手の立ち位置と声は [`writer-persona.md`](writer-persona.md) に従う。
- 章は「問題、一般概念、実装を読む、演習、設計を振り返る」の順を基本とする。
- tutorial、how-to、reference、explanationの役割を混ぜない。
- 対象読者、前提知識、読了後にできること、非scopeを冒頭で示す。
- 一文ごとに改行し、一段落へ一つの論点を置く。
- 行為者と動作を明示し、受動態と曖昧な主語を減らす。
- コード全文を複製せず、論点に必要な断片とrepo相対pathを示す。
- commandには実行場所、成功条件、失敗時の確認先を添える。
- test件数やbuild時間は検証時点の証拠として書き、恒久仕様にしない。
- calloutは連続させず、事実区分、危険、checkpointに絞る。
- 本文には理解と判断に必要な主線を置き、補足、例外、来歴、脇道の実務知識は脚注へ置く。
- 脚注を読まなくても論証が成立するようにする。結論、前提、危険、操作手順を脚注へ隠さない。
- 脚注は「あると嬉しい」情報だけに使い、一つの脚注へ複数の話題を詰め込まない。
- 日本語本文で空虚な強調、同じ結論の言い換え、過剰な予告と総括を避ける。

文章と構成を新しく決める場合は、公式教材または公式style guideを事前調査する。
採用した規則と出典は `foundations.qmd` または `references.bib` に残す。

## 正本と生成物

- 正本は `*.qmd`、`*.bib`、`_quarto.yml`、`styles/`、PowerShell scriptである。
- `docs/learning/_build/`は生成物であり、commitしない。
- generated HTMLとPDFを手編集しない。
- 既存の `docs/tutorial-data-pipeline.md` は移動せず、必要な学習ルートから参照する。
- production codeを教材都合で不自然に変えない。

## 検証

教材変更では、最低限次を実行する。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File docs/learning/check-references.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File docs/learning/check-main-drift.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File docs/learning/build.ps1 -Clean
```

参照する実装経路のfocused testと生成型checkも実行する。
型付き契約の章では次を使う。

```powershell
uv run python -m pytest `
  backend/tests/test_decision_activities.py `
  backend/tests/test_openapi_contract.py
npm run api:check
```

完了時はリポジトリ共通の三gateも通す。

```powershell
uv run python -m pytest
npm run typecheck
npm run build
```

PDFはpage countだけで完了にしない。
全pageを画像化し、日本語font、code wrapping、表、図、header、footer、page transitionを目視する。
15〜30ページを条件とする試作章は、生成PDF上の開始pageと次章の開始pageから実数を記録する。

## main追従後の判断

`check-main-drift.ps1`が参照fileの変更を報告した場合、次を順に確認する。

1. contractのshapeと意味
2. application serviceとpersistence
3. APIとOpenAPI
4. TypeScript生成型
5. frontend
6. focused testとE2E
7. 教材本文、演習、コードマップ

pathが残っているだけでは「教材は最新」と判定しない。
識別子、期待出力、command、画面文言、設計上の限界も確認する。
