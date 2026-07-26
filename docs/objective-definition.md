# Objective Definition

Objective Definitionは「何を良いと判断するか」を固定する不変契約です。
作成可能な入力を定めるProject Design Space、候補を生成・評価するProposal Strategyとは分離します。

## 判断の意味

- `primary_objective`: 改善または目標到達を順位付けする主目的
- `hard_outcome_constraint`: 満たさない案を有望と扱わない必須条件
- `soft_preference`: 重みと正規化範囲を明示した選好
- `reporting_only`: 判断には使わず結果へ併記するoutput

`single_objective`、`constrained_single_objective`、`pareto_multi_objective`を別の意味として保存します。
重み付き和をPareto最適化とは呼びません。
目標未設定でsupportだけを見ていた旧範囲探索は`legacy_screening`として保持し、Objective達成とは表示しません。

## Identityと互換性

- `objective_id`、`revision`、全定義のsemantic digestを固定する
- output key、unit、Taskの科学的方向、Runtimeの予測能力を保存前に検証する
- incumbentは`candidate_revision`または`prediction_snapshot`等のsourceと不変参照を持つ
- Projectの目標から生成したObjective、明示指定、前の検討からの継承を区別する
- 既存Projectへ推測したObjectiveを補完せず、`unbound_legacy`として読む

Projectの目標を変更するとObjective revisionを進め、旧定義を`project_objective_revisions`へ不変保存します。
履歴は`GET /api/projects/{project_id}/objectives`からdigest付きで再取得できます。
Design SpaceやTask境界を変える場合は「このプロジェクトの続き」として新しいProjectへ固定します。
Screening Runは実行時のObjective本体、revision、digest、binding provenanceを保存し、後続変更で再評価しません。

## Legacy screening boundary

従来の`target_goal`と`secondary_goals`は引き続き受け付けます。
実行時に型付きObjectiveへ変換し、`legacy_screening`由来としてRunへ固定します。
明示Objectiveを同時指定した場合は、現在の範囲探索が評価する主目標・副条件と完全に一致する場合だけ実行します。
