# AI Actor Candidate Review v1実験記録

## 今回知りたかったこと

AIへStore、DB、filesystemを渡さず、保存済みProject evidenceだけを読むCandidate decision reviewを、immutable Runとして保存できるかを確認しました。

結論は、providerへ自由なapplication APIを渡さず、先に観測を固定するtyped tool facadeを挟めば、mechanicsと安全境界を再現可能にできます。
一方、実LLM providerはこの環境に設定されていないため、文章の有用性やprovider間の安定性は評価していません。

## ActorとRun

AI Actorは次をprovenanceへ固定します。

- agent definition
- providerとmodel
- policy version
- toolset version
- prompt template version
- sampling settings
- input snapshot digest
- reviewed Candidate revision

fixtureは`provider=test`、`model=scripted-fixture`と明記し、実モデルを装いません。
fixture identityの片方だけを書き換える設定は起動時に拒否します。

Review Runの状態は`running`から次のterminal stateへ一度だけ移ります。

| state | 意味 |
|---|---|
| `completed` | schema、evidence、revision、language guardをすべて通過 |
| `partial` | providerが不完全な出力と明示 |
| `invalid` | schema不正、参照不存在、revision drift、禁止tool、overclaim |
| `failed` | timeoutまたはprovider例外 |

確定済みRunの本文は更新しません。
Human dispositionは`accepted`、`partially_accepted`、`rejected`、`deferred`、`superseded`を別tableへappendします。

## Tool allow-list

providerが呼べるread toolは六つです。

```text
project_summary
candidate_revision
predictive_snapshots
decision_activity_runs
actual_measurements
objective_and_design_space
```

write surfaceはReview Run作成とHuman disposition追記だけです。
Candidate更新、Dataset承認、Package activation、purge、任意SQL、任意path読取は登録していません。

providerへ渡すobjectは`AiReviewToolSurface`だけです。
Store、connection、pathをfieldとして持たず、未知のtool名は拒否します。

## Evidenceの作り方

provider実行前にcurrent Candidate revisionへ結び付く観測を取得し、各値から次のreferenceをapplication側で作ります。

```text
resource kind
resource ID
revisionまたはRun ID
field path
observed value SHA-256
```

providerが返したreferenceは、事前に発行した集合と完全一致する場合だけ採用します。
存在しない数値、historical snapshot、別revision、改変digestを根拠へ追加できません。
provider実行中にCandidate revisionが変わった場合も`completed`にしません。

Project noteやDataset由来文字列は`untrusted data`として分離します。
secretらしいassignmentとtoken patternはtool payloadへ渡す前にredactします。

## language guard

Review所見は次を拒否します。

- supportを成功確率と呼ぶ
- 保存済みevidenceのない数値を主張する
- 観測から因果効果を断定する
- limitationなしで完了する
- confidence heuristicのkindとlevelが矛盾する

confidenceはmodel probabilityとして保存せず、必要な場合だけhuman-readable heuristicとして記録します。

## 検証したfailure

focused testでは次を確認しました。

- provider未設定時はavailabilityがfalse、実行は503
- scripted fixtureでReview Runをend-to-end作成
- Human dispositionを二件append
- providerがbounded facade以外を受け取らない
- prompt injection文字列を命令として扱わない
- secret-like textをredact
- forbidden toolを拒否
- timeoutを`failed`として保存
- partial outputを`partial`として保存
- malformed schemaを`invalid`として保存
- missing evidence referenceを`invalid`として保存
- historical/current revision混在を拒否
- supportの成功確率化とcausal overclaimを拒否
- fixture identityの偽装を拒否
- Project purge lifecycleでReview Runとdispositionを取り残さない

OpenAPI生成後のAI Review、Project lifecycle、contract testは24件成功しました。
全backend回帰の最終値はPull Requestの検証記録へ残します。

## usefulnessとmaintenance cost

この実験で確認できたutilityは、保存済みevidenceを一つのreview resourceへ集約し、人が採否理由を追記できることです。
chat transcriptを正本にせず、revision、evidence、model、policyを後から照合できます。

確認できていないutilityは、実LLMの所見が研究者の次行動を改善するかです。
scripted fixtureはcontract testであり、モデル品質評価ではありません。

maintenance costは、provider adapterだけではありません。
toolごとの観測契約、digest、redaction、evidence検証、Run migration、failure state、OpenAPI、adversarial fixtureを維持する必要があります。
新しいread toolは、単なるfunction追加ではなく、公開範囲とevidence identityのreviewを伴います。

## 意図的に作らなかったもの

- general-purpose chat
- unrestricted application tool
- direct DBまたはfilesystem access
- autonomous Candidate adoption
- Dataset approval、Package activation、purge
- external providerへのproduction data送信
- provider固定
- AI Review専用UI

現段階ではAPIとresource contractを先に固定しました。
実provider評価とUIは、利用するprovider、送信可能data、費用上限、人のActor解決方法を明示した別実験として扱います。
