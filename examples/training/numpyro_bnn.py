"""Train the fixed dense_mlp_v1 Package architecture with NumPyro/NUTS.

This optional training-side example is intentionally outside the application
runtime.  Exported arrays are consumed by the data-only NumPyro adapter.
"""
from __future__ import annotations

from typing import Literal

import jax.numpy as jnp
from jax import nn, random
import numpy as np
import numpyro
import numpyro.distributions as dist
from numpyro.infer import MCMC, NUTS


Family = Literal[
    "normal", "student_t", "lognormal", "bernoulli_logit", "poisson_log",
    "negative_binomial_log", "zero_inflated_poisson_log", "ordinal_logit",
]


def dense_bnn(
    x: jnp.ndarray,
    y: jnp.ndarray | None = None,
    *,
    family: Family,
    hidden_width: int = 8,
    ordinal_cutpoints: tuple[float, ...] = (-0.7, 0.2, 1.0),
) -> None:
    """Two-layer Bayesian neural network with eight supported likelihoods."""
    output_width = 2 if family == "zero_inflated_poisson_log" else 1
    w0 = numpyro.sample("w0", dist.Normal(0, 0.35).expand((x.shape[1], hidden_width)).to_event(2))
    b0 = numpyro.sample("b0", dist.Normal(0, 0.35).expand((hidden_width,)).to_event(1))
    w1 = numpyro.sample("w1", dist.Normal(0, 0.35).expand((hidden_width, output_width)).to_event(2))
    b1 = numpyro.sample("b1", dist.Normal(0, 0.35).expand((output_width,)).to_event(1))
    output = jnp.tanh(x @ w0 + b0) @ w1 + b1
    eta = output[:, 0]

    if family == "normal":
        obs_scale = numpyro.sample("obs_scale", dist.HalfNormal(1.0))
        likelihood = dist.Normal(eta, obs_scale)
    elif family == "student_t":
        obs_scale = numpyro.sample("obs_scale", dist.HalfNormal(1.0))
        df = numpyro.sample("df_minus_two", dist.Exponential(0.2)) + 2
        likelihood = dist.StudentT(df, eta, obs_scale)
    elif family == "lognormal":
        obs_scale = numpyro.sample("obs_scale", dist.HalfNormal(0.5))
        likelihood = dist.LogNormal(eta, obs_scale)
    elif family == "bernoulli_logit":
        likelihood = dist.Bernoulli(logits=eta)
    elif family == "poisson_log":
        likelihood = dist.Poisson(jnp.exp(jnp.clip(eta, -20, 20)))
    elif family == "negative_binomial_log":
        dispersion = numpyro.sample("dispersion", dist.LogNormal(1.0, 0.5))
        likelihood = dist.NegativeBinomial2(jnp.exp(jnp.clip(eta, -20, 20)), dispersion)
    elif family == "zero_inflated_poisson_log":
        likelihood = dist.ZeroInflatedPoisson(
            gate=nn.sigmoid(output[:, 1]),
            rate=jnp.exp(jnp.clip(eta, -20, 20)),
        )
    elif family == "ordinal_logit":
        likelihood = dist.OrderedLogistic(eta, jnp.asarray(ordinal_cutpoints))
    else:
        raise ValueError(f"unsupported family: {family}")
    numpyro.sample("obs", likelihood, obs=y)


def fit_and_export(
    x: np.ndarray,
    y: np.ndarray,
    output: str,
    *,
    family: Family,
    seed: int = 0,
    num_warmup: int = 500,
    num_samples: int = 1000,
) -> None:
    """Run NUTS and export only finite arrays accepted by the app adapter."""
    kernel = NUTS(dense_bnn)
    mcmc = MCMC(kernel, num_warmup=num_warmup, num_samples=num_samples, progress_bar=True)
    mcmc.run(random.PRNGKey(seed), jnp.asarray(x), jnp.asarray(y), family=family)
    posterior = {name: np.asarray(value) for name, value in mcmc.get_samples().items() if name != "df_minus_two"}
    if "df_minus_two" in mcmc.get_samples():
        posterior["df"] = np.asarray(mcmc.get_samples()["df_minus_two"]) + 2
    np.savez(output, **posterior)
