import { expect, test, type APIRequestContext } from "@playwright/test";
import { apiBaseUrl } from "./helpers";

const TASK_ID = "mpea-room-tensile-v1";

function trainingRows() {
  return Array.from({ length: 9 }, (_, index) => ({
    Material: `playground-alloy-${index}`,
    File_Name: `playground-source-${index}`,
    Fe: 45 + index,
    Ni: 55 - index,
    Co: 0,
    Mn: 0,
    Cr: 0,
    Al: 0,
    Ti: 0,
    Cu: 0,
    Si: 0,
    V: 0,
    Nb: 0,
    B: 0,
    Mo: 0,
    Ta: 0,
    "Homogenization? (Yes=1, No=0)": "no",
    "Homogenization temp (°C)": 0,
    "Homogenization time (hr)": 0,
    "Rolling? (Yes=1, No=0)": "no",
    "Rolling temp (°C)": 0,
    "Rolling %": 0,
    "Recrystallization (Y=1, N=0)": "no",
    "Recrystallization temp (°C)": 0,
    "Recrystallization Time (min)": 0,
    "Aging? (yes=1, No=0)": "no",
    "Aging temp (°C)": 0,
    "Aging time (hr)": 0,
    "Tensile Yield Strength (Mpa)": 300 + 10 * index,
    "Ultimate Tensile Strength (MPa)": 500 + 12 * index,
    "Tensile Ductility(%)": 30 - index,
  }));
}

async function prepareTrainingSnapshot(request: APIRequestContext) {
  const datasetsResponse = await request.get(
    `${apiBaseUrl}/api/data-library/datasets?include_archived=true`,
  );
  expect(datasetsResponse.ok()).toBeTruthy();
  const datasets = await datasetsResponse.json() as Array<{
    supported_task_ids: string[];
    profile_revision: { id: string; profile_digest: string };
  }>;
  const source = datasets.find((item) => item.supported_task_ids.includes(TASK_ID));
  expect(source, "bundled MPEA Profile revision").toBeTruthy();
  const suffix = Date.now().toString(36);
  const rows = trainingRows();
  const connectorResponse = await request.post(
    `${apiBaseUrl}/api/data-lifecycle/connectors`,
    {
      data: {
        schema_version: "source-connector/v1",
        name: `Model Playground ${suffix}`,
        connector_type: "object_storage_json_v1",
        source_locator: `repository://model-playground-${suffix}.json`,
        selection: {
          schema_version: "object-selection/v1",
          format: "json_array",
          primary_key: "Material",
          included_fields: [],
          source_adapter_id: "model-playground-json-records",
          source_adapter_version: "1.0.0",
        },
        trigger_policy: "manual_only",
        schedule: null,
      },
    },
  );
  expect(connectorResponse.ok()).toBeTruthy();
  const connector = await connectorResponse.json() as { id: string };
  const fetchResponse = await request.post(
    `${apiBaseUrl}/api/data-lifecycle/connectors/${connector.id}/fetch`,
    {
      data: {
        schema_version: "source-fetch-request/v1",
        trigger_kind: "manual",
        object_content: JSON.stringify(rows),
        object_version: "fixture-v1",
        retry_of: null,
      },
    },
  );
  expect(fetchResponse.ok()).toBeTruthy();
  const fetched = await fetchResponse.json() as { snapshot: { id: string } };
  const numericFields = Object.entries(rows[0])
    .filter(([, value]) => typeof value === "number")
    .map(([key]) => key);
  const recipeResponse = await request.post(
    `${apiBaseUrl}/api/data-lifecycle/recipes`,
    {
      data: {
        schema_version: "curation-recipe/v1",
        recipe_id: `model-playground-${suffix}`,
        version: 1,
        name: "Model Playground fixture",
        steps: [
          {
            kind: "trim_strings_v1",
            fields: [
              "Material",
              "File_Name",
              "Homogenization? (Yes=1, No=0)",
              "Rolling? (Yes=1, No=0)",
              "Recrystallization (Y=1, N=0)",
              "Aging? (yes=1, No=0)",
            ],
          },
          { kind: "coerce_number_v1", fields: numericFields },
          {
            kind: "required_fields_v1",
            fields: ["Material", "File_Name", ...numericFields],
          },
          {
            kind: "target_eligibility_v1",
            fields: [
              "Tensile Yield Strength (Mpa)",
              "Ultimate Tensile Strength (MPa)",
              "Tensile Ductility(%)",
            ],
          },
        ],
      },
    },
  );
  expect(recipeResponse.ok()).toBeTruthy();
  const recipe = await recipeResponse.json() as { id: string };
  const curationResponse = await request.post(
    `${apiBaseUrl}/api/data-lifecycle/raw-snapshots/${fetched.snapshot.id}/curation-runs`,
    {
      data: {
        recipe_resource_id: recipe.id,
        profile_revision_id: source!.profile_revision.id,
        profile_digest: source!.profile_revision.profile_digest,
      },
    },
  );
  expect(curationResponse.ok()).toBeTruthy();
  const curation = await curationResponse.json() as { id: string };
  const approvalResponse = await request.post(
    `${apiBaseUrl}/api/data-lifecycle/curation-runs/${curation.id}/approve`,
    { data: { reason: "Model Playground E2E", overrides: [] } },
  );
  expect(approvalResponse.ok()).toBeTruthy();
  const approved = await approvalResponse.json() as { id: string };
  const snapshotResponse = await request.post(
    `${apiBaseUrl}/api/data-lifecycle/canonical-dataset-revisions/${approved.id}/training-snapshots`,
    {
      data: {
        purpose: "Model Playground comparison",
        targets: [
          { target_key: "TYS", field: "Tensile Yield Strength (Mpa)" },
          { target_key: "UTS", field: "Ultimate Tensile Strength (MPa)" },
          { target_key: "EL", field: "Tensile Ductility(%)" },
        ],
        split: {
          strategy_id: "sorted-group-round-robin-v1",
          group_field: "File_Name",
          folds: 3,
        },
        selection_policy: {
          schema_version: "training-snapshot-selection/v1",
          policy_id: "model-playground-e2e",
          revision: 1,
          exclusions: [{
            kind: "field_equals_any_v1",
            field: "Material",
            values: ["never-match"],
          }],
        },
      },
    },
  );
  expect(snapshotResponse.ok()).toBeTruthy();
  const snapshot = await snapshotResponse.json() as { id: string };
  const previewResponse = await request.get(
    `${apiBaseUrl}/api/model-playground/preview`
    + `?task_id=${TASK_ID}`
    + `&profile_revision_id=${encodeURIComponent(source!.profile_revision.id)}`
    + `&training_snapshot_id=${encodeURIComponent(snapshot.id)}`,
  );
  expect(
    previewResponse.ok(),
    `Model Playground preview: ${await previewResponse.text()}`,
  ).toBeTruthy();
  return {
    profileRevisionId: source!.profile_revision.id,
    trainingSnapshotId: snapshot.id,
  };
}

test("Model Playground builds, resumes, retries, compares and registers fixed evidence", async ({
  context,
  page,
  request,
}) => {
  test.setTimeout(180_000);
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  const fixed = await prepareTrainingSnapshot(request);

  await page.route("**/api/model-playground/preview?*", async (route) => {
    const response = await route.fetch();
    const payload = await response.json();
    const student = payload.recipes?.find(
      (recipe: { recipe_id: string }) =>
        recipe.recipe_id === "student-t-linear-regression.v1",
    );
    if (student) {
      student.lifecycle = "unavailable";
      student.availability = "unavailable_missing_dependency";
      student.reasons = ["optional dependency numpyro is unavailable"];
      student.target_readiness = student.target_readiness.map(
        (target: Record<string, unknown>) => ({
          ...target,
          status: "unavailable_missing_dependency",
          reasons: ["numpyroを追加すると比較できます"],
        }),
      );
    }
    await route.fulfill({ response, json: payload });
  });

  let additiveAttemptResponses = 0;
  await page.route(
    "**/api/model-playground/runs/*/recipes/bayesian-additive-spline.v1/attempts",
    async (route) => {
      const response = await route.fetch();
      const payload = await response.json();
      additiveAttemptResponses += 1;
      if (additiveAttemptResponses === 1) {
        const attempt = [...payload.attempts].reverse().find(
          (item: { recipe_id: string }) =>
            item.recipe_id === "bayesian-additive-spline.v1",
        );
        attempt.status = "failed";
        attempt.result = null;
        attempt.failure = {
          code: "e2e_scoped_failure",
          message: "Additiveの検証証拠を保存できませんでした",
          recovery_hint: "固定identityのまま再試行できます",
        };
      }
      await route.fulfill({ response, json: payload });
    },
  );

  await page.goto(
    `/?view=model-playground&model_task=${TASK_ID}`
    + `&model_profile_revision=${encodeURIComponent(fixed.profileRevisionId)}`
    + `&model_training_snapshot=${encodeURIComponent(fixed.trainingSnapshotId)}`,
  );
  await expect(page.getByRole("heading", { name: "比較する仮説を選ぶ" })).toBeVisible();

  const ridge = page.locator(".playground-recipe-card").filter({ hasText: "ridge.v1" });
  const additive = page.locator(".playground-recipe-card").filter({
    hasText: "bayesian-additive-spline.v1",
  });
  const student = page.locator(".playground-recipe-card").filter({
    hasText: "student-t-linear-regression.v1",
  });
  const initiallySelected = page.locator(".playground-recipe-card input:checked");
  while (await initiallySelected.count()) {
    await initiallySelected.first().uncheck();
  }
  await ridge.getByRole("checkbox", { name: "比較する" }).check();
  await additive.getByRole("checkbox", { name: "比較する" }).check();
  await expect(ridge.getByRole("checkbox", { name: "比較する" })).toBeChecked();
  await expect(additive.getByRole("checkbox", { name: "比較する" })).toBeChecked();
  await expect(student).toContainText("UNAVAILABLE");
  await expect(student).toContainText("numpyroを追加すると比較できます");
  await expect(student.getByRole("checkbox", { name: "比較する" })).toBeDisabled();

  await page.getByRole("button", { name: "固定identityでRunを作成" }).click();
  await expect(page.getByRole("heading", { name: "Recipeごとの進捗" })).toBeVisible();
  await expect(page.getByRole("alert")).toContainText(
    "Additiveの検証証拠を保存できませんでした",
    { timeout: 120_000 },
  );
  const failedAdditive = page.locator(".playground-attempt").filter({
    hasText: "Bayesian additive",
  });
  await expect(failedAdditive.getByRole("button", { name: "同じidentityで再試行" })).toBeVisible();
  await expect(failedAdditive.getByRole("button", { name: "条件を選び直して別Runを作成" })).toBeVisible();
  await failedAdditive.getByRole("button", { name: "同じidentityで再試行" }).click();
  await expect(failedAdditive).toContainText("COMPLETED", { timeout: 120_000 });

  await expect(page.getByRole("row", { name: /Inference/ })).toContainText(
    "analytic-gaussian",
  );
  await expect(page.getByRole("row", { name: /Interval semantics/ })).toContainText(
    "posterior predictive interval",
  );
  await expect(page.getByRole("row", { name: /Capabilities/ })).toContainText(
    "point",
  );
  await expect(page.getByText("未計測").first()).toBeVisible();

  await failedAdditive.getByRole("button", { name: "Model Libraryへ登録" }).click();
  await expect(failedAdditive).toContainText("active Packageは変更していません");
  await failedAdditive.getByRole("button", { name: "locatorをコピー" }).click();
  await expect(failedAdditive.getByRole("button", { name: "コピー済み" })).toBeVisible();

  const runId = new URL(page.url()).searchParams.get("model_run");
  expect(runId).toBeTruthy();
  await page.reload();
  await expect(page.getByText(runId!)).toBeVisible();
  await expect(page.getByText("Model Libraryに登録済み")).toBeVisible();

  const runResponse = await request.get(
    `${apiBaseUrl}/api/model-playground/runs/${runId}`,
  );
  const savedRun = await runResponse.json();
  const packageId = [...savedRun.attempts].reverse().find(
    (attempt: { registration?: unknown }) => attempt.registration,
  ).result.package_id;
  await page.getByRole("button", { name: "Model Libraryへ戻る" }).click();
  const registeredPackage = page.locator(".model-asset-card").filter({
    hasText: packageId,
  });
  await expect(registeredPackage).toBeVisible();
  await registeredPackage.getByRole(
    "button",
    { name: "同じデータでモデルを比較" },
  ).click();
  await expect(page.getByRole("heading", { name: "比較する仮説を選ぶ" })).toBeVisible();
  expect(new URL(page.url()).searchParams.get("model_training_snapshot")).toBe(
    fixed.trainingSnapshotId,
  );
});
