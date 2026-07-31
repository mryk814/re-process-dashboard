---
name: data-contributor
description: Data Library UIから新しいExcel／CSVをEvidence Decision Workbenchへ安全に導入する。更新版、既存Taskへの対応付け、新しい予測問題を意味で仕分け、Dataset／Task／Model Package／Projectと代表予測まで追跡するときに使う。
---

# Data Contributor

[自分のデータで使い始める](../../../docs/operations/data-contributor-start-here.md)を正本として読む。
既定入口はData Library UIであり、commandを先に実行して画面の不足を隠さない。

## UI-firstの境界

優先順位は次の通り。

1. アプリUI
2. UIが示す進捗、warning、再開導線
3. UIで不可能と確認した時点で停止し、`ui_missing`または`ui_blocked`を記録
4. 利用者が継続を明示承認した場合だけ、画面が生成したsetup commandまたは限定fallback
5. read-only診断

アプリ起動、isolated Workspace準備、source hash／licenseのread-only確認はjourney前に行ってよい。
DB direct write、mutation API、tracked fixtureへのsource追加は禁止する。
fallback区間をUI-only完走として報告しない。

## 1. Source intakeを記録する

元ファイルを移動、修正、登録、学習する前に、次を記録する。

- source path、file type、SHA-256
- license、provenance、production／reference／教材／testの用途
- 画面で確認できる行数、missing、duplicate header
- 未確認のdomain meaning

[UI onboarding checklist](references/ui-onboarding-checklist.md)を使い、重要操作の直後に短い記録を残す。
private chain-of-thoughtではなく、第三者へ説明できる意図、観察、判断根拠だけを書く。

## 2. Data Libraryで三経路を選ぶ

「データを追加」から、列名の近さではなく意味で選ぶ。

- **更新版**: 入力、出力、単位、学習一行、relationの意味が同じ。SourceとDataset Revisionだけを更新する。
- **列名・構造が違う**: 意味は既存Taskと同じで、シート名、列名、単位表記、任意列が違う。個人Profileで対応付ける。
- **新しい予測問題**: 既存Taskにない入力／出力を、一行一観測・relationなし・allow-list済み標準回帰として表現できる。

relation、反復集約、専用parser、Feature Pipeline、Runtime adapterが必要なら、その時点でアプリ開発レーンへ移る。
判断できない物理量、目的変数、学習単位、relationは推測せず、人へ確認する。

## 3. UIで意味を確認する

previewとmapping画面で次を確認する。

- input／outputとcomposition／process／categorical等のrole
- canonical quantity、表示名、unit、変換
- one-row-one-observationか
- relationの有無、key、parent、反復の扱い
- physical allowed range
- default exploration range
- training observed range
- outputのgoal direction

観測min／maxは学習データの要約であり、物理的な許容範囲へ自動採用しない。
Taskが使わない補助entityの未対応relationは、画面が任意と示す場合に限り登録を止めない。
必須項目が未解決なら先へ進まない。

## 4. Dataset、Model Package、Projectを追う

画面に表示された順で次を追跡し、各identityと状態を記録する。

1. Dataset登録とDataset Revision
2. estimator選択
3. training source／Profile identity
4. build／verify状態
5. immutable Package ID／version／digestと個人用trusted store
6. 起動中アプリへの「個人Taskとモデルを再読込」
7. Dataset、Task、Packageを明示して新Project作成
8. 代表候補を一つ予測し、予測、区間、supportの表示を確認

画面がsetup commandを表示するだけの工程は`setup_only`である。
strict UI journeyではそこで停止する。継続が承認された場合だけ、表示されたcommandを同じsource／Profile identityで実行し、`fallback_used`として記録する。

既存Project、active Package、保存済みSnapshotを暗黙更新しない。
同じPackage IDを上書きせず、内容が変わるたび新しいidentityを使う。

## 5. Capabilityと失敗を分類する

[capability inventory template](references/ui-capability-inventory-template.json)へ各工程を
`ui_available`、`ui_blocked`、`ui_missing`、`setup_only`、`fallback_used`のいずれかで記録する。

失敗は次へ分類する。

- **data**: 破損、必須値欠損、単位不明、キー重複、対象行ゼロ
- **profile**: シート／列／unit／key／relation roleのmapping不一致
- **tooling**: 依存関係、権限、port、store到達性など処理前の環境問題
- **app bug**: 契約と環境が成立しているのにUIが未捕捉例外、誤判定、不整合な結果になる
- **domain question**: 意味、物理範囲、集約、relationを決める根拠が不足

回避策を作る前に、意図、exact action、期待、visible result、evidence、最後に成功したidentityを残す。
アプリ不具合だけをIssue候補へ分け、データ導入へアプリ開発の責任を広げない。

## 6. Scenario Journeyへ渡す

Project作成と代表予測まで終えたら、[handoff template](references/handoff-template.md)を埋めて
`scenario-journey-evaluator`へ渡す。

handoffにはDataset／Profile／Task／Package／Project identity、UI-only／fallback区間、仮定、
未解決のdomain question、onboarding finding候補を含める。
目標形成、proposal、候補確認、Decision Activity、判断保存は次のSkillの責務である。

## 診断とfallback

commandは既定手順ではない。
UIの不足を記録した後、継続が明示承認された場合、またはread-only診断が必要な場合だけ
[diagnostic／fallback appendix](references/diagnostic-fallback-appendix.md)を読む。

## データ利用レーンを守る

- 元データ、個人Profile、個人Task、個人Packageをgitへ追加しない
- `data/source/`、追跡済みProfile、`models/packages/`を変更しない
- unit test、E2E、Issue、branch、PR、全体gateをデータ追加の完了条件にしない
- 検証を通すために元データ、契約、テストを弱めない
