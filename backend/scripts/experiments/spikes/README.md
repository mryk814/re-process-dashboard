# 拡張性の反証スパイク

[docs/architecture/extensibility-spikes.md](../../../../docs/architecture/extensibility-spikes.md) の
実測値を再現するためのスクリプトです。**アプリ本体でもテストでもありません。**
`npm run test` / `uv run python -m pytest` からは実行されません。

| ケース | スクリプト | 内容 |
| --- | --- | --- |
| A | `spike_case_a.py` | 標準表形式Taskをfixtureで登録し、既存アプリ機能をAPI経由で通す |
| B | `spike_case_b.py` | 溶接語彙なしの3シート構成をObservation Profileで表現し、Training Viewまで通す |
| C | `spike_case_c.py` | 可変長系列を現行契約で表現できないことを7項目で確認する（読み取りのみ） |
| D | `spike_case_d.py` | 疎配合なしの二段Chainを組み、Chain Coreがどこで塞がるかを確認する |
| E | `spike_case_e.py` | 同じ列構成で行だけ違うsourceへ差し替え、契約とコードの変更が不要かを確認する |

現在 A / B / D / E は全項目OK、Cは「現行契約で表現できない」ことの確認なので7項目NGのままが正常です。

## 実行

リポジトリルートから実行します。

```bash
uv run python backend/scripts/experiments/spikes/spike_case_a.py
```

- 成果物（fixture CSV / xlsx、Profile、Package、SQLite）は
  `%TEMP%/material-workbench-spikes/<case>/` へ書きます。`SPIKE_WORK_DIR` で変更できます。
- ケースA / Dは**一時的に** `backend/src/material_workbench/tasks/task_definitions/` へ
  spike用のTaskDefinition JSONを置きます。`finally` で必ず削除します。
- ケースEは既存Taskの `source_env` を一時的に上書きして起動します（`finally` で戻します）。
- 元データ（`data/source/`）、`models/active-packages.json`、既存のTaskDefinitionは変更しません。
  ケースEは差し替え後に、契約とコードのdigestが変わっていないことを自分で検証します。

## 中断した場合

ケースA / Dを強制終了（プロセスkill）した場合、`task_definitions/` に
`spike-*.json` が残る可能性があります。残っていると起動時に
`TaskModule registry must exactly match task definitions` で**明示的に失敗**します
（黙って壊れることはありません）。次で削除してください。

```bash
rm -f backend/src/material_workbench/tasks/task_definitions/spike-*.json
```

## なぜ本番ディレクトリへ置く必要があるのか

ケースAの実測結果です。Package構築は登録の後でしか動きません。

- `tabular_model_builder.build_tabular_package_from_data` が `load_task_contracts()` を
  contract root注入なしで呼ぶ
- `model_lifecycle.canonical_training_dataset` が `task_module()` 経由で
  module-levelの `TASK_MODULES` を直接読む

未登録TaskのPackageを作れないという安全側の性質でもあります。詳細は
[extensibility-spikes.md のケースA](../../../../docs/architecture/extensibility-spikes.md) を参照してください。
