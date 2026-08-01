# apps/web UI開発ルール

ルートの [`AGENTS.md`](../../AGENTS.md) に加えて、このdirectory以下のUI変更には本規則を適用します。

## 最初に読むもの

- [`docs/product/design-system.md`](../../docs/product/design-system.md)
  - 視覚トークン、文字、表、focus、responsive layout、表示語彙の規則
- [`docs/product/ux-change-process.md`](../../docs/product/ux-change-process.md)
  - 利用者の問い、認知負荷、情報順序、配置根拠、実画面反証の手順

## UI構造を変える前に行うこと

新しい画面、主要panel、navigation、form構造、結果配置、empty／error／loading状態、画面間handoffを変更する場合は、実装前にUX Change Briefを残します。

Issueは必須ではありません。PR本文、設計メモ、作業記録のいずれかで構いません。

最低限、次を明示します。

1. 利用者がこの画面で答えたい問い
2. 到達時点で既に確定している文脈
3. この画面で新たに決めること
4. この画面で決めさせないこと
5. 利用者が覚え続ける必要のある情報
6. 再入力、画面往復、主操作、errorからの復旧
7. 主要要素をその順序・位置へ置く理由
8. 既定表示と技術詳細へ分ける情報
9. 追加前に削除・統合・後送りする情報
10. 実画面で確認する受入観察

構造変更では、原則として認知モデルの異なる二案以上を比較します。

- 一画面＋progressive disclosure
- mode分割
- step flow
- 結果中心＋設定side panel
- 一覧＋detail
- graph＋linear alternative

色、余白、角丸、左右配置だけが異なるものは別案として数えません。

## 禁止する近道

- 情報量を減らさず、整列とCSSだけで「認知負荷を改善した」とする
- 操作順序や結果位置を変えず、説明文やtooltipだけを追加する
- safe default、業務上の選択、seed／digest等の再現parameterを同じ視覚階層へ置く
- 前段で確定したDataset、Task、Package、Objective等を再入力・再選択させる
- warningを影響対象の数値や主操作より後ろへ置く
- errorをtoastだけへ出し、失敗箇所・残った情報・再試行対象を示さない
- accessibility test通過を使いやすさの証明とする
- 既存componentやstate ownerの都合を、利用者の作業順序より優先する
- generated OpenAPI型の不一致を`as never`、二重cast、手書きDTOで隠す
- Task ID、モデル名、元データ列名による中央UI分岐を増やす
- 認知負荷を下げる名目でprediction／actual、uncertainty、support、revision、digest、Run、Snapshotの意味を欠落させる

## 配置理由

主要な情報・controlには、「関連しているから」以外の配置理由を持たせます。

例:

- 主実行ボタンは、設定を完了した位置から戻らず実行できる場所へ置く
- support外等のwarningは、影響を受ける数値より前へ置く
- 現在のObjectiveは、結果を解釈する場所の近くへ要約する
- seed、digest、request IDは監査可能性を保ちつつ技術詳細へ置く
- onboarding済みbindingは再選択させずreview cardとして確認する
- partial failureは失敗したresource sectionで説明し、そのsectionだけを再試行できるようにする

配置理由を書けない要素は、削除、統合、後送り、別画面化を検討します。

## 実装時に守る境界

- server state、editing draft、URL state、presentation metadataを分ける
- stale response rejectionとrequest identityを維持する
- immutable revision、Run、Snapshotを現在値で上書きしない
- unavailable capabilityは実行後に失敗させず、必要な時点で理由を示す
- 予測値、実測値、不確かさ、support、acquisition scoreを同じ意味として表示しない
- digestや内部IDを主要ラベルにせず、技術詳細から完全なidentityへ到達可能にする
- canvas、drag、pointer操作を導入するときはkeyboard／linear alternativeを用意する
- 旧経路を長期並存させず、必要なmigrationとfocused testを伴って完全移行する

## 完了前の実画面確認

画面または操作経路を変えた場合は、fresh server／独立Workspaceで、変更に関係する次の状態を確認します。

- 初回状態
- 入力途中
- loading
- stale／partial result
- field errorまたはresourceの部分失敗
- unavailable capability
- back／forwardまたは再読み込み
- small viewportまたは文字拡大
- keyboard操作
- 保存後のresume

テストはDOM構造だけでなく、UX Change Briefに書いた受入観察を検証します。

大きな判断flowでは、実装後に [`scenario-journey-evaluator`](../../.claude/skills/scenario-journey-evaluator/SKILL.md) を使い、実装コードや期待findingを先読みしないActorで反証します。

## レビュー時の最低質問

- このviewportで答える問いは明確か
- 主操作は一つに見えるか
- 何をworking memoryへ預けているか
- 前段の情報を再入力させていないか
- 結果と条件・identityを往復なしで確認できるか
- 詳細設定なしで安全な既定経路を進めるか
- 前提不足、unavailable、stale、partial resultを数値より先に理解できるか
- 配置理由を「関連しているから」以外で説明できるか
- copyやtooltipで構造的問題を隠していないか
- scientific evidenceを簡略化で失っていないか
