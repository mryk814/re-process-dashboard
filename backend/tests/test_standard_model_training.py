from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from build_default_model_package import _fit_gp_hyperparameters  # noqa: E402


def test_stable_gp_training_is_deterministic_scaled_ard_multistart() -> None:
    rng = np.random.default_rng(7)
    x = rng.normal(size=(28, 5))
    y = 12.0 + 3.5 * x[:, 0] + 0.15 * rng.normal(size=len(x))

    first = _fit_gp_hyperparameters(x, y, train_noise=0.04, restarts=3, seed=11)
    second = _fit_gp_hyperparameters(x, y, train_noise=0.04, restarts=3, seed=11)

    np.testing.assert_allclose(first[0], second[0], rtol=0, atol=1e-10)
    assert first[1] == pytest.approx(second[1], abs=1e-10)
    assert first[2] == pytest.approx(second[2], abs=1e-10)
    assert first[0].shape == (x.shape[1],)
    assert np.all(first[0] > 0)
    assert first[0][0] < np.median(first[0][1:])
    diagnostics = first[3]
    assert diagnostics["kernel"] == "ARD-RBF"
    assert diagnostics["restarts"] == 3
    assert diagnostics["input_standardization"] == "per_feature_training_mean_std"
    assert diagnostics["output_standardization"]["scale"] > 0
