import { expect, test } from "@playwright/test";

import {
  CHECK_CONTRACTS,
  createCurrentMainAcceptanceReceipt,
  passed,
  writeCurrentMainAcceptanceReceipt,
} from "./current-main-acceptance-report.mjs";
import { apiBaseUrl } from "./helpers";

const GRAPH_ID = "welding-material-split-output-demo-v1";

function check(id: string, kind: string, resourceId: string) {
  return passed(
    id,
    `${kind}:${resourceId}`,
    CHECK_CONTRACTS["prediction-graph"][id].assertion,
  );
}

type GraphExecution = {
  status: "complete" | "partial" | "unavailable";
  request_id: string;
  graph_revision_id: string;
  graph_revision_digest: string;
  project_binding_revision: number;
  project_binding_digest: string;
  candidate_id: string;
  candidate_revision: number;
  stages: Array<{
    stage_id: string;
    status: "latest" | "stale" | "failed" | "blocked_by_upstream";
    blocked_by_stage_ids: string[];
    result_input_digest: string;
  }>;
  terminal_outputs: Array<{
    output_id: string; status: string; required_for_complete_result: boolean;
  }>;
};

test("current main Journey B preserves authored Graph identity through failure, retry, branch invalidation, and resume", async ({ page, request }, testInfo) => {
  const atlasDigest = process.env.CURRENT_MAIN_CAPABILITY_ATLAS_DIGEST;
  expect(atlasDigest, "runner-pinned Capability Atlas digest").toMatch(/^sha256:[a-f0-9]{64}$/);
  const chainsResponse = await request.get(`${apiBaseUrl}/api/chains`);
  expect(chainsResponse.status(), await chainsResponse.text()).toBe(200);
  const templates = await chainsResponse.json() as Array<{
    definition: Record<string, unknown> & { graph_id?: string; chain_id?: string };
    revisions: Array<{
      graph_id?: string; chain_id?: string; revision: number; revision_digest: string;
      definition: Record<string, unknown>;
    }>;
    is_default: boolean;
    default_revision_id: string | null;
    latest_revision_id: string | null;
  }>;
  const graphTemplate = templates.find(({ definition, is_default }) => (
    definition.graph_id === GRAPH_ID && is_default
  ));
  const legacyTemplate = templates.find(({ definition }) => definition.chain_id === "welding-consumable-a-b-c-v1");
  expect(graphTemplate).toBeTruthy();
  expect(legacyTemplate).toBeTruthy();
  expect(graphTemplate!.default_revision_id).toBe(`${GRAPH_ID}:r2`);
  expect(graphTemplate!.latest_revision_id).toBe(graphTemplate!.default_revision_id);
  const sourceRevision = graphTemplate!.revisions.find(
    ({ graph_id, revision }) => (
      `${graph_id}:r${revision}` === graphTemplate!.default_revision_id
    ),
  )!;
  const sourceRevisionId = `${GRAPH_ID}:r${sourceRevision.revision}`;
  await page.goto("/?view=model-library&asset=graphs");
  await expect(page.getByRole("heading", { name: "モデル資産を確認する" })).toBeVisible();
  const graphCard = page.locator(".model-graph-card").filter({ hasText: GRAPH_ID });
  await expect(graphCard).toBeVisible();
  const revisionDetails = graphCard.locator("details.model-graph-detail").last();
  await revisionDetails.locator("summary").click();
  const draftResponsePromise = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && new URL(response.url()).pathname === "/api/prediction-graph-drafts"
  ));
  await revisionDetails.getByRole("button", { name: "Studioで新しいRevisionを作成" }).click();
  const draftResponse = await draftResponsePromise;
  expect(draftResponse.status(), await draftResponse.text()).toBe(201);
  const createdDraft = await draftResponse.json() as {
    draft_id: string; version: number;
    content: { definition: Record<string, unknown>; project_name: string };
  };
  expect(createdDraft.content.definition).toEqual(graphTemplate!.definition);
  await expect(page).toHaveURL(new RegExp(`view=chain-studio.*draft=${createdDraft.draft_id}`));
  const loadedDraftResponse = await request.get(
    `${apiBaseUrl}/api/prediction-graph-drafts/${createdDraft.draft_id}`,
  );
  expect(loadedDraftResponse.status(), await loadedDraftResponse.text()).toBe(200);
  const loadedDraft = await loadedDraftResponse.json() as typeof createdDraft;
  expect(loadedDraft.content.definition).toEqual(createdDraft.content.definition);
  expect(loadedDraft.content.definition.graph_id).toBe(GRAPH_ID);
  let releaseDraft!: () => void;
  const draftMayLoad = new Promise<void>((resolve) => { releaseDraft = resolve; });
  const resumedDraft = await page.context().newPage();
  await resumedDraft.route(
    new RegExp(`/api/prediction-graph-drafts/${createdDraft.draft_id}$`),
    async (route) => {
      await draftMayLoad;
      await route.continue();
    },
  );
  const catalogResponsePromise = resumedDraft.waitForResponse((response) => (
    response.request().method() === "GET"
    && new URL(response.url()).pathname === "/api/prediction-graphs/catalog"
  ));
  const draftNavigation = resumedDraft.goto(`/?view=chain-studio&draft=${createdDraft.draft_id}`);
  await expect(resumedDraft.getByRole("heading", { name: "指定されたdraftを読み込み中です" })).toBeVisible();
  releaseDraft();
  await draftNavigation;
  const catalogResponse = await catalogResponsePromise;
  expect(catalogResponse.status(), await catalogResponse.text()).toBe(200);
  await expect(resumedDraft.getByLabel("Graph ID")).toHaveValue(GRAPH_ID);
  await resumedDraft.close();

  const validationResponse = await request.post(`${apiBaseUrl}/api/prediction-graphs/validate`, {
    data: { definition: loadedDraft.content.definition },
  });
  expect(validationResponse.status(), await validationResponse.text()).toBe(200);
  const validation = await validationResponse.json() as {
    valid: boolean; definition_digest: string; candidate_adapter_id: string;
    findings: Array<{ code: string; message: string }>;
  };
  expect(validation.valid, JSON.stringify(validation.findings)).toBe(true);
  const publishResponse = await request.post(`${apiBaseUrl}/api/prediction-graphs/publish`, {
    data: { definition: loadedDraft.content.definition },
  });
  expect(publishResponse.status(), await publishResponse.text()).toBe(201);
  const published = await publishResponse.json() as {
    graph_revision_id: string;
    revision: { revision: number; graph_definition_digest: string; revision_digest: string };
  };
  expect(published.revision.graph_definition_digest).toBe(validation.definition_digest);

  const projectResponse = await request.post(`${apiBaseUrl}/api/prediction-graphs/projects`, {
    data: {
      project: { name: `Current main Journey B ${Date.now()}` },
      graph_revision_id: published.graph_revision_id,
      graph_revision_digest: published.revision.revision_digest,
      project_binding_revision: 1,
      project_binding_values: {},
    },
  });
  expect(projectResponse.status(), await projectResponse.text()).toBe(201);
  const project = await projectResponse.json() as {
    id: string; scientific_identity: { identity_kind: string; graph_revision_id: string; graph_revision_digest: string };
  };
  expect(project.scientific_identity).toMatchObject({
    identity_kind: "prediction_graph",
    graph_revision_id: published.graph_revision_id,
    graph_revision_digest: published.revision.revision_digest,
  });

  const legacyRevision = legacyTemplate!.revisions[0];
  const legacyProjectResponse = await request.post(`${apiBaseUrl}/api/projects`, {
    data: {
      name: "Current main Journey B candidate contract",
      scientific_identity: {
        identity_kind: "chain",
        chain_revision_id: `${legacyTemplate!.definition.chain_id}:r${legacyRevision.revision}`,
        chain_revision_digest: legacyRevision.revision_digest,
      },
    },
  });
  expect(legacyProjectResponse.status(), await legacyProjectResponse.text()).toBe(201);
  const legacyProject = await legacyProjectResponse.json() as { id: string };
  const starterResponse = await request.get(`${apiBaseUrl}/api/projects/${legacyProject.id}/chain/candidate-contract`);
  expect(starterResponse.status(), await starterResponse.text()).toBe(200);
  const starter = (await starterResponse.json() as {
    starter_candidate: {
      name: string;
      inputs: {
        process: Record<string, number>;
        categorical: Record<string, string>;
      };
      [key: string]: unknown;
    };
  }).starter_candidate;
  starter.name = "Journey B Graph candidate";
  Object.assign(starter.inputs.process, {
    heat_input_kj_per_mm: 1.43,
    voltage_v: 28.36,
    gas_flow_l_per_min: 25.4,
    wire_feed_speed_m_per_min: 7.5,
    preheat_temp_c: 80,
    test_temperature_c: -20,
  });
  Object.assign(starter.inputs.categorical, {
    shielding_gas: "100%CO2",
    welding_position: "下向",
    test_solution: "5%H2SO4",
  });
  const candidateResponse = await request.post(
    `${apiBaseUrl}/api/prediction-graphs/projects/${project.id}/candidates`,
    { data: starter },
  );
  expect(candidateResponse.status(), await candidateResponse.text()).toBe(201);
  const candidate = await candidateResponse.json() as {
    id: string; revision: number; name: string; inputs: { process: Record<string, number> };
    blend: unknown; editor_state: unknown; blend_validation: unknown; provenance: unknown; input_missing_kinds: unknown;
  };

  const execute = async (value: { id: string; revision: number }, requestId: string) => {
    const response = await request.post(
      `${apiBaseUrl}/api/prediction-graphs/projects/${project.id}/candidates/${value.id}/executions`,
      { data: { candidate_revision: value.revision, request_id: requestId, debounce_ms: 0 } },
    );
    expect(response.status(), await response.text()).toBe(200);
    return await response.json() as GraphExecution;
  };
  const failed = await execute(candidate, "current-main-b-stage-b-failure");
  expect(failed.status).toBe("partial");
  const failedStages = Object.fromEntries(failed.stages.map((stage) => [stage.stage_id, stage]));
  expect(failedStages.B.status).toBe("failed");
  for (const stageId of ["T", "U", "R"]) {
    expect(failedStages[stageId].status).toBe("blocked_by_upstream");
    expect(failedStages[stageId].blocked_by_stage_ids).toEqual(["B"]);
  }
  expect(failedStages.W.status).toBe("latest");

  const rejectedSnapshot = await request.post(
    `${apiBaseUrl}/api/prediction-graphs/projects/${project.id}/candidates/${candidate.id}/snapshots`,
    { data: { candidate_revision: candidate.revision, request_id: "must-not-snapshot-partial", debounce_ms: 0 } },
  );
  expect(rejectedSnapshot.status()).toBe(409);
  expect(await rejectedSnapshot.text()).toContain("最新");
  const snapshotsAfterReject = await request.get(
    `${apiBaseUrl}/api/prediction-graphs/projects/${project.id}/candidates/${candidate.id}/snapshots`,
  );
  const rejectedSnapshots = await snapshotsAfterReject.json() as unknown[];
  expect(rejectedSnapshots).toEqual([]);

  const completed = await execute(candidate, "current-main-b-retry");
  expect(completed.status).toBe("complete");
  expect(completed.stages.every(({ status }) => status === "latest")).toBe(true);
  const completedStages = Object.fromEntries(completed.stages.map((stage) => [stage.stage_id, stage]));

  const update = {
    name: candidate.name,
    inputs: structuredClone(candidate.inputs),
    blend: candidate.blend,
    editor_state: candidate.editor_state,
    blend_validation: candidate.blend_validation,
    provenance: candidate.provenance,
    input_missing_kinds: candidate.input_missing_kinds,
    expected_revision: candidate.revision,
  };
  update.inputs.process.wire_feed_speed_m_per_min = 8.25;
  const updatedResponse = await request.put(
    `${apiBaseUrl}/api/prediction-graphs/projects/${project.id}/candidates/${candidate.id}`,
    { data: update },
  );
  expect(updatedResponse.status(), await updatedResponse.text()).toBe(200);
  const updated = await updatedResponse.json() as { id: string; revision: number };
  const staleResponse = await request.get(
    `${apiBaseUrl}/api/prediction-graphs/projects/${project.id}/candidates/${candidate.id}/execution`,
  );
  expect(staleResponse.status(), await staleResponse.text()).toBe(200);
  const stale = await staleResponse.json() as GraphExecution;
  const staleStages = Object.fromEntries(stale.stages.map((stage) => [stage.stage_id, stage]));
  expect(staleStages.W.status).toBe("stale");
  for (const stageId of ["A", "B", "T", "U", "R"]) expect(staleStages[stageId].status).toBe("latest");

  const branchRerun = await execute(updated, "current-main-b-wire-branch");
  expect(branchRerun.status).toBe("complete");
  const rerunStages = Object.fromEntries(branchRerun.stages.map((stage) => [stage.stage_id, stage]));
  expect(rerunStages.W.result_input_digest).not.toBe(completedStages.W.result_input_digest);
  for (const stageId of ["A", "B", "T", "U", "R"]) {
    expect(rerunStages[stageId].result_input_digest).toBe(completedStages[stageId].result_input_digest);
  }

  const snapshotResponse = await request.post(
    `${apiBaseUrl}/api/prediction-graphs/projects/${project.id}/candidates/${candidate.id}/snapshots`,
    { data: { candidate_revision: updated.revision, request_id: "current-main-b-snapshot", debounce_ms: 0 } },
  );
  expect(snapshotResponse.status(), await snapshotResponse.text()).toBe(201);
  const snapshot = await snapshotResponse.json() as {
    snapshot_id: string;
    identity: {
      graph_revision_id: string; graph_revision_digest: string;
      project_binding_revision: number; project_binding_digest: string;
      candidate_id: string; candidate_revision: number;
    };
  };
  expect(snapshot.identity).toMatchObject({
    graph_revision_id: branchRerun.graph_revision_id,
    graph_revision_digest: branchRerun.graph_revision_digest,
    project_binding_revision: branchRerun.project_binding_revision,
    project_binding_digest: branchRerun.project_binding_digest,
    candidate_id: candidate.id,
    candidate_revision: updated.revision,
  });
  await page.goto(`/?view=chain-graph&project=${project.id}&candidate=${candidate.id}&chain_stage=W`);
  await expect(page.getByRole("heading", { name: "Decision Output summary" })).toBeVisible();
  await expect(page.locator(".chain-graph-node.latest")).toHaveCount(6);
  await page.reload();
  await expect(page).toHaveURL(/chain_stage=W/);
  await expect(page.getByRole("complementary", { name: "Chain inspector" })
    .getByRole("heading", { name: "Stage inspector · W" })).toBeVisible();
  await page.goBack();
  await page.goForward();
  await expect(page).toHaveURL(/chain_stage=W/);
  await expect(page.getByRole("complementary", { name: "Chain inspector" })
    .getByRole("heading", { name: "Stage inspector · W" })).toBeVisible();

  const resources = [
    { kind: "capability_atlas", id: "current-main", identity: { digest: atlasDigest } },
    {
      kind: "loading_state", id: `draft-${createdDraft.draft_id}`,
      identity: {
        surface: "Prediction Graph Studio", label: "指定されたdraftを読み込み中です",
        draft_id: createdDraft.draft_id, visible: true,
      },
    },
    {
      kind: "graph_draft_copy", id: createdDraft.draft_id,
      identity: {
        draft_id: createdDraft.draft_id, version: createdDraft.version,
        source_revision_id: sourceRevisionId,
        source_revision_digest: sourceRevision.revision_digest,
        definition_digest: validation.definition_digest,
      },
    },
    {
      kind: "graph_validation", id: validation.definition_digest,
      identity: {
        definition_digest: validation.definition_digest,
        candidate_adapter_id: validation.candidate_adapter_id,
        valid: validation.valid,
      },
    },
    {
      kind: "published_graph_revision", id: published.graph_revision_id,
      identity: {
        graph_revision_id: published.graph_revision_id,
        revision: published.revision.revision,
        graph_definition_digest: published.revision.graph_definition_digest,
        revision_digest: published.revision.revision_digest,
      },
    },
    {
      kind: "graph_binding", id: project.id,
      identity: {
        project_id: project.id, candidate_id: candidate.id,
        candidate_revision: candidate.revision,
        graph_revision_id: published.graph_revision_id,
        graph_revision_digest: published.revision.revision_digest,
      },
    },
    {
      kind: "failure_partition", id: failed.request_id,
      identity: {
        request_id: failed.request_id, failed_stage_id: "B",
        blocked_stage_ids: ["T", "U", "R"], blocked_by_stage_id: "B",
      },
    },
    {
      kind: "branch_retention", id: `retained-${failed.request_id}`,
      identity: {
        request_id: failed.request_id, retained_stage_id: "W",
        retained_result_input_digest: failedStages.W.result_input_digest,
      },
    },
    {
      kind: "snapshot_rejection", id: `rejected-${failed.request_id}`,
      identity: {
        request_id: failed.request_id, status_code: rejectedSnapshot.status(),
        snapshot_count: rejectedSnapshots.length,
      },
    },
    {
      kind: "graph_retry", id: completed.request_id,
      identity: {
        request_id: completed.request_id, status: completed.status,
        latest_stage_ids: completed.stages.filter(({ status }) => status === "latest").map(({ stage_id }) => stage_id),
      },
    },
    {
      kind: "branch_invalidation", id: branchRerun.request_id,
      identity: {
        request_id: branchRerun.request_id, stale_stage_id: "W",
        retained_stage_ids: ["A", "B", "T", "U", "R"],
        previous_result_input_digest: completedStages.W.result_input_digest,
        changed_result_input_digest: rerunStages.W.result_input_digest,
        retained_result_input_digests: Object.fromEntries(
          ["A", "B", "T", "U", "R"].map((stageId) => [
            stageId,
            rerunStages[stageId].result_input_digest,
          ]),
        ),
      },
    },
    {
      kind: "graph_snapshot", id: snapshot.snapshot_id,
      identity: { snapshot_id: snapshot.snapshot_id, ...snapshot.identity },
    },
    {
      kind: "navigation_resume", id: `graph-${candidate.id}-W`,
      identity: {
        candidate_id: candidate.id, stage_id: "W", url_parameter: "chain_stage=W",
        history_round_trip: true,
      },
    },
  ];
  await writeCurrentMainAcceptanceReceipt(testInfo, createCurrentMainAcceptanceReceipt({
    journey: "prediction-graph",
    atlasDigest: atlasDigest!,
    resources,
    checks: [
      check("capability_atlas_identity", "capability_atlas", "current-main"),
      check("representative_loading_state", "loading_state", `draft-${createdDraft.draft_id}`),
      check("model_library_graph_copy", "graph_draft_copy", createdDraft.draft_id),
      check("draft_validated", "graph_validation", validation.definition_digest),
      check("revision_published", "published_graph_revision", published.graph_revision_id),
      check("graph_project_candidate_bound", "graph_binding", project.id),
      check("direct_failure_and_blocked", "failure_partition", failed.request_id),
      check("unrelated_branch_retained", "branch_retention", `retained-${failed.request_id}`),
      check("partial_snapshot_rejected", "snapshot_rejection", `rejected-${failed.request_id}`),
      check("retry_completed", "graph_retry", completed.request_id),
      check("branch_specific_stale", "branch_invalidation", branchRerun.request_id),
      check("graph_snapshot_identity", "graph_snapshot", snapshot.snapshot_id),
      check("graph_inspection_resume", "navigation_resume", `graph-${candidate.id}-W`),
    ],
  }));
});
