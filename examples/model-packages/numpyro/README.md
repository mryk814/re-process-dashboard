# NumPyro posterior Package examples

学習済みposteriorを安全な固定BNN構造へexportした、production loaderとadapterで検証できる8例です。学習コード自体はPackageへ含めません。各例は同じ2層BNN、異なる尤度・出力supportを示し、deterministic smoke、RuntimeCapability、意味に合う最小quality reportを同梱します。

- `normal/`: `continuous` 出力の `normal` 尤度
- `student_t/`: `continuous` 出力の `student_t` 尤度
- `lognormal/`: `continuous_positive` 出力の `lognormal` 尤度
- `bernoulli_logit/`: `binary` 出力の `bernoulli_logit` 尤度
- `poisson_log/`: `count` 出力の `poisson_log` 尤度
- `negative_binomial_log/`: `count` 出力の `negative_binomial_log` 尤度
- `zero_inflated_poisson_log/`: `count` 出力の `zero_inflated_poisson_log` 尤度
- `ordinal_logit/`: `ordinal` 出力の `ordinal_logit` 尤度

検証例: `uv run python backend/scripts/verify_model_package.py examples/model-packages/numpyro/bernoulli_logit --example`
