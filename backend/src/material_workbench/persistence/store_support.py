from __future__ import annotations

import json
from datetime import UTC, datetime
from material_workbench.contracts.chain_contracts import (
    SingleTaskProjectIdentity,
)
from material_workbench.contracts.candidate_project_contracts import (
    Candidate,
    ProjectCreateInput,
)


def _target_values_json(values: dict[str, object]) -> str:
    serializable = {
        key: value.model_dump() if hasattr(value, "model_dump") else value
        for key, value in values.items()
    }
    return json.dumps(serializable, ensure_ascii=False, sort_keys=True)


def _single_task_identity_json(payload: ProjectCreateInput) -> str:
    bindings = (
        payload.dataset_view_revision_id,
        payload.task_contract_digest,
        payload.model_package_ref_id,
        payload.model_package_manifest_digest,
    )
    if not any(bindings):
        return SingleTaskProjectIdentity(
            identity_kind="single_task",
            task_id=payload.task_id,
            binding_provenance="unbound_legacy",
        ).model_dump_json()
    if not all(bindings):
        raise ValueError("Project single-Task identity has partial immutable bindings")
    identity = SingleTaskProjectIdentity(
        identity_kind="single_task",
        task_id=payload.task_id,
        dataset_view_revision_id=payload.dataset_view_revision_id,
        task_contract_digest=payload.task_contract_digest or None,
        model_package_ref_id=payload.model_package_ref_id,
        model_package_manifest_digest=payload.model_package_manifest_digest or None,
        binding_provenance="explicit",
    )
    return identity.model_dump_json()


PROTECTED_PROJECT_IDS = frozenset({"default"})


class ProjectNotFoundError(LookupError):
    pass


class CandidateLimitError(ValueError):
    pass


class InvalidProjectDecisionError(ValueError):
    pass


class CandidateCopyConflictError(ValueError):
    pass


class ProtectedProjectError(ValueError):
    pass


class ActiveProjectPurgeError(ValueError):
    pass


class ProjectHasSuccessorsError(ValueError):
    pass


class ProjectHasDerivedCandidatesError(ValueError):
    pass


class ProjectGroupConflictError(ValueError):
    pass


class ProjectGroupUnavailableError(ValueError):
    pass


class CandidateArchivedError(ValueError):
    pass


class CandidateRevisionConflictError(ValueError):
    def __init__(self, current: Candidate) -> None:
        super().__init__("候補は別の操作で更新されています")
        self.current = current


class StoreDataIntegrityError(RuntimeError):
    pass


class ChainCatalogConflictError(ValueError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()
