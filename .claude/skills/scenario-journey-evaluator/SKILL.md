---
name: scenario-journey-evaluator
description: Use after a dataset, Prediction Task, and Model Package are ready to complete a frozen decision scenario in the real Material Decision Workbench UI. Produces an identity-aware journey log, decision outcome, and at most five deduplicated UX, contract, evidence, recovery, accessibility, performance, data, profile, or tooling findings. Do not use it to prepare new data, bypass the UI, or fix the application during the journey.
---

# Scenario Journey Evaluator

実データを接続した後の判断作業を、利用者として実画面で完走する。
操作の成否だけでなく、判断結果、復旧可能性、迷い、待機、証拠を再現可能な記録へ変える。

## 責務を分ける

sourceの確認、既存Taskへの対応付け、新Taskのscaffold、Profile、Dataset登録、Model Package準備は
先に`data-contributor`で行う。
このSkillはProject作成以降のUI journeyだけを評価する。

journey中にアプリコードを修正しない。
見つけた問題はfindingへ分け、データ準備の失敗をUI不具合として扱わない。

## 必須入力を確認する

開始前に次をそろえる。

- frozen状態のscenario YAMLまたはJSON
- dataset pathとprovenance／license record
- 利用可能なTask、Dataset、Model Packageのidentity
- productionと分離したWorkspace DBとData Library
- 実ブラウザを操作できる手段
- journey log、screenshot、finding reportのリポジトリ外保存先
- Actorへ見せないevaluator rubric（任意）

[scenario template](references/scenario-template.yaml)を複製し、scenario revisionをfreezeする。
問い、目標、制約、success condition、禁止事項、必須journeyを含む内容のdigestを保存する。

freeze後に内容を簡単にしてはならない。
変更が必要なら元revisionを保持したまま新revisionを作り、変更理由を記録する。
操作不能なstepは削らず、到達不能として残す。

## ActorとEvaluatorを分ける

可能なら文脈を共有しない二つのsessionまたはagentを使う。

Actorには[Actor mission](references/actor-mission-template.md)、frozen scenario、dataset provenance、
準備済みidentity、UI URLだけを渡す。
既知の不具合、期待finding、evaluator rubric、実装コードの知識を渡さない。

Evaluatorにはjourney log、screenshots、decision outcome、scenario、evaluator-only rubricを渡す。
Evaluatorはsanity dataと既存Issueを確認できるが、Actorが見ていない事実を「画面で確認できた」と扱わない。

同一agentで行う場合は弱い分離であることを報告し、Actor開始後にコードやDBを読まない。

## 実画面でjourneyを進める

Computer Use、Chrome、in-app browserなどの実ブラウザ操作を優先する。
利用できない場合だけ、独立DBとfresh serverを使うPlaywrightへ切り替え、その理由を記録する。

setup用CLIとread-only診断はjourney開始前に限り利用できる。
journey開始後は次を禁止する。

- DBのdirect read／writeで画面の答えを得る
- mutation APIでUI操作を飛ばす
- 実装コードから状態や正解を先読みする
- 実測値を捏造する
- 失敗を避けるためscenarioやデータを変更する
- 同じsessionでアプリを修正する

各重要操作を[journey log schema](references/journey-log-schema.md)に従うJSONLとして直ちに記録する。
特にProject、Candidate、Run、Snapshotが変わった操作では、変更後のidentityを必ず残す。

操作のたびに次を観察する。

- 何を操作しているか画面だけで分かるか
- 結果がどこへ現れたか
- 予測、実測、不確かさ、supportを区別できるか
- tab移動、back／forward、新しいbrowser contextでも文脈を復元できるか
- 待機中または失敗時に原因と次の行動が分かるか
- UIを回避してコードやDBを見たくなったか

到達不能になった場合は、最後に成功したidentity、exact action、visible result、screenshotを記録する。
scenarioを変更せず、その地点でActor結果を完了する。

## 判断結果を残す

選択した案だけでなく、比較した案、判断理由、予測区間、support、近い実績、残るリスクを記録する。
予測上の目標達成を実性能達成と断定しない。
小標本、学習範囲、欠測、データ由来の制約をscenarioに応じて明示する。

保存RunまたはSnapshotの再開は、同じpageのrefreshだけで済ませない。
新しいbrowser contextから保存URLまたは画面上の入口を使い、
Project／Candidate／Run identityと判断文脈が一致することを確認する。

## Evaluatorがfindingを整理する

[evaluator rubric](references/evaluator-rubric.md)でtask completion、decision safety、evidence quality、
recoveryを評価する。

findingを次のいずれかへ分類する。

- functional bug
- unclear operation target
- result location ambiguity
- state/context loss
- decision-safety risk
- missing evidence
- accessibility
- performance/wait feedback
- data problem
- profile problem
- tooling problem
- product question
- not an issue

GitHub Issueをtitleとbodyの両方から検索し、同一原因、同一操作、同一impactのIssueを確認する。
既存Issueがあれば新規候補を作らず、関係と追加証拠を記録する。

[finding report template](references/finding-report-template.md)を使う。
Issue候補はseverity順に最大5件とし、件数を成果にしない。
各候補にはscenario／Project／Task identity、再現手順、user intent、expected／observed、
decision impact、visible evidence、severity rationale、scope／non-scope、acceptance criteriaを含める。

## 完了条件

- frozen scenarioとdigestが残る
- Actor入力とEvaluator入力の境界が記録される
- journey JSONLの全重要操作にcurrent identityがある
- 完走または到達不能点が再現可能である
- decision outcomeに根拠と限界がある
- 新しいbrowser contextから保存文脈を再開した、または不能理由がある
- findingが既存Issueと重複確認され、最大5件にまとまる
- dataset、個人Task、Profile、Package、Workspace DBをリポジトリへ追加していない
