"""Identity-safe evidence protocol for Poisson/NB/ZIP count comparisons."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


AdoptionDecision = Literal["experimental", "production", "no_adopt"]


@dataclass(frozen=True)
class CountCandidateEvidence:
    estimator_id: Literal[
        "poisson.v1",
        "negative-binomial-regression.v1",
        "zero-inflated-poisson-regression.v1",
    ]
    cohort_digest: str
    fold_digest: str
    exposure_contract_digest: str
    metrics: dict[str, float]
    structural_zero_evidence: str | None = None


@dataclass(frozen=True)
class CountComparisonProtocol:
    cohort_digest: str
    fold_digest: str
    exposure_contract_digest: str
    candidates: tuple[CountCandidateEvidence, ...]
    adoption_decision: AdoptionDecision
    automatic_selection: Literal[False] = False


def compare_same_cohort_counts(
    candidates: tuple[CountCandidateEvidence, ...],
    *,
    adoption_decision: AdoptionDecision = "experimental",
) -> CountComparisonProtocol:
    """Bind count quality evidence without ranking or activating a Package.

    A score is evidence, not a selection function.  The explicit shared
    identity prevents accidental Poisson/NB/ZIP comparisons across different
    rows, folds, or offset meanings.
    """
    if len(candidates) < 2:
        raise ValueError("count comparison requires at least two same-cohort candidates")
    first = candidates[0]
    identity = (first.cohort_digest, first.fold_digest, first.exposure_contract_digest)
    if any(
        (item.cohort_digest, item.fold_digest, item.exposure_contract_digest) != identity
        for item in candidates[1:]
    ):
        raise ValueError("count candidates must share cohort, fold, and exposure identities")
    zip_items = [item for item in candidates if item.estimator_id == "zero-inflated-poisson-regression.v1"]
    if any(item.structural_zero_evidence is None for item in zip_items):
        raise ValueError("ZIP comparison requires recorded structural-zero evidence")
    return CountComparisonProtocol(
        cohort_digest=first.cohort_digest,
        fold_digest=first.fold_digest,
        exposure_contract_digest=first.exposure_contract_digest,
        candidates=candidates,
        adoption_decision=adoption_decision,
    )
