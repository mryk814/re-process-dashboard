# Data Lifecycle CAS migration benchmark

Issue #312のRaw／Curation row payload分離後に、#255と同じWindows端末・
100,000行narrow fixture・production lifecycleで1回の比較確認を行った。
この値はローカル判断証拠であり、日常pytestの固定性能gateにはしない。

## 結果

| 指標 | #255 baseline（3回中央値） | CAS最終確認（1回） | 判定 |
|---|---:|---:|---|
| Curation save | 13.08秒 | 12.763秒 | 0.317秒改善 |
| process peak working set | 941.5 MB | 884.7 MB | 56.8 MB改善 |
| Lifecycle SQLite増分 | 21.99 MB | 6.30 MB | 71%削減 |
| SQLite増幅率／source | 6.12x | 1.754x | 改善 |

CAS fileは2件、19,976,358 bytesである。SQLiteとCASを合わせた永続化増分は
26,280,102 bytes（source比7.313x）で、総disk量は#255より増えている。
この変更の効果はdisk圧縮ではなく、巨大rowをSQLiteのtransaction・decode境界から
外し、SQLite増幅とprocess peakを下げる点にある。

Fetch saveは4.612秒、従来Detail互換経路は15.562秒であり、#255より遅い。
DetailはIssue #313でsummaryとpage queryへ分割し、初期表示から全row読込を外す。

## 正しさ

- Raw Snapshot: 1
- Curation Run: 1
- Canonical Revision: 1
- Training Snapshot: 1
- Training row: 100,000
- foreign key violation: 0
- source row数との一致: true

## 再現コマンド

```powershell
uv run --extra dev python backend/scripts/benchmark_data_lifecycle.py `
  --worker case `
  --rows 100000 `
  --shape narrow `
  --fixture artifacts/data-lifecycle-benchmark/20260727T184021Z/fixtures/narrow-100000.json `
  --case-workspace artifacts/data-lifecycle-benchmark/cas-final-worker `
  --environment-label development-narrow-cas-final
```
