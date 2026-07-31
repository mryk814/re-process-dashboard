from __future__ import annotations

from dataclasses import dataclass

from decision_workbench.data.profiles.schema import DatasetInputProfile, RelationJoin


@dataclass(frozen=True)
class TaskDataRequirements:
    """Workbook relationships that can change a Task's usable rows."""

    relation_entity_types: frozenset[str]

    def requires_relation(self, join: RelationJoin) -> bool:
        return join.entity_type in self.relation_entity_types


def task_data_requirements(profile: DatasetInputProfile) -> TaskDataRequirements:
    """Derive blocking relation requirements from the declared Prediction Tasks.

    A Profile may describe auxiliary entities for lineage or evidence.  Their
    relationship columns are useful when present, but they must not block
    Dataset registration unless a Task consumes the entity or needs it on the
    route between a consumed entity and an observation parent.
    """

    entity_type_by_role = {
        entity.role: entity.type for entity in profile.shared.entities
    }
    required: set[str] = set()

    for task in profile.tasks.values():
        for mapping in task.mappings:
            entity_type = entity_type_by_role.get(mapping.role)
            if entity_type is not None:
                required.add(entity_type)
            if mapping.parent_entity_type:
                required.add(mapping.parent_entity_type)
        for observation in task.observations:
            required.add(observation.parent_entity_type)
            if observation.parent_column is None:
                entity_type = entity_type_by_role.get(observation.role)
                if entity_type is not None:
                    required.add(entity_type)

    joins_by_type = {
        join.entity_type: join for join in profile.shared.relation.joins
    }
    pending = list(required)
    while pending:
        entity_type = pending.pop()
        join = joins_by_type.get(entity_type)
        if join is None:
            continue
        parent_types = (
            join.edge_parent_entity_types
            if join.edge_parent_entity_types is not None
            else join.parent_entity_types
        )
        for parent_type in parent_types:
            if parent_type not in required:
                required.add(parent_type)
                pending.append(parent_type)

    return TaskDataRequirements(frozenset(required))
