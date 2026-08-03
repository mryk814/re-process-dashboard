"""Reviewed model-hypothesis cards and their non-executable projections."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from decision_workbench.contracts.model_hypothesis_contracts import (
    HypothesisComparisonAssessment,
    HypothesisSplitStrategy,
    HypothesisValidationProtocol,
    InferencePolicy,
    LatentProcess,
    ModelHypothesisCard,
    ModelHypothesisCatalog,
    ModelHypothesisContext,
    ModelHypothesisPresentation,
    ModelPlaygroundHandoff,
    ObservationProtocol,
    PriorPolicy,
    RequiredEvidence,
    SharingStructure,
)
from decision_workbench.modeling.observation_model_builder import (
    TRAINING_CODE_REVISION,
)


def _evidence() -> tuple[RequiredEvidence, RequiredEvidence]:
    return (
        RequiredEvidence(
            kind="synthetic_recovery",
            purpose="既知の生成構造を回復できる条件と限界を確認する",
        ),
        RequiredEvidence(
            kind="counterexample",
            purpose="仮定が崩れるデータで誤った採用を防げることを確認する",
        ),
    )


def _validation(
    *,
    split_strategy: HypothesisSplitStrategy = "grouped_kfold",
    cohort: str,
    metrics: tuple[str, ...],
) -> HypothesisValidationProtocol:
    return HypothesisValidationProtocol(
        split_strategy=split_strategy,
        comparison_cohort=cohort,
        metrics=metrics,
        required_evidence=_evidence(),
    )


_CARDS = (
    ModelHypothesisCard(
        id="ridge-linear-baseline",
        version="1.0.0",
        label="Ridge linear baseline",
        comparison_role="baseline",
        data_grain=(
            "source_row",
            "individual_observation",
            "parent_condition_mean",
            "replicate_context_mean",
        ),
        target_support=("continuous", "continuous_positive"),
        observation_protocol=ObservationProtocol(
            entity_role="材料または工程条件",
            observation_role="測定された連続応答",
            independent_unit="Validation Planで固定したgroup",
            measurement_protocol="Task contractが宣言する測定protocol",
            replicate_role="同一条件の反復測定",
            group_role="leakageを防ぐgroup",
        ),
        latent_process=LatentProcess(
            latent_quantity="入力に対する線形な条件付き平均",
            observation_link="identity link",
            observation_noise="cross-fit残差の経験分布",
            description="正則化した線形平均関数と観測残差を分ける軽量baseline",
        ),
        sharing_structure=SharingStructure(
            kind="global",
            group_role="全学習group",
            shared_parameters=("intercept", "feature coefficients"),
            description="全groupで一つの係数ベクトルを共有する",
        ),
        constraints=("linear conditional mean", "L2 regularization"),
        prior_policy=PriorPolicy(
            policy_id="regularization_only",
            description="確率的priorではなく固定L2正則化を用いる",
        ),
        inference_policy=InferencePolicy(
            policy_id="closed_form_ridge",
            description="allow-list済みRidge solverで決定的に推定する",
        ),
        identifiability_risks=(
            "強い共線性では個別係数の解釈が不安定",
        ),
        required_diagnostics=(
            "grouped out-of-fold residual",
            "support and extrapolation warning",
        ),
        validation_protocol=_validation(
            cohort="同一Task・target cohort・fold assignment",
            metrics=("MAE", "RMSE", "coverage", "interval width"),
        ),
        decision_outputs=(
            "point prediction",
            "empirical prediction interval",
            "baseline comparison evidence",
        ),
        known_failure_modes=(
            "非線形構造を平均化して見落とす",
            "training support外の線形外挿を過信する",
        ),
        required_capabilities=("point", "quantiles", "support_warning"),
        lifecycle_status="standard",
        recipe_identity={
            "kind": "standard_estimator",
            "recipe_id": "ridge.v1",
            "version": "1",
            "execution_status": "available",
        },
    ),
    ModelHypothesisCard(
        id="bayesian-additive-spline",
        version="1.0.0",
        label="Bayesian additive spline",
        comparison_role="candidate",
        data_grain=(
            "source_row",
            "individual_observation",
            "parent_condition_mean",
            "replicate_context_mean",
        ),
        target_support=("continuous", "continuous_positive"),
        observation_protocol=ObservationProtocol(
            entity_role="材料または工程条件",
            observation_role="測定された連続応答",
            independent_unit="Validation Planで固定したgroup",
            measurement_protocol="Task contractが宣言する測定protocol",
            replicate_role="同一条件の反復測定",
            group_role="leakageを防ぐgroup",
        ),
        latent_process=LatentProcess(
            latent_quantity="各入力の滑らかな加法効果の和",
            observation_link="identity link",
            observation_noise="plugin training residual variance",
            description="固定basisとsmoothnessに条件づけた潜在平均と新観測を分離する",
        ),
        sharing_structure=SharingStructure(
            kind="global",
            group_role="全学習group",
            shared_parameters=("intercept", "univariate smooth terms"),
            description="各入力の一変量効果を全groupで共有する",
        ),
        constraints=(
            "additive conditional mean",
            "fixed B-spline basis",
            "constant boundary extrapolation",
        ),
        prior_policy=PriorPolicy(
            policy_id="gaussian_smoothness",
            description="spline係数へ固定Gaussian smoothness priorを置く",
        ),
        inference_policy=InferencePolicy(
            policy_id="analytic_gaussian_posterior",
            description="固定basis・smoothing・noise条件下の解析的posterior",
        ),
        identifiability_risks=(
            "相関入力間で加法寄与の分担が一意にならない",
            "interactionを表現できない",
        ),
        required_diagnostics=(
            "term support",
            "latent credible interval",
            "posterior predictive interval",
        ),
        validation_protocol=_validation(
            cohort="同一Task・target cohort・fold assignment",
            metrics=("MAE", "RMSE", "coverage", "interval width"),
        ),
        decision_outputs=(
            "point prediction",
            "latent mean credible interval",
            "posterior predictive interval",
            "additive term explanation",
        ),
        known_failure_modes=(
            "強いinteractionを加法効果へ誤配分する",
            "固定smoothing条件外のposteriorとして過大解釈する",
        ),
        required_capabilities=(
            "point",
            "quantiles",
            "standard_deviation",
            "parametric_distribution",
            "support_warning",
            "response_curve",
        ),
        lifecycle_status="standard",
        recipe_identity={
            "kind": "standard_estimator",
            "recipe_id": "bayesian-additive-spline.v1",
            "version": "1",
            "execution_status": "available",
        },
    ),
    ModelHypothesisCard(
        id="exact-rbf-gaussian-process",
        version="1.0.0",
        label="Exact RBF Gaussian process",
        comparison_role="candidate",
        data_grain=(
            "source_row",
            "individual_observation",
            "parent_condition_mean",
            "replicate_context_mean",
        ),
        target_support=("continuous", "continuous_positive"),
        observation_protocol=ObservationProtocol(
            entity_role="材料または工程条件",
            observation_role="測定された連続応答",
            independent_unit="Validation Planで固定したgroup",
            measurement_protocol="Task contractが宣言する測定protocol",
            replicate_role="同一条件の反復測定",
            group_role="leakageを防ぐgroup",
        ),
        latent_process=LatentProcess(
            latent_quantity="入力空間上の滑らかな潜在応答関数",
            observation_link="identityまたは固定log1p link",
            observation_noise="Gaussian observation noise",
            description="RBF kernelの潜在関数と新観測noiseを分けて扱う",
        ),
        sharing_structure=SharingStructure(
            kind="global",
            group_role="全学習group",
            shared_parameters=("kernel amplitude", "length scales", "noise"),
            description="一つのkernel hyperparameter集合を全groupで共有する",
        ),
        constraints=(
            "stationary RBF kernel",
            "bounded row capacity",
            "bounded hyperparameter restarts",
        ),
        prior_policy=PriorPolicy(
            policy_id="bounded_gp_hyperparameters",
            description="allow-list済み範囲内でkernel parameterを探索する",
        ),
        inference_policy=InferencePolicy(
            policy_id="bounded_marginal_likelihood",
            description="固定restart上限の周辺尤度最適化を行う",
        ),
        identifiability_risks=(
            "length scaleとnoiseが小標本で代替し得る",
            "stationarity仮定が局所構造を隠す",
        ),
        required_diagnostics=(
            "kernel optimization result",
            "support distance",
            "predictive calibration",
        ),
        validation_protocol=_validation(
            cohort="同一Task・target cohort・fold assignment",
            metrics=("MAE", "RMSE", "coverage", "interval width"),
        ),
        decision_outputs=(
            "point prediction",
            "normal predictive distribution",
            "goal probability where supported",
        ),
        known_failure_modes=(
            "row capacityを超えて計算不能になる",
            "support外でprior meanへ戻る挙動を見落とす",
        ),
        required_capabilities=(
            "point",
            "quantiles",
            "standard_deviation",
            "parametric_distribution",
            "support_warning",
            "response_curve",
        ),
        lifecycle_status="standard",
        recipe_identity={
            "kind": "standard_estimator",
            "recipe_id": "exact-gp-rbf.v1",
            "version": "1",
            "execution_status": "available",
        },
    ),
    ModelHypothesisCard(
        id="welding-charpy-observation-family",
        version="1.0.0",
        label="Welding Charpy grouped observation model",
        comparison_role="candidate",
        data_grain=("grouped_observation_family",),
        target_support=("continuous_positive",),
        observation_protocol=ObservationProtocol(
            entity_role="溶接条件",
            observation_role="試験温度ごとのCharpy吸収エネルギー測定",
            independent_unit="parent welding condition",
            measurement_protocol="Charpy observation familyと試験温度を固定する",
            replicate_role="同一parent・温度の反復試験",
            group_role="parent condition",
        ),
        latent_process=LatentProcess(
            latent_quantity="溶接条件と試験温度に対する平均Charpy応答",
            observation_link="identity link",
            observation_noise="family-specific grouped residual",
            description="parent条件の潜在平均応答と温度別観測を分離する",
        ),
        sharing_structure=SharingStructure(
            kind="global",
            group_role="parent condition",
            shared_parameters=("observation-family feature coefficients",),
            description="Charpy familyの係数をparent条件間で共有する",
        ),
        constraints=(
            "Charpy observation family only",
            "grouped validation by parent condition",
            "reviewed Stage C feature contract",
        ),
        prior_policy=PriorPolicy(
            policy_id="regularization_only",
            description="現行経路は遷移curve priorではなく固定L2正則化を用いる",
        ),
        inference_policy=InferencePolicy(
            policy_id="grouped_ridge",
            description="既存Stage C grouped Ridge builderで推定する",
        ),
        identifiability_risks=(
            "温度supportが狭いgroupでは遷移形状を同定できない",
            "現行Ridgeは物理的な遷移curve parameterを持たない",
        ),
        required_diagnostics=(
            "parent-grouped validation",
            "temperature support",
            "observation-family cohort size",
        ),
        validation_protocol=_validation(
            cohort="charpy:target-usable",
            metrics=("MAE", "RMSE", "group coverage"),
        ),
        decision_outputs=(
            "Charpy point prediction",
            "grouped validation evidence",
            "temperature support warning",
        ),
        known_failure_modes=(
            "試験温度support外の遷移を物理curveとして誤読する",
            "parent conditionを跨ぐrow splitでleakageする",
        ),
        required_capabilities=(
            "point",
            "quantiles",
            "support_warning",
            "grouped_validation",
        ),
        lifecycle_status="shared_specialized",
        recipe_identity={
            "kind": "specialized_builder",
            "recipe_id": TRAINING_CODE_REVISION,
            "version": "1",
            "execution_status": "specialized_only",
        },
    ),
)

_CATALOG = ModelHypothesisCatalog(cards=_CARDS)


def model_hypothesis_catalog() -> ModelHypothesisCatalog:
    """Return the immutable bundled allow-list."""

    return _CATALOG


def validate_model_hypothesis_card(
    payload: Mapping[str, Any],
) -> ModelHypothesisCard:
    """Validate an LLM/developer proposal without executing supplied content."""

    return ModelHypothesisCard.model_validate(payload)


def assess_hypothesis_comparison(
    cards: Iterable[ModelHypothesisCard],
) -> HypothesisComparisonAssessment:
    selected = tuple(cards)
    warnings: list[str] = []
    codes: list[str] = []
    if len(selected) < 2:
        codes.append("single_hypothesis_only")
        warnings.append("比較には少なくとも2件の仮説が必要です。")
    if not any(card.comparison_role == "baseline" for card in selected):
        codes.append("baseline_missing")
        warnings.append(
            "baselineを含まない研究仮説だけの比較は改善量を判断できません。"
        )
    return HypothesisComparisonAssessment(
        status="warning" if warnings else "ready",
        card_ids=tuple(card.id for card in selected),
        warning_codes=tuple(codes),  # type: ignore[arg-type]
        warnings=tuple(warnings),
    )


def present_model_hypothesis(
    card: ModelHypothesisCard,
    context: ModelHypothesisContext,
) -> ModelHypothesisPresentation:
    missing = tuple(
        capability
        for capability in card.required_capabilities
        if capability not in context.available_capabilities
    )
    reasons: list[str] = []
    if context.data_grain not in card.data_grain:
        reasons.append(f"data_grain:{context.data_grain}")
    if context.target_support not in card.target_support:
        reasons.append(f"target_support:{context.target_support}")
    if missing:
        reasons.append("required_capabilities")
    required_data = (
        card.observation_protocol.entity_role,
        card.observation_protocol.observation_role,
        card.observation_protocol.independent_unit,
        card.observation_protocol.measurement_protocol,
        card.observation_protocol.replicate_role,
        card.observation_protocol.group_role,
        card.observation_protocol.time_role,
    )
    return ModelHypothesisPresentation(
        card_id=card.id,
        label=card.label,
        lifecycle_status=card.lifecycle_status,
        compatibility="incompatible" if reasons else "compatible",
        required_data=tuple(item for item in required_data if item is not None),
        missing_contracts=missing,
        incompatibility_reasons=tuple(reasons),
        handoff=ModelPlaygroundHandoff(recipe_identity=card.recipe_identity),
    )
