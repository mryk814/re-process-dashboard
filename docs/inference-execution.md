# 推論実行ポリシー

## 推論仕事の識別

推論は候補単位ではなく、意味が同じ仕事を表す `InferenceKey` 単位で再利用します。
キーはアプリ共通入力、Feature Pipeline、Model Package、support reference、operation、operation parametersから、operationに関係する要素だけを組み立てます。

| operation | Package | Pipeline | support reference | parameters |
|---|---:|---:|---:|---|
| `preview` | yes | yes | no | target values |
| `support` | no | yes | yes | none |
| `similarity` | no | yes | yes | limit |
| `curve` | yes | yes | no | target、variable、point count、range、stage、policy |
| `curve_family` | yes | yes | no | target、vary、level count、point count、policy |
| `detailed` | yes | yes | yes | target values、policy |

候補名、選択状態、画面の表示状態はキーへ含めません。
Package、Pipeline、support referenceの意味が変わる場合は、それぞれのdigestを変えて別の仕事として扱います。

## キャッシュと同時要求

backendのキャッシュはprocess memory内の最大256件の完了結果LRUです。
結果は永続化しません。
同じキーの同時要求は一つのFutureへまとめます。
実行中のentryは同じ計算の重複を防ぐため一時的に上限を超えて保持し、完了後に上限へ戻します。

256件はキャッシュ件数であり、runtimeの同時実行数ではありません。
runtime semaphore、worker process、分散queueは通常経路へ置かず、ASGI serverの実行制御を使います。

ブラウザ側のキャッシュは、通信の集約と同一project内での候補切替時の再利用だけを担当します。
意味の正本にはしません。
projectを完全に読み直すたびに破棄し、versionの判定はbackendの `InferenceKey` へ戻します。

## 観測

`GET /api/diagnostics/inference` はoperation別に次の値を返します。

- hit、miss、coalesced、computationの回数
- computation時間のtotal、last、max、average
- request全体の時間のtotal、last、max、average
- 実行したruntime type

このdiagnosticsはoperation単位の観測用です。
feature変換、predictor、support処理、serializationなどのsub-stage profilerではありません。

## P2の再評価条件

代表端末で次のいずれかを継続的に超えた場合に、推論実行方式を再評価します。

- previewのp95が50ms
- 単一targetの9点curveのp95が100ms
- 最大128点screeningが1秒
- curveまたはdetailed実行中のpreviewのp95が100ms

preview、curve、detailedを再評価するときはdiagnosticsで支配的なoperationを特定し、そのoperationだけを開発用profilerで分解します。
screeningは `InferenceWorkGraph` のdiagnostics対象ではないため、screening API全体を代表条件で別に計測してから分解します。
batchは、対応adapterと標準loopが同じ `PredictiveSummary` の意味値を返すcontractを用意できる場合に限り導入します。
測定結果を更新する場合は、このポリシーへ固定値を書き足さず、日付付きのベンチ記録として保存します。

直近の判断根拠は [2026-07-21の推論ベンチ](benchmarks/2026-07-21-inference.md) にあります。
