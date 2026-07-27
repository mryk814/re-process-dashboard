# Data Lifecycle summary/detail benchmark

Issue #313のsummary projection・row pagination実装後に、#255と同じWindows端末の
100,000行narrow fixtureでproduction lifecycleを1回実行した。

| 指標 | #255 baseline | summary実装後 | 完了条件 |
|---|---:|---:|---:|
| 初期Detail load | 8.54秒 | 0.008秒 | 2秒未満 |
| 初期Detail JSON | 21.98 MB | 3,996 bytes | 10 MB未満 |
| Detail phase working set増分 | 未分離 | 1.14 MB | bounded |
| JSON serialization | baseline内包 | 0.00008秒 | bounded |

100,000行CASへseek indexを構築し、100行pageを5回ずつ読んだ結果は次の通り。
値はbest（括弧内はmax）で、offsetが増えてもCAS先頭から走査しない。

| page | offset 0 | offset 50,000 | offset 99,900 | JSON |
|---|---:|---:|---:|---:|
| Raw | 12.7 ms (15.5) | 12.4 ms (14.1) | 12.0 ms (12.8) | 3.9 KB |
| Curation | 20.5 ms (26.0) | 21.9 ms (28.2) | 21.1 ms (24.6) | 16.9 KB |

Raw 100,000件とCuration 100,000件の初回索引構築は3.67秒だった。
索引はSQLiteへlogical position、status別position、理由行position、CAS byte
range、行SHA-256を保持する。resource単位manifestがCAS SHA-256、row数、
status／理由行の件数を固定し、起動時とpageごとの完全性確認を主キー1行で行う。
以後のpageはlogical positionから100件だけをseekし、選択行のhashを検証する。
Curationの物理配置に依存せず`raw_row_index,row_key`順を固定し、
`status=quarantined`や`reasoned_only=true`の絞り込みも直接取得する。

初期DetailはRaw／Curation row、承認row key、Training split assignmentを含まない。
Connector、取得履歴、品質件数、承認状態、Training概要をSQLiteの
`summary_payload`から返す。Raw／Curation rowは別APIで最大200件ずつ読み、
pageごとに親resource IDと固定digestを返す。

再現時の正しさはRaw、Curation、Canonical、Training各1版、
Training 100,000行、foreign key violation 0、row数一致である。

```powershell
uv run --extra dev python backend/scripts/benchmark_data_lifecycle.py `
  --worker case `
  --rows 100000 `
  --shape narrow `
  --fixture artifacts/data-lifecycle-benchmark/20260727T184021Z/fixtures/narrow-100000.json `
  --case-workspace artifacts/data-lifecycle-benchmark/summary-final-worker `
  --environment-label development-narrow-summary-final
```
