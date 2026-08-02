from __future__ import annotations

from typing import Any

from decision_workbench.contracts.candidate_project_contracts import CandidateInput
from decision_workbench.modeling.missingness import (
    MissingnessOperation,
    require_runtime_operation_allowed,
)


def require_candidate_operation_allowed(
    runtime: Any,
    candidate: CandidateInput,
    *,
    operation: MissingnessOperation,
) -> None:
    require_runtime_operation_allowed(runtime, candidate, operation=operation)
