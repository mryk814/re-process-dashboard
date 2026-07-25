from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from material_workbench.api.dependencies import get_store
from material_workbench.contracts.chain_contracts import (
    ChainDefinition,
    ChainRevision,
)
from material_workbench.persistence.store import Store


router = APIRouter(prefix="/api/chains", tags=["chains"])
StoreDependency = Annotated[Store, Depends(get_store)]


class ChainApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChainTemplateItem(ChainApiModel):
    definition_id: str
    definition: ChainDefinition
    revisions: tuple[ChainRevision, ...]


def _definition_id(definition: ChainDefinition) -> str:
    return f"{definition.chain_id}@{definition.digest.removeprefix('sha256:')[:12]}"


@router.get(
    "",
    response_model=list[ChainTemplateItem],
    operation_id="listChainTemplates",
)
def list_chain_templates(store: StoreDependency) -> list[ChainTemplateItem]:
    revisions = store.list_chain_revisions()
    return [
        ChainTemplateItem(
            definition_id=_definition_id(definition),
            definition=definition,
            revisions=tuple(
                revision
                for revision in revisions
                if revision.chain_id == definition.chain_id
                and revision.chain_definition_digest == definition.digest
            ),
        )
        for definition in store.list_chain_definitions()
    ]


@router.get(
    "/revisions/{revision_id}",
    response_model=ChainRevision,
    operation_id="getChainRevision",
)
def get_chain_revision(
    revision_id: str,
    store: StoreDependency,
) -> ChainRevision:
    revision = store.get_chain_revision(revision_id)
    if revision is None:
        raise HTTPException(404, "Chain Revisionが見つかりません")
    return revision
