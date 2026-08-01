# Proposal Lab UX Change Brief

## 利用者の問い

同じ予測根拠と計算予算で複数のProposal Strategyを試したとき、どの方法を継続評価、
採用候補、不採用と判断できるか。

## 到達時点で確定していること

Project、Task、Package、Training Snapshot、Design Space、Objective、保存済み
Screening Runは確定済みである。Labはそれらを再選択・再計算しない。

## この画面で決めること

- 比較に使う保存済みRun
- 判定対象strategy
- primary criterion
- `experimental`／`production`判定記録／`no_adopt`
- 判定根拠

## この画面で決めさせないこと

- production registryの変更
- experimental strategyの通常Projectへの適用
- arbitrary acquisition実装
- Dataset、Task、Package、Objectiveの再binding
- acquisition scoreを現実の成功確率とみなすこと

## 現在のjourney

通常の範囲探索でRunを保存 → 保存済みRunの下にある研究用Labを開く →
同じprotocolのRunを選ぶ → server validation → 判定記録を保存 → 同じ場所で再開する。

## 認知負荷

- 初見概念: fixture、seed感度、marginal／jointの違い
- 同時選択: Run集合、判定対象、判定、primary criterion、根拠
- 主操作: 「評価記録を保存」
- 覚え続ける情報: strategyとseedの対応。選択行へ併記する
- 再入力: Project identityは再入力しない
- 往復: 保存済みRunとLabを同じ画面に置き、別画面往復を避ける
- 状態／error: seed不足やidentity不一致をLab内へ表示する
- 不可逆操作: reportは不変だがregistryは変更しない

## 構造案

### 案A: 通常の提案設定へexperimental strategyを追加

一つの実行formで比較Runを作りやすいが、研究評価とproduction利用を同じ判断として
見せ、既存Runへexperimental strategyを適用する誤解を招く。

### 案B: 保存済みRunを入力にする折りたたみLab

通常の目的中心の提案導線を変えず、固定済みRunだけを研究評価へ持ち込む。
Run作成と採用審査は二段階になるが、scientific identityと役割を分離できる。

## 採用案と配置根拠

案Bを採用する。Labを保存済み探索の直後へ置くのは、比較対象を別画面で覚えずに
選べるためである。既定では閉じ、通常の有望候補探索へalgorithm審査を混ぜない。
warningとseed整合状態は保存操作より前へ置き、判定結果は同じpanelで再開できる。

## 既定表示と技術詳細

既定ではLabの役割と「自動反映しない」ことだけを見せる。開いた後にRun ID、
strategy ID、seed、budget、各metricを表示する。digestの完全値は保存ReportのAPI証拠へ
残し、主要ラベルにはしない。

## 削除・統合・後送り

strategy parameter編集、memory詳細、ground-truth regret、sequential round chart、
production切替操作は同時表示へ追加しない。利用可能な保存済み証拠だけを比較する。

## 守る証拠とidentity

Task、Package、Runtime Capability digest、Dataset／Training Snapshot、Design Space、
Objective、generator、selector、pool、budget、seedをprotocolへ固定する。各Runの
pool、score、selection digestとmodel call数、runtimeを保存する。marginal rankingと
joint acquisitionを別の意味として表示する。

## 受入観察

- 通常の有望候補探索にexperimental strategyが現れない
- 同じstrategyに同じ2個以上のseedを揃えるまで保存できない
- identityが異なるRunはserverが差分項目付きで拒否する
- 保存後にstrategy別の目標達成、support、seed差を比較できる
- adopt／no-adopt memoを保存してもregistryは変わらない
- keyboardだけでRun選択、判定入力、保存ができる
- small viewportでは判定formが一列になる

## 反証結果

focused backend contract test、Web typecheck、fresh Playwrightで記録する。
実研究者による#581 Actor journeyとground-truth fixtureのregret評価は未実施と明記する。
