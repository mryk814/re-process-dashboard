# Agent throughput paired eval

このdirectoryは、repository固有のagent workflow変更を同一条件で比較するための正本です。
model一般のbenchmarkやleaderboardではありません。runnerはlocal JSONの検証・集計だけを行い、
OpenAIその他のproduction APIを呼びません。

## 正本

- [`cases.json`](cases.json) — 6代表case、固定repository commit、prompt、成功条件、安全境界
- [`schema/agent-throughput-case-v1.json`](schema/agent-throughput-case-v1.json) — case schema
- [`schema/agent-throughput-result-v1.json`](schema/agent-throughput-result-v1.json) — baseline/current共通result schema
- [`schema/agent-throughput-comparison-v1.json`](schema/agent-throughput-comparison-v1.json) — paired comparison schema
- `results/{baseline,current}/` — redact済みresult
- `comparisons/` — 再計算可能な比較結果

caseはpure docs typo、backend one-authority bug、Web local interaction、test-only regression、
API field追加、Model Runtime/scientific contract変更の6件です。未解決Issueや利用者の実dataを
fixtureとして変更しません。

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
`rollback_candidate_profile`にします。取得不能tokenを推測値で埋めません。

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
