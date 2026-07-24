# 外部データをアプリのPrediction Taskにする

`prediction_insight_data_starter_pack` から、性格の異なる4件を選び、起動時に利用できるTask・Dataset・Model Packageとして同梱している。

| Task | データ | 学習単位 | 出力 | 画面で確認すること |
|---|---|---|---|---|
| 熱処理の硬さ・靭性 | 2,400条件 | 独立した条件行 | 硬さ、Charpy | 多目的のトレードオフと入力別の応答曲線 |
| コンクリート圧縮強度 | 1,600配合 | 独立した配合・材齢行 | 圧縮強度 | 材齢、配合量に対する応答曲線 |
| 工具摩耗曲線 | 240 run × 61点 | run内測定点 | VB摩耗量 | runを跨がない検証と切削距離に沿う曲線 |
| 電池容量劣化 | 90 cell × 101点 | cell内サイクル点 | 容量維持率 | cellを跨がない検証、容量劣化曲線、元データの不自然さ |

いずれもデモ用の合成データで、モデル精度の優劣を示すものではない。目的は、外部の表形式データが次の境界を通ることを確認することにある。

この3件はスターターパックを所有する開発者のローカル評価用として同梱する。公開インストーラーや第三者配布物へ含める前には、スターターパック側で再配布ライセンスを明示し、帰属表示を確定すること。現時点の同梱を再配布許諾の根拠にはしない。

1. CSV列の意味を `tabular-dataset-profile/v1` に宣言する。
2. TaskDefinitionで候補入力、目標特性、許容範囲、応答曲線変数を定義する。
3. Profileから共通の特徴量パイプラインを構成する。数値は原則一次・二次項、材齢のような飽和的な軸はProfile指定の`log1p`、区分はone-hotとする。
4. 親条件またはrunを持つデータはその単位でfoldを分け、独立行データは行単位で分ける。基本モデルは正則化回帰、電池はサイクル数に減少単調制約を持つLightGBMとし、out-of-fold残差から予測区間を校正する。
5. Pythonコードを含まない `builtin.linear.v1` Model Packageとして保存する。
6. 起動時にDataset、Profile、PackageをData Libraryへ登録し、サンプルProjectを作る。

## 電池データを「汚さの例」として残す

電池データは、きれいに直してから学習する例にはしていない。元CSVを集計すると、`capacity_ah < 0`が4,577/9,090行、`capacity_percent == 45`への張り付きが5,864/9,090行ある。後者は打ち切り・クリップ・測定限界の可能性が高く、このままでは寿命曲線の後半と80%到達時期の解釈が歪む。

アプリの「データ探索 → 問題から探す」では、この2点をProfileの品質規則から検出する。一方、行を暗黙には除外しない。Packageは全行を使い、`cell_id`単位で検証を分け、サイクル数に減少単調制約を持つLightGBMとして作る。品質問題と予測を並べて「このモデルを判断に使ってよいか」を確認できるようにしている。実運用では、元データ責任者と意味を確認してから、除外・打ち切りモデル・観測モデルのどれを採るかを別版のProfile/Packageとして固定する。

## 初回生成と再学習

次はPackageがまだ存在しない初回生成だけに使う。

```powershell
$env:PYTHONPATH = "backend/src"
uv run python backend/scripts/build_external_tabular_packages.py
```

1タスクだけならtask idを指定する。

```powershell
uv run python backend/scripts/build_external_tabular_packages.py wear-curve-v1
```

生成時に、元データSHA-256、Profile digest、Task入力契約digest、正規化後の学習表digest、smoke predictionをmanifestへ固定する。アプリ起動時にこれらが合わなければPackageは利用されない。

学習データ、Profile、特徴量、学習実装のどれかを変更する再学習では、既存Packageを上書きしない。Profileの`package_id`、manifestの`package_version`、出力ディレクトリを新しい版へ進め、`available-packages.json`へ追加して検証後にactiveを切り替える。既存Projectは旧manifest digestと旧locatorを固定しているため、同じディレクトリを`--replace`すると再現性が壊れる。

## 選ばなかったデータ

- NASA Millは測定VBと補間VBを明確に分ける必要があり、再配布ライセンスも同梱物だけでは確認できないため保留した。
- VicomtechはCC BY-SAの帰属表示が必要で、センサ値を編集可能な設計条件ではなく観測Evidenceとして扱う画面契約を先に作る必要がある。

スターターパック由来の合成データを外部へ再配布する場合は、元リポジトリ側でライセンスを明示してから行う。
