from pathlib import Path
import runpy

import numpy as np
import pytest


numpyro = pytest.importorskip("numpyro")
jnp = pytest.importorskip("jax.numpy")
handlers = pytest.importorskip("numpyro.handlers")


@pytest.mark.parametrize(
    "family",
    [
        "normal", "student_t", "lognormal", "bernoulli_logit", "poisson_log",
        "negative_binomial_log", "zero_inflated_poisson_log", "ordinal_logit",
    ],
)
def test_each_numpyro_bnn_likelihood_builds_a_valid_trace(family: str) -> None:
    path = Path(__file__).resolve().parents[2] / "examples" / "training" / "numpyro_bnn.py"
    dense_bnn = runpy.run_path(path)["dense_bnn"]
    x = jnp.asarray([[0.1, 1.2], [0.2, 1.5], [0.05, 1.0]])
    if family in {"bernoulli_logit"}:
        y = jnp.asarray([0, 1, 0])
    elif family in {"poisson_log", "negative_binomial_log", "zero_inflated_poisson_log"}:
        y = jnp.asarray([0, 2, 1])
    elif family == "ordinal_logit":
        y = jnp.asarray([0, 2, 3])
    elif family == "lognormal":
        y = jnp.asarray([0.8, 1.1, 1.5])
    else:
        y = jnp.asarray([-0.2, 0.5, 1.0])
    trace = handlers.trace(handlers.seed(dense_bnn, rng_seed=1)).get_trace(x, y, family=family)
    assert trace["obs"]["is_observed"] is True
    assert np.isfinite(np.asarray(trace["obs"]["fn"].log_prob(y))).all()
