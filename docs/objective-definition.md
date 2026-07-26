# Objective Definition

Objective Definitionは「何を良いと判断するか」を固定する不変契約です。
作成可能な入力を定めるProject Design Space、候補を生成・評価するProposal Strategyとは分離します。

## 判断の意味

- `primary_objective`: 改善または目標到達を順位付けする主目的
- `hard_outcome_constraint`: 満たさない案を有望と扱わない必須条件
- `soft_preference`: 重みと正規化範囲を明示した選好（契約のみ）
- `reporting_only`: 判断には使わず結果へ併記するoutput

`single_objective`、`constrained_single_objective`、`pareto_multi_objective`を別の意味として保存します。
重み付き和をPareto最適化とは呼びません。
目標未設定でsupportだけを見ていた旧範囲探索は`legacy_screening`として保持し、Objective達成とは表示しません。

現在のproduction Proposal engineが実行するのは、主目的1件と
`at_least`／`at_most`／`between`のhard outcome constraintです。
`soft_preference`、Pareto、`maximize`／`minimize`／`target`は契約として保存できますが、
対応するallow-list acquisitionがないためProposal実行時に明示的に拒否します。

## Identityと互換性

- `objective_id`、`revision`、全定義のsemantic digestを固定する
- output key、unit、Taskの科学的方向、Runtimeの予測能力を保存前に検証する
- incumbentは`candidate_revision`または`prediction_snapshot`等のsourceと不変参照を持つ
- `observed_best`はProject内の実測だけを母集団とし、output keyとunitで絞る
- Projectの目標から生成したObjective、明示指定、前の検討からの継承を区別する
- 既存Projectへ推測したObjectiveを補完せず、`unbound_legacy`として読む

Projectの目標を変更するとObjective revisionを進め、旧定義を`project_objective_revisions`へ不変保存します。
履歴は`GET /api/projects/{project_id}/objectives`からdigest付きで再取得できます。
Design SpaceやTask境界を変える場合は「このプロジェクトの続き」として新しいProjectへ固定します。
Screening Runは実行時のObjective本体、revision、digest、binding provenanceを保存し、後続変更で再評価しません。
さらに、engineへ変換した主対象・方向・hard constraintと、incumbentの値・sourceを保存します。
`observed_best`ではfilter digest、母集団digest、件数、選択した実測／候補／snapshot IDを固定します。
手入力値は`request_override`として自動解決と区別します。

## Legacy screening boundary

従来の`target_goal`と`secondary_goals`は引き続き受け付けます。
明示Objective、Projectに固定したObjective、従来fieldから生成したObjectiveの順で実行正本を選びます。
従来fieldはObjectiveがない場合だけ型付きObjectiveへ変換し、`legacy_screening`由来としてRunへ固定します。
実際のProposal計算へ渡す主目標・副条件は選択したObjectiveから毎回導出するため、
古いscreening fieldがObjectiveと異なっていても計算意味は二重化しません。
