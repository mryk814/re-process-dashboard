# Chainの段単体評価と通し評価

溶接材料 A→B→C Chainは、Stage Cだけの性能と、上流誤差を含む性能を別々に評価します。
二つは同じouter split・同じoutput別cohort・同じMAE/RMSEで比較しますが、一つの「モデル精度」へ合成しません。

## 評価の意味

| 表示 | Stage Cへ渡す溶着金属成分 | 答える問い |
|---|---|---|
| 段単体 | 実測値 | 上流が正しいとき、C単体はどれだけ予測できるか |
| 通し A→B→C | Bの予測値 | 上流誤差を含め、最終特性をどれだけ予測できるか |

Stage Aは固定科学masterによる決定論的変換です。
評価データのStage B canonical inputは同じ変換規則で材料成分へ変換済みなので、Aに統計的な推定誤差は加わりません。

## 漏洩を防ぐnested grouped評価

外側の分割単位は溶接施工（weld-run key）です。
同じ施工に属する観測をtrainとtestへ跨がせません。

各outer foldで通し評価を次の順に作ります。

1. outer test施工をB/C双方の学習から外す。
2. outer train内をさらにinner foldへ分ける。
3. 各inner holdout施工について、その施工を見ていないBモデルの予測を作る。
4. 3のinner OOF予測をCの学習入力にする。
5. outer train全体で学習したBモデルからouter test施工の入力を作り、Cを評価する。

したがって、Cの通し評価を学習する入力にも、Bのin-sample予測は入りません。
成果物は各target/foldについて `inner-grouped-oof`、`outer-train-only`、自己fit違反数、outer test重複数を保存します。

## 欠測と母数

欠測targetのために観測行全体を捨てません。
引張、シャルピー、腐食の各family・各outputで利用可能cohortを作るため、画面には特性ごとの観測数 `n` とsplit group数を表示します。
段単体と通しは、同じ特性内では必ず同じcohortを使います。

## 不変な成果物

コードを実行するファイルはModel Packageや評価成果物へ入れません。
生成済みJSONは次にあります。

- `models/evaluations/welding-consumable-a-b-c-v1.json`

成果物はChain Definition・binding・単位変換digest、Stage A/B/Cの順序・contract digest・Package manifest digest、B/CのDataset Profile digest、元データdigest、決定的fold assignmentを固定します。
APIはProjectが固定したChain RevisionとDataset Viewから元データidentityまでを照合し、一致しない成果物を表示しません。

再生成:

```powershell
uv run python backend/scripts/build_welding_chain_evaluation.py
```
