# Design Prior Package

`design-prior-package/v1` は予測Model Packageから独立した、候補入力の経験分布 `p(x)` の data-only artifact である。
予測値、目的、hard feasibility、predictive supportを保存・推定しない。

- manifest と全 JSON artifact は package root 内の通常ファイルで、size と SHA-256 を検証する。
- Python source、import path、callback、pickle、joblibを含めない。
- P0 は `empirical_rows@1.0.0` と `knn_local@1.0.0` を同一observations artifact上で提供する。
- sampling requestは明示参照（Package ID/version/manifest digest/locator/generator/lane）を持つ。active priorやLHS/Sobolへのfallbackはない。
- samplerは `conservative`／`balanced`／`frontier` を明示し、各proposal pointへ raw sample、neighbor distance、typicality、変換を残す。
- hard feasibilityは既存Task / Project Design Space validatorだけが判定する。範囲外を黙ってclipせず、integer/step snap、conditional inactive、composition balanceは変換証跡に残す。
- Package更新は既存ProjectやProposal Runを変えない。Runは解決済みreferenceと各標本evidenceを保存する。

P0 quality reportは、行数・入力path・比較したgeneratorと限界を固定する。hard constraint違反率、相関差、category co-occurrence、mode coverage、held-out識別、seed sensitivityの比較は、実Taskへ昇格するgenerator評価で追加する。単一scoreで採用しない。
