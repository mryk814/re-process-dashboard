<!--
status: accepted-experiment
owner: modeling
scope: split conformal regression wrapper
-->

# Conformal prediction interval wrapper

## Decision

Point predictorに予測分布、標準偏差、目標達成確率を後付けしない。
split conformal regressionは、既存のModel Packageを変更しないdata-only wrapperとして扱う。

wrapperはbase Package ID／manifest digest、predictor ID、target／unit、feature pipeline identity、calibration Dataset View／Training Snapshot、split・group policy、score ID、finite-sample rule、alpha、calibration score artifactのhash・score count・有限性、build revisionを固定する。
held-out qualityには評価Dataset digest、marginal coverage、幅、group別診断、calibration件数とsmall-sample warning、base point metricを残す。evaluation Datasetはcalibration Datasetと同一にできない。

P0のscoreは`absolute_residual/v1`、ruleは`ceil_n_plus_1_over_coverage/v1`、連続targetの対称区間だけである。wrapperはbase Package digestまたはfeature pipeline identityが異なれば拒否する。

## Capability and prediction semantics

`PredictiveSummary.prediction_interval`はmethodを明示する。conformalはcalibration evidenceを必須にし、quantile、parametric、Bayesian intervalとは同じfield shapeでも区別する。

conformal wrapperが追加するcapabilityは`conformal_interval`だけであり、capability matrixにはwrapper ID／version／manifest digestをlayer identityとして残す。`standard_deviation`、`predictive_samples`、`parametric_distribution`、`goal_probability`、`quantiles`はbase runtimeの宣言を変更しない。したがってUCB／EIなど標準偏差を要する戦略は理由付きで利用不可のままであり、区間だけからCDFや個別候補の確率を推定しない。

## Adopted evidence

- fixture point Packageに対してsafe JSON calibration artifactをhash・size・有限値検証してwrapperを構築できる
- finite-sample order statisticから明示的な`conformal` intervalを返せる
- base manifest digest、feature pipeline identity、held-out/calibration Dataset分離の破綻を拒否できる
- capability matrixではconformal intervalだけを公開し、std／probabilityを公開しない

## Not adopted

- active Packageの自動切替、既存Project／Run／Snapshotの再計算
- CQR、online/adaptive conformal、classification conformal
- 任意Python callback、pickle、joblib、user-defined score function
- production UIの表示。実際にwrapperをactive Packageとして採用するIssueで、`Conformal予測区間`、level、calibration source／件数、support外warningを画面に追加する

## Verification

`backend/tests/test_conformal_intervals.py` はwrapperのbase binding、artifact検証、finite-sample interval、capability不足を確認する。Package実体やruntime adapterは変更していないため、release acceptanceは関連Packageを実際に採用する節目まで実施しない。
