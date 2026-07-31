# Evaluator rubric

Actorとは別の文脈で、scenario、journey log、screenshots、decision outcomeを評価する。
画面証拠にない事実をActorが確認したことにしない。

## 評価軸

### Task completion

- frozen scenarioのrequired journeyを完走したか
- 未完の場合、到達不能点と最後に成功したidentityが再現可能か
- scenarioを途中で簡単にしていないか

### Decision safety

- 予測と実測を区別したか
- prediction interval、不確かさ、supportを成功確率と混同していないか
- データ量、学習範囲、欠測、domain上の限界を判断へ含めたか

### Context and recovery

- Project、Candidate、Run、Snapshotのidentityを追跡できるか
- back／forward、tab移動、新しいbrowser contextから文脈を復元できたか
- 保存済み結果を最新計算で暗黙更新したと誤認していないか

### UX evidence

- intent、action、expected、observedが分かれているか
- screenshotは同じviewportで、対象と結果位置を示しているか
- 待機時間、backtrack、visible error、次の行動が記録されているか

## Finding判定

data、profile、tooling、application、product question、not an issueを先に分ける。
同一原因・操作・impactを持つGitHub Issueをtitle/bodyから検索する。
重複には新しいIssue候補を作らず、既存番号と追加証拠を記録する。

Issue候補はseverity順に最大5件とする。
finding数ではなくdecision impactと再現性を優先する。
