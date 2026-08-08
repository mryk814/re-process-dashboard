# Agent throughput paired eval

このdirectoryは、repository固有のagent workflow変更を同一条件で比較するための正本です。
model一般のbenchmarkやleaderboardではありません。runnerはlocal JSONの検証・集計だけを行い、
OpenAIその他のproduction APIを呼びません。

## 正本

- [`cases.json`](cases.json) — 6代表case、固定repository commit、prompt、成功条件、安全境界
- [`schema/agent-throughput-case-v1.json`](schema/agent-throughput-case-v1.json) — case schema
- [`schema/agent-throughput-result-v1.json`](schema/agent-throughput-result-v1.json) — baseline/current共通result schema
- [`schema/agent-throughput-run-receipt-v1.json`](schema/agent-throughput-run-receipt-v1.json) — session由来metricsとfixture identityのreceipt schema
- [`schema/agent-throughput-comparison-v1.json`](schema/agent-throughput-comparison-v1.json) — paired comparison schema
- `results/{baseline,current}/` — redact済みresult
- `comparisons/` — 再計算可能な比較結果

caseはpure docs typo、backend one-authority bug、Web local interaction、test-only regression、
API field追加、Model Runtime/scientific contract変更の6件です。未解決Issueや利用者の実dataを
fixtureとして変更しません。各caseは固定commitへ適用できる実patch、patch SHA-256、
materialized diff SHA-256、changed path、setup/reset commandを持ちます。

```powershell
$env:AGENT_THROUGHPUT_WORKTREE = "C:\agent-throughput\worktree"
node scripts/agent-throughput-eval.mjs setup --case docs-typo --workspace $env:AGENT_THROUGHPUT_WORKTREE
node scripts/agent-throughput-eval.mjs reset --case docs-typo --workspace $env:AGENT_THROUGHPUT_WORKTREE
```

setupは別のclean worktree、catalog固定HEAD、patch digestを要求します。resetはmaterialized diffと
changed pathが完全一致する場合だけreverse applyし、無関係な変更があれば拒否します。focused
testでは6件すべてをfresh pinned worktreeへ実際にsetup/resetしています。

## 現在保存している証拠

2026-08-08のresultは、6 caseそれぞれについて実際に取得したrepository context observationです。

| 指標 | baseline | current | provenance |
|---|---:|---:|---|
| root `AGENTS.md` bytes | 15,760 | 6,928 | 固定git objectのSHA-256 |
| root `AGENTS.md` lines | 214 | 117 | 同上 |
| visible repo Skill | 12 | 6 | `skill-inventory/v1` strict checker |

これはagent taskの実行結果ではありません。fresh session/workspace、model、reasoning、first edit、
tool call、verification duration、provider token、human quality reviewは取得していないため、`null`、
`not_run`、`not_evaluated`として保存しています。instruction bytesはcredit proxyですがprovider token
ではなく、そのことをschema上でも明示しています。raw transcript、HOME path、secretは保存しません。

したがって現在の6比較はすべて`incomparable`で、decisionは
`rollback_candidate_profile`です。これは次のeval runでcandidate profileを採用しないという
fail-closed判定であり、runnerがrepository guidanceや利用者configを自動で戻すという意味では
ありません。fast/deep profileの適用範囲は、paired quality evidenceが揃うまで更新しません。

## 同一条件contract

比較可能にするにはbaseline/currentの次をすべて一致・証明します。

- case ID/version、prompt digest、fixture digest、success criteria digest
- repository commit
- model family、reasoning setting
- fresh session、fresh workspace

candidateはさらにrequired focused evidenceとquality reviewがpassする必要があります。
High/Critical finding、success criteria未達、verification未達は、tokenや時間の改善で相殺せず
`rollback_candidate_profile`にします。agent runの採用には全必須metrics、実在する#837 passed
receipt、receipt content/commit/environment/command/duration一致、digest付きindependent human review artifactが
必要です。各caseは必須gate IDとexact argvを固定し、全gateのreceiptを1対1で要求します。receiptは
model/reasoning、fresh session/workspace、fixture digest、全metrics、provider usage、human review digestを
含むcontent-addressed run/session receiptへ結合します。catalogのcase別resource ceiling超過、またはelapsed/tool call/duplicate command/
verification time/input-output tokenがbaselineの1.25倍を超え、かつcatalogのminimum regression
delta以上悪化した場合もrollbackします。取得不能tokenを
推測値で埋めません。

content digestが証明するのはartifactのintegrityだけで、providerまたはhumanのauthenticityでは
ありません。2026-08-08時点ではauthenticated external recorder／署名検証が未実装のため、
完全な合成fixtureを含め、すべてのcomparisonは`authenticated_external_receipt:unsupported`として
`incomparable`かつ`rollback_candidate_profile`になります。外部認証が入るまでadoptionはunsupportedです。

`access_token`、`client_secret`、`private_key`、cookie、Bearer、API key等は保存前にredactでき、
redactされずresult、receipt、reviewへ混入した場合はrejectします。

## Commands

```powershell
npm.cmd run agent-eval:test
npm.cmd run agent-eval:check
node scripts/agent-throughput-eval.mjs show-case --case backend-one-authority
```

context observationを正本から再生成し、比較driftを更新する場合だけ次を実行します。

```powershell
npm.cmd run agent-eval:observe -- --profile baseline
npm.cmd run agent-eval:observe -- --profile current
npm.cmd run agent-eval:compare
```

`observe`はagentを起動せず、production API費用を発生させません。将来の実agent runは
`agent-throughput-result/v1`へproviderが正式に返したmetricsと#837 receipt digestだけをimportし、
private transcriptやprovider内部reasoningを持ち込みません。
