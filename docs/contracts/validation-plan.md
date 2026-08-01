# Validation Plan

`validation-plan/v1` は、標準Model Packageが「どの行を同じ評価単位として扱い、どの方向へ情報を流さないか」を固定するallow-list契約です。
任意のPython callbackや自動split選択は受け付けません。

## 正本と参照関係

- 型と割当: `backend/src/decision_workbench/modeling/training/validation_plan.py`
- Training Recipe: sharedな`validation_plan`、またはtarget別の`validation_plans_by_target`
- Package provenance: `reference/training-recipe.json`の`evaluation`
- Quality evidence: `reports/quality-report.json`の`validation_plans`と`validation_diagnostics`

既存Packageが持つfold digestは読み替えません。
Validation Planを明示しない既存authoring経路は、従来と同じparent group・seed・fold割当から同じfold digestを作り、新しいPackageだけがplan本体とplan digestを追加で記録します。
明示した新planのfold digestはplan digestも含むため、旧digestと同じ意味に見せません。

## P0 strategy

| strategy | validation role | 防ぐ漏洩 |
| --- | --- | --- |
| `kfold` | 一意な行 | 通常の独立row評価 |
| `grouped_kfold` | `parent_key`等のgroup role | 同一材料・装置・cell・runの跨り |
| `stratified_kfold` | binary target | class不足 |
| `stratified_grouped_kfold` | group roleとbinary target | group跨りとclass不足 |
| `temporal_holdout` | 明示的な数値time role | 未来から過去への漏洩 |
| `grouped_temporal` | group roleとtime role | group跨りと未来情報の両方 |

Dataset row orderそのものは時刻として扱いません。
temporal strategyは`time_key`が各training contextで一つの有限値へ解決できない場合に停止します。
grouped temporal holdoutで同じgroupがtrainとholdoutへ跨る場合も停止します。

## build前診断

明示planは要求fold数を黙って減らしません。
group数、class数、time role、minimum train size、gapを割当前に検査し、不成立ならPackage staging中に失敗して部分Packageを残しません。
targetごとにcohortが違う場合は各targetで別のassignment、fold digest、plan evidenceを発行します。

Feature Pipelineは学習前に固定され、canonical featureは有限値を要求するため、標準builderに学習時imputationやfeature selectionはありません。
Ridge／Exact GPのstandardizationと各estimatorのcalibrationは既存のouter／inner training fold内fitを維持し、honest evaluation後に全target cohortでfinal modelをfitします。

## 非スコープ

- rolling originとfull backtesting
- 任意callback
- 自動的な最良split選択
- row orderを時刻とみなすfallback
- online learning、causal identification
