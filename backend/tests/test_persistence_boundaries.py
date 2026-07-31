from __future__ import annotations

import ast
from pathlib import Path

from decision_workbench.application import workspace_bundle
from decision_workbench.persistence.candidate_repository import CandidateRepository
from decision_workbench.persistence.chain_repository import ChainRepository
from decision_workbench.persistence.evidence_repository import EvidenceRepository
from decision_workbench.persistence.project_repository import ProjectRepository
from decision_workbench.persistence.store import Store
from decision_workbench.persistence.store_unit_of_work import WorkbenchUnitOfWork


def test_store_facade_declares_only_connection_and_migration_ownership() -> None:
    declared = {
        name
        for name, value in Store.__dict__.items()
        if callable(value) and not name.startswith("__")
    }
    assert declared == {"_connect", "_init"}


def test_aggregate_and_cross_aggregate_commands_have_explicit_owners() -> None:
    assert CandidateRepository.update_candidate
    assert ChainRepository.claim_chain_execution
    assert EvidenceRepository.create_snapshot

    assert "create_project" not in ProjectRepository.__dict__
    assert "archive_project" not in ProjectRepository.__dict__
    assert "update_chain_candidate" not in CandidateRepository.__dict__
    assert "create_snapshot_and_actual" not in EvidenceRepository.__dict__
    assert WorkbenchUnitOfWork.create_project
    assert WorkbenchUnitOfWork.archive_project
    assert WorkbenchUnitOfWork.update_chain_candidate
    assert WorkbenchUnitOfWork.create_snapshot_and_actual


def test_workspace_bundle_facade_exposes_use_cases_not_phase_implementation() -> None:
    assert workspace_bundle.create_workspace_backup.__module__.endswith(
        "workspace_bundle.backup"
    )
    assert workspace_bundle.prepare_workspace_restore.__module__.endswith(
        "workspace_bundle.restore_plan"
    )
    assert workspace_bundle.commit_workspace_restore.__module__.endswith(
        "workspace_bundle.service"
    )
    assert not hasattr(workspace_bundle, "_inspect_bundle")
    assert not hasattr(workspace_bundle, "_install_resources")


def test_workspace_trust_phases_do_not_reimport_unowned_high_level_services() -> None:
    bundle = (
        Path(__file__).parents[1] / "src" / "decision_workbench" / "application"
        / "workspace_bundle"
    )
    restricted = {
        "archive.py",
        "backup.py",
        "resource_install.py",
        "service.py",
        "shared.py",
    }
    forbidden = {
        "openpyxl",
        "decision_workbench.application.project_runtime",
        "decision_workbench.persistence.store",
        "decision_workbench.tasks.task_registry",
    }
    for filename in restricted:
        tree = ast.parse((bundle / filename).read_text(encoding="utf-8"))
        imported = {
            node.module
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module is not None
        } | {
            alias.name
            for node in tree.body
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        assert imported.isdisjoint(forbidden), (
            filename,
            sorted(imported & forbidden),
        )
        if filename != "resource_install.py":
            assert not any(
                module.startswith("decision_workbench.modeling") for module in imported
            ), filename
