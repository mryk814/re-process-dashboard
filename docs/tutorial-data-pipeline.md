# 最小教材データで追う Data → Profile → Feature → Model

この文書は、Material Decision Workbench がExcelを読み、予測に使える形へ変換するまでを、同梱の最小教材データだけで追う開発者向けガイドです。

教材の目的は精度競争ではありません。1件ずつ覚えられる規模で、キーのつながり、反復試験、部分欠損、特徴量化、Model Packageの固定契約を確認することです。

## 1. 教材の全体像

正本は [`data/source/material_workbench_tutorial_v1.xlsx`](../data/source/material_workbench_tutorial_v1.xlsx) です。

| 種類 | 件数 | 覚えるポイント |
|---|---:|---|
| 成分ロット | 4 | CとMnだけを2水準で変える |
| 熱延条件 | 6 | 同じ成分で仕上温度だけが違う対を含む |
| 焼鈍条件 | 6 | ライン速度（LS）と最高温度を変える |
| 熱延引張 | 8 | 6条件＋2反復 |
| 焼鈍引張 | 10 | 6条件＋4反復、反復2行に部分欠損 |
| 穴広げ | 8 | 6条件＋2反復 |

成分は次の4ロットだけです。

| key | C | Mn | 意味 |
|---|---:|---:|---|
| ME-01 | 0.05 | 1.00 | 低C・低Mn |
| ME-02 | 0.08 | 1.00 | 高C・低Mn |
| ME-03 | 0.05 | 1.50 | 低C・高Mn |
| ME-04 | 0.08 | 1.50 | 高C・高Mn |

TaskDefinitionとの契約上、ほかの成分列も存在しますが、教材では固定値です。「列が多い」ことと「学ぶ変数が多い」ことを分けています。

```mermaid
flowchart LR
  M["溶製 4"] --> H["熱延条件 6"]
  H --> HT["熱延引張 8"]
  H --> C["冷延条件 6"]
  C --> A["焼鈍条件 6"]
  A --> AT["焼鈍引張 10"]
  A --> HE["穴広げ 8"]
  A --> HP["焼鈍履歴 36点"]
```

## 2. Excelで表しているもの

各実体シートは1行1実体です。

- `溶製`：成分ロット
- `熱延`、`冷延`、`焼鈍`：工程条件
- `熱延引張`、`焼鈍引張`、`焼鈍穴広げ`：観測
- `焼鈍履歴`：1焼鈍条件に複数の時刻・温度点
- `relation`：実体同士を結ぶリンク

重要なのは、`relation` の1行を学習1行として扱わないことです。学習の観測単位は試験シートの行で、`relation` はその試験がどの工程条件と成分に由来するかを解決するために使います。

たとえば `AN-02` には `TT-02` と `TT-03` という2回の引張試験があります。工程条件は1件、観測は2件です。モデル学習時は同じ `parent_key` の反復をまとめ、親工程条件の平均を学習点として使います。

### 部分欠損

`TT-03` はYS、`TT-07` はELを空欄にしています。

- TSモデルには両方の行を使える
- YSモデルは`TT-03`を使わない
- ELモデルは`TT-07`を使わない
- 行全体を一律に捨てない

この違いを確認するための欠損であり、偶然の欠損を統計的に説明する教材ではありません。

## 3. ProfileはExcelの方言を正規形へ写す

教材専用Profileは [`dataset-input-profile-tutorial.json`](../backend/src/material_workbench/dataset-input-profile-tutorial.json) です。

このProfileは既存の薄板Task用Profileを継承し、`概要.項目` に `教材データID` があることだけを固有マーカーにします。これにより、同じシート名を持つ別Workbookと自動判定が衝突しません。

Profileが決めるのは次の内容です。

1. どのシートがどのroleか
2. 各実体のkey列
3. `relation` の親子順序とcardinality
4. Excel列名からcanonical pathへの対応
5. 単位変換
6. 学習利用・有効判定のポリシー
7. 観測の目的変数と補助値

Profileはモデルではありません。Excelの列名をアプリ内部の意味へ翻訳する入力契約です。

```text
Excel「C[mass%]」
  → Profile: composition.C / mass% → %
  → canonical input: composition.C
```

## 4. Importerが作る正規化データ

入口は `material_workbench.importer.load_workbook_data()` です。教材Workbookでは次の結果になります。

```text
composition          4
hot_rolling_features 6
anneal_features      6
observations         26
lineage              48 links
detected_quality     0
```

処理の順序は次のとおりです。

1. Profileを自動判定する
2. keyの一意性と参照先を検査する
3. `relation` からlineageを解決する
4. 工程条件、成分、観測をcanonical pathへ写す
5. 判定列から学習可能性を付ける
6. 焼鈍履歴を時刻順のheat patternへする
7. 目的変数ごとに欠損を保持する

確認コマンド:

```powershell
$env:PYTHONPATH = "backend/src"
uv run python -c "from material_workbench.importer import load_workbook_data; d=load_workbook_data('data/source/material_workbench_tutorial_v1.xlsx'); print(d.profile_id, len(d.observations))"
```

## 5. Feature Pipelineはcanonical inputから決定的に計算する

Feature PipelineはModel Packageに含まれるPythonコードではありません。実装コードはアプリ本体にあり、PackageにはID、version、入力path、出力特徴量の順序と参照artifactを保存します。

### 焼鈍

実装は [`feature_pipeline.py`](../backend/src/material_workbench/feature_pipeline.py) です。

- 成分値
- LS
- ヒートパターンの最高温度、保持、加熱・冷却挙動
- 成分と熱履歴を組み合わせた材料観点の派生特徴

教材の焼鈍履歴では、炉内位置を固定し、`時間 = 位置 / LS` で時刻を作っています。そのため、LSを下げると同じ炉内位置までの時間が長くなります。モデルにはLSそのものとheat pattern由来の特徴の両方が入り、相関の強い特徴を因果効果とは解釈しません。

### 熱延

実装は [`hot_rolling_feature_pipeline.py`](../backend/src/material_workbench/hot_rolling_feature_pipeline.py) です。

- 成分値
- 熱延工程値
- 炭素当量などの材料観点の派生値
- 成分と工程の交互作用

固定列も特徴量には存在しますが、教材内で変化しない列は学習上の識別情報を持ちません。

## 6. Model Packageは再現用の固定成果物

教材のactive Packageは次の2つです。

- `models/packages/annealed-gp-tutorial-v1`
- `models/packages/hot-rolled-tutorial-v1`

Packageには次を保存します。

- `manifest.json`
- Feature PipelineのID・version・特徴量順序
- 学習データとProfileのdigest
- モデルartifact
- 品質レポート
- smoke入力と期待値

焼鈍は目的変数ごとのExact GPです。反復は親工程条件単位へ集約し、同一親内のばらつきは観測ノイズの手掛かりとして使います。

熱延教材は独立工程条件が6件しかありません。通常データ用のHorseshoe/NUTSをこの規模へ無理に適用せず、ridge近似posteriorを使います。これは教材専用の安定した予測分布で、係数の因果解釈や精度評価を目的にしません。12条件以上では既存のRegularized Horseshoe経路を使います。

再生成:

```powershell
$env:PYTHONPATH = "backend/src"
uv run python backend/scripts/build_default_model_package.py `
  --source data/source/material_workbench_tutorial_v1.xlsx `
  --output models/packages/annealed-gp-tutorial-v1 `
  --package-id annealed-gp-tutorial-v1 --replace

uv run python backend/scripts/build_hot_rolling_model_package.py `
  --source data/source/material_workbench_tutorial_v1.xlsx `
  --output models/packages/hot-rolled-tutorial-v1 `
  --package-id hot-rolled-tutorial-v1 --replace
```

## 7. なぜWorkbook、Profile、Packageを別々に版管理するか

3つは変更理由が違います。

| 成果物 | 変わる理由 |
|---|---|
| Workbook | 行・列・値・関係が変わった |
| Profile | Excel列の意味や単位対応が変わった |
| Feature Pipeline | canonical inputから作る特徴が変わった |
| Model Package | 学習データ・特徴量・モデルのどれかが変わった |

PackageのmanifestはWorkbook SHA、Profile digest、canonical training dataset digest、input contract digestを持ちます。どれかが変わったPackageを同じdirectoryへ黙って差し替えません。新しいIDで作り、activeを切り替えます。

旧Workbookと旧Packageを配布資源に残しているのは、保存済みProjectが固定したmanifest SHAを解決できるようにするためです。初回起動の既定は教材だけですが、過去の判断記録は壊しません。

## 8. 変更するときの最短チェック

1. 新しいExcelを別名で作る。既存の`data/source`正本を上書きしない
2. Profileの固有markerで自動判定を一意にする
3. Importerで件数、lineage、部分欠損を確認する
4. Feature Pipelineのgolden testを更新する
5. Packageを新IDで生成しverifyする
6. `models/active-packages.json`を切り替える
7. fresh DBと既存DBの両方で起動する

精度指標が良いかより、どの行がどの意味で使われたか、再現契約が閉じているかを先に確認します。
