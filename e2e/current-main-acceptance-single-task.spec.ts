import { readFileSync } from "node:fs";
import { expect, test, type Locator, type Page } from "@playwright/test";

import {
  CHECK_CONTRACTS,
  createCurrentMainAcceptanceReceipt,
  passed,
  writeCurrentMainAcceptanceReceipt,
} from "./current-main-acceptance-report.mjs";
import { apiBaseUrl, starterCandidate, type ProjectBinding } from "./helpers";

const TASK_ID = "current-main-calce-onboarding-v1";

async function openAdvancedSettings(page: Page) {
  const details = page.locator(".screening-advanced-settings");
  if (!(await details.evaluate((element) => element.hasAttribute("open")))) {
    await details.locator("> summary").click();
  }
}

function column(cards: Locator, name: string) {
  return cards.filter({ hasText: name });
}

function check(journey: "single-task", id: string, kind: string, resourceId: string) {
  return passed(
    id,
    `${kind}:${resourceId}`,
    CHECK_CONTRACTS[journey][id].assertion,
  );
}

test("current main Journey A onboards one public CSV and preserves its decision evidence through resume", async ({ page, request }, testInfo) => {
  const atlasDigest = process.env.CURRENT_MAIN_CAPABILITY_ATLAS_DIGEST;
  expect(atlasDigest, "runner-pinned Capability Atlas digest").toMatch(/^sha256:[a-f0-9]{64}$/);
  const derivation = JSON.parse(readFileSync(
    "docs/reports/battery-calce-cs2-derivation.json", "utf8",
  )) as {
    source: { provider: string; landing_page: string };
    derived_csv_sha256: string; rows: number;
  };
  expect(derivation.source.landing_page).toBe("https://calce.umd.edu/data");

  await page.goto("/?view=data-library");
  const paths = page.getByRole("region", { name: "追加するデータはどれですか" });
  await paths.getByRole("button", { name: /新しい予測問題/ }).click();
  const onboarding = page.getByRole("region", { name: "完全に新しいTaskを準備" });
  await onboarding.locator('input[type="file"]').setInputFiles(
    "data/source/external/battery_calce_cs2_cycles.csv",
  );
  await onboarding.getByRole("button", { name: "CSVをプレビュー" }).click();
  await expect(onboarding).toContainText("3,131行・8列・relations 0件");
  await onboarding.getByLabel("Task ID").fill(TASK_ID);
  await onboarding.getByLabel("表示名").first().fill("Current-main CALCE容量維持率");
  await expect(onboarding.getByLabel("標準Estimator")).toHaveValue("ridge.v1");

  const cards = onboarding.locator(".csv-task-columns article");
  await column(cards, "cell_id").getByLabel("役割").selectOption("categorical");
  const cycle = column(cards, "cycle_index");
  await cycle.getByLabel("役割").selectOption("process");
  await cycle.getByLabel("単位").fill("cycle");
  await cycle.getByLabel("物理的許容範囲 min,max").fill("1,2000");
  await cycle.getByLabel("通常範囲 min,max").fill("1,829");
  await cycle.getByLabel("学習範囲 min,max").fill("1,829");
  const rate = column(cards, "discharge_rate_c");
  await rate.getByLabel("役割").selectOption("process");
  await rate.getByLabel("単位").fill("C");
  await rate.getByLabel("物理的許容範囲 min,max").fill("0.1,5");
  await rate.getByLabel("通常範囲 min,max").fill("0.5,1");
  await rate.getByLabel("学習範囲 min,max").fill("0.5,1");
  const capacity = column(cards, "capacity_percent");
  await capacity.getByLabel("役割").selectOption("output");
  await capacity.getByLabel("単位").fill("%");
  await capacity.getByLabel("目標方向").selectOption("at_least");
  await capacity.getByLabel("妥当範囲 min,max").fill("0,110");
  await capacity.getByLabel("表示範囲 min,max").fill("40,105");
  for (const ignored of ["capacity_ah", "observed_at", "source_file", "source_local_cycle"]) {
    await column(cards, ignored).getByLabel("役割").selectOption("");
  }
  await onboarding.getByLabel("1行=1観測であることを確認した").check();
  await onboarding.getByLabel("relationsなしであることを確認した").check();
  const prepare = onboarding.getByRole("button", {
    name: "Task・モデル・Datasetを準備してProject作成へ",
  });
  await expect(prepare).toBeEnabled();
  const preparedPromise = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && new URL(response.url()).pathname === "/api/data-library/csv-onboarding/prepare"
  ));
  await prepare.click();
  await expect(onboarding.getByRole("button", { name: "準備中…" })).toBeDisabled();
  const preparedResponse = await preparedPromise;
  expect(preparedResponse.status(), await preparedResponse.text()).toBe(200);
  const prepared = await preparedResponse.json() as {
    state: string; task_id: string; dataset_view_revision_id: string;
    dataset_revision_id: string; source_sha256: string;
    model_package_ref_id: string; reused_existing: boolean;
  };
  expect(prepared).toMatchObject({
    state: "ready",
    task_id: TASK_ID,
    source_sha256: derivation.derived_csv_sha256,
    reused_existing: false,
  });

  const readinessResponse = await request.get(`${apiBaseUrl}/api/readiness`);
  expect(readinessResponse.status(), await readinessResponse.text()).toBe(200);
  const readiness = await readinessResponse.json() as {
    ready: boolean; available_tasks: string[];
  };
  expect(readiness.ready).toBe(true);
  expect(readiness.available_tasks).toContain(TASK_ID);

  const creationOptionsResponse = await request.get(`${apiBaseUrl}/api/project-creation-options`);
  expect(creationOptionsResponse.status(), await creationOptionsResponse.text()).toBe(200);
  const creationOptions = await creationOptionsResponse.json() as {
    task_contract_digests: Record<string, string>;
    model_packages: Array<{ id: string; manifest_digest: string }>;
  };
  const preparedPackageOption = creationOptions.model_packages.find(({ id }) => (
    id === prepared.model_package_ref_id
  ));
  expect(preparedPackageOption).toBeTruthy();
  const binding: ProjectBinding = {
    task_id: TASK_ID,
    dataset_view_revision_id: prepared.dataset_view_revision_id,
    model_package_ref_id: prepared.model_package_ref_id,
    task_contract_digest: creationOptions.task_contract_digests[TASK_ID],
    model_package_manifest_digest: preparedPackageOption!.manifest_digest,
  };
  expect(binding.task_contract_digest).toBeTruthy();
  const datasetsResponse = await request.get(`${apiBaseUrl}/api/data-library/datasets`);
  expect(datasetsResponse.status(), await datasetsResponse.text()).toBe(200);
  const datasets = await datasetsResponse.json() as Array<{
    data_asset: { original_filename: string; sha256: string };
    profile_revision: { id: string; profile_digest: string };
    dataset_revision: { id: string };
    dataset_views: Array<{ id: string }>;
    supported_task_ids: string[];
  }>;
  const dataset = datasets.find((item) => item.dataset_revision.id === prepared.dataset_revision_id);
  expect(dataset).toBeTruthy();
  expect(dataset!.dataset_revision.id).toBe(prepared.dataset_revision_id);
  const packagesResponse = await request.get(`${apiBaseUrl}/api/data-library/model-packages`);
  expect(packagesResponse.status(), await packagesResponse.text()).toBe(200);
  const packages = await packagesResponse.json() as Array<{
    id: string; package_id: string; task_id: string; manifest_digest: string;
    task_contract_digest: string; storage_scope: string;
    manifest_json: { predictors: Array<{ runtime_type: string }> };
  }>;
  const modelPackage = packages.find(({ id }) => id === prepared.model_package_ref_id);
  expect(modelPackage).toBeTruthy();
  expect(modelPackage).toMatchObject({
    task_id: TASK_ID,
    storage_scope: "personal",
  });
  expect(modelPackage!.manifest_json.predictors[0].runtime_type).toBe("builtin.linear.v1");

  await page.goto("/?view=model-library&asset=packages");
  await expect(page.getByRole("heading", { name: "モデル資産を確認する" })).toBeVisible();
  const packageCard = page.locator(".model-asset-card").filter({ hasText: TASK_ID });
  await expect(packageCard).toBeVisible();
  await expect(packageCard.getByRole("button", { name: "Projectを作成", exact: true })).toBeEnabled();

  const projectResponse = await request.post(`${apiBaseUrl}/api/projects`, {
    data: { name: `Current main Journey A ${Date.now()}`, ...binding },
  });
  expect(projectResponse.status(), await projectResponse.text()).toBe(201);
  const project = await projectResponse.json() as { id: string };
  const starter = await starterCandidate(request, TASK_ID);
  const candidateResponse = await request.post(`${apiBaseUrl}/api/projects/${project.id}/candidates`, {
    data: { ...starter, name: "Journey A 基準候補" },
  });
  expect(candidateResponse.status(), await candidateResponse.text()).toBe(201);
  const candidate = await candidateResponse.json() as {
    id: string; revision: number; name: string; inputs: { process: Record<string, number> };
    provenance: unknown;
  };

  await page.goto(`/?view=candidates&project=${project.id}&candidate=${candidate.id}`);
  await expect(page.getByRole("heading", { name: /候補比較表/ })).toBeVisible();
  await expect(page.locator(".comparison-prediction-table")).toBeVisible();
  await expect(page.locator(".target-support-list").first()).toBeVisible();

  await page.goto(`/?view=explore&project=${project.id}`);
  await page.locator(".screening-mode-options").getByRole("button", { name: /有望候補を探す/ }).click();
  await page.getByLabel(/主目標: .*の下限/).fill("70");
  const goalPromise = page.waitForResponse((response) => (
    response.request().method() === "POST" && new URL(response.url()).pathname === "/api/screening"
  ));
  await page.locator(".screening-question-action .primary-button").click();
  const goalResponse = await goalPromise;
  expect(goalResponse.status(), await goalResponse.text()).toBe(201);
  const goalRun = await goalResponse.json() as {
    id: string; purpose: string; objective_definition_digest: string;
  };

  await page.locator(".screening-mode-options").getByRole("button", { name: /実験バッチを組む/ }).click();
  await page.getByLabel("バッチ件数").fill("5");
  await openAdvancedSettings(page);
  const batchSettings = page.getByRole("region", { name: "バッチの詳細設定" });
  await batchSettings.getByLabel("Control条件").selectOption({ label: "Journey A 基準候補" });
  await batchSettings.getByLabel("Control反復数").fill("2");
  const batchPromise = page.waitForResponse((response) => (
    response.request().method() === "POST" && new URL(response.url()).pathname === "/api/screening"
  ));
  await page.locator(".screening-question-action .primary-button").click();
  const batchResponse = await batchPromise;
  expect(batchResponse.status(), await batchResponse.text()).toBe(201);
  const batchRun = await batchResponse.json() as { id: string };
  const batchSurface = page.getByRole("region", { name: "実験バッチ", exact: true });
  const controlRows = batchSurface.locator("tbody tr").filter({ hasText: "固定Control" });
  const controlCount = await controlRows.count();
  const replicateCount = await controlRows.filter({ hasText: "反復" }).count();
  expect(controlCount).toBeGreaterThan(0);
  expect(replicateCount).toBeGreaterThan(0);
  await expect(controlRows.getByRole("button", { name: "この条件を候補にする" })).toHaveCount(0);
  const promotedPromise = page.waitForResponse((response) => (
    response.request().method() === "POST" && new URL(response.url()).pathname.endsWith("/candidates")
  ));
  await batchSurface.getByRole("button", { name: "この条件を候補にする" }).first().click();
  const promotedResponse = await promotedPromise;
  expect(promotedResponse.status(), await promotedResponse.text()).toBe(201);
  const promotedId = (await promotedResponse.json() as { candidates: Array<{ id: string }> }).candidates[0].id;
  const promotedResponseDetail = await request.get(
    `${apiBaseUrl}/api/projects/${project.id}/candidates/${promotedId}`,
  );
  expect(promotedResponseDetail.status(), await promotedResponseDetail.text()).toBe(200);
  const promoted = await promotedResponseDetail.json() as {
    id: string; revision: number; name: string; inputs: { process: Record<string, number> };
    provenance: { source_kind: string; source_ref: {
      run_id: string; source_run_id: string; batch_member_role: string;
      batch_member_order: number; point_index: number;
    } };
  };
  expect(promoted.provenance.source_ref).toMatchObject({
    run_id: batchRun.id,
    source_run_id: goalRun.id,
  });
  expect(["control", "replicate"]).not.toContain(promoted.provenance.source_ref.batch_member_role);

  await page.goto(`/?view=candidates&project=${project.id}&candidate=${promoted.id}&candidate_section=actuals`);
  const panel = page.getByRole("region", { name: "予測と実測の照合" });
  await panel.getByRole("button", { name: "実測を登録" }).click();
  await panel.getByLabel("実測値", { exact: true }).fill("72");
  await panel.getByLabel("実験番号").fill("CURRENT-MAIN-A");
  const historyUrl = new RegExp(`/api/projects/${project.id}/candidates/${promoted.id}/prediction-vs-actual$`);
  await page.route(historyUrl, async (route) => route.fulfill({
    status: 503,
    contentType: "application/json",
    body: JSON.stringify({ detail: "acceptance refresh failure" }),
  }));
  const actualPromise = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && new URL(response.url()).pathname.endsWith(`/candidates/${promoted.id}/actuals`)
  ));
  await panel.getByRole("button", { name: /の予測と実測を保存/ }).click();
  const actualResponse = await actualPromise;
  expect(actualResponse.status(), await actualResponse.text()).toBe(201);
  const actual = await actualResponse.json() as { id: string; snapshot_id: string };
  const recoveryNotice = "実測と固定Snapshotは保存済みです";
  await expect(panel.getByRole("status")).toContainText(recoveryNotice);
  await page.unroute(historyUrl);

  const fixedCycleIndex = promoted.inputs.process.cycle_index;
  const changedInputs = structuredClone(promoted.inputs);
  changedInputs.process.cycle_index += 1;
  const updatedResponse = await request.put(`${apiBaseUrl}/api/projects/${project.id}/candidates/${promoted.id}`, {
    data: {
      name: promoted.name, inputs: changedInputs, provenance: promoted.provenance,
      expected_revision: promoted.revision,
    },
  });
  expect(updatedResponse.status(), await updatedResponse.text()).toBe(200);
  const updated = await updatedResponse.json() as { revision: number };
  const comparisonResponse = await request.get(
    `${apiBaseUrl}/api/projects/${project.id}/candidates/${promoted.id}/prediction-vs-actual`,
  );
  expect(comparisonResponse.status(), await comparisonResponse.text()).toBe(200);
  const comparison = await comparisonResponse.json() as {
    comparisons: Array<{
      snapshot_id: string; candidate_revision: number;
      prediction: { canonical_input: { process: { cycle_index: number } } };
    }>;
  };
  expect(comparison.comparisons[0]).toMatchObject({
    snapshot_id: actual.snapshot_id,
    candidate_revision: promoted.revision,
  });
  expect(comparison.comparisons[0].prediction.canonical_input.process.cycle_index).toBe(fixedCycleIndex);
  expect(fixedCycleIndex).not.toBe(changedInputs.process.cycle_index);

  await page.reload();
  await expect(page).toHaveURL(new RegExp(`candidate=${promoted.id}.*candidate_section=actuals`));
  await expect(panel.getByRole("columnheader", { name: "固定予測" })).toBeVisible();
  await expect(panel.getByRole("columnheader", { name: "実測" })).toBeVisible();
  await page.goBack();
  await page.goForward();
  await expect(page).toHaveURL(new RegExp(`candidate=${promoted.id}.*candidate_section=actuals`));

  const resources = [
    { kind: "capability_atlas", id: "current-main", identity: { digest: atlasDigest } },
    {
      kind: "csv_onboarding_receipt", id: prepared.dataset_revision_id,
      identity: {
        state: prepared.state, task_id: TASK_ID,
        source_filename: "battery_calce_cs2_cycles.csv",
        source_sha256: prepared.source_sha256,
        dataset_revision_id: prepared.dataset_revision_id, format: "csv",
        row_count: derivation.rows, public_source_url: derivation.source.landing_page,
      },
    },
    {
      kind: "readiness_receipt", id: TASK_ID,
      identity: { task_id: TASK_ID, ready: readiness.ready, available_task: true },
    },
    {
      kind: "scientific_binding", id: project.id,
      identity: {
        task_id: TASK_ID, profile_revision_id: dataset!.profile_revision.id,
        profile_digest: dataset!.profile_revision.profile_digest,
        dataset_revision_id: dataset!.dataset_revision.id,
        dataset_view_revision_id: binding.dataset_view_revision_id,
        model_package_ref_id: binding.model_package_ref_id,
        task_contract_digest: binding.task_contract_digest,
        model_package_manifest_digest: binding.model_package_manifest_digest,
      },
    },
    {
      kind: "standard_package_publication", id: modelPackage!.id,
      identity: {
        package_id: modelPackage!.package_id, model_package_ref_id: modelPackage!.id,
        manifest_digest: modelPackage!.manifest_digest, estimator_id: "ridge.v1",
        runtime_type: "builtin.linear.v1", storage_scope: modelPackage!.storage_scope,
        reused_existing: prepared.reused_existing,
      },
    },
    {
      kind: "model_library_surface", id: `package-${modelPackage!.id}`,
      identity: { task_id: TASK_ID, model_package_ref_id: modelPackage!.id, action: "Projectを作成" },
    },
    {
      kind: "loading_state", id: "csv-prepare",
      identity: {
        surface: "Data Library CSV onboarding", label: "準備中…",
        control_disabled: true,
      },
    },
    {
      kind: "candidate_evaluation", id: candidate.id,
      identity: { candidate_id: candidate.id, revision: candidate.revision, target: "capacity_percent", support_visible: true },
    },
    {
      kind: "screening_run", id: goalRun.id,
      identity: {
        run_id: goalRun.id, purpose: goalRun.purpose,
        objective_definition_digest: goalRun.objective_definition_digest,
      },
    },
    {
      kind: "batch_exclusion", id: batchRun.id,
      identity: {
        batch_run_id: batchRun.id, control_count: controlCount,
        replicate_count: replicateCount, promotable_control_count: 0,
      },
    },
    {
      kind: "promoted_candidate", id: promoted.id,
      identity: {
        candidate_id: promoted.id, revision: promoted.revision,
        batch_run_id: promoted.provenance.source_ref.run_id,
        source_run_id: promoted.provenance.source_ref.source_run_id,
        batch_member_role: promoted.provenance.source_ref.batch_member_role,
        point_index: promoted.provenance.source_ref.point_index,
      },
    },
    {
      kind: "actual_evidence", id: actual.id,
      identity: {
        actual_id: actual.id, snapshot_id: actual.snapshot_id,
        fixed_revision: promoted.revision, current_revision: updated.revision,
        property: "capacity_percent",
      },
    },
    {
      kind: "prediction_snapshot", id: actual.snapshot_id,
      identity: {
        snapshot_id: actual.snapshot_id, candidate_id: promoted.id,
        candidate_revision: promoted.revision, canonical_cycle_index: fixedCycleIndex,
      },
    },
    {
      kind: "actual_evidence", id: `save-${actual.id}`,
      identity: {
        actual_id: actual.id, snapshot_id: actual.snapshot_id,
        refresh_status: 503, notice: recoveryNotice,
      },
    },
    {
      kind: "navigation_resume", id: `candidate-${promoted.id}`,
      identity: {
        candidate_id: promoted.id, url_parameter: "candidate_section=actuals",
        history_round_trip: true,
      },
    },
  ];
  await writeCurrentMainAcceptanceReceipt(testInfo, createCurrentMainAcceptanceReceipt({
    journey: "single-task",
    atlasDigest: atlasDigest!,
    resources,
    checks: [
      check("single-task", "capability_atlas_identity", "capability_atlas", "current-main"),
      check("single-task", "public_single_table_onboarded", "csv_onboarding_receipt", prepared.dataset_revision_id),
      check("single-task", "onboarded_task_ready", "readiness_receipt", TASK_ID),
      check("single-task", "profile_dataset_package_bound", "scientific_binding", project.id),
      check("single-task", "standard_package_published", "standard_package_publication", modelPackage!.id),
      check("single-task", "model_library_handoff", "model_library_surface", `package-${modelPackage!.id}`),
      check("single-task", "representative_loading_state", "loading_state", "csv-prepare"),
      check("single-task", "candidate_prediction_support", "candidate_evaluation", candidate.id),
      check("single-task", "goal_screening_saved", "screening_run", goalRun.id),
      check("single-task", "batch_controls_excluded", "batch_exclusion", batchRun.id),
      check("single-task", "batch_point_provenance_promoted", "promoted_candidate", promoted.id),
      check("single-task", "actual_fixed_prediction_distinct", "actual_evidence", actual.id),
      check("single-task", "immutable_snapshot_revision", "prediction_snapshot", actual.snapshot_id),
      check("single-task", "failure_safe_saved_evidence", "actual_evidence", `save-${actual.id}`),
      check("single-task", "candidate_resume", "navigation_resume", `candidate-${promoted.id}`),
    ],
  }));
});
