# Chain Studio candidate／evidence adapter UX Change Brief

## 利用者の問い

公開済みChainの候補を編集し、変更で古くなったStageだけが再計算されたことを確認して、
判断時点をSnapshotへ固定したい。

## 到達時点で固定済みの情報

Projectはimmutable Chain Revision、Stage contract、Package、Dataset/Profileを既に固定している。
この画面でそれらを再選択させない。

## この画面で決めること／決めさせないこと

- 決める: 外部入力の値、候補、実行後にSnapshotへ固定する時点。
- 決めさせない: candidate adapter、Stage構成、Package、Dataset、actual-conditioned分析を
  通常Snapshotへ混ぜること。

## working memory、再入場、移動

候補revision、Stage freshness、現在結果、選択中Snapshotを同じStudio shellで保持する。
再入場時は保存済み候補とSnapshotを読み、Project内のChain graphへ移動してもscientific
identityを変えない。主要操作は一画面内に置き、専門的な証跡とJSONはdetailsへ畳む。

## 構造案の比較

1. domain別Workbenchを別々に維持する。各画面は局所的に分かりやすいが、scalar Chainが
   疎配合APIへ誤接続し、実行・鮮度・Snapshotの共通挙動も二重化する。
2. 共通Studio shellがlifecycleを所有し、固定registryからcandidate editorとevidence
   rendererだけを選ぶ。scalarは契約駆動inputとgeneric evidence、sparse-blendは既存の
   BlendEditorとactual-conditioned evidenceを使う。

案2を採用する。dynamic pluginは作らず、frontend bundle内の型付きallow-listだけを正本にする。

## error、復旧、削減

未対応adapterはBlend UIへfallbackせず、adapter IDと利用不能理由を表示する。
入力保存の競合は既存のrevision rebaseを維持し、失敗時もdraftと直前のStage結果を残す。
scalar画面からBlend契約、actual-conditioned API、不確かさ伝播APIを呼ばない。

## 受入観察

- scalar Projectで基準候補を作り、数値変更後にaffected Stageだけがstaleになり、
  partial recomputation後にSnapshotを固定できる。
- sparse-blend Projectで既存の配合、lock、validation、実測別analysisを維持する。
- 未対応adapterではdomain APIを呼ばず、理由付きunavailableになる。
- keyboardで候補、外部入力、Snapshot、generic evidenceへ到達できる。
