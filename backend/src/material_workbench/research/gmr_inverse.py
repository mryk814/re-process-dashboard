from __future__ import annotations

from dataclasses import asdict, dataclass
from time import perf_counter
from typing import Callable

import numpy as np
from scipy.special import logsumexp


@dataclass(frozen=True)
class ConstraintSet:
    lower: np.ndarray
    upper: np.ndarray
    minimum_opposite_sign_product: float = 0.20

    def rejection_reasons(self, point: np.ndarray) -> tuple[str, ...]:
        reasons: list[str] = []
        if np.any(point < self.lower) or np.any(point > self.upper):
            reasons.append("outside_bounds")
        if point[0] * point[1] > -self.minimum_opposite_sign_product:
            reasons.append("cross_field_mode_constraint")
        return tuple(reasons)

    def filter(self, points: np.ndarray) -> tuple[np.ndarray, int]:
        accepted = [point for point in points if not self.rejection_reasons(point)]
        return np.asarray(accepted, dtype=float), len(points) - len(accepted)


@dataclass(frozen=True)
class GmrCandidate:
    point: tuple[float, ...]
    component: int
    conditional_weight: float
    conditional_log_density: float


class JointGaussianMixture:
    """Small deterministic EM/GMR implementation for the research spike only."""

    def __init__(self, components: int = 2, regularization: float = 1e-5) -> None:
        if components < 1:
            raise ValueError("components must be positive")
        self.components = components
        self.regularization = regularization
        self.center: np.ndarray | None = None
        self.scale: np.ndarray | None = None
        self.weights: np.ndarray | None = None
        self.means: np.ndarray | None = None
        self.covariances: np.ndarray | None = None

    def fit(self, x: np.ndarray, y: np.ndarray, iterations: int = 120) -> "JointGaussianMixture":
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float).reshape(-1, 1)
        if x.ndim != 2 or len(x) != len(y):
            raise ValueError("x and y must have matching rows")
        joint = np.column_stack([x, y])
        self.center = joint.mean(axis=0)
        self.scale = joint.std(axis=0)
        self.scale[self.scale < 1e-9] = 1.0
        z = (joint - self.center) / self.scale
        order = np.argsort(z[:, 0])
        groups = np.array_split(order, self.components)
        self.weights = np.full(self.components, 1 / self.components)
        self.means = np.stack([z[group].mean(axis=0) for group in groups])
        base_covariance = np.cov(z, rowvar=False) + np.eye(z.shape[1]) * self.regularization
        self.covariances = np.stack([base_covariance.copy() for _ in groups])

        for _ in range(iterations):
            log_responsibilities = np.column_stack([
                np.log(self.weights[index] + 1e-15)
                + _normal_log_density(z, self.means[index], self.covariances[index])
                for index in range(self.components)
            ])
            log_responsibilities -= logsumexp(
                log_responsibilities,
                axis=1,
                keepdims=True,
            )
            responsibilities = np.exp(log_responsibilities)
            counts = responsibilities.sum(axis=0) + 1e-12
            self.weights = counts / len(z)
            self.means = (responsibilities.T @ z) / counts[:, None]
            for index in range(self.components):
                delta = z - self.means[index]
                weighted = responsibilities[:, index, None] * delta
                covariance = weighted.T @ delta / counts[index]
                self.covariances[index] = covariance + (
                    np.eye(z.shape[1]) * self.regularization
                )
        return self

    def conditional_modes(self, target: float, minimum_weight: float = 0.03) -> list[GmrCandidate]:
        if any(
            value is None
            for value in (
                self.center,
                self.scale,
                self.weights,
                self.means,
                self.covariances,
            )
        ):
            raise RuntimeError("fit must be called first")
        assert self.center is not None
        assert self.scale is not None
        assert self.weights is not None
        assert self.means is not None
        assert self.covariances is not None
        target_scaled = (target - self.center[-1]) / self.scale[-1]
        component_logs: list[float] = []
        conditional_means: list[np.ndarray] = []
        conditional_covariances: list[np.ndarray] = []
        for index in range(self.components):
            mean = self.means[index]
            covariance = self.covariances[index]
            variance_y = max(float(covariance[-1, -1]), self.regularization)
            covariance_xy = covariance[:-1, -1]
            conditional_mean = mean[:-1] + (
                covariance_xy / variance_y * (target_scaled - mean[-1])
            )
            conditional_covariance = covariance[:-1, :-1] - np.outer(
                covariance_xy,
                covariance_xy,
            ) / variance_y
            conditional_covariance += (
                np.eye(len(conditional_mean)) * self.regularization
            )
            component_logs.append(
                float(
                    np.log(self.weights[index] + 1e-15)
                    - 0.5
                    * (
                        np.log(2 * np.pi * variance_y)
                        + (target_scaled - mean[-1]) ** 2 / variance_y
                    )
                )
            )
            conditional_means.append(conditional_mean)
            conditional_covariances.append(conditional_covariance)
        normalized_logs = np.asarray(component_logs) - logsumexp(component_logs)
        result: list[GmrCandidate] = []
        for index, log_weight in enumerate(normalized_logs):
            weight = float(np.exp(log_weight))
            if weight < minimum_weight:
                continue
            scaled_point = conditional_means[index]
            point = scaled_point * self.scale[:-1] + self.center[:-1]
            log_density = float(
                log_weight
                + _normal_log_density(
                    scaled_point.reshape(1, -1),
                    conditional_means[index],
                    conditional_covariances[index],
                )[0]
            )
            result.append(GmrCandidate(
                point=tuple(float(value) for value in point),
                component=index,
                conditional_weight=weight,
                conditional_log_density=log_density,
            ))
        return sorted(result, key=lambda item: item.conditional_weight, reverse=True)


def _normal_log_density(
    values: np.ndarray,
    mean: np.ndarray,
    covariance: np.ndarray,
) -> np.ndarray:
    delta = values - mean
    sign, log_determinant = np.linalg.slogdet(covariance)
    if sign <= 0:
        raise ValueError("covariance must be positive definite")
    solved = np.linalg.solve(covariance, delta.T).T
    return -0.5 * (
        len(mean) * np.log(2 * np.pi)
        + log_determinant
        + np.sum(delta * solved, axis=1)
    )


def synthetic_process_data(
    rows: int = 360,
    seed: int = 453,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    modes = rng.integers(0, 2, size=rows)
    latent = rng.uniform(-1.0, 1.0, size=rows)
    x = np.empty((rows, 2), dtype=float)
    first = modes == 0
    x[first, 0] = -1.50 + 0.48 * latent[first] + rng.normal(0, 0.06, first.sum())
    x[first, 1] = 1.20 + 0.32 * latent[first] + rng.normal(0, 0.05, first.sum())
    x[~first, 0] = 1.50 - 0.42 * latent[~first] + rng.normal(0, 0.06, (~first).sum())
    x[~first, 1] = -1.20 + 0.36 * latent[~first] + rng.normal(0, 0.05, (~first).sum())
    y = true_process_response(x) + rng.normal(0, 0.35, rows)
    return x, y, modes


def true_process_response(points: np.ndarray) -> np.ndarray:
    points = np.asarray(points, dtype=float)
    negative_mode = points[:, 0] < 0
    latent = np.where(
        negative_mode,
        (points[:, 0] + 1.50) / 0.48,
        (1.50 - points[:, 0]) / 0.42,
    )
    expected_second = np.where(
        negative_mode,
        1.20 + 0.32 * latent,
        -1.20 + 0.36 * latent,
    )
    manifold_penalty = 22.0 * (points[:, 1] - expected_second) ** 2
    return 92.0 + 5.0 * latent - manifold_penalty


class QuadraticForwardModel:
    def __init__(self, ridge: float = 1e-5) -> None:
        self.ridge = ridge
        self.coefficients: np.ndarray | None = None

    @staticmethod
    def features(x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=float)
        return np.column_stack([
            np.ones(len(x)),
            x[:, 0],
            x[:, 1],
            x[:, 0] ** 2,
            x[:, 1] ** 2,
            x[:, 0] * x[:, 1],
        ])

    def fit(self, x: np.ndarray, y: np.ndarray) -> "QuadraticForwardModel":
        features = self.features(x)
        penalty = np.eye(features.shape[1]) * self.ridge
        penalty[0, 0] = 0.0
        self.coefficients = np.linalg.solve(
            features.T @ features + penalty,
            features.T @ y,
        )
        return self

    def predict(self, x: np.ndarray) -> np.ndarray:
        if self.coefficients is None:
            raise RuntimeError("fit must be called first")
        return self.features(x) @ self.coefficients


def _select_diverse(
    pool: np.ndarray,
    score: np.ndarray,
    count: int = 2,
    minimum_distance: float = 0.8,
) -> np.ndarray:
    chosen: list[np.ndarray] = []
    for index in np.argsort(score):
        point = pool[index]
        if not chosen or all(np.linalg.norm(point - prior) >= minimum_distance for prior in chosen):
            chosen.append(point)
        if len(chosen) == count:
            break
    return np.asarray(chosen)


def run_historical_replay(seed: int = 453) -> dict[str, object]:
    x, y, modes = synthetic_process_data(seed=seed)
    train_x, replay_x = x[:280], x[280:]
    train_y, replay_y = y[:280], y[280:]
    constraints = ConstraintSet(
        lower=np.asarray([-2.3, -1.9]),
        upper=np.asarray([2.3, 1.9]),
    )
    gmr = JointGaussianMixture(components=2).fit(train_x, train_y)
    forward = QuadraticForwardModel().fit(train_x, train_y)
    rng = np.random.default_rng(seed + 1)
    raw_pool = rng.uniform(constraints.lower, constraints.upper, size=(6000, 2))
    pool, _ = constraints.filter(raw_pool)
    pool_prediction = forward.predict(pool)
    scale = train_x.std(axis=0)
    replay_targets = replay_y[::4]
    strategies = (
        "gmr_modes",
        "historical_neighbor",
        "forward_optimization",
        "bo_surrogate",
        "manual",
    )
    records: dict[str, list[dict[str, object]]] = {strategy: [] for strategy in strategies}

    gp_train = train_x[::2]
    gp_y = train_y[::2]
    kernel = np.exp(
        -0.5
        * np.sum(
            ((gp_train[:, None, :] - gp_train[None, :, :]) / scale) ** 2,
            axis=2,
        )
    )
    cholesky = np.linalg.cholesky(kernel + np.eye(len(gp_train)) * 0.08)
    alpha = np.linalg.solve(cholesky.T, np.linalg.solve(cholesky, gp_y))
    pool_kernel = np.exp(
        -0.5
        * np.sum(((pool[:, None, :] - gp_train[None, :, :]) / scale) ** 2, axis=2)
    )
    gp_mean = pool_kernel @ alpha
    solved = np.linalg.solve(cholesky, pool_kernel.T)
    gp_std = np.sqrt(np.maximum(1.0 - np.sum(solved**2, axis=0), 1e-8))

    for target in replay_targets:
        generated: dict[str, tuple[np.ndarray, list[dict[str, object]]]] = {}
        started = perf_counter()
        gmr_modes = gmr.conditional_modes(float(target))
        gmr_points, rejected = constraints.filter(
            np.asarray([candidate.point for candidate in gmr_modes]),
        )
        generated["gmr_modes"] = (
            gmr_points,
            [
                {
                    "component": candidate.component,
                    "conditional_weight": candidate.conditional_weight,
                    "conditional_log_density": candidate.conditional_log_density,
                }
                for candidate in gmr_modes
                if not constraints.rejection_reasons(np.asarray(candidate.point))
            ],
        )
        gmr_elapsed = (perf_counter() - started) * 1000

        nearest_order = np.argsort(np.abs(train_y - target))
        nearest_points = _select_diverse(
            train_x[nearest_order],
            np.arange(len(nearest_order), dtype=float),
        )
        generated["historical_neighbor"] = (
            nearest_points,
            [{} for _ in nearest_points],
        )
        generated["forward_optimization"] = (
            _select_diverse(pool, np.abs(pool_prediction - target)),
            [],
        )
        bo_score = np.abs(gp_mean - target) - 0.45 * gp_std
        generated["bo_surrogate"] = (_select_diverse(pool, bo_score), [])
        generated["manual"] = (np.asarray([[-1.50, 1.20]]), [])

        for strategy, (points, metadata) in generated.items():
            started_strategy = perf_counter()
            if len(points) == 0:
                records[strategy].append({
                    "target": float(target),
                    "candidates": [],
                    "rejected": rejected if strategy == "gmr_modes" else 0,
                    "elapsed_ms": gmr_elapsed if strategy == "gmr_modes" else 0.0,
                })
                continue
            predictions = forward.predict(points)
            truth = true_process_response(points)
            nearest_distance = np.min(
                np.linalg.norm(
                    (points[:, None, :] - train_x[None, :, :]) / scale,
                    axis=2,
                ),
                axis=1,
            )
            candidate_records = []
            for index, point in enumerate(points):
                details = metadata[index] if index < len(metadata) else {}
                candidate_records.append({
                    "point": [float(value) for value in point],
                    "forward_prediction": float(predictions[index]),
                    "true_value": float(truth[index]),
                    "target_error": float(abs(truth[index] - target)),
                    "nearest_distance": float(nearest_distance[index]),
                    "extrapolation": bool(nearest_distance[index] > 1.5),
                    "mode": "negative_temperature" if point[0] < 0 else "positive_temperature",
                    "constraint_reasons": list(constraints.rejection_reasons(point)),
                    **details,
                })
            elapsed = (
                gmr_elapsed
                if strategy == "gmr_modes"
                else (perf_counter() - started_strategy) * 1000
            )
            records[strategy].append({
                "target": float(target),
                "candidates": candidate_records,
                "rejected": rejected if strategy == "gmr_modes" else 0,
                "elapsed_ms": elapsed,
            })

    tolerance = 1.25
    summary: dict[str, dict[str, float | int | None]] = {}
    for strategy, runs in records.items():
        candidates = [
            candidate
            for run in runs
            for candidate in run["candidates"]  # type: ignore[index]
        ]
        hit_positions: list[int] = []
        hits = 0
        for run in runs:
            run_candidates = run["candidates"]  # type: ignore[index]
            position = next(
                (
                    index + 1
                    for index, candidate in enumerate(run_candidates)
                    if candidate["target_error"] <= tolerance
                ),
                None,
            )
            if position is not None:
                hits += 1
                hit_positions.append(position)
        diversities = []
        mode_counts = []
        for run in runs:
            run_candidates = run["candidates"]  # type: ignore[index]
            mode_counts.append(len({item["mode"] for item in run_candidates}))
            if len(run_candidates) > 1:
                points = np.asarray([item["point"] for item in run_candidates])
                diversities.append(float(np.linalg.norm(points[0] - points[1])))
        summary[strategy] = {
            "replay_targets": len(runs),
            "goal_hit_rate": hits / len(runs),
            "constraint_pass_rate": (
                sum(not item["constraint_reasons"] for item in candidates) / len(candidates)
                if candidates
                else 0.0
            ),
            "mean_pairwise_diversity": float(np.mean(diversities)) if diversities else 0.0,
            "mean_nearest_distance": (
                float(np.mean([item["nearest_distance"] for item in candidates]))
                if candidates
                else None
            ),
            "mean_modes_presented": float(np.mean(mode_counts)),
            "mean_experiments_to_hit": (
                float(np.mean(hit_positions)) if hit_positions else None
            ),
            "mean_generation_ms": float(np.mean([run["elapsed_ms"] for run in runs])),
        }

    return {
        "schema_version": "gmr-inverse-replay/v1",
        "seed": seed,
        "dataset": {
            "kind": "synthetic_two-route_process",
            "train_rows": len(train_x),
            "replay_rows": len(replay_x),
            "replay_targets": len(replay_targets),
            "input_variables": ["normalized_temperature", "normalized_hold_time"],
            "target": "normalized_property",
            "known_modes": sorted(set(int(value) for value in modes)),
        },
        "constraints": {
            "lower": constraints.lower.tolist(),
            "upper": constraints.upper.tolist(),
            "cross_field": "x0 * x1 <= -0.20",
        },
        "goal_tolerance": tolerance,
        "summary": summary,
        "example_target": records["gmr_modes"][len(replay_targets) // 2],
        "decision": {
            "status": "hold",
            "reason": (
                "複数の高密度経路を高速に提示できるが、合成連続2変数だけの結果であり、"
                "カテゴリ・組成和・実データbiasを含むTaskで再検証するまでproduction strategyにはしない"
            ),
            "next_gate": (
                "実TaskのTraining Snapshotを使い、grouped historical replayで"
                "forward optimizationとBOに対する非劣性を確認する"
            ),
        },
    }


def report_as_dict(report: dict[str, object]) -> dict[str, object]:
    return {
        **report,
        "implementation": {
            "gmr_candidate_contract": asdict(GmrCandidate((0.0, 0.0), 0, 1.0, 0.0)),
            "note": "shape example only; production Proposal Strategy registry is unchanged",
        },
    }
