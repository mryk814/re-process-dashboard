# 2026-07-21の推論ベンチ

この文書は2026-07-21時点の開発端末とModel Packageに対する測定記録です。
現行の実行ポリシーと再評価条件は [推論実行ポリシー](../inference-execution.md) を正本にします。

## 測定条件

Windows 11の開発端末で、同一processのFastAPI `TestClient` と当時の既定Model Packageを使い、各操作を8回測定しました。

## 結果

| task / operation | average | min | max |
|---|---:|---:|---:|
| 焼鈍 preview | 5.44ms | 4.16ms | 11.77ms |
| 焼鈍 detailed | 17.17ms | 15.73ms | 20.94ms |
| 焼鈍 TS curve 9点 | 3.75ms | 3.23ms | 4.62ms |
| 焼鈍 全4出力curve 9点（当時の旧API） | 8.60ms | 8.14ms | 9.29ms |
| 熱延 preview | 2.78ms | 2.27ms | 4.24ms |
| 熱延 detailed | 12.44ms | 10.41ms | 14.78ms |

最大128点のscreeningは焼鈍500.58ms、熱延166.48msでした。
curveを意図的に400ms停止させて並行要求した場合も、previewは14.22msで完了しました。

## 当時の判断

この測定では、最大screeningが1秒未満、通常previewが50ms未満でした。
そのため `predict_many`、runtime semaphore、progressive curve、worker processは導入せず、不要なsimilarityと非表示curveを計算しない方針を選びました。

この判断は測定時点の環境に対するものです。
Model Package、データ量、runtime、端末が変わった場合は、現行ポリシーの再評価条件に従って測り直します。
