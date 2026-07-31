---
name: scenario-journey-evaluator
description: Use after Data Contributor has prepared a prediction-ready Project to turn an ambiguous decision brief into goals, proposals, candidate verification, a Decision Activity, revision, save, and resume in the real Evidence Decision Workbench UI. Produces explainable journey artifacts and at most five deduplicated findings.
---

# Scenario Journey Evaluator

引き継いだProjectで曖昧な判断課題を利用者として完走し、判断結果、復旧可能性、迷い、
待機、画面証拠を再現可能な記録へ変える。

## Data Contributorとの境界

source、license、Profile、Dataset、Task、Model Package、Project、代表予測までは
`data-contributor`が担当する。
このSkillは[handoff](../data-contributor/references/handoff-template.md)を受け取り、
そのProjectで目標形成から始める。Dataset、Task、Packageを作り直さない。

UI data onboardingもscenario全体のphase A証拠として保持するが、Actorが重複実行しない。
handoffがない場合はData Contributorへ戻す。

## UI-firstの境界

優先順位は、UI、UIの進捗／warning／再開導線、到達不能の記録、明示承認後の限定fallback、
read-only診断の順とする。
journey開始後は次を禁止する。

- DB direct read／writeやmutation APIで画面を飛ばす
- 実装コードから状態や正解を先読みする
- 実測値を捏造する
- 失敗を避けるためscenarioやデータを変更する
- 同じsessionでアプリを修正する

strict UI journeyで操作不能なら停止し、`ui_missing`または`ui_blocked`としてfinding化する。
fallback区間をUI-only完走として数えない。

## 曖昧なScenarioをfreezeする

[scenario template](references/scenario-template.yaml)へ利用者のbrief、known context、禁止事項だけを入れ、
revisionとdigestをfreezeする。
数値目標、制約値、selection policy、proposal strategyの正解をActorへ与えない。

Actorが画面から具体化するものは次の通り。

- objective
- hard constraints
- soft preferences
- trade-offs
- assumptions
- acquisition／goal-search strategy

最初の具体化を`goal formulation v1`として固定する。
途中でObjectiveを一箇所変更するときは`v2`を作り、理由、旧Run、新Runを結ぶ。
scenario自体を簡単にする変更は別revisionとして元を保持する。

## ActorとEvaluatorを分ける

可能なら文脈を共有しない二つのsessionまたはagentを使う。

Actorには[Actor mission](references/actor-mission-template.md)、frozen scenario、Data Contributor handoff、
UI URL、出力先だけを渡す。
既知の不具合、期待finding、sanity data、evaluator rubric、実装コードの知識を渡さない。

Evaluatorにはjourney artifacts、screenshots、saved decision、scenario、
[evaluator rubric](references/evaluator-rubric.md)を渡す。
画面証拠にない事実を「Actorが確認した」と扱わない。
同一agentなら弱い分離であることを記録し、Actor開始後にコードやDBを読まない。

private chain-of-thoughtを要求しない。
記録するのは一〜三文の説明可能な意図、観察、判断根拠である。

## Journeyを進める

Computer Use、Chrome、in-app browserなど実ブラウザを優先する。
利用できない場合だけ、独立DBとfresh serverを使うPlaywrightへ切り替え、理由とcapabilityを記録する。

各重要操作の直後に[journey log schema](references/journey-log-schema.md)へ一行記録し、
[journey map](references/journey-map-template.md)を更新する。

### B. Goal formulation

- Data Explorerで分布、採用データ、近い実績を見る
- briefを数値目標、hard constraint、soft preference、trade-offへ分ける
- 仮定と未解決の問いを`goal formulation v1`へ残す

### C. Proposal

- 利用可能なproposal strategyから目的に合うものを選ぶ
- strategy名ではなく「なぜ今この案を評価する価値があるか」と限界を記録する
- rankedとdiverseを比較し、堅実案、探索案、多様案を区別する

### D. Candidate verification

- 複数候補のprediction、interval、support、historical evidence、constraintを比較する
- 候補差分、入力ばらつき、目標到達案などから目的に合うDecision Activityを一つ以上選ぶ
- Activityの選択理由と、見ても解消しない不確かさを記録する

### E. Revision and replay

- Objectiveを一箇所変更して`goal formulation v2`を作る
- 旧Runと新Runを混同せず、stale表示と対応identityを確認する
- decision noteを保存する
- 新しいbrowser contextから保存URLまたは画面上の入口で再開する

操作不能なら最後に成功したidentity、exact action、visible result、screenshotを記録して終了する。
scenarioを変更して回避しない。

## 判断成果物を残す

- [journey map](references/journey-map-template.md)
- journey JSONL
- [goal formulations](references/goal-formulations-template.md)
- [candidate comparison](references/candidate-comparison-template.md)
- [decision memo](references/decision-memo-template.md)
- [UI capability inventory](references/ui-capability-inventory-template.json)
- screenshots

選択案だけでなく比較案、予測区間、support、近い実績、残るリスクを残す。
予測上の目標達成を実性能達成と断定しない。

## Evaluatorがfindingを整理する

[evaluator rubric](references/evaluator-rubric.md)でtask completion、decision safety、evidence quality、
recoveryを評価する。
data、profile、tooling、application、product question、not an issueを先に分ける。

GitHub Issueをtitleとbodyから検索し、同一原因、操作、impactなら新規候補を作らない。
[finding report template](references/finding-report-template.md)を使い、Issue候補はseverity順に最大5件とする。
自動でIssueを作成せず、件数を成果にしない。

## 完了条件

- frozen scenarioとdigestがある
- Data Contributor handoffとonboarding証拠を保持している
- Actorがbriefからgoal formulation v1を作った
- proposal strategyの理由と限界、ranked／diverse、堅実／探索／多様を比較した
- Decision Activityを目的から選んだ
- goal formulation v2、変更理由、旧Run／新Run identityが結ばれた
- journeyの重要操作にidentity、capability、evidenceがある
- 完走または到達不能点を再現できる
- 新しいbrowser contextから再開した、または不能理由がある
- findingを重複確認し最大5件へまとめた
- 個人データ、Task、Profile、Package、Workspaceをリポジトリへ追加していない
