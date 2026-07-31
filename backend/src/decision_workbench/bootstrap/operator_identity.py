"""Fail closed when an operator still supplies a retired identity key."""

from __future__ import annotations

import os
from collections.abc import Mapping


LEGACY_OPERATOR_ENV_PREFIX = "MATERIAL_WORKBENCH_"
CURRENT_OPERATOR_ENV_PREFIX = "DECISION_WORKBENCH_"


class LegacyOperatorEnvironmentError(RuntimeError):
    """A retired operator variable would otherwise be silently ignored."""


def reject_legacy_operator_environment(
    environment: Mapping[str, str] | None = None,
) -> None:
    values = os.environ if environment is None else environment
    retired = sorted(
        name for name in values if name.startswith(LEGACY_OPERATOR_ENV_PREFIX)
    )
    if not retired:
        return
    replacements = ", ".join(
        f"{name} -> {name.replace(LEGACY_OPERATOR_ENV_PREFIX, CURRENT_OPERATOR_ENV_PREFIX, 1)}"
        for name in retired
    )
    raise LegacyOperatorEnvironmentError(
        "旧環境変数は利用できません。"
        f"次の名前へ置き換えて再起動してください: {replacements}"
    )
