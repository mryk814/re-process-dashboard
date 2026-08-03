# CALCE電池データのSourceから実測評価までの参照ループ

## この縦切りで確かめること

`battery-degradation-v1` を代表Taskとして、既存の境界を次の順で接続する。

```text
CALCE派生CSV（読取専用）
→ Raw Snapshot
→ Curation Run
→ Canonical Dataset Revisionの承認
→ Training Snapshot
→ 決定的な学習CSV
→ data-only Model Package
→ 固定参照を持つProject
→ Counterfactual Activityの提案を候補化
→ 保持していた実測値のActual登録
→ 予測対実測
```

これは汎用ETL、アプリ内学習、自動再学習、active Packageの自動切替ではない。
承認済みTraining SnapshotをProfile family materializer registry経由で既存のtabular
Package builder入力へ渡す代表受入経路である。CALCE Profileはbattery exact entryへ
解決し、source adapter identityが一致しない場合に汎用tabular entryへfallbackしない。

## データと責任境界

| 境界 | 責任・識別 |
|---|---|
| 公開データ所有者 | University of Maryland CALCE Battery Research Group |
| リポジトリ内派生物 | `data/source/external/battery_calce_cs2_cycles.csv` |
| 派生処理 | `backend/scripts/generators/prepare_calce_battery_dataset.py` |
| 更新方法 | CALCE公式ZIPのdigestを確認し、`artifacts/derived-data/`へ再生成して差分をレビューする。生成スクリプトは`data/source/`への書込みを拒否する |
| 一行の意味 | 一つの完全な放電サイクル |
| row identity | `cell_id\|source_file\|source_local_cycle`。再生成される通算`cycle_index`は識別子にしない |
| Profile | `calce-cs2-battery-capacity-v1` |
| Prediction Task | `battery-degradation-v1` |
| 正解値の戻し方 | 保持した一行の`capacity_percent`を、同じProject CandidateのActualとして登録する |

元データの出典、派生方法、解釈上の制約は
[`data/source/external/battery_README.md`](../../data/source/external/battery_README.md)
を参照する。
この受入では `CS2_35` の829行すべてをversioned Training Snapshot selection
policyで学習対象外にする。正常なholdoutを品質不良としてquarantineしない。
Raw Snapshotと承認済みCanonical Datasetは3,131行を保持し、
Training Snapshotは残る2,302行だけを固定する。
Activityはcycle 400の基準候補からcycle 300を提案し、
`CS2_35|CS2_35_10_22_10.xlsx|46` の実測を候補化後に戻す。
モデル精度の優劣ではなく、証拠の接続と再開性を確認するための固定例である。

## 複合row identityの扱い

Source Connector v1のupstream keyは一つのfieldを参照する。
CALCEの安定した一意性は三列の組で決まるため、source adapterが取得前に
`_source_row_key=cell_id|source_file|source_local_cycle` を決定的に作る。
adapterのIDとversionはConnector selectionへ保存され、
Connector configuration digestの一部になる。

この変換は元CSVへ書き戻さない。
Raw Snapshotには元fieldと合成keyを共に残し、Curation、承認、Training Snapshotは
同じkeyで一行を追跡する。

## Packageへ固定するprovenance

Package manifestの `provenance.source_lifecycle` は次を型付きで保持する。

- Connector IDとconfiguration digest
- Raw Snapshot IDとdigest
- Recipe IDとdigest
- Curation Run IDとdigest
- Profile Revision IDとdigest
- Canonical Dataset Revision IDとdigest
- Training Snapshot IDとdigest
- Training Snapshot selection policy digest
- source adapterとmaterialization adapterのID／version
- materialized training CSVのSHA-256とrow count

`training_data_id` はmaterialized CSVのSHA-256を引き続き表す。
ProjectはDataset View Revision、Model Package Ref、Package manifest digestを固定する。
Dataset View memberにも同じSource Lifecycle provenanceを保存するため、Projectから
Dataset側とPackage側の両方を照合できる。

予測Snapshotの `model_meta.source_lifecycle` にも同じ値を保存する。
Actual登録後の予測対実測は、実測登録時に固定した予測とこのprovenanceを返し、
最新Packageで再計算しない。

## 実行

リポジトリ直下で、隔離された作業先を指定して実行する。

```powershell
uv run --extra dev python backend/scripts/acceptance/reference_data_loop_acceptance.py `
  --workspace artifacts/reference-data-loop
```

結果は `<workspace>/acceptance-report.json` に出る。
`data/source/`、checked-in Package、active Package設定は変更しない。

同じコマンドをもう一度実行すると、同じ意味の資産を再利用する。
受入テストは二回の実行結果が完全一致し、Raw Snapshot、Curation Run、
Training Snapshot、Decision Activity Run、Prediction Snapshot、Actualの件数が
増えないことを確認する。

## 再開点

各段階は直前の不変ID・digestから再開する。

| 失敗位置 | 再開 |
|---|---|
| fetch後 | 同じ内容なら既存Raw Snapshotを再利用する |
| curation／承認後 | 同じdigestのRun／Revisionを再利用する |
| Training Snapshot後 | Snapshot IDから承認済みrowを再解決する |
| materialize後 | 同じbytesならCSVを再利用し、異なる既存fileは拒否する |
| Package build後 | Snapshot digestとmaterialized SHA由来のPackage IDを検証して再利用する |
| Project／Candidate後 | 固定参照と入力が一致する既存checkpointを再利用する |
| Activity後 | semantic identityが同じRunを再利用する |
| Actual後 | experiment numberとpropertyが一致する既存Actualと固定Snapshotを再利用する |

別内容を同じPackage directoryやmaterialized CSVへ上書きする経路は持たない。
materialization adapter version、Training Snapshot digest、materialized SHA、
training builder revisionのいずれかが変われば新しい保存先とPackage identityになる。
adapterやbuilderの挙動を変える変更では、対応するversion／revisionを必ず更新する。
Profile RevisionとTaskを明示解決し、Snapshotのtarget cohort／split assignmentは
materialization resultへそのまま引き渡す。
