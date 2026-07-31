"""Fixed allow-list of adapters compiled into the application."""

from __future__ import annotations

from material_workbench.modeling.packages.ports import Adapter
from material_workbench.modeling.packages.contracts import (
    RUNTIME_TYPES,
    PackageContractError,
)


def builtin_adapters() -> tuple[Adapter, ...]:
    """Instantiate only the adapters approved by application code."""

    from material_workbench.adapters.builtin_additive_terms import (
        BuiltinAdditiveTermsAdapter,
    )
    from material_workbench.adapters.builtin_deterministic_linear import (
        BuiltinDeterministicLinearAdapter,
    )
    from material_workbench.adapters.builtin_exact_gp import BuiltinExactGPAdapter
    from material_workbench.adapters.builtin_heteroscedastic_gp import (
        BuiltinHeteroscedasticExactGPAdapter,
    )
    from material_workbench.adapters.builtin_linear import BuiltinLinearAdapter
    from material_workbench.adapters.builtin_posterior_linear import (
        BuiltinPosteriorLinearAdapter,
    )
    from material_workbench.adapters.builtin_quantile_linear import (
        BuiltinQuantileLinearAdapter,
    )
    from material_workbench.adapters.gpytorch_static import GPyTorchStaticAdapter
    from material_workbench.adapters.lightgbm_booster import LightGBMBoosterAdapter
    from material_workbench.adapters.numpyro_posterior import (
        NumpyroDensePosteriorAdapter,
    )
    from material_workbench.adapters.sklearn_skops import SklearnSkopsAdapter

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
