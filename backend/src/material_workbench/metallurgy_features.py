from __future__ import annotations

import math
from typing import Mapping


def transformation_temperature_proxies(composition: Mapping[str, float]) -> dict[str, float]:
    """Return lightweight steel transformation-temperature proxies.

    These are deliberately transparent educational features, not grade
    acceptance criteria or causal estimates. Terms requiring unavailable
    elements are omitted rather than silently imputed.
    """

    c = composition["C"]
    si = composition["Si"]
    mn = composition["Mn"]
    ni = composition["Ni"]
    cr = composition["Cr"]
    mo = composition["Mo"]
    ti = composition["Ti"]
    n = composition["N"]
    return {
        "ac1_proxy_c": 723.0 - 10.7 * mn - 16.9 * ni + 29.1 * si + 16.9 * cr,
        "ac3_proxy_c": 910.0 - 203.0 * math.sqrt(c) - 15.2 * ni + 44.7 * si + 31.5 * mo,
        "ms_proxy_c": 539.0 - 423.0 * c - 30.4 * mn - 17.7 * ni - 12.1 * cr - 7.5 * mo,
        "ti_after_tin_proxy": max(ti - 3.42 * n, 0.0),
    }


def ar3_temperature_proxy(composition: Mapping[str, float], thickness_mm: float) -> float:
    """Simplified composition/thickness Ar3 proxy for demo feature design."""

    return (
        910.0
        - 310.0 * composition["C"]
        - 80.0 * composition["Mn"]
        - 20.0 * composition["Cu"]
        - 15.0 * composition["Cr"]
        - 55.0 * composition["Ni"]
        - 80.0 * composition["Mo"]
        + 0.35 * (thickness_mm - 8.0)
    )
