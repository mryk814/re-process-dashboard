# Shared multi-output GP 再評価

> 合成デモデータによるアプリ・モデル契約の評価であり、材料現象の因果的根拠ではない。

- source SHA-256: `87e2321d5ce1071c9fe57bea95ed773609a88d63c7d5cc08bd0a15592634bef9`
- cohort: 92 parent conditions
- split: 5-fold deterministic grouped by parent condition
- 最終判断: **見送り**

## Complete cohortのtarget相関

| Target | TS | YS | EL | lambda |
|---|---:|---:|---:|---:|
| TS | +1.000 | +0.999 | -0.998 | -0.999 |
| YS | +0.999 | +1.000 | -0.999 | -1.000 |
| EL | -0.998 | -0.999 | +1.000 | +0.999 |
| lambda | -0.999 | -1.000 | +0.999 | +1.000 |

強い相関は合成デモ生成過程の特徴であり、共有modelの採用根拠にはしない。

## Target別比較

| Target | Model | MAE | RMSE | 90% coverage | Calibration error |
|---|---|---:|---:|---:|---:|
| TS | Single | 79.0114 | 496.2150 | 0.978 | 0.164 |
| TS | Shared | 80.6270 | 496.4041 | 0.967 | 0.147 |
| YS | Single | 57.4531 | 368.3590 | 0.989 | 0.169 |
| YS | Shared | 58.8791 | 368.5087 | 0.967 | 0.153 |
| EL | Single | 3.4236 | 19.8833 | 0.978 | 0.147 |
| EL | Shared | 3.4712 | 19.8874 | 0.957 | 0.123 |
| lambda | Single | 9.5242 | 60.8959 | 0.978 | 0.161 |
| lambda | Shared | 9.7401 | 60.9195 | 0.957 | 0.139 |

## Plausibility-clean sensitivity

TaskDefinitionの物理範囲外を含む 1 parent conditionsを副解析だけから除外した。主解析からは削除していない。

| Target | Single RMSE | Shared RMSE | Shared変化 |
|---|---:|---:|---:|
| TS | 38.4547 | 46.1017 | +19.9% |
| YS | 24.5179 | 28.7353 | +17.2% |
| EL | 1.6924 | 1.8868 | +11.5% |
| lambda | 3.7647 | 4.5775 | +21.6% |

## 負の転移

| Target | RMSE変化 | Calibration error変化 | 判定 |
|---|---:|---:|---|
| TS | +0.0% | -0.016 | なし |
| YS | +0.0% | -0.016 | なし |
| EL | +0.0% | -0.024 | なし |
| lambda | +0.0% | -0.022 | なし |

## 成果物と性能

| Model | Artifact bytes | Load ms | Batch 100 inference ms |
|---|---:|---:|---:|
| Single | 309,900 | 1.052 | 7.844 |
| Shared | 1,118,864 | 1.098 | 287.578 |

## 判断ルール

adopt only when at least 3/4 targets improve RMSE by >=2%, no target has >3% RMSE or calibration degradation, and mean calibration error is not >0.02 worse in both the primary and plausibility-clean sensitivity cohorts

Runtime／Model Packageを採用しない判断の場合、adapter、Package、active設定は追加しない。
