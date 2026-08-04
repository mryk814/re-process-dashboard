"""Transaction owner for the Model Exploration Run aggregate."""

from __future__ import annotations

import json

from decision_workbench.contracts.model_playground_contracts import (
    ModelExplorationRecipeAttempt,
    ModelExplorationRun,
)


class ModelExplorationRunNotFoundError(LookupError):
    pass


class ModelExplorationRunConflictError(RuntimeError):
    def __init__(self, current: ModelExplorationRun) -> None:
        super().__init__("Model Playground Runは別の操作で更新されています")
        self.current = current


class ModelExplorationRunMutationError(ValueError):
    pass


def _payload_json(run: ModelExplorationRun) -> str:
    return json.dumps(
        run.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _run(row: object) -> ModelExplorationRun:
    return ModelExplorationRun.model_validate_json(
        str(row["payload_json"])  # type: ignore[index]
    )


def _terminal_attempt_transition_is_allowed(
    previous: ModelExplorationRecipeAttempt,
    current: ModelExplorationRecipeAttempt,
) -> bool:
    if previous.status == "completed":
        return (
            current.status == "completed"
            and current.model_copy(update={"registration": previous.registration})
            == previous
            and (
                previous.registration is None
                or current.registration == previous.registration
            )
        )
    return current == previous


def _validate_mutation(
    previous: ModelExplorationRun,
    current: ModelExplorationRun,
) -> None:
    if previous.run_id != current.run_id:
        raise ModelExplorationRunMutationError("Run identity cannot change")
    if previous.definition != current.definition:
        raise ModelExplorationRunMutationError(
            "immutable Model Exploration context cannot change"
        )
    if current.execution_revision != previous.execution_revision + 1:
        raise ModelExplorationRunMutationError(
            "execution revision must increase by exactly one"
        )
    previous_ids = [item.attempt_id for item in previous.attempts]
    current_ids = [item.attempt_id for item in current.attempts]
    if current_ids[: len(previous_ids)] != previous_ids:
        raise ModelExplorationRunMutationError(
            "recipe attempts are append-only"
        )
    if len(current_ids) > len(previous_ids) + 1:
        raise ModelExplorationRunMutationError(
            "one mutation may append at most one recipe attempt"
        )
    for index, prior in enumerate(previous.attempts):
        updated = current.attempts[index]
        if prior.status == "running":
            if (
                updated.attempt_id != prior.attempt_id
                or updated.recipe_id != prior.recipe_id
                or updated.sequence != prior.sequence
                or updated.recipe_digest != prior.recipe_digest
                or updated.hypothesis != prior.hypothesis
                or updated.inference_identity != prior.inference_identity
                or updated.started_at != prior.started_at
                or updated.status == "running"
                and updated != prior
            ):
                raise ModelExplorationRunMutationError(
                    "running attempt identity cannot change"
                )
        elif not _terminal_attempt_transition_is_allowed(prior, updated):
            raise ModelExplorationRunMutationError(
                "completed or failed attempt evidence cannot be overwritten"
            )


class ModelExplorationRepository:
    def create_model_exploration_run(
        self,
        run: ModelExplorationRun,
    ) -> ModelExplorationRun:
        with self._connect() as connection:  # type: ignore[attr-defined]
            connection.execute(
                "INSERT INTO model_exploration_runs("
                "id,context_digest,execution_revision,payload_json,"
                "execution_payload_digest,created_at,updated_at"
                ") VALUES (?,?,?,?,?,?,?)",
                (
                    run.run_id,
                    run.definition.context_digest,
                    run.execution_revision,
                    _payload_json(run),
                    run.execution_payload_digest,
                    run.created_at.isoformat(),
                    run.updated_at.isoformat(),
                ),
            )
        return run

    def get_model_exploration_run(
        self,
        run_id: str,
    ) -> ModelExplorationRun | None:
        with self._connect() as connection:  # type: ignore[attr-defined]
            row = connection.execute(
                "SELECT payload_json FROM model_exploration_runs WHERE id=?",
                (run_id,),
            ).fetchone()
        return _run(row) if row is not None else None

    def list_model_exploration_runs(self) -> tuple[ModelExplorationRun, ...]:
        with self._connect() as connection:  # type: ignore[attr-defined]
            rows = connection.execute(
                "SELECT payload_json FROM model_exploration_runs "
                "ORDER BY updated_at DESC,id"
            ).fetchall()
        return tuple(_run(row) for row in rows)

    def replace_model_exploration_run(
        self,
        run: ModelExplorationRun,
        *,
        expected_revision: int,
    ) -> ModelExplorationRun:
        previous = self.get_model_exploration_run(run.run_id)
        if previous is None:
            raise ModelExplorationRunNotFoundError(run.run_id)
        if previous.execution_revision != expected_revision:
            raise ModelExplorationRunConflictError(previous)
        _validate_mutation(previous, run)
        with self._connect() as connection:  # type: ignore[attr-defined]
            changed = connection.execute(
                "UPDATE model_exploration_runs SET "
                "execution_revision=?,payload_json=?,execution_payload_digest=?,"
                "updated_at=? WHERE id=? AND execution_revision=?",
                (
                    run.execution_revision,
                    _payload_json(run),
                    run.execution_payload_digest,
                    run.updated_at.isoformat(),
                    run.run_id,
                    expected_revision,
                ),
            ).rowcount
        if changed != 1:
            current = self.get_model_exploration_run(run.run_id)
            if current is None:
                raise ModelExplorationRunNotFoundError(run.run_id)
            raise ModelExplorationRunConflictError(current)
        return run
