"""Fixed allow-list of adapters compiled into the application."""

from __future__ import annotations

from decision_workbench.modeling.packages.ports import Adapter
from decision_workbench.modeling.packages.contracts import (
    RUNTIME_TYPES,
    PackageContractError,
)


def builtin_adapters() -> tuple[Adapter, ...]:
    """Instantiate only the adapters approved by application code."""

    from decision_workbench.adapters.builtin_additive_terms import (
        BuiltinAdditiveTermsAdapter,
    )
    from decision_workbench.adapters.builtin_deterministic_linear import (
        BuiltinDeterministicLinearAdapter,
    )
    from decision_workbench.adapters.builtin_exact_gp import BuiltinExactGPAdapter
    from decision_workbench.adapters.builtin_heteroscedastic_gp import (
        BuiltinHeteroscedasticExactGPAdapter,
    )
    from decision_workbench.adapters.builtin_linear import BuiltinLinearAdapter
    from decision_workbench.adapters.builtin_posterior_linear import (
        BuiltinPosteriorLinearAdapter,
    )
    from decision_workbench.adapters.builtin_quantile_linear import (
        BuiltinQuantileLinearAdapter,
    )
    from decision_workbench.adapters.gpytorch_static import GPyTorchStaticAdapter
    from decision_workbench.adapters.lightgbm_booster import LightGBMBoosterAdapter
    from decision_workbench.adapters.numpyro_posterior import (
        NumpyroDensePosteriorAdapter,
    )
    from decision_workbench.adapters.sklearn_skops import SklearnSkopsAdapter

    return (
        BuiltinLinearAdapter(),
        BuiltinDeterministicLinearAdapter(),
        BuiltinExactGPAdapter(),
        BuiltinHeteroscedasticExactGPAdapter(),
        BuiltinAdditiveTermsAdapter(),
        BuiltinQuantileLinearAdapter(),
        BuiltinPosteriorLinearAdapter(),
        SklearnSkopsAdapter(),
        LightGBMBoosterAdapter(),
        GPyTorchStaticAdapter(),
        NumpyroDensePosteriorAdapter(),
    )


class AdapterRegistry:
    """Resolve runtime types without accepting package-provided imports."""

    def __init__(self, adapters: tuple[Adapter, ...] | None = None) -> None:
        selected = builtin_adapters() if adapters is None else adapters
        self._adapters = {adapter.runtime_type: adapter for adapter in selected}
        if set(self._adapters) != RUNTIME_TYPES:
            raise PackageContractError(
                "adapter registry must implement exactly the approved runtime types"
            )

    def adapter_for(self, runtime_type: str) -> Adapter:
        try:
            return self._adapters[runtime_type]
        except KeyError as exc:
            raise PackageContractError(f"runtime is not registered: {runtime_type}") from exc
