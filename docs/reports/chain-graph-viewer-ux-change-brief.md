# Chain graph viewer UX Change Brief

Issue #630 / 2026-08-01

## 利用者の問い

固定済みのChain Projectについて、どの外部条件がどのStageへ入り、どの出力が次段へ渡るか、また現在の結果がその固定契約に追従しているかを確認したい。

## 到達時点で確定していること

ProjectのChain Revision、Definition digest、Stageのcontract／Package／Dataset lockはProject scientific identityで固定済みである。候補をURLで選んだ場合だけ、その候補のlive executionを付加して読める。

## この画面で決めること／決めさせないこと

利用者が決めるのは「この接続・固定参照・実行状態を判断に使えるか」のみである。Stage、binding、unit conversion、candidate、snapshotを編集・再実行・保存させない。Dataset、Task、Packageを再選択させない。

## 現在のjourneyと認知負荷

Project内の「Chain構成」→ 外部入力とStageを確認 → 実際のbinding railを選ぶ → inspectorで両端のport surfaceと変換を読む。図を使わない場合は同じ接続のbinding表から確認する。覚え続ける情報はなく、固定参照は各nodeで必要時に開く。候補未選択・実行未実施は実行状態なしとして残し、推測した最新表示にはしない。

## 構造案

### 案A: 候補編集画面の常設Stage railを詳細化

編集と実行結果の直近で読めるが、read-only確認と候補編集を同時に考える必要があり、scalar Chainの編集面が存在しない問題も隠してしまう。

### 案B: Project内の独立したread-only Chain構成面（採用）

固定Revisionを入口にし、graph、linear table、inspectorを一つの問いへまとめる。候補のlive executionは補助情報として後から重ね、候補編集・snapshot・actual analysisの導線を変えない。

## 採用案と配置根拠

上部のidentity stripは接続を読む前に「何が固定されているか」を確認するため、nodeは左から右のStage順で原因から結果へ追えるため、binding表はcanvasを使えない場合も同じ意味を順番に読めるため、inspectorは選択後だけ表示してdigest等を初見の主画面から外すために置く。

## 既定表示と技術詳細

既定ではChain label、revision/digestの短縮表示、Stage kind、contract ID、固定surface上の入出力port数、live freshnessを表示する。各binding railはsource／target canonical path、分岐または合流、unit conversionを文字で示す。digest全文、Package/Dataset/Profile lock、value kind／quantity／unit／basis、conversion factor/offsetはdetailsまたはinspectorへ後送りする。

## 削除・統合・後送り

候補編集control、snapshot、actual-conditioned analysis、raw result値は既存画面に残す。node position、viewport、collapsed stateをscientific definitionへ保存しない。初期版はcontract順の自動配置のみとする。

## 守る証拠とidentity

Chain Definition／Revision、binding digest、unit conversion digest、Stage contract／Package／Dataset lockを読み取り専用で表示する。Stage portのvalue kind／quantity／unit／basisは、Chain Revision登録時に検証したStageContractSurfaceをRevision外の添付表へ保存して読む。latest／running／stale／failedはStage executionのstateをそのまま表示し、未実行をlatestと扱わない。surfaceが存在しない旧Revisionだけは推測で補わず、理由付きdegradedとして明示する。

## 受入観察と反証結果

- Stage名や数に依存せず、external input、branch／merge、conversionをbinding tableとgraphの双方で確認できる。
- TabキーとEnter／Spaceだけでbinding rail、node、binding inspectorへ到達できる。
- 候補をURLで開いたとき、Stageごとのlatest等が表示される。
- fresh Playwrightでactual railとtableの端点同値、fixed lock、keyboard inspector、axeを確認する。

残る制約は、migration以前に登録され、再登録もされないRevisionには添付surfaceがないことだけである。その場合も現行Task・material名・任意のfallbackから推論せず、該当stageとedgeをdegradedにする。
