"""Deterministic offline evidence for Generative Design candidate generation.

This module is deliberately outside the production Proposal registry.  It
compares generators against hidden fixture truth and produces review evidence;
it never loads a research model or enables a production strategy.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Literal

import numpy as np
from scipy.stats import norm, rankdata

from decision_workbench.domain.proposal_generation import (
    _latin_hypercube_unit,
    _sobol_unit,
)


GENERATOR_IDS = (
    "latin_hypercube",
    "sobol",
    "empirical_rows",
    "knn_local",
    "gaussian_rank_copula",
)
SELECTION_POLICIES = ("direct_objective", "conservative_diverse")
SCHEMA_VERSION = "generative-design-lab-report/v2"
GENERATOR_VERSION = "1.0.0"
PROTOCOL_VERSION = "generative-design-lab-protocol/v1"
KNN_ALPHA_RANGE = (0.05, 0.55)
COPULA_CORRELATION_SHRINKAGE = 0.97
COPULA_QUANTILE_METHOD = "linear"
VAE_HIDDEN_DIMENSIONS = 6
VAE_LATENT_DIMENSIONS = 2
VAE_EPOCHS = 240
VAE_BETA = 0.03
VAE_LEARNING_RATE = 0.015
DIRECT_DIVERSITY_WEIGHT = 0.0
CONSERVATIVE_DIVERSITY_WEIGHT = 0.35
CONSERVATIVE_NEAREST_THRESHOLD = 0.10
CONSERVATIVE_PENALTY_WEIGHT = 20.0
SUPPORT_POLICY = "retain_with_explicit_status"
NEAR_DUPLICATE_POLICY_VERSION = "mixed-distance-near-duplicate/v1"
NEAR_DUPLICATE_DISTANCE_THRESHOLD = 0.02


@dataclass(frozen=True)
class LabFixture:
    fixture_id: str
    fixture_version: str
    observations: tuple[tuple[float, float], ...]
    categories: tuple[int, ...]
    category_count: int
    hidden_oracle_id: str
    predictive_model_id: str
    feasibility_id: str


@dataclass(frozen=True)
class CandidatePool:
    points: np.ndarray
    categories: np.ndarray
    operation: dict[str, object]


def _digest(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def generator_parameter_payload(
    generator_id: str,
    *,
    seed: int,
    budget: int,
) -> dict[str, object]:
    shared: dict[str, object] = {
        "generator_id": generator_id,
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "budget": budget,
        "numeric_dimensions": 2,
    }
    parameters: dict[str, object]
    if generator_id == "latin_hypercube":
        parameters = {
            "unit_dimensions": 3,
            "category_mapping": "equal-width-floor",
        }
    elif generator_id == "sobol":
        parameters = {
            "unit_dimensions": 3,
            "scrambled": True,
            "category_mapping": "equal-width-floor",
        }
    elif generator_id == "empirical_rows":
        parameters = {"selection": "uniform-with-replacement"}
    elif generator_id == "knn_local":
        parameters = {
            "neighbor_policy": "uniform-same-category-excluding-anchor",
            "alpha_range": KNN_ALPHA_RANGE,
        }
    elif generator_id == "gaussian_rank_copula":
        parameters = {
            "category_policy": "empirical-frequency-conditioned",
            "rank_method": "average",
            "correlation_shrinkage": COPULA_CORRELATION_SHRINKAGE,
            "quantile_method": COPULA_QUANTILE_METHOD,
        }
    elif generator_id == "tiny_vae_research":
        parameters = {
            "architecture": {
                "hidden_dimensions": VAE_HIDDEN_DIMENSIONS,
                "latent_dimensions": VAE_LATENT_DIMENSIONS,
                "activation": "tanh",
                "output": "sigmoid",
            },
            "epochs": VAE_EPOCHS,
            "beta": VAE_BETA,
            "optimizer": "adam",
            "learning_rate": VAE_LEARNING_RATE,
            "gradient_clip": (-1.0, 1.0),
            "category_decode": "argmax",
        }
    else:
        raise ValueError(f"unknown research generator: {generator_id}")
    return {**shared, "parameters": parameters}


def _fixture(
    fixture_id: str,
    points: np.ndarray,
    categories: np.ndarray,
    *,
    category_count: int = 1,
) -> LabFixture:
    return LabFixture(
        fixture_id=fixture_id,
        fixture_version="1.0.0",
        observations=tuple(tuple(float(value) for value in row) for row in points),
        categories=tuple(int(value) for value in categories),
        category_count=category_count,
        hidden_oracle_id=f"{fixture_id}-hidden-oracle/v1",
        predictive_model_id=f"{fixture_id}-biased-predictor/v1",
        feasibility_id=f"{fixture_id}-hard-validator/v1",
    )


def fixtures() -> tuple[LabFixture, ...]:
    """Return four bounded fixtures spanning the Issue #682 failure modes."""

    t = np.linspace(0.08, 0.92, 48)
    correlated = np.column_stack((t, np.clip(t + 0.045 * np.sin(9 * t), 0, 1)))

    category = np.repeat((0, 1), 24)
    mode_t = np.tile(np.linspace(-1, 1, 24), 2)
    mixed = np.column_stack(
        (
            np.where(category == 0, 0.24 + 0.12 * mode_t, 0.76 + 0.12 * mode_t),
            np.where(category == 0, 0.72 - 0.08 * mode_t, 0.28 + 0.08 * mode_t),
        )
    )

    first_mode = np.column_stack(
        (0.18 + 0.07 * np.linspace(-1, 1, 24), 0.34 - 0.05 * np.linspace(-1, 1, 24))
    )
    second_mode = np.column_stack(
        (0.60 + 0.08 * np.linspace(-1, 1, 24), 0.18 + 0.04 * np.linspace(-1, 1, 24))
    )
    composition = np.vstack((first_mode, second_mode))

    trap_x = np.linspace(0.08, 0.68, 48)
    trap = np.column_stack(
        (trap_x, 0.16 + 0.66 * trap_x + 0.035 * np.sin(12 * trap_x))
    )
    zeros = np.zeros(48, dtype=int)
    return (
        _fixture("correlated-continuous", correlated, zeros),
        _fixture(
            "mixed-categorical-modes",
            mixed,
            category,
            category_count=2,
        ),
        _fixture("constrained-composition", composition, zeros),
        _fixture("offline-optimization-trap", trap, zeros),
    )


def _arrays(fixture: LabFixture) -> tuple[np.ndarray, np.ndarray]:
    return (
        np.asarray(fixture.observations, dtype=float),
        np.asarray(fixture.categories, dtype=int),
    )


def _hard_feasible(
    fixture: LabFixture,
    points: np.ndarray,
    categories: np.ndarray,
) -> np.ndarray:
    inside = np.all((points >= 0) & (points <= 1), axis=1)
    if fixture.fixture_id == "correlated-continuous":
        return inside & (np.abs(points[:, 1] - points[:, 0]) <= 0.15)
    if fixture.fixture_id == "mixed-categorical-modes":
        known_category = (categories >= 0) & (categories < 2)
        category_mode = np.where(
            categories == 0,
            (points[:, 0] <= 0.48) & (points[:, 1] >= 0.50),
            (points[:, 0] >= 0.52) & (points[:, 1] <= 0.50),
        )
        return inside & known_category & category_mode
    if fixture.fixture_id == "constrained-composition":
        return inside & (points[:, 0] + points[:, 1] <= 0.92)
    curve = 0.16 + 0.66 * points[:, 0]
    return inside & (np.abs(points[:, 1] - curve) <= 0.17)


def _hidden_oracle(fixture: LabFixture, points: np.ndarray, categories: np.ndarray) -> np.ndarray:
    if fixture.fixture_id == "mixed-categorical-modes":
        target = np.where(categories == 0, 0.30, 0.72)
        return 1.0 - 2.2 * np.abs(points[:, 0] - target) - 0.3 * np.abs(points[:, 1] - (1 - target))
    if fixture.fixture_id == "constrained-composition":
        return 1.0 - 2.0 * ((points[:, 0] - 0.58) ** 2 + (points[:, 1] - 0.20) ** 2)
    target_y = 0.16 + 0.66 * points[:, 0]
    target_x = 0.62 if fixture.fixture_id == "offline-optimization-trap" else 0.68
    return 1.0 - 1.8 * (points[:, 0] - target_x) ** 2 - 2.5 * np.abs(points[:, 1] - target_y)


def _predictive_value(fixture: LabFixture, points: np.ndarray, categories: np.ndarray) -> np.ndarray:
    actual = _hidden_oracle(fixture, points, categories)
    if fixture.fixture_id != "offline-optimization-trap":
        return actual + 0.04 * np.sin(11 * points[:, 0])
    # A deliberately misspecified model rewards a corner outside observed support.
    trap = 1.8 * np.exp(
        -((points[:, 0] - 0.96) ** 2 + (points[:, 1] - 0.80) ** 2) / 0.008
    )
    return actual + trap


def _mixed_distance(
    left: np.ndarray,
    left_categories: np.ndarray,
    right: np.ndarray,
    right_categories: np.ndarray,
) -> np.ndarray:
    numeric = np.mean((left[:, None, :] - right[None, :, :]) ** 2, axis=2)
    categorical = (left_categories[:, None] != right_categories[None, :]).astype(float)
    return np.sqrt((2 * numeric + categorical) / 3)


def _nearest_observation_distance(
    fixture: LabFixture,
    points: np.ndarray,
    categories: np.ndarray,
) -> np.ndarray:
    observed, observed_categories = _arrays(fixture)
    return _mixed_distance(points, categories, observed, observed_categories).min(axis=1)


def _independent_pool(
    fixture: LabFixture,
    *,
    method: Literal["latin_hypercube", "sobol"],
    budget: int,
    seed: int,
) -> CandidatePool:
    unit = (
        _latin_hypercube_unit(budget, 3, seed)
        if method == "latin_hypercube"
        else _sobol_unit(budget, 3, seed)
    )
    categories = np.minimum(
        (unit[:, 2] * fixture.category_count).astype(int),
        fixture.category_count - 1,
    )
    return CandidatePool(
        points=unit[:, :2],
        categories=categories,
        operation={
            "optional_dependency": None,
            "training_required": False,
            "artifact_bytes": 0,
        },
    )


def _empirical_pool(fixture: LabFixture, *, budget: int, seed: int) -> CandidatePool:
    observed, categories = _arrays(fixture)
    rng = np.random.default_rng(seed)
    indexes = rng.integers(0, len(observed), size=budget)
    return CandidatePool(
        points=observed[indexes].copy(),
        categories=categories[indexes].copy(),
        operation={
            "optional_dependency": None,
            "training_required": False,
            "artifact_bytes": 0,
        },
    )


def _knn_pool(fixture: LabFixture, *, budget: int, seed: int) -> CandidatePool:
    observed, categories = _arrays(fixture)
    rng = np.random.default_rng(seed)
    points: list[np.ndarray] = []
    sampled_categories: list[int] = []
    for _ in range(budget):
        anchor_index = int(rng.integers(len(observed)))
        same_mode = np.flatnonzero(categories == categories[anchor_index])
        same_mode = same_mode[same_mode != anchor_index]
        neighbor_index = int(same_mode[int(rng.integers(len(same_mode)))])
        alpha = float(rng.uniform(*KNN_ALPHA_RANGE))
        points.append(
            (1 - alpha) * observed[anchor_index] + alpha * observed[neighbor_index]
        )
        sampled_categories.append(int(categories[anchor_index]))
    return CandidatePool(
        points=np.asarray(points),
        categories=np.asarray(sampled_categories),
        operation={
            "optional_dependency": None,
            "training_required": False,
            "artifact_bytes": 0,
        },
    )


def _copula_pool(fixture: LabFixture, *, budget: int, seed: int) -> CandidatePool:
    observed, categories = _arrays(fixture)
    rng = np.random.default_rng(seed)
    sampled_categories = rng.choice(categories, size=budget, replace=True)
    points = np.empty((budget, 2), dtype=float)
    for category in range(fixture.category_count):
        output_indexes = np.flatnonzero(sampled_categories == category)
        if not len(output_indexes):
            continue
        mode = observed[categories == category]
        uniforms = np.column_stack(
            (
                rankdata(mode[:, 0], method="average") / (len(mode) + 1),
                rankdata(mode[:, 1], method="average") / (len(mode) + 1),
            )
        )
        gaussian = norm.ppf(np.clip(uniforms, 1e-6, 1 - 1e-6))
        correlation = np.corrcoef(gaussian, rowvar=False)
        correlation = (
            COPULA_CORRELATION_SHRINKAGE * correlation
            + (1 - COPULA_CORRELATION_SHRINKAGE) * np.eye(2)
        )
        latent = rng.multivariate_normal(np.zeros(2), correlation, size=len(output_indexes))
        quantiles = norm.cdf(latent)
        for axis in range(2):
            points[output_indexes, axis] = np.quantile(
                mode[:, axis],
                quantiles[:, axis],
                method=COPULA_QUANTILE_METHOD,
            )
    return CandidatePool(
        points=points,
        categories=sampled_categories.astype(int),
        operation={
            "optional_dependency": "scipy",
            "training_required": True,
            "artifact_bytes": int(2 * 2 * 8),
        },
    )


class _TinyVae:
    """Small NumPy VAE used only to make the deep-candidate decision concrete."""

    def __init__(self, input_dim: int, *, seed: int) -> None:
        rng = np.random.default_rng(seed)
        hidden = VAE_HIDDEN_DIMENSIONS
        latent = VAE_LATENT_DIMENSIONS
        self.parameters = {
            "encoder": rng.normal(0, 0.2, (input_dim, hidden)),
            "encoder_bias": np.zeros(hidden),
            "mean": rng.normal(0, 0.2, (hidden, latent)),
            "mean_bias": np.zeros(latent),
            "log_variance": rng.normal(0, 0.05, (hidden, latent)),
            "log_variance_bias": np.zeros(latent),
            "decoder": rng.normal(0, 0.2, (latent, hidden)),
            "decoder_bias": np.zeros(hidden),
            "output": rng.normal(0, 0.2, (hidden, input_dim)),
            "output_bias": np.zeros(input_dim),
        }
        self.seed = seed

    @staticmethod
    def _sigmoid(value: np.ndarray) -> np.ndarray:
        return 1 / (1 + np.exp(-np.clip(value, -30, 30)))

    def fit(
        self,
        values: np.ndarray,
        *,
        epochs: int = VAE_EPOCHS,
    ) -> tuple[float, ...]:
        rng = np.random.default_rng(self.seed + 1)
        first_moment = {key: np.zeros_like(value) for key, value in self.parameters.items()}
        second_moment = {key: np.zeros_like(value) for key, value in self.parameters.items()}
        history: list[float] = []
        beta = VAE_BETA
        for step in range(1, epochs + 1):
            parameter = self.parameters
            encoder_hidden = np.tanh(values @ parameter["encoder"] + parameter["encoder_bias"])
            mean = encoder_hidden @ parameter["mean"] + parameter["mean_bias"]
            raw_log_variance = (
                encoder_hidden @ parameter["log_variance"]
                + parameter["log_variance_bias"]
            )
            log_variance = np.clip(raw_log_variance, -4, 4)
            standard_deviation = np.exp(0.5 * log_variance)
            noise = rng.normal(size=mean.shape)
            latent = mean + standard_deviation * noise
            decoder_hidden = np.tanh(
                latent @ parameter["decoder"] + parameter["decoder_bias"]
            )
            reconstruction = self._sigmoid(
                decoder_hidden @ parameter["output"] + parameter["output_bias"]
            )
            reconstruction_loss = float(np.mean((reconstruction - values) ** 2))
            kl = float(
                0.5
                * np.mean(
                    np.sum(
                        mean**2 + np.exp(log_variance) - 1 - log_variance,
                        axis=1,
                    )
                )
            )
            history.append(reconstruction_loss + beta * kl)

            reconstruction_gradient = (
                2 * (reconstruction - values) / reconstruction.size
            )
            output_linear_gradient = (
                reconstruction_gradient * reconstruction * (1 - reconstruction)
            )
            gradients: dict[str, np.ndarray] = {}
            gradients["output"] = decoder_hidden.T @ output_linear_gradient
            gradients["output_bias"] = output_linear_gradient.sum(axis=0)
            decoder_hidden_gradient = (
                output_linear_gradient @ parameter["output"].T
            )
            decoder_linear_gradient = decoder_hidden_gradient * (
                1 - decoder_hidden**2
            )
            gradients["decoder"] = latent.T @ decoder_linear_gradient
            gradients["decoder_bias"] = decoder_linear_gradient.sum(axis=0)
            latent_gradient = decoder_linear_gradient @ parameter["decoder"].T

            sample_count = len(values)
            mean_gradient = latent_gradient + beta * mean / sample_count
            log_variance_gradient = (
                latent_gradient * noise * 0.5 * standard_deviation
                + beta
                * 0.5
                * (np.exp(log_variance) - 1)
                / sample_count
            )
            log_variance_gradient *= (
                (raw_log_variance >= -4) & (raw_log_variance <= 4)
            )
            gradients["mean"] = encoder_hidden.T @ mean_gradient
            gradients["mean_bias"] = mean_gradient.sum(axis=0)
            gradients["log_variance"] = (
                encoder_hidden.T @ log_variance_gradient
            )
            gradients["log_variance_bias"] = log_variance_gradient.sum(axis=0)
            encoder_hidden_gradient = (
                mean_gradient @ parameter["mean"].T
                + log_variance_gradient @ parameter["log_variance"].T
            )
            encoder_linear_gradient = encoder_hidden_gradient * (
                1 - encoder_hidden**2
            )
            gradients["encoder"] = values.T @ encoder_linear_gradient
            gradients["encoder_bias"] = encoder_linear_gradient.sum(axis=0)

            for key, gradient in gradients.items():
                gradient = np.clip(gradient, -1, 1)
                first_moment[key] = 0.9 * first_moment[key] + 0.1 * gradient
                second_moment[key] = (
                    0.999 * second_moment[key] + 0.001 * gradient**2
                )
                corrected_first = first_moment[key] / (1 - 0.9**step)
                corrected_second = second_moment[key] / (1 - 0.999**step)
                self.parameters[key] -= (
                    VAE_LEARNING_RATE
                    * corrected_first
                    / (np.sqrt(corrected_second) + 1e-8)
                )
        return tuple(history)

    def sample(self, count: int, *, seed: int) -> np.ndarray:
        rng = np.random.default_rng(seed)
        latent = rng.normal(size=(count, VAE_LATENT_DIMENSIONS))
        hidden = np.tanh(
            latent @ self.parameters["decoder"] + self.parameters["decoder_bias"]
        )
        return self._sigmoid(
            hidden @ self.parameters["output"] + self.parameters["output_bias"]
        )

    @property
    def artifact_bytes(self) -> int:
        return sum(value.nbytes for value in self.parameters.values())


def _vae_pool(fixture: LabFixture, *, budget: int, seed: int) -> CandidatePool:
    observed, categories = _arrays(fixture)
    encoded = np.column_stack(
        (
            observed,
            np.eye(fixture.category_count, dtype=float)[categories],
        )
    )
    model = _TinyVae(encoded.shape[1], seed=seed)
    history = model.fit(encoded)
    generated = model.sample(budget, seed=seed + 2)
    sampled_categories = generated[:, 2:].argmax(axis=1).astype(int)
    return CandidatePool(
        points=generated[:, :2],
        categories=sampled_categories,
        operation={
            "optional_dependency": "numpy-only research implementation",
            "training_required": True,
            "epochs": len(history),
            "final_training_loss": round(float(history[-1]), 8),
            "artifact_bytes": model.artifact_bytes,
            "safe_production_adapter": False,
        },
    )


def generate_pool(
    fixture: LabFixture,
    generator_id: str,
    *,
    budget: int,
    seed: int,
) -> CandidatePool:
    if generator_id in {"latin_hypercube", "sobol"}:
        return _independent_pool(
            fixture,
            method=generator_id,
            budget=budget,
            seed=seed,
        )
    if generator_id == "empirical_rows":
        return _empirical_pool(fixture, budget=budget, seed=seed)
    if generator_id == "knn_local":
        return _knn_pool(fixture, budget=budget, seed=seed)
    if generator_id == "gaussian_rank_copula":
        return _copula_pool(fixture, budget=budget, seed=seed)
    if generator_id == "tiny_vae_research":
        if fixture.fixture_id != "mixed-categorical-modes":
            raise ValueError("tiny VAE spike is limited to mixed-categorical-modes")
        return _vae_pool(fixture, budget=budget, seed=seed)
    raise ValueError(f"unknown research generator: {generator_id}")


def _select_batch(
    fixture: LabFixture,
    pool: CandidatePool,
    *,
    policy: Literal["direct_objective", "conservative_diverse"],
    batch_size: int,
) -> tuple[np.ndarray, dict[str, object]]:
    feasible = _hard_feasible(fixture, pool.points, pool.categories)
    indexes = np.flatnonzero(feasible)
    if not len(indexes):
        return np.asarray([], dtype=int), {
            "shortfall": batch_size,
            "diversity_weight": (
                DIRECT_DIVERSITY_WEIGHT
                if policy == "direct_objective"
                else CONSERVATIVE_DIVERSITY_WEIGHT
            ),
            "policy_digest": _digest({"policy": policy, "version": "1.0.0"}),
        }
    predicted = _predictive_value(
        fixture,
        pool.points[indexes],
        pool.categories[indexes],
    )
    nearest = _nearest_observation_distance(
        fixture,
        pool.points[indexes],
        pool.categories[indexes],
    )
    if policy == "direct_objective":
        utility = predicted
        penalty = np.zeros_like(predicted)
        diversity_weight = DIRECT_DIVERSITY_WEIGHT
    else:
        penalty = CONSERVATIVE_PENALTY_WEIGHT * np.maximum(
            nearest - CONSERVATIVE_NEAREST_THRESHOLD,
            0,
        )
        utility = predicted - penalty
        diversity_weight = CONSERVATIVE_DIVERSITY_WEIGHT

    selected_local: list[int] = []
    normalized_utility = (
        (utility - utility.min()) / (np.ptp(utility) + 1e-12)
        if len(utility) > 1
        else np.ones_like(utility)
    )
    while len(selected_local) < min(batch_size, len(indexes)):
        remaining = [
            index for index in range(len(indexes)) if index not in selected_local
        ]
        if not selected_local:
            choice = max(remaining, key=lambda index: (utility[index], -index))
        else:
            distances = _mixed_distance(
                pool.points[indexes[remaining]],
                pool.categories[indexes[remaining]],
                pool.points[indexes[selected_local]],
                pool.categories[indexes[selected_local]],
            ).min(axis=1)
            score = normalized_utility[remaining] + diversity_weight * distances
            choice = remaining[int(np.argmax(score))]
        selected_local.append(choice)
    selected = indexes[np.asarray(selected_local, dtype=int)]
    return selected, {
        "shortfall": batch_size - len(selected),
        "diversity_weight": diversity_weight,
        "conservative_penalty_mean": round(float(penalty[selected_local].mean()), 8),
        "policy_digest": _digest(
            {
                "policy": policy,
                "version": "1.0.0",
                "nearest_threshold": CONSERVATIVE_NEAREST_THRESHOLD,
                "penalty_weight": (
                    CONSERVATIVE_PENALTY_WEIGHT
                    if policy == "conservative_diverse"
                    else 0
                ),
                "diversity_weight": diversity_weight,
                "tie_break": "source-index",
            }
        ),
    }


def _oracle_reference(fixture: LabFixture) -> float:
    grid = np.linspace(0, 1, 161)
    points = np.asarray([(left, right) for left in grid for right in grid])
    category_values = range(fixture.category_count)
    best = -np.inf
    for category in category_values:
        categories = np.full(len(points), category, dtype=int)
        feasible = _hard_feasible(fixture, points, categories)
        if feasible.any():
            best = max(
                best,
                float(
                    _hidden_oracle(
                        fixture,
                        points[feasible],
                        categories[feasible],
                    ).max()
                ),
            )
    return best


def evaluate_run(
    fixture: LabFixture,
    generator_id: str,
    policy: Literal["direct_objective", "conservative_diverse"],
    *,
    budget: int,
    batch_size: int,
    seed: int,
) -> dict[str, object]:
    pool = generate_pool(fixture, generator_id, budget=budget, seed=seed)
    feasible = _hard_feasible(fixture, pool.points, pool.categories)
    nearest = _nearest_observation_distance(
        fixture,
        pool.points,
        pool.categories,
    )
    observed, observed_categories = _arrays(fixture)
    selected, selection = _select_batch(
        fixture,
        pool,
        policy=policy,
        batch_size=batch_size,
    )
    selected_actual = _hidden_oracle(
        fixture,
        pool.points[selected],
        pool.categories[selected],
    )
    selected_predicted = _predictive_value(
        fixture,
        pool.points[selected],
        pool.categories[selected],
    )
    support = {
        "supported": float(np.mean(nearest <= 0.12)),
        "caution": float(np.mean((nearest > 0.12) & (nearest <= 0.25))),
        "extrapolated": float(np.mean(nearest > 0.25)),
    }
    pool_conditions = [
        (
            round(float(point[0]), 10),
            round(float(point[1]), 10),
            int(category),
        )
        for point, category in zip(pool.points, pool.categories, strict=True)
    ]
    unique_conditions = set(pool_conditions)
    observed_conditions = {
        (
            round(float(point[0]), 10),
            round(float(point[1]), 10),
            int(category),
        )
        for point, category in zip(observed, observed_categories, strict=True)
    }
    observed_modes = {
        (int(category), "low-x" if point[0] < 0.5 else "high-x")
        for point, category in zip(observed, observed_categories, strict=True)
    }
    generated_modes = {
        (int(category), "low-x" if point[0] < 0.5 else "high-x")
        for point, category in zip(pool.points, pool.categories, strict=True)
    }
    selected_pairwise = (
        _mixed_distance(
            pool.points[selected],
            pool.categories[selected],
            pool.points[selected],
            pool.categories[selected],
        )
        if len(selected) > 1
        else np.asarray([[0.0]])
    )
    pairwise_distances = (
        selected_pairwise[np.triu_indices(len(selected), k=1)]
        if len(selected) > 1
        else np.asarray([], dtype=float)
    )
    exploitation = (
        (nearest[selected] > 0.15)
        & ((selected_predicted - selected_actual) > 0.20)
    )
    result = {
        "fixture_id": fixture.fixture_id,
        "fixture_version": fixture.fixture_version,
        "generator_id": generator_id,
        "generator_version": GENERATOR_VERSION,
        "selection_policy": policy,
        "seed": seed,
        "budget": budget,
        "batch_size": batch_size,
        "identity": {
            "dataset_view_digest": _digest(asdict(fixture)),
            "training_snapshot_digest": _digest(
                {
                    "fixture": fixture.fixture_id,
                    "observations": fixture.observations,
                    "categories": fixture.categories,
                    "cohort": "all-observed-rows",
                }
            ),
            "task_digest": _digest(
                {"fixture": fixture.fixture_id, "task": "two-numeric-mixed/v1"}
            ),
            "feature_recipe_digest": _digest({"identity": "unit-input/v1"}),
            "validation_plan_digest": _digest(
                {"hard_validator": fixture.feasibility_id}
            ),
            "design_space_digest": _digest(
                {"bounds": [[0, 1], [0, 1]], "categories": fixture.category_count}
            ),
            "design_prior_digest": _digest(
                {
                    "observations": fixture.observations,
                    "categories": fixture.categories,
                }
            ),
            "predictive_model_digest": _digest(
                {"predictor": fixture.predictive_model_id}
            ),
            "predictive_capability_digest": _digest(
                {
                    "representation": "point-estimate-with-support-status",
                    "target": "predicted_fixture_score",
                    "version": "1.0.0",
                }
            ),
            "objective_digest": _digest(
                {
                    "target": "predicted_fixture_score",
                    "direction": "maximize",
                    "source": fixture.predictive_model_id,
                }
            ),
            "generator_parameter_digest": _digest(
                generator_parameter_payload(
                    generator_id,
                    seed=seed,
                    budget=budget,
                )
            ),
            "selection_policy_digest": selection["policy_digest"],
            "support_policy_digest": _digest(
                {"support_policy": SUPPORT_POLICY, "version": "1.0.0"}
            ),
            "pool_budget_digest": _digest(
                {
                    "pool_size": budget,
                    "rejection_budget": budget,
                    "repair_budget": 0,
                }
            ),
            "hidden_oracle_digest": _digest(
                {"hidden_oracle": fixture.hidden_oracle_id}
            ),
        },
        "feasibility": {
            "hard_violation_rate": round(float(1 - feasible.mean()), 8),
            "rejection_rate": round(float(1 - feasible.mean()), 8),
            "proposal_shortfall": selection["shortfall"],
            "repair_rate": 0.0,
        },
        "plausibility": {
            "mean_nearest_distance": round(float(nearest.mean()), 8),
            "marginal_mean_shift": round(
                float(np.mean(np.abs(pool.points.mean(axis=0) - observed.mean(axis=0)))),
                8,
            ),
            "pairwise_correlation_error": round(
                float(
                    abs(
                        np.corrcoef(pool.points, rowvar=False)[0, 1]
                        - np.corrcoef(observed, rowvar=False)[0, 1]
                    )
                ),
                8,
            ),
            "two_sample_moment_distance": round(
                float(
                    np.linalg.norm(pool.points.mean(axis=0) - observed.mean(axis=0))
                    + np.linalg.norm(
                        np.cov(pool.points, rowvar=False)
                        - np.cov(observed, rowvar=False)
                    )
                ),
                8,
            ),
            "typical_rate": round(float(np.mean(nearest <= 0.05)), 8),
            "near_edge_rate": round(
                float(np.mean((nearest > 0.05) & (nearest <= 0.20))),
                8,
            ),
            "low_density_rate": round(float(np.mean(nearest > 0.20)), 8),
            "category_coverage": round(
                len(set(int(value) for value in pool.categories))
                / fixture.category_count,
                8,
            ),
            "category_frequency_error": round(
                float(
                    np.mean(
                        [
                            abs(
                                np.mean(pool.categories == category)
                                - np.mean(observed_categories == category)
                            )
                            for category in range(fixture.category_count)
                        ]
                    )
                ),
                8,
            ),
            "mode_coverage": round(
                len(generated_modes & observed_modes) / max(1, len(observed_modes)),
                8,
            ),
        },
        "novelty": {
            "observed_duplicate_rate": round(
                float(
                    np.mean(
                        [
                            condition in observed_conditions
                            for condition in pool_conditions
                        ]
                    )
                ),
                8,
            ),
            "pool_duplicate_rate": round(
                1 - len(unique_conditions) / budget,
                8,
            ),
            "frontier_rate": round(float(np.mean(nearest > 0.20)), 8),
            "unseen_mode_rate": round(
                len(generated_modes - observed_modes)
                / max(1, len(generated_modes)),
                8,
            ),
        },
        "predictive_safety": {
            "support_rates": {key: round(value, 8) for key, value in support.items()},
            "hidden_oracle_regret": (
                round(float(_oracle_reference(fixture) - selected_actual.max()), 8)
                if len(selected)
                else None
            ),
            "predicted_actual_gap": (
                round(float(np.mean(selected_predicted - selected_actual)), 8)
                if len(selected)
                else None
            ),
            "optimizer_exploitation_rate": (
                round(float(exploitation.mean()), 8) if len(selected) else None
            ),
        },
        "batch_quality": {
            "selected": len(selected),
            "best_actual_objective": (
                round(float(selected_actual.max()), 8) if len(selected) else None
            ),
            "median_actual_objective": (
                round(float(np.median(selected_actual)), 8)
                if len(selected)
                else None
            ),
            "mean_pairwise_diversity": (
                round(float(pairwise_distances.mean()), 8)
                if len(pairwise_distances)
                else 0.0
            ),
            "near_duplicate_rate": (
                round(
                    float(
                        np.mean(
                            pairwise_distances
                            <= NEAR_DUPLICATE_DISTANCE_THRESHOLD
                        )
                    ),
                    8,
                )
                if len(pairwise_distances)
                else 0.0
            ),
            "near_duplicate_policy": {
                "version": NEAR_DUPLICATE_POLICY_VERSION,
                "distance_threshold": NEAR_DUPLICATE_DISTANCE_THRESHOLD,
            },
            "category_coverage": (
                round(
                    len(set(int(value) for value in pool.categories[selected]))
                    / fixture.category_count,
                    8,
                )
                if len(selected)
                else 0.0
            ),
        },
        "selection": selection,
        "operation": pool.operation,
    }
    return result


def build_report(
    *,
    seeds: tuple[int, ...] = (17, 41, 83),
    budget: int = 128,
    batch_size: int = 8,
) -> dict[str, object]:
    if len(seeds) < 2 or len(set(seeds)) != len(seeds):
        raise ValueError("Generative Design Lab requires two or more unique seeds")
    if budget < batch_size or batch_size < 2:
        raise ValueError("budget must cover a batch of at least two candidates")
    fixture_set = fixtures()
    runs = [
        evaluate_run(
            fixture,
            generator_id,
            policy,
            budget=budget,
            batch_size=batch_size,
            seed=seed,
        )
        for fixture in fixture_set
        for generator_id in GENERATOR_IDS
        for seed in seeds
        for policy in SELECTION_POLICIES
    ]
    # The deep spike is intentionally bounded to one representative small-data fixture.
    mixed_fixture = next(
        fixture
        for fixture in fixture_set
        if fixture.fixture_id == "mixed-categorical-modes"
    )
    runs.extend(
        evaluate_run(
            mixed_fixture,
            "tiny_vae_research",
            policy,
            budget=budget,
            batch_size=batch_size,
            seed=seed,
        )
        for seed in seeds
        for policy in SELECTION_POLICIES
    )

    protocol = {
        "schema_version": PROTOCOL_VERSION,
        "seeds": seeds,
        "candidate_budget": budget,
        "pool_size": budget,
        "rejection_budget": budget,
        "repair_budget": 0,
        "batch_size": batch_size,
        "support_policy": SUPPORT_POLICY,
        "near_duplicate_policy": {
            "version": NEAR_DUPLICATE_POLICY_VERSION,
            "distance_threshold": NEAR_DUPLICATE_DISTANCE_THRESHOLD,
        },
        "generator_ids": (*GENERATOR_IDS, "tiny_vae_research"),
        "generator_parameter_contracts": {
            generator_id: generator_parameter_payload(
                generator_id,
                seed=seeds[0],
                budget=budget,
            )["parameters"]
            for generator_id in (*GENERATOR_IDS, "tiny_vae_research")
        },
        "selection_policies": SELECTION_POLICIES,
        "fixed_identity_fields": (
            "dataset_view_digest",
            "training_snapshot_digest",
            "task_digest",
            "feature_recipe_digest",
            "validation_plan_digest",
            "design_space_digest",
            "design_prior_digest",
            "predictive_model_digest",
            "predictive_capability_digest",
            "objective_digest",
            "generator_parameter_digest",
            "selection_policy_digest",
            "support_policy_digest",
            "pool_budget_digest",
            "hidden_oracle_digest",
        ),
        "production_registry_changed": False,
    }
    adoption_memos = (
        {
            "generator_id": "knn_local",
            "status": "experimental",
            "primary_criterion": "low hard-violation and stable local plausibility",
            "trade_off": "preserves observed modes but explores slowly between modes",
            "registry_changed": False,
        },
        {
            "generator_id": "gaussian_rank_copula",
            "status": "experimental",
            "primary_criterion": "joint continuous fidelity with non-duplicate proposals",
            "trade_off": "category-conditioned ranks help mixed modes; hard composition constraints still require independent validation",
            "registry_changed": False,
        },
        {
            "generator_id": "tiny_vae_research",
            "status": "no_adopt",
            "primary_criterion": "representative small-data value beyond kNN/copula",
            "trade_off": "training and a new safe runtime/artifact contract add cost without evidence across all fixtures",
            "reason": "one small mixed fixture cannot establish calibration, constraint safety, or seed stability for production",
            "registry_changed": False,
        },
        {
            "selection_policy": "conservative_diverse",
            "status": "experimental",
            "primary_criterion": "reduce OOD optimizer exploitation while retaining batch diversity",
            "trade_off": "may omit high predicted-value frontier candidates",
            "registry_changed": False,
        },
    )
    generator_summaries: list[dict[str, object]] = []
    summary_generators = (*GENERATOR_IDS, "tiny_vae_research")
    for fixture in fixture_set:
        for generator_id in summary_generators:
            for policy in SELECTION_POLICIES:
                matching = [
                    run
                    for run in runs
                    if run["fixture_id"] == fixture.fixture_id
                    and run["generator_id"] == generator_id
                    and run["selection_policy"] == policy
                ]
                if not matching:
                    continue
                regrets = np.asarray(
                    [
                        run["predictive_safety"]["hidden_oracle_regret"]
                        for run in matching
                    ],
                    dtype=float,
                )
                exploitation = np.asarray(
                    [
                        run["predictive_safety"]["optimizer_exploitation_rate"]
                        for run in matching
                    ],
                    dtype=float,
                )
                generator_summaries.append(
                    {
                        "fixture_id": fixture.fixture_id,
                        "generator_id": generator_id,
                        "selection_policy": policy,
                        "seed_count": len(matching),
                        "mean_hard_violation_rate": round(
                            float(
                                np.mean(
                                    [
                                        run["feasibility"]["hard_violation_rate"]
                                        for run in matching
                                    ]
                                )
                            ),
                            8,
                        ),
                        "mean_nearest_distance": round(
                            float(
                                np.mean(
                                    [
                                        run["plausibility"]["mean_nearest_distance"]
                                        for run in matching
                                    ]
                                )
                            ),
                            8,
                        ),
                        "mean_hidden_oracle_regret": round(
                            float(regrets.mean()),
                            8,
                        ),
                        "regret_seed_range": round(
                            float(np.ptp(regrets)),
                            8,
                        ),
                        "mean_optimizer_exploitation_rate": round(
                            float(exploitation.mean()),
                            8,
                        ),
                        "exploitation_seed_range": round(
                            float(np.ptp(exploitation)),
                            8,
                        ),
                        "mean_batch_diversity": round(
                            float(
                                np.mean(
                                    [
                                        run["batch_quality"][
                                            "mean_pairwise_diversity"
                                        ]
                                        for run in matching
                                    ]
                                )
                            ),
                            8,
                        ),
                        "mean_near_duplicate_rate": round(
                            float(
                                np.mean(
                                    [
                                        run["batch_quality"][
                                            "near_duplicate_rate"
                                        ]
                                        for run in matching
                                    ]
                                )
                            ),
                            8,
                        ),
                    }
                )
    reproducible = {
        "protocol": protocol,
        "fixtures": [asdict(fixture) for fixture in fixture_set],
        "runs": runs,
        "generator_summaries": generator_summaries,
        "adoption_memos": adoption_memos,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        **reproducible,
        "result_digest": _digest(reproducible),
        "limitations": (
            "synthetic hidden-oracle fixtures are not evidence of real materials feasibility",
            "generator likelihood is never used as hard feasibility or predictive support",
            "the tiny VAE is research code, not a loadable production artifact",
            "GPU, privacy, active learning, and predictive-model retraining are out of scope",
        ),
    }


def render_adoption_memo(report: dict[str, object]) -> str:
    """Render the human review surface from the immutable numeric report."""

    summaries = report["generator_summaries"]

    def summary(fixture_id: str, generator_id: str, policy: str) -> dict[str, object]:
        return next(
            item
            for item in summaries
            if item["fixture_id"] == fixture_id
            and item["generator_id"] == generator_id
            and item["selection_policy"] == policy
        )

    mixed_knn = summary(
        "mixed-categorical-modes",
        "knn_local",
        "direct_objective",
    )
    mixed_copula = summary(
        "mixed-categorical-modes",
        "gaussian_rank_copula",
        "direct_objective",
    )
    composition_knn = summary(
        "constrained-composition",
        "knn_local",
        "direct_objective",
    )
    composition_copula = summary(
        "constrained-composition",
        "gaussian_rank_copula",
        "direct_objective",
    )
    mixed_vae = summary(
        "mixed-categorical-modes",
        "tiny_vae_research",
        "direct_objective",
    )
    vae_operation = next(
        run["operation"]
        for run in report["runs"]
        if run["generator_id"] == "tiny_vae_research"
    )
    trap_rows = [
        (
            generator,
            summary(
                "offline-optimization-trap",
                generator,
                "direct_objective",
            ),
            summary(
                "offline-optimization-trap",
                generator,
                "conservative_diverse",
            ),
        )
        for generator in ("latin_hypercube", "sobol")
    ]
    trap_table = "\n".join(
        "| {generator} | {direct:.3f} | {conservative:.3f} |".format(
            generator=generator,
            direct=direct["mean_optimizer_exploitation_rate"],
            conservative=conservative["mean_optimizer_exploitation_rate"],
        )
        for generator, direct, conservative in trap_rows
    )
    return f"""# Generative Design Lab adoption memo

<!-- generated from {report["schema_version"]}; result-digest: {report["result_digest"]} -->

## 判断

- `kNN local`: **experimental継続**。少量・混合modeでhard violationを出さず、最も観測近傍に留まる。一方でmode間探索は弱い。
- `Gaussian rank copula`: **experimental継続**。混合category内で非重複候補を作り、kNNより広げられる。ただしsimplex／total constraintはcopula likelihoodと別に検証し、違反をrejectする必要がある。
- `tiny VAE`: **no-adopt**。小さなmixed fixtureで実学習・samplingは再現できたが、一つのfixtureではseed安定性、constraint安全、校正を示せず、安全なdata-only artifact／allow-list runtimeもない。
- `conservative_diverse`: **experimental継続**。予測値だけの選抜よりOOD攻略を抑え、batch diversityを残した。production registryは変更しない。

## 固定protocol

- 再生成: `uv run python backend/scripts/experiments/run_generative_design_lab.py`
- 数値正本: [`generative-design-lab-report.json`](generative-design-lab-report.json)
- fixture: correlated continuous / mixed categorical modes / constrained composition / offline optimization trap
- seed: `{", ".join(str(seed) for seed in report["protocol"]["seeds"])}`
- candidate budget: `{report["protocol"]["candidate_budget"]}` / batch: `{report["protocol"]["batch_size"]}`
- generator、selection、Dataset View、Task、Feature Recipe、Validation Plan、Design Space、Prior、Predictive Model、Objective、hidden oracleのdigestをrunごとに固定
- hard feasibility、plausibility、predictive support、novelty、objective、batch diversityは別指標

## kNNとcopula

| fixture | generator | hard violation | mean nearest distance |
| --- | --- | ---: | ---: |
| mixed modes | kNN | {mixed_knn["mean_hard_violation_rate"]:.3f} | {mixed_knn["mean_nearest_distance"]:.3f} |
| mixed modes | copula | {mixed_copula["mean_hard_violation_rate"]:.3f} | {mixed_copula["mean_nearest_distance"]:.3f} |
| composition | kNN | {composition_knn["mean_hard_violation_rate"]:.3f} | {composition_knn["mean_nearest_distance"]:.3f} |
| composition | copula | {composition_copula["mean_hard_violation_rate"]:.3f} | {composition_copula["mean_nearest_distance"]:.3f} |

kNNは観測mode内の補間なので、今回のcomposition fixtureではtotal constraintを保った。
copulaは各categoryのrank相関と周辺分布を保ちながら重複を避けたが、joint densityはhard constraintの代替ではない。

## OOD optimization trap

値は「選抜batch中、観測supportから離れ、予測とhidden oracleが大きく乖離した候補」の比率。

| generator | direct objective | conservative + diversity |
| --- | ---: | ---: |
{trap_table}

保守的penaltyは予測値と一体化したsupport scoreではない。
保存されたnearest distanceから独立に計算し、hard validatorを通った候補へだけ適用した。

## Deep candidate

NumPyだけの小さなVAEをmixed fixtureの各seedで240 epoch学習し、同じcandidate budgetを生成した。
hard violationは平均{mixed_vae["mean_hard_violation_rate"]:.3f}、mean nearest distanceは
{mixed_vae["mean_nearest_distance"]:.3f}、parameter artifact相当は
{vae_operation["artifact_bytes"]} bytesだった。
これは依存とartifact境界を具体的に評価するspikeであり、production runtimeではない。
学習済みweightを任意Pythonとして読み込まず、Lab結果からregistryを自動変更しない。

## 限界

- synthetic hidden oracleは実材料のscience／equipment feasibilityを証明しない。
- wall-clock値は環境依存なのでresult digestへ入れていない。bounded epoch、候補数、parameter bytesをoperation evidenceとした。
- generator likelihoodをhard feasibilityまたはpredictive supportに使用していない。
- GPU、privacy保証、active learning、予測モデル再学習は評価していない。
"""
