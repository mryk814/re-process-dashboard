# Proposal pool inference benchmark — 2026-07-26

## 判断

- 全runtimeで、Task定義を候補ごとに再読込せずruntime初期化時に固定する。
- pool全体では`predict_core`とsupport summaryだけを計算し、類似実績は選抜候補へ後から付ける。
- native batchは、効果が大きかったLightGBM adapterだけ採用する。
- builtin linear、exact GP、posterior runtimeへのbatch追加は今回見送る。

## 条件

- Windows 11、ローカル開発環境
- 同じcanonical candidateを複製してruntime単体を計測
- `uv run python backend/scripts/benchmark_proposal_pool.py --count 256 --repeats 2`
- 値は2回の中央値。起動時のDataset／Package読込時間は含めない

## 修正後の計測

| Task | runtime | full scalar 256件 | pool scalar 256件 | native batch 256件 |
|---|---|---:|---:|---:|
| 焼鈍 | exact GP | 547.6 ms | 370.6 ms | 非採用 |
| 工具摩耗 | exact GP | 506.8 ms | 345.9 ms | 非採用 |
| 電池劣化 | LightGBM | 358.5 ms | 261.1 ms | 34.9 ms |
| MPEA室温引張 | builtin linear | 103.4 ms | 89.1 ms | 非採用 |
| 溶接Stage C | builtin linear | 184.7 ms | 89.1 ms | 非採用 |

LightGBM predictor単体の2,048件では、逐次約2,470 msに対してnative
batchは約21 msだった。adapter境界を増やす根拠として十分な差がある。

一方、builtin linearのpredictor単体は2,048件で約30〜32 msから
24〜27 ms、exact GPも提案全体で最大約0.8秒の短縮見込みだった。
現時点では複雑さに見合わない。

## 修正前に確認したボトルネック

候補ごとの`predict_core`がTask JSONを全件再読込していた。2,048件へ外挿した
full predictionは、電池LightGBM約64.6秒、MPEA linear約20.4秒、溶接Stage C
linear約20.4秒だった。Task定義をruntime初期化時に固定したread-only試験では、
同じ2,048件がそれぞれ約2.39秒、0.88秒、0.68秒まで短縮した。

したがって、最初に汎用batch契約を広げるのではなく、偶発的な反復I/Oを除去する
ことが本質的な修正だった。

## 同値性の境界

- native batchは入力順と件数を維持し、違反時はProposal実行を失敗させる。
- scalarとbatchで予測要約、canonical input、support evidenceを比較する。
- Proposalのseed、Generator／Distance version、Package provenanceは従来の
  Run契約に残し、pool評価方式から独立させる。
- 類似実績は選抜後に同じruntimeの`similarity`で生成するため、保存される候補の
  evidenceは従来と同じである。
