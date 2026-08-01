# Chain graph viewer UX Change Brief

Issue #630 / 2026-08-01

## 利用者の問い

固定済みのChain Projectについて、どの外部条件がどのStageへ入り、どの出力が次段へ渡るか、また現在の結果がその固定契約に追従しているかを確認したい。

## 到達時点で確定していること

ProjectのChain Revision、Definition digest、Stageのcontract／Package／Dataset lockはProject scientific identityで固定済みである。候補をURLで選んだ場合だけ、その候補のlive executionを付加して読める。

## この画面で決めること／決めさせないこと

利用者が決めるのは「この接続・固定参照・実行状態を判断に使えるか」のみである。Stage、binding、unit conversion、candidate、snapshotを編集・再実行・保存させない。Dataset、Task、Packageを再選択させない。

## 現在のjourneyと認知負荷

Project内の「Chain構成」→ 左から右へStageを確認 → 気になるexternal inputまたはbindingを選ぶ → inspectorで端点と変換を読む。図を使わない場合は同じ順番のbinding表から確認する。覚え続ける情報はなく、固定参照は各nodeで必要時に開く。候補未選択・実行未実施は実行状態なしとして残し、推測した最新表示にはしない。

## 構造案

### 案A: 候補編集画面の常設Stage railを詳細化

編集と実行結果の直近で読めるが、read-only確認と候補編集を同時に考える必要があり、scalar Chainの編集面が存在しない問題も隠してしまう。

### 案B: Project内の独立したread-only Chain構成面（採用）

固定Revisionを入口にし、graph、linear table、inspectorを一つの問いへまとめる。候補のlive executionは補助情報として後から重ね、候補編集・snapshot・actual analysisの導線を変えない。

## 採用案と配置根拠

上部のidentity stripは接続を読む前に「何が固定されているか」を確認するため、nodeは左から右のStage順で原因から結果へ追えるため、binding表はcanvasを使えない場合も同じ意味を順番に読めるため、inspectorは選択後だけ表示してdigest等を初見の主画面から外すために置く。

## 既定表示と技術詳細

既定ではChain label、revision/digestの短縮表示、Stage kind、contract ID、入出力binding数、live freshnessを表示する。digest全文、Package/Dataset/Profile lock、conversion factor/offset、canonical pathはdetailsまたはinspectorへ後送りする。

## 削除・統合・後送り

候補編集control、snapshot、actual-conditioned analysis、raw result値は既存画面に残す。node position、viewport、collapsed stateをscientific definitionへ保存しない。初期版はcontract順の自動配置のみとする。

## 守る証拠とidentity

Chain Definition／Revision、binding digest、unit conversion digest、Stage contract／Package／Dataset lockを読み取り専用で表示する。latest／running／stale／failedはStage executionのstateをそのまま表示し、未実行をlatestと扱わない。DefinitionにないStage outputのtype、quantity、basisは推測で補わずdegradedとして明示する。

## 受入観察と反証結果

- Stage名や数に依存せず、external input、branch／merge、conversionをbinding tableとgraphの双方で確認できる。
- TabキーとEnter／Spaceだけでexternal port、node、binding inspectorへ到達できる。
- 候補をURLで開いたとき、Stageごとのlatest等が表示される。
- fresh Playwrightでtable、fixed lock、keyboard inspector、axeを確認した。

残る制約は、既存Chain DefinitionがStage outputの完全なport surfaceを永続化していないことである。そのためこのviewerはDefinitionが持つpathとconversionを表示し、欠けた物理metadataを現在のTaskや材料固有名から補完しない。
