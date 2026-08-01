# apps/web UI開発ルール

ルートの [`AGENTS.md`](../../AGENTS.md) に加えて、このdirectory以下のUI変更には本規則を適用します。

## UI構造変更の入口

新しい画面、redesign、navigation、onboarding、form構造、dashboard／Workbench、
table／graph／visual editor、empty／loading／error／stale状態、主要CTA、結果配置、
画面間handoffを変更する前に
[`frontend-ux-architect`](../../.agents/skills/frontend-ux-architect/SKILL.md)を使います。

詳細なUX Change Brief、構造案比較、禁止事項、受入観察はSkillへ集約し、次を正本として読みます。

- [`docs/product/ux-change-process.md`](../../docs/product/ux-change-process.md)
- [`docs/product/design-system.md`](../../docs/product/design-system.md)

typo、copyだけの訂正、既存tokenへの機械的な置換など、情報順序と操作構造を変えない変更では
`frontend-ux-architect`を強制しません。

## 実装時に守る境界

- server state、editing draft、URL state、presentation metadataを分ける
- stale response rejectionとrequest identityを維持する
- immutable revision、Run、Snapshotを現在値で上書きしない
- unavailable capabilityは実行前に理由と代替を示す
- 予測値、実測値、不確かさ、support、acquisition scoreを同じ意味として表示しない
- digestや内部IDを主要ラベルにせず、技術詳細から完全なidentityへ到達可能にする
- canvas、drag、pointer操作にはkeyboard／linear alternativeを用意する
- generated OpenAPI型の不一致をcastや手書きDTOで隠さない
- Task ID、model名、元データ列名による中央UI分岐を増やさない
- 旧経路を長期並存させず、必要なmigrationとfocused testを伴って完全移行する

## 完了前の実画面確認

画面または操作経路を変えた場合は、fresh server／独立Workspaceで、変更に関係する
初回、入力途中、loading、stale／partial、field error／resource failure、unavailable、
back／forward、small viewport／文字拡大、keyboard、保存後resumeを確認します。

大きな判断flowでは、実装コードや期待findingを先読みしないActorとして
[`scenario-journey-evaluator`](../../.agents/skills/scenario-journey-evaluator/SKILL.md)を使います。
