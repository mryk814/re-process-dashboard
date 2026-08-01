# scripts 開発ルール

ルートの [`AGENTS.md`](../AGENTS.md) に加えて、tooling変更には [`検証予算と停止条件`](../docs/operations/verification-budget.md) を適用します。

## 小さく始める

verification planner、docs checker、inventory checkerの変更は、変更したscript自身のcontract testとdiffを最初の予算にします。

`verification-tooling`という分類だけを理由に、application build、full pytest、default Playwrightを追加しません。実際にそれらの選択や実行へ影響する変更だけ、代表plan fixtureまたは対象gateを追加します。

## plannerを緑にするための検証をしない

plannerのfollow-up表示を消す目的だけにcheckpoint／release gateを実行しません。直接証拠と後日の横断証拠を分けます。

unknown pathを見つけた場合は、即座に最大gateへ倒す前に、既存path ruleまたはauthorityで分類可能かを確認します。

## stop

変更した分類、gate選択、exit semanticsを代表fixtureで一度証明し、同一commitのtestが成功したら止めます。test自身を変更していないapplication suiteを重複実行しません。

独立reviewは、runnerのexit code、security gate、release artifact selectionを変える場合だけ要求します。文言・catalog metadata・局所fixture追加はself-reviewで完了できます。
