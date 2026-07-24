# synthetic_heat_treatment

熱処理・材料設計の検証用疑似データです。

- targets: `hardness_hv`, `charpy_j`
- 画面案:
  - 硬さと靭性のトレードオフ散布図
  - 処理条件スライダー
  - 合格領域表示
  - 目標硬さを満たしつつ靭性を落としすぎない条件探索

今の単一targetアプリを拡張して「multi-target / tradeoff view」を作る余地が大きい例です。
