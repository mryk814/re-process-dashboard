# Issue #702: 過去実測・実験バッチからの候補化

```yaml
change_class: structural
authority: 候補化の入口、Screening batchの採用操作、Candidate provenance表示
expected_scope: shared API、証拠表、Screening batch、Candidate作成元
verification_budget:
  - generated OpenAPI/typecheck
  - provenanceとbatch行操作のfocused unit test
  - backend/UI統合後にhistorical reloadとbatch 3件のfresh E2E 1本
not_planned:
  - default Playwright
  - full pytest
  - checkpoint
review: independent-adversarial
stop_condition: 行単位の採用と保存後の由来再表示を一度ずつ反証する
```

## 利用者の問い

過去の実測record、または保存済み実験batchの一員を、入力を打ち直さず次の比較候補として採用したい。

## 到達時点で確定していること

Project、Dataset revision、Prediction Task、Model Package、Objective、Screening Runは到着時点で固定している。

## この画面で決めること／決めさせないこと

決めるのは「このrecord／batch memberを候補に採用するか」だけである。固定済みのDataset、Task、Package、Objective、Runや、実測値を予測値として扱うかは再選択させない。

## 構造案

### 案A: 証拠・batchの各行で採用する

根拠を読んだ同じ行で一件ずつ候補化する。既存の作業順とbatch各memberの役割を保ち、Control／反復を新規候補にしない。

### 案B: 一括handoff wizard

複数のrecordまたはbatchを選んで最後にまとめて候補化する。比較対象と採用判断を同時に増やし、既存Screeningの読み順を分断する。

採用は案A。候補化は自動実行せず、実績値・距離・batch roleを読んだ行に明示actionを置く。

## 配置と証拠

- 類似実測の行では、flat tableのrecordをそのまま採用する。relation／lineageがなくても操作をdisabledにしない。
- 実験batchの行では、acquisition-ranked memberだけを候補にできる。固定Controlと反復は観測計画なので新規Candidate化しない。
- Candidate detailでは「過去の実測値（actual）」を作成元として明示し、現在のprediction、interval、supportと同じ値として表示しない。
- saved Runはbatchがある場合に`0件提案`ではなく、保存済みの実験batch枠数を表示する。

## 受入観察

1. relationなしのProjectで、近い実測recordを一行の操作から候補にでき、再表示時にもDataset revisionとrecord identityを追える。
2. 3件のdiverse batchでは、各memberを一件ずつ正確にCandidateへ採用でき、Control／反復の行は候補化を促さない。
3. 候補detailでactual historical outcomeとcurrent Candidate resultを取り違えない。

## 未確認事項

fresh E2Eはbackend/UI統合後に一度だけ実行する。今回のUI sliceではunit/typecheckまでとする。
