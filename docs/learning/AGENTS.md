# 開発教材の編集規約

このディレクトリは、Material Decision Workbenchの実装を題材にした開発教材の正本です。
後継の編集者は、文章量を増やすことより、現mainの実装へ正確に接続し続けることを優先します。

## 作業レーン

- 長期branchは `learning/textbook` とする。
- 専用worktreeはcanonical checkoutと分け、利用者のhome directory配下に置く。
- Issue単位の変更は `learning/textbook` から短命branchを切ってもよい。
- productionのmainへ反映する成果物はPull Requestでreviewし、mergeする。
- 教材レーンへmainを取り込むときは、履歴を見える形にするため `git merge origin/main` を使う。
- 教材レーンの公開済み履歴をrebaseまたはforce-pushしない。
- mainの吸収前に、worktreeの未commit変更と他worktreeの所有branchを確認する。

専用worktreeを初めて作る場合は、canonical checkoutで次を実行する。

```powershell
git fetch origin
$learningWorktree = Join-Path $env:USERPROFILE "projects\re-process-dashboard-learning"
git worktree add $learningWorktree learning/textbook
```

`learning/textbook`がまだ存在しない場合は、現在の`origin/main`から作る。

```powershell
$learningWorktree = Join-Path $env:USERPROFILE "projects\re-process-dashboard-learning"
git worktree add -b learning/textbook `
  $learningWorktree `
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
  - path: "path/to/contract.py"
    id: "chapter-contract"
    role: "contract"
    symbols:
      - "ContractName"
  - path: "path/to/test.py"
    role: "test"
```

章と用語集の接続は、同じfront matterへ次の欄を置く。

```yaml
chapter_id: "unit-id"
prerequisite_concepts:
  - "concept-id"
introduced_concepts:
  - "concept-id"
reinforced_concepts: []
future_concepts: []
historical_concepts: []
```

概念の正本は`concepts/concepts.json`、形式契約は`concepts/concept.schema.json`とする。
`glossary.qmd`と`concept-map.qmd`は生成物であり、手編集しない。
概念を追加または変更した後は、`node docs/learning/check-concepts.mjs --write`で二つのQMDを再生成する。
通常のcheckとbuildは生成物の古さ、参照先path、前提の循環、状態と章内roleの矛盾を拒否する。

`verified_commit`は、参照実装とtestを実際に確認したcommitへ更新する。
教材だけを編集したcommitへ機械的に置き換えない。

`code_references`は構造化形式だけを使い、文字列path形式へ戻さない。
本文から参照するentryには、教材全体で一意なkebab-caseの`id`を付ける。
`role`は`contract`、`domain`、`application`、`persistence`、`api`、`generated`、`frontend`、`test`、`fixture`、`build`、`docs`から選ぶ。
追跡価値があるclass、function、定数、componentだけを`symbols`へ登録する。
本文では `{{{< code-ref chapter-contract >}}}` または `{{{< code-ref chapter-contract symbol=ContractName >}}}` を使い、GitHub URLとline番号を手書きしない。
HTMLのlinkとPDFの短い参照表記は、`verified_commit`のblobからbuild時に生成する。
GitHub repositoryのfull nameは `code-reference-config.json` だけへ定義する。
旧文字列形式を見つけた場合は、pathを構造化し、roleを付け、本文で根拠として使う主要symbolだけを追加してからcheckを通す。

## 文章と構成

- 書き手の立ち位置と声は [`writer-persona.md`](writer-persona.md) に従う。
- 日本語の本文では、定着した日本語の専門用語を優先する。コードや論文が英語だからという理由だけで、英単語を普通名詞や形容詞として文へ混ぜない。
- 英語のまま残すのは、コード識別子、ファイルパス、APIフィールド、製品名、ライブラリ名、画面上の正確な表示、文献の原題に限る。識別子や正確な表示はバッククォートで囲み、日本語の説明と区別する。
- 英語の名詞に日本語の助詞（の、を、が、は、に、へ、で）を直接つなげない。「constraintsの層」ではなく「制約の層」、「scoreへ畳み込む」ではなく「総合点へ畳み込む」と書く。コード上の名前を指すなら `constraints` のようにバッククォートで囲む。
- 実装の型名・クラス名を本文で使う場合は、コード識別子（バッククォートで囲む）と概念名（日本語で説明する）のどちらとして扱うかを段落の先頭で確定する。同じ語を両方の役割で使い分けない。`Connector設定`のようなバッククォートなしの宙ぶらりん表記を避ける。
- 文献検索に原語が役立つ用語は、初出だけ「日本語（英語）」と示してよい。その後は日本語へ統一する。日本語訳が読者に対象を想起させない場合は、先に具体的な対象や操作を説明してから名前を付ける。
- 代表的な訳語は、`quantile`を「分位点」、`uncertainty`を「不確かさ」、`nominal coverage`を「目標被覆率」、`empirical coverage`を「経験被覆率」、`calibration`を「較正」、`robustness`を「頑健性」、`progressive disclosure`を「段階的開示」、`feasibility`を「実行可能性」、`tolerance`（リスクの文脈）を「許容度」、`narrowing`を「型の絞り込み」、`fallback`を「退避表示」または「代替手段」とする。文脈に応じて「入力ばらつきに対する頑健性」のように対象を補う。
- 新しい章、追記、改稿では、本文へ着手する前に [`write-learning-chapter`](../../.claude/skills/write-learning-chapter/SKILL.md) を使う。英語の概念名を起点に翻訳せず、読者が見る具体物、区別する関係、誤判断、読了後の行為を日本語で定めてから段落を書く。
- `node docs/learning/check-japanese-prose.mjs`は、執筆後に既知の逆戻りを検出する安全網として使う。禁止語リスト（統計・ML用語の英語混在）と、バッククォート外の英語名詞＋日本語助詞の接合を検出する。この検査に通ることを、自然な日本語または教育的に良い文章の証明にしない。検出漏れがあった場合は許可リストではなく禁止パターンの追加を検討する。
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
- 脚注は、直近の主張の誤読を防ぐ限定、用語の来歴、一次資料への導線、現在の問いに接続する補足と脇道の実務知識に使う。
- 脚注へ置く補足は、本文なしでも論証が成立し、現在の問いとの接続を一文で説明できるものに限る。章を越えて一般化する資料は章末の「参考文献を読む」へ置く。
- 日本語本文で空虚な強調、同じ結論の言い換え、過剰な予告と総括を避ける。

文章と構成を新しく決める場合は、公式教材または公式style guideを事前調査する。
採用した規則と出典は `foundations.qmd` または `references.bib` に残す。

### 数理を含む章

数理を含む章は [`math-style-guide.qmd`](math-style-guide.qmd) に従う。
式の前に問いと仮定を置き、記号表へ意味、型、単位を示す。
数式上の量はcontract、code、testへ対応付け、実装との差分を隠さない。

review依頼前に [`数理レビューchecklist`](math-style-guide.qmd#sec-math-review-checklist) を完了する。
少なくとも次を確認する。

- random variable、観測値、推定値、予測値、実測値を区別した
- 条件付け、添字、単位、maximizeまたはminimizeの向きを示した
- Definition、Assumption、Approximation、Estimator、Algorithm、Implementationを区別した
- 手計算できる数値例、破綻条件、代替または停止条件を置いた
- 数学上の量と現mainのfield、symbol、testを対応付けた
- 未実装の手法をcurrent featureとして書いていない
- HTMLとPDFで式を目視し、式を読めなくても本文から意味を追える

数理reviewの記録には、確認commit、対象の式、fixture、test、未確認範囲を残す。
数式の見た目だけを確認して数理reviewを完了としない。

## 正本と生成物

- 正本は `*.qmd`、`*.md`、`*.bib`、`_quarto*.yml`、`styles/`、PowerShell scriptである。
- `_quarto.yml`は共通設定、`_quarto-site.yml`は統合HTMLの章順、`_quarto-reader.yml`は学習者向けPDFの章順を管理する。
- 統合HTMLは学習者向け教材と編集と保守のガイドを別partにし、一つの検索索引へ収録する。
- 学習者向けPDFへ `foundations.qmd`、`writer-persona.md`、`code-map.qmd`、`learning-paths/`、`tooling.qmd`、`evaluation.qmd` を含めない。
- profileごとに共通本文をコピーしない。同じQMDを双方のchapter listから参照する。
- `site` profileの`solution-placement`は`inline-disclosure`、`reader` profileは`answer-chapter`とする。
- 解答本文は`.exercise-solution` blockへ一度だけ書き、HTML用とPDF用に複製しない。
- 参考文献の書誌情報は `references.bib`、教材上の役割と読書案内は `reference-annotations.json` を正本にする。
- `docs/learning/_build/`は生成物であり、commitしない。
- generated HTMLとPDFを手編集しない。
- `docs/examples/tutorial-data-pipeline.md` は教材用の具体例として `examples/` に置き、一般契約の正本として扱わない。
- production codeを教材都合で不自然に変えない。

## 演習と解答

各演習には一意な`exercise-<unit>-<NN>` IDを付ける。
問題本文の直後に`### 成功条件`を置き、必要な場合だけ`### ヒント`を加える。
成功条件は、提出物、判定境界、観察するtestのいずれかを具体的に示す。

解答は次の形で書く。

```markdown
[演習1の解答](#answer-contract-01)は、回答を固定してから開きます。

::: {#answer-contract-01 .exercise-solution data-label="演習1の解答"}
#### 解答例

完成した回答を書く。

#### 解答の理由

前提、境界、trade-off、testとの対応を書く。

#### よくある不十分な回答

不足する条件と、その回答が成立する限定条件を書く。
:::
```

answer IDは対応するexercise IDの`exercise-`を`answer-`へ置き換える。
HTMLのsummaryへ答えの内容を書かず、「解答例を見る」に固定する。
検証command、記録template、失敗時の確認先は問題側へ残し、実測件数を唯一の解答にしない。
章末チェックの解答は`answer-<unit>-chapter-check`として別のsolution blockへ入れる。
問題側へ完成表、test mapping、期待HTTP codeを残さない。

## 検証

教材変更では、最低限次を実行する。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File docs/learning/check-references.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File docs/learning/check-main-drift.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File docs/learning/test-main-drift.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File docs/learning/check-exercise-solutions.ps1
node docs/learning/check-code-references.mjs
node docs/learning/check-drift-reviews.mjs
node docs/learning/reviews/check-reviews.mjs
node docs/learning/evaluations/edition-2/test-observations.mjs
node docs/learning/evaluations/edition-2/check-observations.mjs
node docs/learning/test-concepts.mjs
node docs/learning/test-concept-order.mjs
node docs/learning/check-concept-order.mjs
node docs/learning/test-figures.mjs
node docs/learning/check-figures.mjs
node docs/learning/test-labs.mjs
node docs/learning/check-labs.mjs
node docs/learning/test-japanese-prose.mjs
node docs/learning/check-japanese-prose.mjs
node docs/learning/check-lab-reproducibility.mjs
node docs/learning/test-repository-reference-states.mjs
node docs/learning/check-repository-reference-states.mjs
powershell -NoProfile -ExecutionPolicy Bypass -File docs/learning/scripts/test-bootstrap-book-tools.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File docs/learning/build.ps1 -Clean
```

参考文献のURL到達性を再確認するときだけ、外部networkを使う次の検査を追加する。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File docs/learning/check-reference-urls.ps1
```

参照する実装経路のfocused testと生成型checkも実行する。

## IssueとPull Requestの状態

現在の状態を本文の根拠にするときは`repository-reference-states.json`へ`mode: current`、観測state、`verified_at`、参照文書を記録する。
過去の実測や判断を説明する参照は`mode: historical`とし、GitHub側の現在値へ自動で書き換えない。
networkが使える更新時は`node docs/learning/check-repository-reference-states.mjs --online`でcurrent参照を照合する。
offline検査は最後の観測stateと日時だけを検証し、現在値を確認したとは扱わない。
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
学習者向けPDFの目次に保守専用章がないことと、統合HTMLの検索索引に両partがあることも確認する。
15〜30ページを条件とする試作章は、生成PDF上の開始pageと次章の開始pageから実数を記録する。

## 組版toolのversion更新

組版toolは`tools.lock.json`を正本とし、systemのPATHから選ばない。
versionを更新するときは、version、公式HTTPS URL、file size、SHA-256、archive内の実行file path、version patternを同じ変更で更新する。
Quartoは公式checksum fileとGitHub release asset digestを照合する。
独立したchecksum fileがないtoolは、GitHub release APIのasset digestと取得したarchiveのhashを照合する。
取得archiveから計算したhashだけを根拠にlockを更新しない。

更新後は、空の`-ToolRoot`でonline bootstrapを行い、誤ったhashで失敗するtest、`-Offline`でのcache再利用、空白を含むcustom root、cleanなHTMLとPDFの生成を確認する。
checksumは転送中の破損や意図しない差替えを検出するが、署名ではない。
展開後の実行fileはversionとready markerで検査しており、全fileの改ざん検知までは保証しない。

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

判定はfile単位で終えず、教材内の検証可能な主張を単位にする。
分類、更新対象、no-changeの理由、検証結果は [`drift-reviews/index.qmd`](drift-reviews/index.qmd) に従ってrecordへ残す。
新しいreviewでは [`drift-reviews/template.json`](drift-reviews/template.json) をコピーし、`node docs/learning/check-drift-reviews.mjs`でPR差分との対応を検査する。
証拠が不足したclaimを`evidence_unavailable`として残している間は、対応する章の`verified_commit`を進めない。
