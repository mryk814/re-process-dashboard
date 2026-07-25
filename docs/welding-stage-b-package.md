# 溶接材料 Stage B Task / Package

`welding-consumable-stage-b-v1` は、材料成分と溶接contextから実測相当の
溶着金属16成分を予測する単独Taskです。合成データの精度競争ではなく、
多段Chainの中間教師データを正しく扱えることを確認するPackageです。

## 学習行の正本

学習単位は `溶着金属成分` の300観測です。
`relationEx` の3,403行は上流キーを解決する索引にだけ使います。
同じ溶着金属分析を参照する下流試験が増えても、Stage Bの学習行は増えません。
同一分析について上流の配合・フープ・溶接条件・溶接施工が矛盾する場合は、
任意の1件を選ばず、その観測を除外します。

読取規則の正本は
`backend/src/material_workbench/data/welding-stage-b-profile-v1.json`
です。Excelのシート名、キー列、成分列、単位、categorical choicesは
コードへ埋め込まず、このProfileで固定します。
Projectが固定したProfile Revisionはresolverからcompilerまでそのまま渡します。
同梱Profileへ読み替えません。Data Libraryへの登録前検証も同じcompilerを使うため、
画面で確認したmappingとruntimeのmappingが一致します。

## 入出力と分割

- 入力：材料成分31軸（mass% whole wire）
- 入力：入熱、電圧、シールドガス、ガス流量、溶接姿勢
- 入力：合金鉄・純金属粉・脱酸剤の配合比加重D50
- 出力：C, Si, Mn, P, S, Ni, Cr, Mo, Cu, Ti, B, Nb, V, Al, N, O

Profileは31入力軸と16出力軸の順序、入力basis
`mass% whole wire`、出力basis `mass% deposited metal` を契約として持ちます。
軸の削除・置換・並べ替え、basis変更はProfile読込時に拒否します。

欠測targetは入力行全体を捨てず、targetごとに利用cohortを作ります。
評価は `溶接施工_key**` をgroupにした5-foldです。同じ施工を
train/testへ跨がせません。Profileから一度だけ作ったfold割当を学習・評価・
digestで共用し、空の施工keyは学習対象にしません。Packageはtargetごとに
profile、transform、cohort、foldのdigestと実際のfold割当を保持します。

## Stage Aとの統合点

現在のStage B source compilerは、元ExcelのProfile mappingからStage Aと同じ
whole-wire材料成分を決定論的に再構成します。これは学習データ作成の境界です。
Chain実行時は、この計算を再利用して二重実装にせず、
Issue #160のStage A Package出力を `Candidate.inputs.composition` へbindingします。
Stage BのTask、feature pipeline、Packageはその31軸だけを読み、
原料IDや疎な配合明細を直接特徴量にしません。

## 再生成と確認

```powershell
$env:PYTHONPATH = "backend/src"
uv run python backend/scripts/build_welding_stage_b_assets.py --replace
uv run python backend/scripts/model_workflow.py verify --task welding-consumable-stage-b-v1 --package models/packages/welding-consumable-stage-b-ridge-v1
```

Developer Centerの「学習View」では、Stage Bを選ぶとtargetごとの
利用行数・欠測理由・施工group数と観測行を確認できます。
Taskは `actual_measurement` を宣言しており、候補画面で固定予測と
溶着金属の実測を照合できます。
