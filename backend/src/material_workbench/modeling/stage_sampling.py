"""Application allow-list for sampling semantics of immutable model packages."""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from material_workbench.modeling.model_packages import VerifiedModelPackage


@dataclass(frozen=True)
class PackageSamplingCapability:
    method: str
    output_dependence: str


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
            method="independent-residual-normal-from-q05-q95/v1",
            output_dependence="independent",
        ),
        (
            "welding-stage-c-ridge-v1",
            "c6bcbefd7de06afa40d4463196c210dc79d45bcf94a32d22c8a3180660d353b1",
        ): PackageSamplingCapability(
            method="independent-residual-normal-from-q05-q95/v1",
            output_dependence="independent",
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
    return capability
