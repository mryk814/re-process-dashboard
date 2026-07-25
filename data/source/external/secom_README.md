# UCI SECOM実測分類データ

`secom_stress.csv`は、UCI Machine Learning RepositoryのSECOMデータを列名付きCSVへ変換したものです。

## 出典とライセンス

- データセット：https://archive.ics.uci.edu/dataset/179/secom
- DOI：10.24432/C54305
- 作成者：Michael McCann、Adrian Johnston
- ライセンス：CC BY 4.0

出典表示を維持したうえで、共有および改変できます。

## アプリ内の位置付け

SECOMは、製造センサからpass / failを推定する二値分類Taskです。

元データは1,567行、実ファイル上590センサ列、多数の欠損、116の定数列、6.6%のfailを含みます。

UCIのページは591 featuresと記載していますが、配布された`secom.data`の各行は590値です。

変換では実ファイルを正本とし、この差を診断レポートへ残しています。

候補フォームへ590列を並べず、層化fold内の選択で繰り返し採用された12センサだけを表示します。

モデルはLightGBM二値分類を層化5-foldで検証し、out-of-fold予測でPlatt calibrationを行います。

匿名センサの関連を因果効果としては解釈しません。

再生成手順と診断は、次のファイルにあります。

- `backend/scripts/prepare_secom_stress_dataset.py`
- `backend/scripts/analyze_secom_sensor_selection.py`
- `docs/reports/secom-stress-diagnostic.json`
- `docs/reports/secom-sensor-selection.json`
