# 推論実行ポリシー

## 基本方針

推論は候補単位ではなく、意味が同じ仕事を表す `InferenceKey` 単位で再利用する。
キーは canonical input、Feature Pipeline、Model Package、support reference、operation、operation parameters から、operationに関係する要素だけを組み立てる。

| operation | Package | Pipeline | support reference | parameters |
|---|---:|---:|---:|---|
| preview | yes | yes | no | target values |
| support | no | yes | yes | none |
| similarity | no | yes | yes | limit |
| curve | yes | yes | no | target, variable, point count, policy |
| detailed | yes | yes | yes | target values, policy |

キャッシュはprocess memory内の最大256件の完了結果LRUとする。永続化しない。同じキーの同時要求は一つのFutureへcoalesceする。実行中のentryはcoalescingを壊さないため一時的に上限を超えて保持し、完了後に上限へ戻す。これはruntime concurrency上限ではない。代表runtimeの実測ではsemaphoreがpreviewを重い処理の後ろへ並べる不利益が上回るため、P2条件に達するまではASGI serverの実行制御を使う。

ブラウザ側のcacheは通信のcoalescingと同一project内の候補切替再利用だけを担当し、意味の正本にはしない。projectを完全に読み直すたびに破棄し、Package / Pipeline / support referenceのversion判定はbackendの `InferenceKey` へ必ず戻す。

## 観測

`GET /api/diagnostics/inference` でoperation別のhit、miss、coalesced、computation回数と、computation/要求全体のtotal・last・max・average時間を確認できる。外部監視基盤や分散queueは使用しない。

## 2026-07-21の実測とP2判断

Windows 11の開発端末、同一processのFastAPI `TestClient`、既定Model Packageで各操作を8回測定した。

| task / operation | average | min | max |
|---|---:|---:|---:|
| 焼鈍 preview | 5.44 ms | 4.16 ms | 11.77 ms |
| 焼鈍 detailed | 17.17 ms | 15.73 ms | 20.94 ms |
| 焼鈍 TS curve 9点 | 3.75 ms | 3.23 ms | 4.62 ms |
| 焼鈍 全4出力curve 9点（旧API） | 8.60 ms | 8.14 ms | 9.29 ms |
| 熱延 preview | 2.78 ms | 2.27 ms | 4.24 ms |
| 熱延 detailed | 12.44 ms | 10.41 ms | 14.78 ms |

最大128点のscreeningは焼鈍500.58 ms、熱延166.48 msだった。さらにcurveを意図的に400 ms停止させて並行要求した場合も、previewは14.22 msで完了した。

現時点では `predict_many`、runtime semaphore、progressive curve、worker processを導入しない。最大screeningが1秒未満、通常previewが50 ms未満であり、batchや二重並列の複雑さより、不要なsimilarityと非表示curveを計算しない効果を優先するためである。

次のいずれかを代表端末で継続的に超えた場合にP2を再評価する。

- previewのp95が50 ms
- 単一targetの9点curveのp95が100 ms
- 最大128点screeningが1秒
- curve/detailed実行中のpreviewのp95が100 ms

再評価時は、まずこのdiagnosticsで支配的なoperationを特定し、そのoperationだけを開発用profilerでfeature、predictor、support、serializationへ分解する。現diagnosticsはsub-stage profilerではない。batchは対応adapterと標準loopが同じ `PredictiveSummary` 意味値を返すcontractを用意できる場合に限り導入する。
