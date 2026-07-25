# 外部データをPrediction Taskへ接続する

表形式データをPrediction Taskにするときは、Dataset、Profile、TaskDefinition、Model Packageを分けて固定します。
新しいDatasetを登録しただけでは予測可能にしません。

## 同梱タスク

| Task | データ | 学習単位 | 出力 | 用途 |
|---|---|---|---|---|
| 熱処理の硬さと靭性 | 合成2,400条件 | 独立条件 | 硬さ、Charpy | 多目的のトレードオフ |
| コンクリート圧縮強度 | 合成1,600配合 | 配合と材齢 | 圧縮強度 | 材齢と配合量の応答曲線 |
| 工具摩耗曲線 | 合成240 run | run内測定点 | VB摩耗量 | runを跨がない検証 |
| 電池容量劣化 | CALCE実測4 cell、3,131 cycle | cell内サイクル | 容量維持率 | 実測劣化曲線とセル単位検証 |
| SECOM工程異常 | UCI実測1,567件、590 sensor | 独立測定行 | fail確率 | 欠損・高次元・クラス不均衡を含む分類 |

前3件はアプリ境界を確認する合成データです。
電池とSECOMは公開実測データから派生しています。

## CALCE電池データ

電池タスクはCS2-33からCS2-36までの4セルを使います。
学習行は完全な放電サイクルで、検証foldはセル単位です。
同一セルの前半と後半をtrain/testへ分けないため、系列の記憶を精度と誤認しません。

入力へ残したのは放電レートと通算サイクル数です。
周囲温度やセル化学種は候補ごとに変えられる情報として確定できないため、旧合成タスクから削除しました。

モデルはサイクル数に減少単調制約を持つLightGBMです。
予測区間はセル単位out-of-fold残差から校正するため、入力位置ごとの潜在不確かさを表すGPの区間とは意味が異なります。

セル数が4しかなく、0.5Cと1Cに各2セルです。
放電レート差を因果効果として解釈することはできません。

## SECOM工程異常分類

SECOMはpass / failを目的変数とする二値分類Taskです。

元の590センサすべてを候補フォームへ出すと操作不能になるため、外側の層化foldごとにLightGBMのgain上位12列を選び、fold間の選択頻度とgainで代表12センサを固定しました。

選択を含むnested out-of-fold ROC AUCは約0.685です。
代表12センサを固定した後のCVは約0.798ですが、選択後の評価なので性能値としては前者を参照します。

最終モデルは層化5-foldのout-of-fold予測を使ってPlatt calibrationを行い、画面には回帰値ではなくfail確率を表示します。

99行は代表センサの欠損により学習対象外ですが、元CSVから削除せず品質確認に残します。

センサ名は匿名です。
応答曲線はモデルの関連を調べる道具であり、工程因果の根拠にはしません。

`docs/reports/secom-stress-diagnostic.json`と`docs/reports/secom-sensor-selection.json`は、公式ファイルから再生成できる検査結果です。

## 再生成

CALCEの公式ZIPを一時ディレクトリへ配置して、派生CSVを作ります。

```powershell
uv run python backend/scripts/prepare_calce_battery_dataset.py `
  --raw-root C:\path\to\calce-zips
```

UCIの`secom.data`と`secom_labels.data`を展開して、診断用CSVを作ります。

```powershell
uv run python backend/scripts/prepare_secom_stress_dataset.py `
  --raw-root C:\path\to\secom
```

代表センサ選択を再現します。

```powershell
uv run python backend/scripts/analyze_secom_sensor_selection.py
```

電池またはSECOM Packageを再学習するときは、LightGBM runtimeを有効にします。

```powershell
uv run --extra runtime-lightgbm python `
  backend/scripts/build_external_tabular_packages.py battery-degradation-v1
```

```powershell
uv run --extra runtime-lightgbm python `
  backend/scripts/build_external_tabular_packages.py secom-yield-risk-v1
```

生成時に、元データSHA-256、Profile digest、入力契約digest、学習表digest、smoke predictionをmanifestへ固定します。
