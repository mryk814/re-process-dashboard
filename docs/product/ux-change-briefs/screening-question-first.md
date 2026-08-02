# Screening question-first UX Change Brief

## 利用者の問い

材料研究者が、現在のProjectと候補を基準に「どこを探索し、次に試す条件をどう選ぶか」を判断する。

## 到達時点で確定していること

Project、Dataset、Prediction Task、Model Package、Design Space、Objective、基準Candidateは上流で確定している。保存済みRunはimmutableな証拠であり、現在の編集値で読み替えない。

## この画面で決めること

探索目的を選び、必要な場合だけ探索条件を編集し、Runを実行する。成功後は提案、予測区間、support、類似実績を読んでCandidateへ明示的に追加する。

## この画面で決めさせないこと

Projectの固定identity、モデルの選択、Candidate比較、Activity評価はこのScreening sliceで再設計しない。

## 現在のjourneyと認知負荷

従来は目的を選んだ後も編集フォームが常時展開され、最後のRunの判断要約へ到達する前に変数表と詳細設定を通過していた。利用者は、現在読んでいるRunと未実行draftを作業記憶で区別する必要があった。

## 構造案

### 案A: 常設編集面の上へ判断カードを追加

実装差分は小さいが、判断と編集が同時表示のままで、スクロール量と同時判断数を減らせない。

### 案B: 判断 → 根拠 → optional edit

目的を最初に選び、保存済みRunと現在Runの診断を次に読む。探索条件は明示的なdisclosureで開く。初回とfield validation失敗時だけ編集面を開き、成功Runの表示時は閉じる。

案Bを採用する。Screeningだけで完結し、#715のURL／Run owner、#713の予測区間、#703のfailure recoveryを変えずに情報順序を直せるためである。

## 配置根拠

1. 「何をしたいですか？」を最初に置き、画面の問いを固定する。
2. 保存済みRunと現在Runの判断要約を編集より前へ置き、数値と固定Run identityを往復なしで読む。
3. validation／transport failureは判断要約の近くに置き、保持済み結果とretry scopeを先に示す。
4. 編集面は「探索条件を編集」に集約する。初回は開き、成功後は閉じ、field error時は対象を直せるよう再度開く。
5. 地図、表、予測区間、support、類似実績、Run evidenceは削除せず、判断要約の後に維持する。

## 削除・統合・後送り

- 画面上部と編集末尾に重複していた実行ボタンを、編集末尾の一つへ統合する。
- seed、support policy、strategy等の再現設定は既存の詳細設定内に維持する。
- Candidate／Activityの再構成は別sliceへ送る。

## 受入観察

- 初回は目的の後に編集面が開き、keyboardだけで条件を変更して実行できる。
- 成功後はRunの判断要約が編集表より前に見え、編集面は閉じている。
- 「探索条件を編集」を開くと同じdraftと基準Candidateが保持される。
- validation失敗では編集面が開き、field diagnosis、最後の成功Run、同条件retryが保持される。
- URLで選んだRun、予測区間のcoverage/method、support、Candidate追加は従来どおりである。

## 検証予算

```yaml
change_class: structural
authority: ScreeningPage information order and edit disclosure
verification_budget:
  - web typecheck and import boundaries
  - nearest focused UI unit
  - fresh screening Playwright one path covering success and validation recovery
not_planned:
  - default Playwright
  - full frontend or backend suite
  - Candidate or Activity tests
review: self
stop_condition:
  - success and validation recovery prove the new order once on the current commit
```
