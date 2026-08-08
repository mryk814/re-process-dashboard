# Issue #791 Standard Ordinal Regression evidence

`ordered-logit.v1` は、Task が宣言した category 順序だけを使う experimental な標準 builder です。label の辞書順・出現頻度から順序を推論しません。

固定 seed 791 の48行 synthetic cohortで、同じ行、同じcategory順序、同じ2-fold assignmentを用いて fold-local category-frequency baseline と比較しました。実測値の正本は [`issue-791-standard-ordinal-regression-evidence.json`](issue-791-standard-ordinal-regression-evidence.json) です。

| model | ordinal MAE | ranked probability score |
|---|---:|---:|
| fold-local category frequency | 0.751736 | 0.485243 |
| `ordered-logit.v1` | 0.280026 | 0.132865 |

全foldのNUTS診断は固定されたR-hat、ESS、divergence gateを通過しました。ただし、これはbuilderの実行可能性とordinal signal回復を示す一つのsynthetic cohortに限られます。adoption statusは `experimental` のままとし、production claimや既存Packageの自動置換は行いません。

NumPyro/JAXが無い場合、samplingが失敗した場合、診断gateを満たさない場合、またはtraining foldからTask categoryが欠ける場合はbuildを停止します。continuous、nominal、その他estimatorへのfallbackはありません。
