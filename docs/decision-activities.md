# 検討アクティビティ

## 判断に必要な問いを実行単位にする

候補比較では、予測値を並べるだけでは答えられない問いが生じる。
たとえば「製造ばらつきがあっても目標を満たすか」と「この候補を基準候補から変えた理由は何か」では、必要な入力と計算結果が異なる。

**検討アクティビティ**は、判断に使う問い、必要な機能、入力パラメーター、結果契約をまとめた実行単位である。
画面名やモデル名をアクティビティの識別子に含めないため、Prediction TaskやModel Packageが変わっても、必要なruntime機能がそろえば同じ問いを実行できる。

現行版は次のアクティビティを提供する。

- **ロバストネス／公差解析**：候補の入力を指定した公差内で変動させ、目標達成の安定性を確認する。
- **候補差分の要因分解**：保存済みの2つの候補revisionで予測が違う理由を、入力別の置換寄与と残差で示す。

## アクティビティの追加境界

パラメーターと結果は `schema_version` を判別子とする型付きunionである。
任意JSONは受け取らず、新しいアクティビティは明示的にallow-listする。

アクティビティを1件追加するときに触るのは次の4か所だけである。

1. `contracts/decision_activity_contracts.py` — パラメーターモデル、結果モデル、definition、unionへのメンバー追加
2. `application/decision_activity_<name>.py` — そのアクティビティの `prepare` と `compute`
3. `application/decision_activity_registry.py` — registryへの1 entry
4. `apps/web/src/features/workbench/decisionActivities/` — そのアクティビティのview 1件とregistryへの1 entry

既存アクティビティのservice、API、UIへ分岐を追加しない。
共通部分（`application/decision_activities.py`、`api/decision_activities.py`、`DecisionActivityPanel.tsx`）は
activity_idを名指ししてはならず、テストで固定している。

必要条件はresource種別ごとに1か所で判定する。
`candidate` は保存済み候補revision、`comparison_candidate` は別候補または同じ候補の過去revision、
`objective_definition` はProjectへ固定したObjective Definitionを要求する。
アクティビティごとに利用可否のコードを増やさない。

## 利用可否の判定

APIは、Projectに固定されたTask DefinitionとModel Packageから、アクティビティごとの利用可否を返す。
現行のロバストネス解析は保存済み候補のrevisionとpreview予測runtimeを必要とし、
目標到達案はさらにProject-level Design SpaceとObjective Definitionを必要とする。
必要条件を満たさない場合、UIは実行ボタンを無効にするだけでなく、不足している条件を表示する。

この判定はTask名やモデル名の列挙ではない。
新しいTaskやallow-list済みのモデルadapterを追加しても、同じ機能契約を満たせば利用できる。

## 公差サンプルの生成

利用者は変動させる数値入力と公差を指定する。
契約は絶対幅、相対幅、一様分布の上下限、打切り正規分布を表現できる。
初期UIは、現在値を中心とする絶対幅を入力する。

生成した値はTask Definitionの許容範囲と候補制約で検証する。
許容範囲を超える公差指定は実行前に拒否し、候補制約を満たさないサンプルは結果から除外する。
値を許容範囲へclipすると指定した分布とは別の分布になるため、clipは行わない。

ライン速度連動のヒートパターンでは、ライン速度を変えたときに各測定点の経過時間も設備位置に対応して変える。
どの入力が経過時間を逆比例で動かすかは、TaskDefinitionの
`response_curve_variables[].time_transform = "inverse_heat_time"` が正本である。
共通処理は特定の列名を知らない。

組成合計にbalance項目が宣言されているTaskでは、他成分の変動分だけbalance項目を再計算する。
宣言のない補正を推測して適用せず、balance項目そのものを公差対象にする指定も拒否する。

## 候補差分の要因分解

基準候補と比較候補の予測差を、入力ごとの寄与と残差に分ける。

寄与は、比較候補にその入力だけを基準候補の値へ戻したときの出力差である。
1入力ずつの局所的な置換であり、因果効果ではない。
寄与の合計と実際の差の残りは、入力どうしの交互作用として**残差へ明示する**。
残差を各入力へ按分しない。

置換した条件がTaskの制約を満たさない場合は実行を拒否する。値を制約内へ寄せない。
相違した入力が多い比較は、1入力あたり1回の追加予測が必要になるため上限で拒否する。
黙って一部の入力を切り捨てることはしない。

予測値の差とモデルの予測不確実性は別々に表示する。両者を合算した帯は作らない。
基準候補と比較候補それぞれのモデル支持状態も併記する。

## 目標へ届く最小変更

保存済み候補revisionを基準に、Project-level Design Space内の条件を評価する。
目標達成とhard outcome constraintを満たす案を、Design Space幅で正規化したL1距離、
カテゴリ変更ペナルティ、soft preference、モデル支持状態で順位付けする。

baseline strategyはseed付きSobol候補と一変数の座標線を評価する
`normalized-l1-sobol-v1` である。一変数で到達可能な境界は二分探索で詰める。
一般逆問題の厳密解やBayesian optimizationとは呼ばない。

- Task、組成合計、条件付き制約、Project Design Spaceを満たさない条件は除外し、clipしない
- 変更不可fieldと変更項目数上限を守る
- 支持範囲外の案は無警告で先頭にせず、supported、caution、extrapolatedの順に扱う
- 到達案がない場合は、特性ごとの最良値と不足量を返す
- 予測上の達成を実測の達成保証と呼ばない
- 各特性には判定に使ったcanonical Predictionを保存し、点予測による達成状態と予測区間を分けて表示する

旧Runにはcanonical Predictionが無い場合がある。その場合は再計算したように見せず、
保存結果では区間情報を利用できないことを明示する。

Activity Runは基準候補revision、Design Space／Objective／Package／Feature Pipeline、
strategy version、seedを固定する。実行だけでは候補を作らない。
利用者が結果内の一案を明示選択したときだけ通常Candidateを作り、
候補provenanceから元Runとproposalへ戻れるようにする。

## 二種類の区間

解析結果は、**入力ばらつき区間**と**モデル不確実性区間**を別々に保持する。

- **入力ばらつき区間**：公差サンプルに対する点予測の中央90%区間。
- **モデル不確実性区間**：基準候補についてruntimeが返した予測区間。

前者は製造条件の変動を表し、後者はモデルの予測に付随する不確実性を表す。
両者を一つの帯へ足し合わせるには、モデル区間の確率的な意味と入力分布を結合する契約が必要になるため、現行版では統合しない。

結果には、目標特性ごとの達成率、観測した最悪値、支持範囲外率、注意域率、代表的な未達条件も含める。
入力と出力の相関係数は、公差内で出力のばらつきと結び付きが強い入力を探す補助情報である。
局所サンプル内の相関から因果効果は判断しない。

## Design Spaceとの境界

現行の新規ProjectはProject-level Design Spaceを正本として保持する。
ロバストネス解析と目標到達案は、そのrevisionとdigestを実行時に固定する。
旧ProjectでDesign Spaceが未固定の場合は、Activityを利用可能に見せない。

## 保存と再現

実行結果はSQLiteへ不変のrunとして保存する。
同一性には、ProjectとTask契約、候補IDとrevision、正規化済み入力、Model Package、Feature Pipeline、アクティビティ版、パラメーター、seedを含める。
同じ同一性で再実行した場合は、保存済みrunを返す。

候補を編集するとrevisionが変わる。
旧revisionで開始した応答は新revisionの画面へ反映せず、保存済みrunの来歴は旧revisionを指し続ける。
runが参照する候補を削除した場合、候補は物理削除せずアーカイブする。
アクティビティの実行から候補や予測スナップショットを自動作成することはない。
目標到達案だけは、保存済みRunから選択したproposalを明示操作で通常Candidateへ昇格できる。
