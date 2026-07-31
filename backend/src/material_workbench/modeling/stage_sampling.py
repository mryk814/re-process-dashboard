"""Application allow-list for sampling semantics of immutable model packages."""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from material_workbench.modeling.packages.verification import VerifiedModelPackage


@dataclass(frozen=True)
class PackageSamplingCapability:
    method: str
    output_dependence: str
    output_bounds: Mapping[str, tuple[float | None, float | None]]


_STAGE_B_OUTPUTS = (
    "C", "Si", "Mn", "P", "S", "Ni", "Cr", "Mo",
    "Cu", "Ti", "B", "Nb", "V", "Al", "N", "O",
)
_STAGE_C_OUTPUT_BOUNDS = {
    "TS": (0.0, None),
    "YS": (0.0, None),
    "EL": (0.0, 100.0),
    "RA": (0.0, 100.0),
    "CHARPY_ENERGY": (0.0, None),
    "BRITTLE_FRACTURE": (0.0, 100.0),
    "CORROSION_RATE": (0.0, None),
}


# Existing packages are immutable: enabling Chain sampling must not rewrite a
# manifest and invalidate an already-pinned Chain Revision or snapshot.  The
# application therefore opts in exact, reviewed manifest digests.  Changing
# any package byte removes the capability until this allow-list is reviewed.
_ALLOWLIST = MappingProxyType(
    {
        (
            "welding-consumable-stage-b-ridge-v1",
            "670f57fad186c409cb12bf50af47169c57f3b902d37518698b39b09aff1a3380",
        ): PackageSamplingCapability(
            method="independent-residual-normal-bounded-from-q05-q95/v1",
            output_dependence="independent",
            output_bounds=MappingProxyType(
                {target: (0.0, None) for target in _STAGE_B_OUTPUTS}
            ),
        ),
        # v1と同じbuilder・同じ元データから、TaskDefinitionのラベル修正（#359）で
        # 契約digestだけが変わった版。predictor構成・出力・境界はv1と同一である
        # ことを確認して同じsampling semanticsを許可する。
        (
            "welding-consumable-stage-b-ridge-v2",
            "570e2902e33aa3530b1127e40bab7783c78e2ad94895fc79f085b325da10af8c",
        ): PackageSamplingCapability(
            method="independent-residual-normal-bounded-from-q05-q95/v1",
            output_dependence="independent",
            output_bounds=MappingProxyType(
                {target: (0.0, None) for target in _STAGE_B_OUTPUTS}
            ),
        ),
        # v2と同じ学習器・予測artifact。v3はStage B正本Profileのdigestを
        # canonical training datasetにも固定し、provenanceを一貫させた版。
        (
            "welding-consumable-stage-b-ridge-v3",
            "b22594d3e26f68728bca297efba61e29bb832d5f0aaa80abee46f60f01d72987",
        ): PackageSamplingCapability(
            method="independent-residual-normal-bounded-from-q05-q95/v1",
            output_dependence="independent",
            output_bounds=MappingProxyType(
                {target: (0.0, None) for target in _STAGE_B_OUTPUTS}
            ),
        ),
        (
            "welding-stage-c-ridge-v1",
            "c6bcbefd7de06afa40d4463196c210dc79d45bcf94a32d22c8a3180660d353b1",
        ): PackageSamplingCapability(
            method="independent-residual-normal-bounded-from-q05-q95/v1",
            output_dependence="independent",
            output_bounds=MappingProxyType(_STAGE_C_OUTPUT_BOUNDS),
        ),
    }
)


def sampling_capability_for_package(
    package: VerifiedModelPackage,
) -> PackageSamplingCapability | None:
    capability = _ALLOWLIST.get(
        (package.manifest.package_id, package.manifest_sha256)
    )
    if capability is None:
        return None
    predictors = package.manifest.predictors
    if not predictors or any(
        predictor.runtime_type != "builtin.linear.v1"
        or predictor.predictive_family != "empirical_quantiles"
        for predictor in predictors
    ):
        return None
    if set(capability.output_bounds) != {predictor.target for predictor in predictors}:
        return None
    return capability
