# Activity / Data Explorer journey audit

Issue #511 の対象を、fresh workspace の実画面で確認した記録です。
確認日は 2026-07-30、通常幅 1280 px と狭幅 900 px を使用しました。

## Activity

| 操作 | 対象 | 結果位置 | 現在選択 | 戻り方 | 監査結果 |
|---|---|---|---|---|---|
| Project概要から「入力ばらつきに強いか」を開く | Projectの選択候補 | 候補比較表より下 | 技術名だけを表示 | 「閉じる」 | 問いと実行画面の名称が変わり、長い比較表を越えて結果へ移動していた |
| 公差を設定して解析 | 選択候補の固定revision | 同じActivity内 | 候補名は比較表まで戻らないと確認困難 | Activityを閉じる | Runは自動保存されるが、何を解析した結果かが見出しにない |
| 保存Runを選ぶ | 同じ候補の保存Run | 設定の直下 | 最新Runは点と日時で識別 | Run一覧または閉じる | URLで再開でき、保存結果自体は失われない |

対応後は、Activityを比較表の直前に置き、入口と同じユーザーの問いを見出しとタブに使います。
候補名と技術上のActivity名は副情報として併記し、操作対象と保存Runの文脈を一画面で読めるようにします。

## Data Explorer

| 操作 | 対象 | 結果位置 | 現在選択 | 戻り方 | 監査結果 |
|---|---|---|---|---|---|
| 検索結果からノードを選ぶ | 選択した実績ノード | 系譜グラフ、確認メモ、詳細 | リストと詳細見出しに表示 | 検索結果を再選択 | 通常幅では明確。900 pxでは詳細より確認メモが先に現れ、選択結果と主要操作が遠い |
| 系譜の接続ノードを選ぶ | グラフ上の接続ノード | 同じ画面を選択ノード中心に更新 | URLとグラフで表示 | ブラウザ履歴または検索結果 | 選択は保持されるが、詳細の位置は画面幅で変わる |
| 「候補ストックへ追加」 | 選択ノードと上流条件の組合せ | 上部通知と候補件数 | 詳細は元ノードのまま | 上部「候補を比較」 | 追加後も同じボタンに見え、候補をどこで確認するかが詳細内に残らない |

対応後は、900 px以下でノード詳細を確認メモより先に並べます。
候補化中はボタンを一貫して無効化し、完了後は詳細内に追加件数と「候補を比較」で確認することを残します。

## Browser evidence

- `output/playwright/issue-511-audit/01-project-overview.png`
- `output/playwright/issue-511-audit/02-activity-entry.png`
- `output/playwright/issue-511-audit/03-activity-result.png`
- `output/playwright/issue-511-audit/04-data-explorer-selected.png`
- `output/playwright/issue-511-audit/05-data-explorer-narrow.png`
- `output/playwright/issue-511-audit/06-data-explorer-narrow-after.png`
- `output/playwright/issue-511-audit/07-activity-narrow-after.png`
- `output/playwright/issue-511-audit/08-activity-wide-after.png`
- `output/playwright/issue-511-audit/09-activity-deeplink-after.png`
- `output/playwright/issue-511-audit/10-activity-narrow-deeplink-after.png`

`01`から`05`は実装前の摩擦、`06`から`10`は実装後の通常幅・狭幅と保存URLからの再開を示します。
特に`10`は900 px幅で、問い、対象候補、技術名、Activity切替、閉じる操作を同時に確認した証拠です。
