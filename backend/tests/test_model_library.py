from __future__ import annotations

from fastapi.testclient import TestClient

from decision_workbench.application.chains import (
    resolve_task_stage_lock,
    resolve_task_stage_surface,
)
from decision_workbench.contracts.chain_contracts import (
    CandidateGraphInputSource,
    ChainBinding,
    ChainPort,
    ChainStage,
    ChainStageLock,
    DecisionOutput,
    ExternalBindingSource,
    GraphInput,
    PredictionGraphDefinition,
    StageContractSurface,
    build_prediction_graph_revision,
)
from decision_workbench.contracts.data_library_contracts import (
    ModelPackageRefCreateInput,
)


def _runtime_context(client: TestClient):
    return client.app.state.runtime_context


def test_model_library_lists_four_asset_families_without_local_locators(
    client: TestClient,
) -> None:
    store = client.app.state.store
    context = _runtime_context(client)
    before = (
        len(store.list_projects(include_archived=True)),
        len(store.list_chain_definitions()),
        len(store.list_chain_revisions()),
        len(
            context.workspace_catalog.list_model_package_refs(
                include_archived=True
            )
        ),
    )
    response = client.get("/api/model-library")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "model-library-catalog/v1"
    assert payload["tasks"]
    assert payload["packages"]
    assert payload["transforms"]
    assert payload["graphs"]
    assert all(item["asset_type"] == "task" for item in payload["tasks"])
    assert all(item["asset_type"] == "package" for item in payload["packages"])
    assert all(
        item["asset_type"] == "transform" for item in payload["transforms"]
    )
    assert all(item["asset_type"] == "graph" for item in payload["graphs"])
    encoded = response.text.lower()
    assert '"locator"' not in encoded
    assert '"manifest_json"' not in encoded
    assert "c:\\\\" not in encoded
    predictive_packages = [
        item for item in payload["packages"] if item["predictor_families"]
    ]
    assert predictive_packages
    assert all(item["feature_pipeline"] for item in predictive_packages)
    assert all(
        {
            "predictor_id",
            "target",
            "runtime_type",
            "predictive_family",
            "architecture_id",
        }
        == set(family)
        for item in predictive_packages
        for family in item["predictor_families"]
    )
    assert all("feature_recipe" in item for item in predictive_packages)
    assert any(item["validation_plans"] for item in predictive_packages)
    after = (
        len(store.list_projects(include_archived=True)),
        len(store.list_chain_definitions()),
        len(store.list_chain_revisions()),
        len(
            context.workspace_catalog.list_model_package_refs(
                include_archived=True
            )
        ),
    )
    assert after == before


def test_model_library_relates_tasks_packages_data_and_legacy_graphs(
    client: TestClient,
) -> None:
    payload = client.get("/api/model-library").json()

    tasks_by_id = {item["task_id"]: item for item in payload["tasks"]}
    packages = payload["packages"]
    assert any(
        package["reference_id"]
        in tasks_by_id[package["task_id"]]["package_reference_ids"]
        for package in packages
    )
    assert any(
        package["data_references"]["dataset_revision_ids"]
        and package["data_references"]["profile_digests"]
        and package["data_references"]["source_sha256s"]
        for package in packages
    )
    explicitly_experimental = [
        package
        for package in packages
        if "experimental" in package["version"].lower()
    ]
    assert explicitly_experimental
    assert all(
        package["state"]["lifecycle"] == "research_only"
        for package in explicitly_experimental
    )
    assert all(
        package["feature_pipeline"]
        and package["predictor_families"]
        and package["validation_plans"]
        for package in explicitly_experimental
    )
    legacy_revisions = [
        revision
        for graph in payload["graphs"]
        for definition in graph["definitions"]
        for revision in definition["revisions"]
        if revision["revision_contract"]["schema_version"]
        == "chain-revision/v1"
    ]
    assert legacy_revisions
    assert all(
        revision["state"]["lifecycle"] == "compatibility_only"
        for revision in legacy_revisions
    )
    assert all(
        definition["projection"]["topology"]["topological_layers"]
        for graph in payload["graphs"]
        for definition in graph["definitions"]
    )


def _publish_status_fixture(client: TestClient) -> tuple[str, str]:
    context = _runtime_context(client)
    registry = context.task_registry
    catalog = context.workspace_catalog
    task_id = ""
    task_surface = None
    task_lock = None
    for candidate_task_id in registry.available_task_ids:
        try:
            candidate_surface = resolve_task_stage_surface(
                registry,
                candidate_task_id,
            )
            candidate_lock = resolve_task_stage_lock(
                catalog,
                registry,
                candidate_surface,
            )
        except ValueError:
            continue
        task_id = candidate_task_id
        task_surface = candidate_surface
        task_lock = candidate_lock
        break
    assert task_id and task_surface is not None and task_lock is not None
    active_package = next(
        package
        for package in catalog.list_model_package_refs(
            include_archived=True
        )
        if package.task_id == task_id
        and package.task_contract_digest == task_lock.contract_digest
        and package.manifest_digest.removeprefix("sha256:")
        == task_lock.package_manifest_digest.removeprefix("sha256:")
    )
    historical_digest = "sha256:" + "f" * 64
    historical_package = catalog.upsert_model_package_ref(
        ModelPackageRefCreateInput(
            package_id=active_package.package_id,
            task_id=active_package.task_id,
            task_contract_digest=active_package.task_contract_digest,
            manifest_digest=historical_digest,
            locator=active_package.locator,
            manifest_json=active_package.manifest_json,
        )
    )
    optional_surface = StageContractSurface(
        stage_kind="deterministic_transform",
        contract_id="missing-optional-transform",
        contract_digest="sha256:" + "d" * 64,
        input_ports=(
            ChainPort(
                path="process.optional_input",
                value_kind="number",
                quantity="optional_input",
                unit="1",
            ),
        ),
        output_ports=(
            ChainPort(
                path="out",
                value_kind="number",
                quantity="optional_output",
                unit="1",
            ),
        ),
    )
    stages = (
        ChainStage(
            stage_id="required-task",
            stage_kind="task",
            contract_id=task_id,
        ),
        ChainStage(
            stage_id="optional-transform",
            stage_kind="deterministic_transform",
            contract_id=optional_surface.contract_id,
        ),
    )
    inputs = tuple(
        GraphInput(
            input_id=port.path,
            label=port.path,
            port=port,
            role="design_variable",
            value_source=CandidateGraphInputSource(
                source_kind="candidate",
                candidate_path=port.path,
            ),
            default_presentation_group="task",
        )
        for index, port in enumerate(task_surface.input_ports)
    ) + (
        GraphInput(
            input_id="process.optional_input",
            label="Optional input",
            port=optional_surface.input_ports[0],
            role="scenario_context",
            value_source=CandidateGraphInputSource(
                source_kind="candidate",
                candidate_path="process.optional_input",
            ),
            default_presentation_group="optional",
        ),
    )
    bindings = tuple(
        ChainBinding(
            target_stage_id="required-task",
            target_input_path=port.path,
            source=ExternalBindingSource(
                source_kind="external",
                path=port.path,
            ),
        )
        for index, port in enumerate(task_surface.input_ports)
    ) + (
        ChainBinding(
            target_stage_id="optional-transform",
            target_input_path="process.optional_input",
            source=ExternalBindingSource(
                source_kind="external",
                path="process.optional_input",
            ),
        ),
    )
    definition = PredictionGraphDefinition(
        graph_id="model-library-status-fixture",
        label="Model Library status fixture",
        stages=stages,
        inputs=inputs,
        bindings=bindings,
        decision_outputs=(
            DecisionOutput(
                output_id="required-output",
                source_stage_id="required-task",
                source_output_key=task_surface.output_ports[0].path,
                label="Required output",
                group="decision",
                role="primary_objective",
                required_for_complete_result=True,
            ),
            DecisionOutput(
                output_id="optional-output",
                source_stage_id="optional-transform",
                source_output_key="out",
                label="Optional output",
                group="diagnostic",
                role="diagnostic",
                required_for_complete_result=False,
            ),
        ),
    )
    contracts = {
        ("task", task_id): task_surface,
        (
            "deterministic_transform",
            optional_surface.contract_id,
        ): optional_surface,
    }
    optional_lock = ChainStageLock(
        contract_digest=optional_surface.contract_digest,
        package_manifest_digest="sha256:" + "e" * 64,
    )
    store = client.app.state.store
    store.register_chain_definition(definition)
    first = build_prediction_graph_revision(
        definition,
        revision=1,
        contracts=contracts,
        stage_locks={
            "required-task": task_lock,
            "optional-transform": optional_lock,
        },
    )
    store.register_chain_revision(first, contracts=contracts)
    second = build_prediction_graph_revision(
        definition,
        revision=2,
        contracts=contracts,
        stage_locks={
            "required-task": task_lock.model_copy(
                update={"package_manifest_digest": historical_digest}
            ),
            "optional-transform": optional_lock,
        },
    )
    store.register_chain_revision(second, contracts=contracts)
    return definition.graph_id, historical_package.id


def test_model_library_distinguishes_degraded_superseded_and_unavailable(
    client: TestClient,
) -> None:
    graph_id, historical_package_id = _publish_status_fixture(client)

    payload = client.get("/api/model-library").json()
    historical_package = next(
        item
        for item in payload["packages"]
        if item["reference_id"] == historical_package_id
    )
    assert historical_package["state"]["availability"] == "unavailable"
    assert historical_package["feature_pipeline"]
    assert historical_package["predictor_families"]
    assert historical_package["validation_plans"]
    graph = next(item for item in payload["graphs"] if item["graph_id"] == graph_id)
    revisions = sorted(
        (
            revision
            for definition in graph["definitions"]
            for revision in definition["revisions"]
        ),
        key=lambda item: item["revision"],
    )
    assert revisions[0]["state"]["availability"] == "degraded", [
        (stage["stage_id"], stage["reason"])
        for stage in revisions[0]["stages"]
    ]
    assert revisions[0]["state"]["lifecycle"] == "superseded"
    assert revisions[1]["state"]["availability"] == "unavailable"
    assert revisions[1]["state"]["lifecycle"] == "current"
    required_stage = next(
        stage
        for stage in revisions[1]["stages"]
        if stage["stage_id"] == "required-task"
    )
    assert "active" in required_stage["reason"]
    transform = next(
        item
        for item in payload["transforms"]
        if item["transform_id"] == "missing-optional-transform"
    )
    assert transform["state"]["availability"] == "unavailable"
    assert transform["graph_revision_ids"] == [
        f"{graph_id}:r1",
        f"{graph_id}:r2",
    ]
    assert graph["latest_revision_id"] == f"{graph_id}:r2"
    assert graph["state"] == revisions[1]["state"]
    for revision in revisions:
        state = revision["state"]
        assert state["reason"]
        assert state["impact"]
        assert state["recovery_hint"]
