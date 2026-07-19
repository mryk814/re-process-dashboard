# NumPyro BNN training examples

`numpyro_bnn.py` は、アプリの `dense_mlp_v1` に対応する2層Bayesian neural networkをNUTSで学習し、安全なposterior arrayだけを `.npz` へexportする学習側の例です。

同じBNNで次の8つを選べます。

- Normal
- Student-t（外れ値に頑健な連続値）
- LogNormal（正の連続値）
- Bernoulli-logit（二値）
- Poisson-log（計数）
- Negative Binomial-log（過分散計数）
- zero-inflated Poisson-log（過剰ゼロ計数）
- ordinal logit（順序カテゴリ）

学習環境は `uv sync --extra runtime-numpyro` で追加します。アプリはこの学習コードを実行・復元せず、export済みarrayと固定architectureだけを読みます。
