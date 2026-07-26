# 複数出力で共有するモデル成果物

| 項目 | 内容 |
|---|---|
| 状態 | I/Oパターンは維持、shared multi-output GP runtime／Packageは見送り |
| 最終判断日 | 2026-07-26 |
| 再評価 | [再生成可能な比較レポート](../reports/shared-multi-output-gp-evaluation.md) |
| 関連 | Issue #195、旧Draft PR #44 |

## 最終判断

一つの結合model artifactを複数targetのPredictorSpecからtarget index付きで参照するI/Oパターンは、将来の実装候補として維持する。
ただし、現行の焼鈍データと契約ではshared multi-output GPを採用しない。

次は追加しない。

- `builtin.multitask_gp.v1` 等のruntime adapter
- shared GP Model Package
- active Package設定
- ProjectやSnapshotの互換経路

旧Draft PR #44のコードや当時の品質値は移植せず、今回の採否根拠にも使っていない。

## 現行mainからの再評価

`backend/scripts/evaluate_shared_multioutput_gp.py` が、現行の
`data/source/material_workbench_process_v1.xlsx` とFeature Pipelineから比較を再生成する。

比較条件は次の通り。

- TS、YS、EL、lambdaのすべてを持つ92 parent conditions
- parent condition meanを一つの学習行とする
- 5-fold deterministic grouped split
- fold内だけで入力・出力を標準化する
- SingleはtargetごとにRBF長さとnoiseを選ぶ
- Sharedはfold内target相関をidentityへ25% shrinkした正定値coregionalizationを使う
- MAE、RMSE、50/80/90/95% coverage、calibration errorを同じholdoutで計算する
- 合成デモデータのため、相関を材料現象の因果的根拠と解釈しない

主解析では、意図的に含めた物理範囲外の1 parent conditionがRMSEを支配し、SingleとSharedの差をほぼ隠した。
そのrowを主解析から黙って削除せず、TaskDefinitionのplausibility rangeを使った副解析を別に示した。

副解析ではSharedのRMSEがSingleに対して次のように悪化した。

| Target | Shared RMSE変化 |
|---|---:|
| TS | +19.9% |
| YS | +17.2% |
| EL | +11.5% |
| lambda | +21.6% |

平均calibration errorはSingleの0.045に対してSharedは0.162である。
全targetで強い相関が見えるが、その相関を共有してもholdout判断性能は改善せず、負の転移が起きた。

候補artifactはSingle合計約310 KBに対してShared約1.12 MB、今回の同一runでの100件推論は約8 msに対して約288 msだった。
この値は性能保証ではなく同一run内の比較だが、品質改善なしに複雑さと費用だけが増える方向を示す。

## 採否ルール

次を両方のcohortで満たす場合だけ採用するルールを事前に固定した。

- 4 target中3 target以上でRMSEが2%以上改善
- RMSEまたはcalibration errorが3%相当以上悪化するtargetがない
- 平均calibration errorが0.02を超えて悪化しない

今回、改善targetは0で、plausibility-clean副解析では全targetに負の転移があるため見送る。
一部targetだけを共有する採否単位も検討したが、今回の組合せには採用できるsubsetがない。

実験上のShared GPはtarget間共分散を持つjoint normalを計算するが、採用しない実験結果をproductionのjoint prediction capabilityとして公開しない。現在のRuntime Capability、target別PredictiveSummary、保存済みSnapshotの意味は変更しない。

## 再検討条件

次のいずれかが揃った場合は、新しいIssueで再評価する。

- 実データで、target間の共有がholdout予測に寄与する根拠がある
- complete cohortを不自然に狭めず、部分欠損を明示的に扱える
- target subsetまたは低rank構造が、負の転移を避けて事前採否ルールを満たす
- joint sampleが実際のDecision Activityに必要になる

採用する場合も旧PRをrebase／cherry-pickしない。
現行mainのadapter／Model Package契約から新規実装し、次を必須とする。

- 上限付きsafe NPZ
- 精度・共分散行列の正定値検証
- 負の予測分散の明示エラー
- artifact内target順序とmanifest target／indexの完全照合
- Package digest＋artifact digestに固定した共有cache
- target別PredictiveSummary、品質、来歴、Snapshot不変性
- 新Packageを自動active化しないこと
