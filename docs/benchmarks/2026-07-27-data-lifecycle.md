# Data Lifecycle 容量・応答時間ベンチマーク

実施日: 2026-07-27  
対象: Issue #255  
生の集約証拠: `docs/benchmarks/2026-07-27-data-lifecycle.json`

## 結論

現行のSQLite `TEXT` payload方式は、小規模データではそのまま継続できる。
一方、production契約内で通せる3列100,000行でも、固定済みのsoft triggerを2件踏んだ。

- Curation中央値: 13.08秒（閾値10秒）
- phase working set増分: 647.8 MB（閾値512 MB）
- Detail: 8.54秒、21.98 MB（read/UI閾値2秒または10 MB）

したがって、identity・digest・summary・承認状態はSQLiteへ残し、row payloadをcontent-addressed fileへ分離する後続実装を開始する。
同時に、Source ingressとDetail APIは別の問題として分離する。

1. #311 — 5,000,000文字を超えるSourceをfile/object経路で取り込む。
2. #312 — Raw／Curation row payloadをcontent-addressed fileへ分離する。
3. #313 — Detailをsummary・pagination・filter-before-parseへ分割する。

SQLiteの`DELETE`／`FULL`方針は維持する。
通常競合50操作ではbusy・失敗・外部キー違反が0件であり、外部payload化だけを理由にjournal policyは変えない。

## Fixture

二つの決定的shapeを各fresh process・fresh DBで3回ずつ測定した。

- narrow: 3列（`id`, `x`, `target`）
- representative: 20列（数値、カテゴリ、高cardinality lot、日本語、nullable値）

100,000行のrepresentative fixtureは35,019,885文字で、productionの5,000,000文字契約に拒否された。
これは測定失敗ではなく現行ingress境界である。
100万行はnarrowでも約35,937,350文字になるため、production契約を迂回せず非実行とした。

## Core結果

時間は3回の中央値。memoryはprocess lifetimeの最大working set。

| Shape | 行数 | Fetch | Curation | Detail | Detail body | DB増分 | DB/Source | Peak working set |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| narrow | 1,000 | 44.60 ms | 149.44 ms | 86.48 ms | 0.22 MB | 0.21 MB | 5.93x | 74.9 MB |
| representative | 1,000 | 146.65 ms | 472.92 ms | 305.67 ms | 0.89 MB | 0.88 MB | 2.42x | 91.1 MB |
| narrow | 10,000 | 244.69 ms | 1.32 s | 0.83 s | 2.19 MB | 2.19 MB | 6.09x | 156.7 MB |
| representative | 10,000 | 1.29 s | 4.58 s | 2.98 s | 8.83 MB | 8.83 MB | 2.43x | 301.5 MB |
| narrow | 100,000 | 2.30 s | 13.08 s | 8.54 s | 21.98 MB | 21.99 MB | 6.12x | 941.5 MB |
| representative | 100,000 | — | — | — | — | — | — | 契約拒否 |

各measured caseはRaw Snapshot、Curation Run、Canonical Revision、Training Snapshot、Detailまで通し、row count・digest参照・外部キー整合性を確認した。

## 履歴とglobal volume

250行、対象Connector 10 revisionのDetailを5回測定した。
さらに無関係Connectorを10件追加し、それぞれにRaw／Curation／Canonical／Trainingを作成した。

- 追加前Detail中央値: 319.51 ms
- 追加後Detail中央値: 398.97 ms
- slowdown: 1.249x

固定triggerの2xには届かなかった。
ただし現行`detail()`が全Curation／Canonical／Training payloadをdecodeしてからPythonで絞る構造は、後続のDetail API分割でfilter-before-parseへ置換する。

## 同時read／write

250行、10 iterationで、barrierにより4 Detail readと1 changed Snapshot writeを同時開始した。

- read 40件: p95 109.49 ms
- write 10件: p95 76.87 ms
- `sqlite_busy`: 0
- operation失敗: 0
- 外部キー違反: 0

校正用に12.5秒のexclusive lockを保持した場合は、約7.39秒で`database is locked`となった。
これはforced contentionのpolicy確認であり、通常負荷の失敗件数には含めない。

## Packaged Windows

同一buildをlocal NTFS上のfolder portableとNSIS installedで実行した。
各modeで実HTTPを使い、1,000行・3列の全Data Lifecycleを通した。

| Mode | 初回usable | 再起動usable | Fetch parse完了 | Curation parse完了 | Detail parse完了 | DB増分 | Process tree peak |
|---|---:|---:|---:|---:|---:|---:|---:|
| portable | 25.04 s | 15.45 s | 30.47 ms | 47.78 ms | 25.58 ms | 253,952 B | 875.6 MB |
| installed | 28.43 s | 17.57 s | 28.76 ms | 47.97 ms | 24.09 ms | 253,952 B | 878.6 MB |

1,000行のLifecycleによるprocess-tree peak増加はportableで0、installedで8,192 bytesだった。
同一disk上では保存先modeによるLifecycle差は実質的に見られない。
USB・network shareの性能へは一般化しない。

初回／再起動とも10秒閾値を超えたため、起動性能は#251のWindows統合受入で継続して扱う。
この値にはModel PackageやDatasetの起動も含まれ、Data Lifecycle DBだけの時間ではない。

## 判断規則と着地

閾値は本測定前にbenchmark report schemaへ固定した。

- representative ingress拒否: file/object ingressを分離
- persistence hard trigger 1件、またはsoft trigger 2件: content-addressed payload化
- Detail 2秒超または10 MB超: summary／pagination／lazy read
- forced lockだけ、portable path差だけでは外部payload化しない

今回の判定:

- Ingress hard trigger: **該当**
- Persistence hard trigger: 非該当
- Persistence soft trigger: **2件該当**
- Read/UI trigger: **該当**
- 通常Concurrency trigger: 非該当
- Packaged startup trigger: **該当**

benchmarkのためのproduction schema変更は行っていない。

## 再現コマンド

```powershell
uv run --extra dev python backend/scripts/benchmark_data_lifecycle.py `
  --scales 1000 10000 100000 `
  --shapes narrow representative `
  --repeats 3 `
  --history-rows 250 `
  --history-depth 10 `
  --unrelated-connectors 10 `
  --concurrency-rows 250 `
  --concurrency-iterations 10 `
  --output artifacts/data-lifecycle-benchmark/core-report.json

npm.cmd run package:windows

uv run --extra dev python backend/scripts/benchmark_data_lifecycle.py `
  --reuse-core-report artifacts/data-lifecycle-benchmark/core-report.json `
  --packaged-results artifacts/data-lifecycle-packaged-portable.json `
  --packaged-results artifacts/data-lifecycle-packaged-installed.json `
  --output artifacts/data-lifecycle-benchmark/final-report.json `
  --summary-output docs/benchmarks/2026-07-27-data-lifecycle.json
```

性能値はこのWindows端末でのローカル判断証拠であり、日常pytestの数値gateにはしない。
