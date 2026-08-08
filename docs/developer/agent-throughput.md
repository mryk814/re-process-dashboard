# Agent throughput guide

この文書は、repositoryの安全境界を維持しながら、AIによる日常実装のtoken消費、lead time、重複作業を抑える運用を定めます。

正しさを弱めるための規約ではありません。変更が壊し得る最も近い境界を一度反証し、必要な場合だけ調査・Skill・検証を広げます。

## 基本方針

GPT-5.6系では、細かな逐次手順を重複して与えるより、次を短く固定します。

- domain context
- hard constraint
- autonomy／approval boundary
- success criteria
- stop condition

同じ規則は一箇所だけを正本にします。root `AGENTS.md`はrouterと不変条件を所有し、領域固有の詳細はnested `AGENTS.md`、検証詳細は`docs/operations/`、専門workflowはrepo Skillが所有します。

## 作業directoryをscopeへ合わせる

Codexはproject rootから現在のworking directoryまでの`AGENTS.md`をinstruction chainへ加えます。作業領域が明確なら、開始directoryを合わせます。

```powershell
# backendの一authorityだけを変更
codex --profile re-process-fast --cd backend

# Web UIの一component／hookだけを変更
codex --profile re-process-fast --cd apps/web

# E2E spec／helperだけを変更
codex --profile re-process-fast --cd e2e

# docsだけを変更
codex --profile re-process-fast --cd docs

# 複数authorityまたは科学contractを変更
codex --profile re-process-deep --cd .
```

`--cd backend`で開始しても、repository root前提のpytest commandはrootをworkdirとして実行します。`backend/`直下から通常の`pytest`を実行してcwd由来のimport failureを実装failureと誤認しません。

一つのIssue／PRは原則として一つのfresh sessionで扱います。別Issueへ移る場合は、長い会話contextを持ち越すより、branch、Issue、PR本文、verification artifactへ必要な状態を保存して新しいsessionを開始します。

## Fast profile

`$CODEX_HOME/re-process-fast.config.toml`の例です。

```toml
model = "gpt-5.6-terra"
model_reasoning_effort = "low"
model_verbosity = "low"

[agents]
enabled = false
```

利用対象:

- typo／copy／docs link
- 一つのserviceまたはcomponentのbug fix
- 既存contract内のvalidation
- test-only修正
- 局所API error mapping
- 既存patternへ沿う小さなfeature

Fast profileでも、rootとnested `AGENTS.md`のhard invariantは省略しません。

## Deep profile

`$CODEX_HOME/re-process-deep.config.toml`の例です。

```toml
model = "gpt-5.6-sol"
model_reasoning_effort = "medium"
model_verbosity = "medium"

[agents]
enabled = true
max_concurrent_threads_per_session = 2
default_subagent_model = "gpt-5.6-terra"
default_subagent_reasoning_effort = "low"
```

利用対象:

- contract／API／persistenceをまたぐ変更
- migration／restore／security
- Model Package artifact／runtime semantics
- scientific identity
-複数画面journey
-原因不明または再現不安定なfailure
- architecture boundary変更

subagentは、独立して検証できるread-only調査または所有fileが分離できる作業だけに使います。一つの小さな修正を複数agentへ重複調査させません。

profile fileはuser-level設定です。repositoryへcredential、provider、個人設定をcommitしません。Codex CLIでは`--profile <name>`が`$CODEX_HOME/<name>.config.toml`をbase configへ重ねます。

## 日常実装のfast path

### micro

例:

- typo
- label／ariaの明白な欠落
- docs link
-既存token置換
- test expectationだけの更新

進め方:

```text
対象確認
→ 最小変更
→ 最寄りcheck一つ
→ diff
→ stop
```

written budget、Skill、subagent、aggregate runner、full suite、independent reviewは既定で使いません。

### local

例:

- 一つのapplication service
- 一つのReact state transition
-既存contract内のfield validation
- 一つのAPI error mapping

進め方:

1. ownerと最寄りtestを確認する
2. 最小の完全な変更を実装する
3. focused unit／pytestを一度実行する
4. browser interactionを変えた場合だけ対象specをfreshで一度実行する
5. diffがscope内なら止める

### structural／critical

`verification-budget-planner`で短い予算を作り、関連Skillと正本文書を読みます。path数やfile長だけで昇格しません。

## 直接検証の例

### Backend

repository rootから、変更authorityに最も近いtestを指定します。

```powershell
uv run --extra dev python -m pytest backend/tests/test_target.py -q
```

最寄りtestを特定できない場合は、即座に`backend/tests`全体へ広げる前に、owner、caller、contract testを短く確認します。

### Web unit／type

```powershell
npm run test -w apps/web
npm run typecheck -w apps/web
```

局所testだけを選べる場合は、その直接commandを反復loopに使います。Web変更というpathだけでDesktop、全inventory、全OpenAPIを毎回実行しません。現行plannerが広く選ぶ場合は、実際の変更境界と省略理由をPRへ記録します。

### Browser

interactionを変えた場合だけ、対象specをfresh serverで一度実行します。

```powershell
npx playwright test e2e/<target>.spec.ts
```

`PLAYWRIGHT_REUSE_SERVER=1`は編集loop専用です。merge前証拠にはfresh実行を使います。同じjourneyを複数runnerで重複確認しません。

### Docs／instructions

```powershell
npm run docs:check
git diff --check
```

application code、generated contract、command semanticsを変えていないinstruction／docs変更へ、full pytestやdefault Playwrightを追加しません。

## Skillの選び方

日常変更はSkillなしが既定です。

| 状況 | 入口 |
|---|---|
| 原因layerが不明、再現不安定、再発 | `systematic-debugging` |
| screen／navigation／form／handoff構造を変更 | `frontend-ux-architect` |
| package／registry／adapter／migration境界を変更 | `re-process-architecture-review` |
| structural／criticalの検証量を決める | `verification-budget-planner` |
| 外部dataを既存機能で接続 | `data-contributor` |
| 複数画面の判断journeyをActor評価 | `scenario-journey-evaluator` |

明白な一authority修正へfull Skill workflowを使いません。外部vendored Skillを直接複数選ぶ代わりに、repo orchestration Skillを一つ選びます。

## Stop condition

次を満たしたら終了します。

- 変更したbehavior／contractが現在commitで一度証明された
- 必要なerror／recovery pathが直接確認された
- diffがscope内にある
- 新しい原因仮説が残っていない

次は停止を妨げません。

- 後日のcheckpointで確認する横断項目
- CIが所有する同一commitのfull suite
- 当該変更と無関係な既知failure
- `not_run`として正確に記録したgate

上位gateが下位testを包含して成功した後、同じcommitで下位testを再実行しません。

## 改善の追跡

### Paired eval

[`benchmarks/agent-throughput`](../../benchmarks/agent-throughput/README.md)に、pure docs、
backend one-authority、Web local interaction、test-only、API field、Model Runtime/scientific
contractの6代表caseと、baseline/current共通result schemaを置きます。

比較はsame commit、prompt、fixture、success criteria、model family、reasoning、fresh session、
fresh workspaceが全て揃った場合だけ成立します。品質未達やHigh findingをresource改善で相殺せず、
取得不能なtoken、tool call、lead time、verification時間は`null`のままにします。local instruction
bytesはprovider tokenではないproxyとしてprovenanceを残します。

agent runを採用する場合は、全必須metrics、必須gate ID/exact argvごとの#837 passed receiptとcommit/environment/
command/duration整合、digest付きindependent human reviewが必要です。全gate receiptはmodel/reasoning、
fresh session/workspace、fixture digest、provider usageを束ねたcontent-addressed run/session receiptへ
結合します。case別resource ceiling、またはbaseline比
1.25倍とcatalogのminimum regression deltaを共に超える大幅悪化はrollbackします。6 caseのpatchは固定commitの別clean worktreeへsetupし、
materialized diff digest一致時だけresetできます。credentialやHOME pathを含む記録はrejectします。

run/session receiptのdigestはintegrityだけを証明し、provider／human authenticityは証明しません。
authenticated external recorderまたは署名検証が未実装の間は、agent run evidenceが揃っていても
`authenticated_external_receipt:unsupported`としてincomparable／rollbackにし、profile adoptionを許可しません。

2026-08-08時点ではroot instruction 15,760 bytes／214 linesから6,928 bytes／117 lines、
visible Skill 12件から6件へのcontext変化だけが実測済みです。6 caseのfresh paired agent runと
quality reviewは未実施なので、比較は全件`incomparable`、candidate profileはfail closedで
rollback判定です。fast／deep profileの適用範囲はまだevidence更新しません。

```powershell
npm.cmd run agent-eval:test
npm.cmd run agent-eval:check
```

- #837 — focused evidence reuseとsemantic verification分類
- #838 — GPT-5.6代表taskのtoken／lead-time eval
- #839 — Codexへ常時露出するSkillの整理

このguide自体を速さの証明とは扱いません。代表taskで品質、token、tool call、verification時間を比較し、悪化した変更は戻します。

## 外部仕様

- [OpenAI: Custom instructions with AGENTS.md](https://developers.openai.com/codex/guides/agents-md)
- [OpenAI: Codex configuration reference](https://developers.openai.com/codex/config-reference)
- [OpenAI: Codex CLI reference](https://developers.openai.com/codex/cli/reference)
- [OpenAI: GPT-5.6 model guidance](https://developers.openai.com/api/docs/guides/latest-model)
