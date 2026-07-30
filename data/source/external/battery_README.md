# CALCE CS2電池容量劣化データ

`battery_calce_cs2_cycles.csv` は、University of Maryland CALCE Battery Research Groupが公開するCS2セルの実測ログから作成した派生データです。

## 出典

- 公開ページ：https://calce.umd.edu/data
- 対象セル：CS2-33、CS2-34、CS2-35、CS2-36
- セル仕様：LiCoO2系、定格1.1 Ahのprismatic cell
- 充電条件：0.5CのCCCV
- 放電条件：CS2-33とCS2-34は0.5C、CS2-35とCS2-36は1C

CALCEは実験データをopen accessとして提供し、出版物で利用する場合は対応論文の引用を求めています。
再利用時は公開ページの最新条件を確認してください。

## 派生方法

学習行は、一つの完全な放電サイクルです。
Arbinログの累積`Discharge_Capacity(Ah)`について、同一workbook内の隣接サイクル間の増分を各サイクルの容量としました。
0.5 Ah未満または1.35 Ah超の増分は、不完全サイクルまたは境界レコードとして派生表から除いています。

容量維持率の基準は、セルごとに保持された最初の5サイクルの容量中央値です。
workbookはセル内でSHA-256により重複排除し、観測日時順に通算サイクル番号を付けています。

再生成手順と元ZIPのdigestは、`backend/scripts/generators/prepare_calce_battery_dataset.py`
および`docs/reports/battery-calce-cs2-derivation.json`にあります。
スクリプトの既定出力は`artifacts/derived-data/`であり、`data/source/`配下への
書込みは拒否されます。再生成物を正本へ採用するときは、row identity・件数・digest・
派生レポートをレビューした上で、スクリプトとは別の明示的な変更として扱います。

各行の安定した識別子は
`cell_id + source_file + source_local_cycle`です。
観測日時順の`cycle_index`はモデル入力用の通算座標であり、
データ更新で振り直され得るためrow identityには使用しません。

## 解釈上の制約

セル数は4です。
放電レートごとに2セルしかないため、放電レート効果とセル固有差を完全には分離できません。
このPackageは精度の基準ではなく、実測系列をセル単位で分割し、外挿範囲と不確かさを確認する教材です。
